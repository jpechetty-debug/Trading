
import streamlit as st
import yfinance as yf

st.title("YFinance Debugger in Streamlit")

ticker = "SBILIFE.NS"
st.write(f"Attempting to fetch {ticker}...")

try:
    stock = yf.Ticker(ticker)
    hist = stock.history(period="1mo")
    
    if hist.empty:
        st.error("History is empty.")
    else:
        st.success(f"Fetched {len(hist)} records.")
        st.write(hist.tail())
except Exception as e:
    st.error(f"Exception: {e}")
