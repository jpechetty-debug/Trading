"""
Position V7.0 — Trade lifecycle management with:
  - Gap stop logic (V6.1)
  - Trailing stop (V6.5: activates at +2R, locks at +1R)  
  - End-of-Day flatten (V6.5: auto-close at 15:15 IST)
  - Max holding period (V6.5: auto-exit stale positions)
  - V7.0 Structural Exits: RS deterioration, Structure break, Vol collapse
"""
import src.config as config
from src.logger import get_logger

log = get_logger(__name__)

class Position:
    def __init__(self, trade_id, ticker, direction, entry, stop, target, r_unit, shares, features=None):
        self.trade_id = trade_id
        self.ticker = ticker
        self.direction = direction
        self.entry = entry
        self.stop = stop
        self.initial_stop = stop
        self.target = target
        self.r_unit = r_unit                # For sizing
        self.entry_atr = features.get('atr_val', 0) if features else 0
        self.shares = shares
        self.features = features or {}     # Features at entry

        self.open = True
        self.exit_price = None
        self.exit_reason = None
        self.entry_time = None
        self.exit_time = None

        self.trailing_activated = False
        self.best_price = entry
        self.bars_held = 0

    def _close(self, price, reason, timestamp):
        self.exit_price = price
        self.exit_reason = reason
        self.exit_time = timestamp
        self.open = False

    def _check_trailing_stop(self, current_price):
        if self.direction == "LONG":
            self.best_price = max(self.best_price, current_price)
            unrealized_r = (self.best_price - self.entry) / self.r_unit if self.r_unit > 0 else 0
            if unrealized_r >= config.TRAILING_STOP_ACTIVATION_R and not self.trailing_activated:
                self.trailing_activated = True
                new_stop = self.entry + (config.TRAILING_STOP_LOCK_R * self.r_unit)
                self.stop = max(self.stop, new_stop)
        else:
            self.best_price = min(self.best_price, current_price)
            unrealized_r = (self.entry - self.best_price) / self.r_unit if self.r_unit > 0 else 0
            if unrealized_r >= config.TRAILING_STOP_ACTIVATION_R and not self.trailing_activated:
                self.trailing_activated = True
                new_stop = self.entry - (config.TRAILING_STOP_LOCK_R * self.r_unit)
                self.stop = min(self.stop, new_stop)

    def _is_eod_flatten_time(self, timestamp):
        try:
            return (timestamp.hour > config.EOD_FLATTEN_HOUR or
                    (timestamp.hour == config.EOD_FLATTEN_HOUR and
                     timestamp.minute >= config.EOD_FLATTEN_MINUTE))
        except AttributeError:
            return False

    def _check_structural_exit(self, bar):
        """
        V7.0: Structural Exit Logic
        Exit when the edge has deteriorated before hitting stop/target.
        """
        price = bar['Close']
        rs = bar.get('RS_Score', 1.0)
        atr = bar.get('ATR', self.entry_atr)
        ema = bar.get('EMA_20', price)

        if self.direction == "LONG":
            if rs < 0.05: return "RS_DETERIORATION"
            if price < ema: return "STRUCTURE_BREAK"
            if self.entry_atr > 0 and atr < 0.5 * self.entry_atr: return "VOL_COLLAPSE"
        else:
            if price > ema: return "STRUCTURE_BREAK"
            # Short RS is inverse or not implemented yet, skip for now

        return None

    def update(self, bar, current_time):
        """
        V7.0: Full lifecycle update with structural exits.
        """
        if not self.open: return

        self.bars_held += 1
        open_price = bar['Open']
        high = bar['High']
        low = bar['Low']
        close = bar['Close']

        if self.direction == "LONG":
            if open_price < self.stop:
                self._close(open_price, "STOP_GAP", current_time)
                return
            if low <= self.stop:
                self._close(self.stop, "STOP", current_time)
                return
            if high >= self.target:
                self._close(self.target, "TARGET", current_time)
                return
            self._check_trailing_stop(high)
        else:
            if open_price > self.stop:
                self._close(open_price, "STOP_GAP", current_time)
                return
            if high >= self.stop:
                self._close(self.stop, "STOP", current_time)
                return
            if low <= self.target:
                self._close(self.target, "TARGET", current_time)
                return
            self._check_trailing_stop(low)

        # V7.0 Structural Exit
        exit_reason = self._check_structural_exit(bar)
        if exit_reason:
            self._close(close, exit_reason, current_time)
            return

        if self._is_eod_flatten_time(current_time):
            flatten_price = round((high + low) / 2, 2)
            self._close(flatten_price, "EOD_FLATTEN", current_time)
            return

        if self.bars_held >= config.MAX_HOLD_BARS:
            stale_price = round((high + low) / 2, 2)
            self._close(stale_price, "MAX_HOLD", current_time)
            return

    def calculate_net_pnl(self, cost_model):
        if self.exit_price is None: return 0.0
        diff = (self.exit_price - self.entry) if self.direction == "LONG" else (self.entry - self.exit_price)
        gross = diff * self.shares
        adv = self.features.get('vol_ma_20', 0)
        friction = cost_model.round_trip_cost(self.entry, self.exit_price, self.shares, adv=adv)
        return gross - friction

    def calculate_net_r(self, cost_model):
        if self.exit_price is None: return 0.0
        net_pnl = self.calculate_net_pnl(cost_model)
        risk_per_share = abs(self.entry - self.initial_stop)
        total_risk = risk_per_share * self.shares
        if total_risk == 0: return 0.0
        return round(net_pnl / total_risk, 2)
