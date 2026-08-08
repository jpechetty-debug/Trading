
import yfinance as yf
import pandas as pd

ticker = "SBILIFE.NS"
print(f"Testing yf.download for {ticker}...")
try:
    # Test yf.download with threads=False
    hist = yf.download(ticker, period="1mo", threads=False, progress=False)
    
    if hist.empty:
        print("FAILURE: Download returned empty.")
    else:
        print(f"SUCCESS: Downloaded {len(hist)} records.")
        print(hist.tail())
        print("Columns:", hist.columns)
except Exception as e:
    print(f"ERROR: {e}")
