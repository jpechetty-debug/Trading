import yfinance as yf
import pandas as pd
import os
from src.logger import get_logger

log = get_logger(__name__)

STORAGE_DIR = "src/data/storage"

if not os.path.exists(STORAGE_DIR):
    os.makedirs(STORAGE_DIR)

from collections import OrderedDict

class DataStore:
    """
    The Single Source of Truth for Historical Data.
    Phase 1: Fetches from yfinance, stores as Parquet.
    """
    
    def __init__(self, max_cache=200):
        self.cache = OrderedDict() # Warm Cache (RAM) with LRU
        self.max_cache = max_cache

    def update_ticker(self, ticker):
        """Downloads full history and saves to local Parquet"""
        try:
            log.info("Ingesting %s...", ticker)
            
            if not ticker.endswith(".NS") and not ticker == "^NSEI" and not ticker.endswith(".BO"): 
                 ticker += ".NS"
            
            # Max 59d used to be safe within the 60d limit for 5m data
            df = yf.download(ticker, period="59d", interval="5m", progress=False, threads=False)
            
            if df is None or df.empty:
                log.error("No data for %s", ticker)
                return None
            
            # Cleaning / Normalization
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            
            df.dropna(inplace=True)
            
            if df.empty:
                log.error("Data empty after cleaning for %s", ticker)
                return None
                
            # Save to Parquet (Fast & Type-Safe)
            filename = f"{STORAGE_DIR}/{ticker}.parquet"
            df.to_parquet(filename, engine='pyarrow')
            
            # Update Cache (LRU)
            self.cache[ticker] = df
            self.cache.move_to_end(ticker)
            if len(self.cache) > self.max_cache:
                self.cache.popitem(last=False) # Evict LRU
            
            log.info("Saved %d rows to %s", len(df), filename)
            return df
        except Exception as e:
            log.error("Error updating %s: %s", ticker, e)
            return None

    def load_ticker(self, ticker):
        """Loads data from local fast storage with Warm Cache & Optimization"""
        if not ticker.endswith(".NS") and not ticker == "^NSEI": 
             ticker += ".NS"
             
        # 1. Check Warm Cache (RAM)
        if ticker in self.cache:
            self.cache.move_to_end(ticker) # Mark used
            return self.cache[ticker]
             
        filename = f"{STORAGE_DIR}/{ticker}.parquet"
        if not os.path.exists(filename):
            log.warning("%s not found locally. Triggering update...", ticker)
            return self.update_ticker(ticker)
        
        # 2. Optimized Read (Memory Map + PyArrow)
        df = pd.read_parquet(filename, engine='pyarrow', memory_map=True)
        
        # 3. Populate Cache (LRU)
        self.cache[ticker] = df
        if len(self.cache) > self.max_cache:
            self.cache.popitem(last=False) # Evict LRU
            
        return df

# Usage
if __name__ == "__main__":
    store = DataStore()
    store.update_ticker("RELIANCE.NS")
    store.update_ticker("^NSEI") # Essential for RS
