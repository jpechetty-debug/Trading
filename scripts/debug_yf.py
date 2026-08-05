
import yfinance as yf
import pandas as pd

ticker = "SBILIFE.NS"
print(f"Testing yfinance for {ticker}...")
try:
    stock = yf.Ticker(ticker)
    hist = stock.history(period="1mo")
    
    if hist.empty:
        print("FAILURE: History is empty.")
    else:
        print(f"SUCCESS: Fetched {len(hist)} records.")
        print(hist.tail())
except Exception as e:
    print(f"ERROR: {e}")
