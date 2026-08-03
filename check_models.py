import google.generativeai as genai
import os
from dotenv import load_dotenv

load_dotenv()
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

    with open("models.txt", "w") as f:
        print("Writing to file...")
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                f.write(f"{m.name}\n")
        print("Done.")
except Exception as e:
    print(f"ERROR: {e}")
