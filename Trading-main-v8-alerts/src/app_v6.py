import streamlit as st
import pandas as pd
import sys
import os
import datetime
import pytz

# Ensure project root is in path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.scanner import Scanner
from src.brain_b_v5 import BrainBV5

# --- Config ---
st.set_page_config(page_title="Indian Stock AI V7.0", layout="wide", page_icon="🦅")
st.markdown("""
<style>
    .stMetric {background-color: #0E1117; padding: 10px; border-radius: 5px; border: 1px solid #333;}
    .success {color: #00FF00;}
    .error {color: #FF0000;}
</style>
""", unsafe_allow_html=True)

# --- Init Engine ---
@st.cache_resource
def load_scanner():
    return Scanner()

scanner = load_scanner()
brain_b = BrainBV5()

# --- Header ---
st.title("🦅 Indian Stock AI V7.0 • Institutional Engine")
st.caption("Prop Desk Dashboard: Dynamic Sizing, Portfolio Vol Targeting & Market Breadth active.")

# --- Sidebar ---
with st.sidebar:
    st.header("🦅 System Status")
    
    # Market Status Logic
    def get_market_status():
        ist = pytz.timezone('Asia/Kolkata')
        now = datetime.datetime.now(ist)
        current_time = now.time()
        day = now.weekday()
        
        if day >= 5:
            return "MARKET CLOSED", "Weekend (Hours: Mon-Fri, 09:15-15:30 IST)", "🔴"
        
        pre_morning = datetime.time(9, 0)
        market_open = datetime.time(9, 15)
        market_close = datetime.time(15, 30)
        
        if pre_morning <= current_time < market_open:
            return "PRE-MARKET", "Pre-Opening (09:00 - 09:15 IST)", "🟡"
        elif market_open <= current_time < market_close:
            return "MARKET OPEN", "Live Trading (09:15 - 15:30 IST)", "🟢"
        else:
            return "MARKET CLOSED", "Closed (Hours: 09:15 - 15:30 IST)", "🔴"

    status_text, status_desc, icon = get_market_status()
    st.markdown(f"### {icon} {status_text}")
    st.caption(f"**Session**: {status_desc}")
    
    st.info("✅ V7.0 Engine Online")
    st.info("✅ Institutional Liquidity Filter Active")
    st.info("✅ Adaptive Regime Weights Active")
    st.text(f"Risk Unit: ₹10,000")
    
    if st.button("🔄 Clear Cache"):
        st.cache_data.clear()

st.divider()

# --- Main Interface ---

col1, col2 = st.columns([1, 3])

with col1:
    st.subheader("Market Context")
    if st.button("📡 Check Market Regime"):
        with st.spinner("Fetching Nifty 50 Data..."):
            nifty_df, regime = scanner.fetch_market_context()
            st.session_state['regime'] = regime
            
            if nifty_df is not None:
                st.session_state['nifty_val'] = nifty_df['Close'].iloc[-1]
            else:
                st.session_state['nifty_val'] = 0.0
                st.error("Could not fetch Nifty Data. Defaulting to Neutral.")
    
    if 'regime' in st.session_state:
        regime = st.session_state['regime']
        val = st.session_state['nifty_val']
        color = "green" if regime == "Bullish" else "red"
        st.markdown(f"### :{color}[{regime}]")
        st.metric("Nifty 50", f"{val:.2f}")
        
        if regime == "Bearish":
            st.warning("⚠️ Longs penalized. Shorts favored.")
        else:
            st.success("✅ Longs authorized.")

with col2:
    st.subheader("Opportunity Scanner")
    if st.button("🚀 Run V7.0 Scan (Parallel)"):
        if 'regime' not in st.session_state:
            st.error("Please check Market Regime first!")
        else:
            with st.spinner("Scanning 60 Tickers... Extracting Economic Truth..."):
                # We need to manually inject context since the scanner does it internally normally
                # But scanner.scan_market() does fetch context again. 
                # Let's just call scan_market() which handles everything.
                results = scanner.scan_market()
            
            if not results:
                st.warning("🦅 No setups found. The machine is disciplined.")
            else:
                st.success(f"Found {len(results)} Diamond Setups")
                
                # Convert to DataFrame for Display
                df_data = []
                for res in results:
                    risk_amount = res['shares'] * abs(res['entry'] - res['stop'])
                    df_data.append({
                        "Ticker": res['ticker'],
                        "Score": res['kill_score'],
                        "Dir": res['direction'],
                        "Entry": res['entry'],
                        "Stop": res['stop'],
                        "Target": res['target'],
                        "Shares": res['shares'],
                        "Risk (₹)": round(risk_amount, 2),
                        "Reasons": ", ".join(res['reasons'])
                    })
                
                df = pd.DataFrame(df_data)
                st.dataframe(
                    df, 
                    column_config={
                        "Score": st.column_config.ProgressColumn("Score", min_value=0, max_value=10, format="%d"),
                        "Risk (₹)": st.column_config.NumberColumn("Risk", format="₹%.2f"),
                    },
                    use_container_width=True
                )
                
                # Detailed Cards for Top 3
                st.divider()
                st.subheader("💎 Top Picks Details")
                for i, row in df.iterrows():
                    if i >= 3: break
                    with st.expander(f"{row['Ticker']} ({row['Dir']}) - Score: {row['Score']}/10", expanded=True):
                        c1, c2, c3, c4 = st.columns(4)
                        c1.metric("Entry", row['Entry'])
                        c2.metric("Stop", row['Stop'])
                        c3.metric("Target", row['Target'])
                        c4.metric("SIZE", f"{row['Shares']} qty", delta=f"₹{row['Risk (₹)']}")
                        st.caption(f"**Thesis**: {row['Reasons']}")
                        
                        # Fetch Brain B Commentary
                        with st.spinner(f"Brain B analyzing institutional sentiment for {row['Ticker']}..."):
                            analysis = brain_b.generate_commentary(
                                row['Ticker'], 
                                {"direction": row['Dir'], "kill_score": row['Score']}
                            )
                        
                        if analysis:
                            st.markdown("---")
                            score = analysis.get('sentiment_score', 5.0)
                            score_color = "green" if score >= 7 else ("orange" if score >= 4 else "red")
                            st.markdown(f"**BrainB Sentiment**: :{score_color}[{score}/10]")
                            st.markdown(f"*{analysis.get('commentary', 'No commentary available.')}*")
                            
                            risk = analysis.get('risk_warning', 'N/A')
                            if risk and risk != "N/A":
                                st.warning(f"**Risk Warning:** {risk}")
