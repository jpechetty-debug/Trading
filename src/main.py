# Assumes run from src/: python main.py
from project_types import BrainAOutput, Action, BrainBOutput, Regime
from brain_a import brain_a_engine
from brain_b import BrainB
from validator import validate_output
from metrics import log_decision

def main():
    # Mock NSE data (e.g., RELIANCE snapshot)
    mock_data = {
        "price": 2854.50,
        "volume": 1234567,
        "volatility": 0.42  # From V3 factors
    }
    
    print("Starting Indian Stock Analysis AI V4.1")
    print(f"Mock Data: {mock_data}")
    
    # Flow: Data → Brain A → Brain B → Validate → Log
    brain_a_out = brain_a_engine(mock_data)
    
    brain_b = BrainB()
    brain_b_out = brain_b.decide(brain_a_out)
    
    if validate_output(brain_b_out):
        log_decision(brain_a_out, brain_b_out)

if __name__ == "__main__":
    main()
