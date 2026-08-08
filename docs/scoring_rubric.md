# Kill Score Rubric (V6.5)

The Kill Score is an **Adaptive Institutional Rating** (0-10) that dynamically shifts weights based on the volatility regime.

## 1. Adaptive Weighting System

| Component | Neutral Weight | High Vol Weight (1.5x) | Low Vol Weight | Logic |
| :--- | :---: | :---: | :---: | :--- |
| **Structure** | 2 | 1.6 | 2.6 | Price/EMA/VWAP alignment. |
| **Patterns** | 2 | 2.6 | 1.6 | NR7 Breakout / Vol Ignition. |
| **RS Sweet Spot**| 4 | 4 | 4 | RS between 0.1 and 0.45. |
| **Context** | 1 | 1 | 1 | Market Regime alignment. |
| **RS Slope** | 1 | 1 | 1 | RS acceleration. |

**Regime Detection**:
- **⚡ High Vol**: Realized Vol (20d) > 1.5x Realized Vol (60d). Patterns take priority.
- **❄️ Low Vol**: Realized Vol (20d) <= 1.5x Realized Vol (60d). Structure takes priority.

## 2. Institutional Penalty Filters

| Penalty | Impact | Logic |
| :--- | :---: | :--- |
| **Extension** | -3 | Triggered if RS > 0.45 (Overextended). |
| **Liquidity** | -5 | **V6.5 Hard Floor**: ADV < 500k OR Turnover < ₹20Cr OR Spread Proxy > 1.5. |

## 3. Threshold Definition

- **Target Threshold**: 6.0
- **Requirement**: A score of 6.0 ensures that at least 3 high-conviction signals must align.

## 4. Components of "True Vol Ignition"
1. **Volume**: > 3x Average Volume (20-period).
2. **Body**: Candle body > 70% of total range.
3. **ATR Expansion**: Candle range > 0.8x Current ATR.

## 5. Reward-to-Risk (RR) Verification
Even if a score is >= 6.0, the **Execution Model** performs a secondary veto:
- **Target**: `min(4R, Resistance Clearance)`.
- **Veto**: Discard if Projected Reward / Risk < **2.0**.
