import datetime
import pytz
import time
import pandas as pd
import src.config as config

IST = pytz.timezone('Asia/Kolkata')


def market_timestamp(value):
    """Naive inputs denote IST; aware inputs retain their instant in IST."""
    timestamp = pd.Timestamp(value)
    if pd.isna(timestamp):
        raise ValueError('A valid market timestamp is required')
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_convert("Asia/Kolkata").tz_localize(None)
    return timestamp


def market_index(values):
    index = pd.DatetimeIndex(pd.to_datetime(values))
    if index.tz is not None:
        index = index.tz_convert("Asia/Kolkata").tz_localize(None)
    return index


def is_flatten_time(value):
    timestamp = market_timestamp(value)
    return (timestamp.hour, timestamp.minute) >= (config.EOD_FLATTEN_HOUR, config.EOD_FLATTEN_MINUTE)


def is_entry_time(value):
    timestamp = market_timestamp(value)
    return (timestamp.weekday() < 5
            and (timestamp.hour, timestamp.minute) >= (9, 15)
            and not is_flatten_time(timestamp))

class MarketClock:
    def __init__(self):
        self.open_time = datetime.time(9, 15)
        self.close_time = datetime.time(15, 30)

    def is_market_open(self):
        now = datetime.datetime.now(IST)
        # 1. Check Weekend
        if now.weekday() > 4: # 5=Sat, 6=Sun
            return False, "WEEKEND"
        
        # 2. Check Time
        current_time = now.time()
        if current_time < self.open_time:
            return False, "PRE_MARKET"
        if current_time >= self.close_time:
            return False, "POST_MARKET"
            
        return True, "MARKET_OPEN"

    def wait_for_next_candle(self, interval_minutes=5):
        """
        Sleeps until the next candle close + buffer.
        Example: If it's 09:17, sleeps until 09:20:05.
        """
        now = datetime.datetime.now(IST)
        
        if not isinstance(interval_minutes, int) or interval_minutes <= 0 or 60 % interval_minutes:
            raise ValueError('Candle interval must be a positive divisor of 60 minutes')
        target_time = now.replace(second=0, microsecond=0) + datetime.timedelta(
            minutes=interval_minutes - now.minute % interval_minutes, seconds=5)

        seconds_to_wait = (target_time - now).total_seconds()
        
        print(f"💤 Sleeping {seconds_to_wait:.1f}s until {target_time.strftime('%H:%M:%S')} (Candle Close)...")
        if seconds_to_wait > 0:
            time.sleep(seconds_to_wait)
