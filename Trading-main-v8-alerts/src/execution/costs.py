class NSECostModel:
    """
    Institutional Cost Calculator for Indian Markets (Equity Intraday).
    Includes STT, Brokerage, GST, Stamp Duty, and Impact Cost.
    """
    def __init__(self, instrument_type="EQUITY_INTRADAY"):
        self.type = instrument_type
        self.stt_rate = 0.00025 if instrument_type == "EQUITY_INTRADAY" else 0.001
        self.txn_charge_rate = 0.0000345
        self.gst_rate = 0.18
        self.stamp_duty_rate = 0.00003
        # CORRECTED SEBI FEE (10 Rs per crore = 0.000001)
        self.sebi_fees = 0.000001
        
        # Slippage/Impact: 0.05% per side ( Conservative)
        self.slippage_pct = 0.0005 

    def calculate_leg_cost(self, price, quantity, side, adv=0):
        turnover = price * quantity
        brokerage = min(20, 0.0003 * turnover)
        
        stt = turnover * self.stt_rate if side == "SELL" else 0
        if self.type == "EQUITY_DELIVERY": stt = turnover * self.stt_rate
        
        txn_charges = turnover * self.txn_charge_rate
        gst = (brokerage + txn_charges) * self.gst_rate
        sebi = turnover * self.sebi_fees
        stamp_duty = turnover * self.stamp_duty_rate if side == "BUY" else 0
        
        # UPGRADE 6.4: Non-linear Slippage Model
        if adv > 2_000_000:
             current_slippage = 0.0005 # Flat 0.05% for high liquidity
        elif adv > 0:
             # Scales with impact: min(0.15%, (quantity / adv) * 0.5)
             impact = (quantity / adv) * 0.5
             current_slippage = min(0.0015, impact)
        else:
             current_slippage = self.slippage_pct # Fallback
             
        slippage_cost = turnover * current_slippage
        
        return brokerage + stt + txn_charges + gst + sebi + stamp_duty + slippage_cost

    def round_trip_cost(self, entry_price, exit_price, quantity=1, adv=0):
        """
        Calculates total friction for a complete trade cycle.
        """
        buy_cost = self.calculate_leg_cost(entry_price, quantity, "BUY", adv=adv)
        sell_cost = self.calculate_leg_cost(exit_price, quantity, "SELL", adv=adv)
        return buy_cost + sell_cost
