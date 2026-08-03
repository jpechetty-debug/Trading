from project_types import BrainAOutput, BrainBOutput

def log_decision(brain_a_out: BrainAOutput, brain_b_out: BrainBOutput):
    print("=== Decision Log ===")
    print(f"Brain A: Signal prob = {brain_a_out.base_signal:.2f}, Regime = {brain_a_out.regime.value}, Veto = {brain_a_out.veto}")
    if brain_a_out.veto:
        print(f"Veto Reason: {brain_a_out.veto_reason}")
    print(f"Brain B: Action = {brain_b_out.action.value} ({brain_b_out.action.name}), Size = {brain_b_out.size}%, Reasoning = {brain_b_out.reasoning}")
    print("====================")
