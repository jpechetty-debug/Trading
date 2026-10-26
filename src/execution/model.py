import math
from numbers import Real
import src.config as config
from src.models import Order
from src.logger import get_logger

log = get_logger(__name__)

class ExecutionModel:
    """
    V7.0: Advanced Execution Model with Dynamic Position Sizing.
    Scalars: Conviction (Kill Score), Portfolio Drawdown, and Portfolio Volatility.
    """
    def __init__(self, 
                 base_risk_inr=None,
                 stop_mult=None,
                 target_mult=None):
        self.base_risk_inr = config.RISK_PER_TRADE if base_risk_inr is None else base_risk_inr
        self.stop_mult = config.STOP_LOSS_MULTIPLIER if stop_mult is None else stop_mult
        self.target_mult = config.TARGET_MULTIPLIER if target_mult is None else target_mult
        if not all(math.isfinite(v) and v > 0 for v in (self.base_risk_inr, self.stop_mult, self.target_mult)):
            raise ValueError('Risk budget and ATR multipliers must be finite and positive')

    def calculate_dynamic_risk(self, kill_score, portfolio_vol, current_dd):
        """
        V7.0: Conviction-Weighted Risks
        
        Scalars:
        1. Conviction (0.75x to 1.25x): Scale risk based on Kill Score (6.0 to 10.0 scale)
        2. Drawdown (0.5x to 1.0x): Defensive scaling when underwater
        3. Volatility (Target 12%): Global scaling to keep portfolio vol stable
        """
        if not all(math.isfinite(v) for v in (kill_score, portfolio_vol, current_dd)):
            raise ValueError('Sizing inputs must be finite')
        if portfolio_vol < 0 or current_dd < 0:
            raise ValueError('Volatility and drawdown must be nonnegative')

        # 1. Conviction Multiplier (0.75x at score 6.0 to 1.25x at score 10.0)
        # Formula: conviction = 0.75 + (kill_score - 6.0) / (10.0 - 6.0) * 0.5
        conviction = config.CONVICTION_MIN_MULT + \
                    (kill_score - config.KILL_SCORE_THRESHOLD) / \
                    (10.0 - config.KILL_SCORE_THRESHOLD) * \
                    (config.CONVICTION_MAX_MULT - config.CONVICTION_MIN_MULT)
        conviction = max(config.CONVICTION_MIN_MULT, min(config.CONVICTION_MAX_MULT, conviction))

        # 2. Drawdown Scalar (Defensive)
        # Reduce risk by up to 50% as drawdown approaches threshold (15%)
        # Formula: dd_scalar = max(0.5, 1.0 - (current_dd / max_dd) * 0.5)
        dd_scalar = max(0.5, 1.0 - (current_dd / config.MAX_DD_THRESHOLD) * 0.5)

        # 3. Portfolio Volatility Scalar (Target 12% Annualized)
        # annualized_vol = portfolio_vol * (252 ** 0.5)
        # vol_scalar = min(1.5, target_vol / annualized_vol)
        annualized_vol = portfolio_vol * (252 ** 0.5)
        if annualized_vol > 0:
            vol_scalar = min(1.5, config.TARGET_PORTFOLIO_VOL / annualized_vol)
        else:
            vol_scalar = 1.0
            
        final_risk = self.base_risk_inr * conviction * dd_scalar * vol_scalar
        return round(final_risk, 2)

    def generate_orders(self, signal, portfolio_vol=None, current_dd=0.0, *, entry_price=None, equity=None):
        """
        Generates buy/sell levels and performs dynamic sizing.
        """
        price = signal.get('close')
        atr = signal.get('atr')
        direction = signal.get('direction')
        if portfolio_vol is None:
            portfolio_vol = config.TARGET_PORTFOLIO_VOL / math.sqrt(252)
        kill_score = signal.get('kill_score', 6.0)
        rejected = Order(entry=0.0, stop=0.0, target=0.0, shares=0,
                         risk_reward=0.0, valid_rr=False).to_dict()
        if direction not in ('LONG', 'SHORT'):
            return rejected
        numeric_inputs = (price, atr, kill_score, portfolio_vol, current_dd)
        if not all(isinstance(v, Real) and math.isfinite(v) for v in numeric_inputs):
            return rejected
        if price <= 0 or atr <= 0 or portfolio_vol < 0 or current_dd < 0:
            return rejected
        if not all(isinstance(v, Real) and math.isfinite(v) and v > 0
                   for v in (self.base_risk_inr, self.stop_mult, self.target_mult)):
            return rejected
        if entry_price is not None and not (isinstance(entry_price, Real) and math.isfinite(entry_price) and entry_price > 0):
            return rejected
        if equity is not None and not (isinstance(equity, Real) and math.isfinite(equity) and equity > 0):
            return rejected
        
        # 1. Structural Levels
        swing_high = signal.get('swing_high', price)
        swing_low = signal.get('swing_low', price)
        # Missing/None means no unbroken structural barrier was identified.
        # Explicit numeric barriers must remain valid; do not turn malformed
        # wrong-side targets into trades by taking an absolute reward distance.
        resistance = signal.get('resistance')
        support = signal.get('support')
        if not all(isinstance(v, Real) and math.isfinite(v) and v > 0
                   for v in (swing_high, swing_low)):
            return rejected
        if any(v is not None and not (isinstance(v, Real) and math.isfinite(v) and v > 0)
               for v in (resistance, support)):
            return rejected

        if direction == "LONG":
            entry = price
            atr_stop = price - (self.stop_mult * atr)
            stop = min(atr_stop, swing_low - (0.1 * atr))
            max_target = entry + (self.target_mult * atr)
            target = min(max_target, resistance * 0.995) if resistance is not None else max_target
        else: # SHORT
            entry = price
            atr_stop = price + (self.stop_mult * atr)
            stop = max(atr_stop, swing_high + (0.1 * atr))
            max_target = entry - (self.target_mult * atr)
            target = max(max_target, support * 1.005) if support is not None else max_target

        # Stops/targets belong to the completed signal bar. A later fill changes
        # entry risk and reward, never the already-known structural levels.
        if entry_price is not None:
            entry = entry_price

        # 2. Sizing Math
        # Size against the same rounded prices that will actually be executed.
        entry, stop, target = (round(value, 2) for value in (entry, stop, target))
        if direction == 'LONG':
            risk_distance, reward_distance = entry - stop, target - entry
        else:
            risk_distance, reward_distance = stop - entry, entry - target
        if min(entry, stop, target) <= 0 or risk_distance <= 0 or reward_distance <= 0:
            return rejected
        risk_reward = reward_distance / risk_distance if risk_distance > 0 else 0
        
        if risk_distance <= 0:
            # Degenerate stop (entry == stop or inverted): risk is undefined.
            shares = 0
        else:
            # Dynamic Risk Calculation
            dynamic_risk_inr = self.calculate_dynamic_risk(kill_score, portfolio_vol, current_dd)
            if equity is not None:
                dynamic_risk_inr = min(dynamic_risk_inr, equity * config.MAX_TRADE_RISK_PCT)
            shares = int(dynamic_risk_inr / risk_distance)

        # NOTE: We deliberately do NOT floor shares to a minimum of 1.
        # `shares == 0` means either risk_distance was degenerate, or the
        # per-share risk exceeds the whole allocated risk budget — in both
        # cases forcing a 1-share trade (the old behavior) opens a position
        # with no real risk control behind it. Callers must treat
        # shares == 0 as "do not open this trade" (folded into valid_rr
        # below so existing callers that gate on valid_rr get this for
        # free).
        valid_rr = (risk_reward >= 2.0) and (shares > 0)

        return Order(
            entry=round(entry, 2),
            stop=round(stop, 2),
            target=round(target, 2),
            shares=shares,
            risk_reward=round(risk_reward, 2),
            valid_rr=valid_rr
        ).to_dict()
