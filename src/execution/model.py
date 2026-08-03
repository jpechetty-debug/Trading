import math
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
                 base_risk_inr=config.RISK_PER_TRADE, 
                 stop_mult=config.STOP_LOSS_MULTIPLIER, 
                 target_mult=config.TARGET_MULTIPLIER):
        self.base_risk_inr = base_risk_inr
        self.stop_mult = stop_mult
        self.target_mult = target_mult

    def calculate_dynamic_risk(self, kill_score, portfolio_vol, current_dd):
        """
        V7.0: Conviction-Weighted Risks
        
        Scalars:
        1. Conviction (0.75x to 1.25x): Scale risk based on Kill Score (6.0 to 10.0 scale)
        2. Drawdown (0.5x to 1.0x): Defensive scaling when underwater
        3. Volatility (Target 12%): Global scaling to keep portfolio vol stable
        """
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

    def generate_orders(self, signal, portfolio_vol=0.12 / (252**0.5), current_dd=0.0):
        """
        Generates buy/sell levels and performs dynamic sizing.
        """
        price = signal['close']
        atr = signal['atr']
        direction = signal['direction']
        kill_score = signal.get('kill_score', 6.0)
        
        # 1. Structural Levels
        swing_high = signal.get('swing_high', price)
        swing_low = signal.get('swing_low', price)
        resistance = signal.get('resistance', price + (self.target_mult * atr))
        support = signal.get('support', price - (self.target_mult * atr))

        if direction == "LONG":
            entry = price
            atr_stop = price - (self.stop_mult * atr)
            stop = min(atr_stop, swing_low - (0.1 * atr))
            max_target = entry + (4.0 * atr)
            target = min(max_target, resistance * 0.995)
        else: # SHORT
            entry = price
            atr_stop = price + (self.stop_mult * atr)
            stop = max(atr_stop, swing_high + (0.1 * atr))
            max_target = entry - (4.0 * atr)
            target = max(max_target, support * 1.005)

        # 2. Sizing Math
        risk_distance = abs(entry - stop)
        reward_distance = abs(target - entry)
        risk_reward = reward_distance / risk_distance if risk_distance > 0 else 0
        
        if risk_distance == 0:
            shares = 0
        else:
            # Dynamic Risk Calculation
            dynamic_risk_inr = self.calculate_dynamic_risk(kill_score, portfolio_vol, current_dd)
            shares = int(dynamic_risk_inr / risk_distance)
            
        shares = max(1, shares)

        return Order(
            entry=round(entry, 2),
            stop=round(stop, 2),
            target=round(target, 2),
            shares=shares,
            risk_reward=round(risk_reward, 2),
            valid_rr=risk_reward >= 2.0
        ).to_dict()
