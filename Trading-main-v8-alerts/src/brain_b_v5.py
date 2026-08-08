import os
import json
import time
import google.generativeai as genai
from datetime import datetime
from dotenv import load_dotenv
import pytz
from src.logger import get_logger

log = get_logger(__name__)

class BrainBV5:
    def __init__(self):
        load_dotenv()
        api_key = os.getenv("GEMINI_API_KEY")
        if api_key:
            genai.configure(api_key=api_key)
        else:
            log.warning("GEMINI_API_KEY not set — BrainB commentary will use fallback.")
        self.model = genai.GenerativeModel('gemini-1.5-flash')
        self.tz = pytz.timezone('Asia/Kolkata')

    def get_time_mode(self):
        now = datetime.now(self.tz).time()
        if now < datetime.strptime("10:15", "%H:%M").time(): return "OPENING_RANGE"
        if now < datetime.strptime("13:30", "%H:%M").time(): return "MID_DAY_CHOP"
        return "CLOSING_TREND"


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
