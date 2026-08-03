import sys
try:
    import google.generativeai as genai
    import streamlit
    print(f"Python Executable: {sys.executable}")
    print("SUCCESS: google.generativeai and streamlit imported.")
except ImportError as e:
    print(f"ERROR: Failed to import. {e}")
except Exception as e:
    print(f"ERROR: {e}")
