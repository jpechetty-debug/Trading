import yfinance as yf
import pandas as pd

def test_fetch():
    print("Testing ^NSEI...")
    df = yf.download("^NSEI", period="5d", interval="1d")
    print(f"^NSEI Empty: {df.empty}")
    if not df.empty:
        print(df.tail())
    
    print("\nTesting NIFTYBEES.NS...")
    df_bees = yf.download("NIFTYBEES.NS", period="5d", interval="1d")
    print(f"NIFTYBEES.NS Empty: {df_bees.empty}")
    if not df_bees.empty:
        print(df_bees.tail())

if __name__ == "__main__":
    test_fetch()
