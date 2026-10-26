# Kill Score Rubric (V6.5)

The Kill Score is a deterministic score (0–10) whose weights vary with recent
volatility. It is not a calibrated probability of success or evidence of an edge.

## 1. Adaptive Weighting System

| Component | Neutral Weight | High Vol Weight (1.5x) | Low Vol Weight | Logic |
| :--- | :---: | :---: | :---: | :--- |
| **Structure** | 2 | 1.6 | 2.6 | Price/EMA/VWAP alignment. |
| **Patterns** | 2 | 2.6 | 1.6 | NR7 Breakout / Vol Ignition. |
| **RS Sweet Spot**| 4 | 4 | 4 | Longs only: RS between 0.1 and 0.45 percentage points. |
| **Context** | 1 | 1 | 1 | Market Regime alignment. |
| **RS Slope** | 1 | 1 | 1 | Longs only: RS slope above the configured threshold. |

**Regime Detection**:
- **⚡ High Vol**: standard deviation of 20 five-minute returns > 1.5x that of 60 five-minute returns.
- **❄️ Low Vol**: the comparison is false. These are bar windows, not trading days.

The table's neutral weights are configuration base values; the implementation
selects the high- or low-volatility adjustment. Short scoring is asymmetric:
it has no mirrored negative-RS sweet-spot/slope bonus. This requires separate
strategy validation before interpreting long and short scores as equivalent.

## 2. Institutional Penalty Filters

| Penalty | Impact | Logic |
| :--- | :---: | :--- |
| **Extension** | -3 | Triggered if RS > 0.45 (Overextended). |
| **Liquidity** | -5 | 20-bar average five-minute volume ≤ 500,000 shares, average five-minute turnover ≤ ₹2 crore, or candle range / ATR ≥ 1.5. |

`MIN_AVG_5M_VOLUME` and `MIN_AVG_5M_TURNOVER` set those floors. They are not daily
ADV thresholds. The range/ATR ratio is not a measured bid/ask spread. This is a
score penalty, not an independent execution veto. Values were renamed to expose
their units; they have not been tuned or converted into a daily-liquidity model.

## 3. Threshold Definition

- **Target Threshold**: 6.0
- **Interpretation**: the score is a weighted sum; passing does not guarantee any
  particular number of components. Direction permissions, data freshness,
  risk/reward, cash, and portfolio limits are separate gates.
- **Polling alert threshold**: `ALERT_KILL_SCORE_THRESHOLD` defaults to 7.0;
  alerts are signals, not broker orders or portfolio risk approval.

## 4. Components of "True Vol Ignition"
1. **Volume**: > 3x Average Volume (20-period).
2. **Body**: Candle body > 70% of total range.
3. **ATR Expansion**: Candle range > 0.8x Current ATR.

## 5. Reward-to-Risk (RR) Verification
Even if a score is >= 6.0, the **Execution Model** performs a secondary veto:
- **Levels**: the prior 20-bar high/low, shifted one bar, define resistance and
  support. The signal candle cannot establish its own target barrier.
- **Long target**: signal close + 4 ATR, capped at 99.5% of unbroken resistance.
  If the completed signal close has already broken resistance, no resistance
  cap applies. Shorts use signal close − 4 ATR, floored at 100.5% of unbroken support.
- **API**: an absent/`None` barrier means no unbroken level was identified.
  Numeric barriers must be finite and positive; malformed wrong-side targets
  remain invalid. Raw prior levels are retained in the feature record.
- **Veto**: signed directional reward / initial stop distance must be ≥ **2.0**.
  Stops and targets remain anchored to the signal; both RR and quantity are
  rechecked at the next opening fill. A gap cannot erase an unbroken barrier.
- **R**: initial entry-to-stop distance, not ATR. The default target is 4 ATR,
  not 4R. The 0.75–1.25 conviction band is only one sizing multiplier;
  drawdown, volatility, risk ceilings, available cash, and costs also apply.
