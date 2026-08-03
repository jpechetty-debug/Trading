from project_types import BrainBOutput, Action

def validate_output(brain_b_output: BrainBOutput) -> bool:
    # Contract checks
    if brain_b_output.action == Action.NO_TRADE and brain_b_output.size != 0.0:
        raise ValueError("No trade action but size > 0")
    if brain_b_output.size < 0 or brain_b_output.size > 5.0:  # Max 5% sanity
        raise ValueError("Invalid size")
    print("Validation passed.")
    return True
