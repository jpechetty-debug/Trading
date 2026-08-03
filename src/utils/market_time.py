import datetime
import pytz
import time

IST = pytz.timezone('Asia/Kolkata')

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
        if current_time > self.close_time:
            return False, "POST_MARKET"
            
        return True, "MARKET_OPEN"

    def wait_for_next_candle(self, interval_minutes=5):
        """
        Sleeps until the next candle close + buffer.
        Example: If it's 09:17, sleeps until 09:20:05.
        """
        now = datetime.datetime.now(IST)
        
        # Calculate next interval mark
        next_minute = (now.minute // interval_minutes + 1) * interval_minutes
        
        # Handle hour rollover
        target_hour = now.hour
        if next_minute >= 60:
            next_minute = 0
            target_hour += 1
            if target_hour >= 24:
                target_hour = 0

        target_time = now.replace(hour=target_hour, minute=next_minute, second=5, microsecond=0)
        
        # If target is in the past (e.g. just crossed it), add interval
        if target_time <= now:
            target_time += datetime.timedelta(minutes=interval_minutes)

        seconds_to_wait = (target_time - now).total_seconds()
        
        print(f"💤 Sleeping {seconds_to_wait:.1f}s until {target_time.strftime('%H:%M:%S')} (Candle Close)...")
        if seconds_to_wait > 0:
            time.sleep(seconds_to_wait)
