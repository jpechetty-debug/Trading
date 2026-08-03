import os
import json
import time
import google.generativeai as genai
from datetime import datetime
from dotenv import load_dotenv
import pytz
from src.logger import get_logger

log = get_logger(__name__)

load_dotenv()
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

class BrainBV5:
    def __init__(self):
        self.model = genai.GenerativeModel('gemini-1.5-flash')
        self.tz = pytz.timezone('Asia/Kolkata')

    def get_time_mode(self):
        now = datetime.now(self.tz).time()
        if now < datetime.strptime("10:15", "%H:%M").time(): return "OPENING_RANGE"
        if now < datetime.strptime("13:30", "%H:%M").time(): return "MID_DAY_CHOP"
        return "CLOSING_TREND"

    def _mechanical_fallback(self, ticker, brain_a_data):
        """Fallback if AI fails"""
        price = brain_a_data['current_price']
        atr = brain_a_data['atr']
        direction = brain_a_data['direction']
        score = brain_a_data['kill_score']
        
        # Dynamic Sizing Formula (Fallback)
        size = min(score / 10.0, 1.0)

        stop_mult = 2.0 if direction == "LONG" else -2.0
        t1_mult = 1.5 if direction == "LONG" else -1.5
        t2_mult = 3.0 if direction == "LONG" else -3.0

        return {
            "ticker": ticker,
            "direction": direction,
            "kill_score": score,
            "entry": price,
            "stop": round(price - (stop_mult * atr), 2),
            "targets": {
                "t1": round(price + (t1_mult * atr), 2),
                "t2": round(price + (t2_mult * atr), 2)
            },
            "time_mode": "FALLBACK",
            "position_size_pct": size,
            "confidence": "MECHANICAL",
            "reasoning": "AI Unavailable. Calculated via ATR."
        }

    def generate_commentary(self, ticker, brain_a_data, retries=2):
        """
        V6.4 REFACTOR: GenAI is now strictly a COMMENTARY LAYER.
        Provides institutional context and sentiment, not trade math.
        """
        time_mode = self.get_time_mode()
        score = brain_a_data['kill_score']
        
        for attempt in range(retries + 1):
            try:
                # Prompt shifted to Sentiment & Contextual Commentary
                prompt = f"""
                ACT AS: Institutional Desk Analyst.
                TICKER: {ticker}
                DIRECTION: {brain_a_data['direction']}
                KILL SCORE: {score}/10
                CONTEXT: {time_mode}
                
                TASK: Provide institutional commentary and sentiment score (0-10).
                DO NOT set primary trade math. Focus on "Why the setup is/isn't high conviction".
                
                OUTPUT JSON: {{ 
                    "ticker": "{ticker}", 
                    "sentiment_score": float,
                    "commentary": "string",
                    "catalyst_check": "string",
                    "risk_warning": "string"
                }}
                """
                
                resp = self.model.generate_content(prompt)
                
                text = resp.text
                start = text.find("{")
                end = text.rfind("}") + 1
                if start == -1: raise ValueError("Invalid JSON")
                
                analysis = json.loads(text[start:end])
                
                # Merge with deterministic data from Brain A
                analysis['direction'] = brain_a_data['direction']
                analysis['kill_score'] = score
                analysis['time_mode'] = time_mode
                
                return analysis

            except Exception as e:
                if attempt == retries:
                    return {
                        "ticker": ticker,
                        "sentiment_score": 5.0,
                        "commentary": "GenAI Commentary Unavailable.",
                        "catalyst_check": "N/A",
                        "risk_warning": str(e)
                    }
                time.sleep(2)
