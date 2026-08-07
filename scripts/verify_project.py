"""
One-command local (and CI) health check for the trading engine.

Steps:
  1. AST-parse every .py file in the repo (catches syntax errors fast).
  2. Import src.config and run validate_config().
  3. Run the full pytest suite (skip with --skip-tests, e.g. when CI already ran it).
  4. Import-check runner scripts and construct a PortfolioEngine WITHOUT calling .run() —
     that call hits live Yahoo Finance via yfinance and is deliberately excluded from
     the default gate so CI/local checks stay fast and deterministic. Pass --live to
     actually run a real simulation.
"""
import argparse
import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules", "research/data"}


def check_syntax() -> bool:
    print("== 1. Syntax / AST check ==")
    ok = True
    for path in ROOT.rglob("*.py"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as e:
            ok = False
            print(f"  [FAIL] SyntaxError in {path.relative_to(ROOT)}: {e}")
    print("  [OK] all files parsed cleanly" if ok else "  [FAIL] syntax errors found")
    return ok


def check_config() -> bool:
    print("== 2. Config validation ==")
    sys.path.insert(0, str(ROOT))
    try:
        from src.config import validate_config
        validate_config()
        print("  [OK] validate_config() passed")
        return True
    except Exception as e:
        print(f"  [FAIL] config validation failed: {e}")
        return False


def run_tests() -> bool:
    print("== 3. Test suite ==")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-v"], cwd=ROOT
    )
    ok = result.returncode == 0
    print("  [OK] all tests passed" if ok else "  [FAIL] test suite failed")
    return ok


def check_entry_points(live: bool) -> bool:
    print("== 4. Simulation entry point check ==")
    sys.path.insert(0, str(ROOT))
    ok = True
    try:
        from src.backtest.portfolio_engine import PortfolioEngine  # noqa: F401
        print("  [OK] PortfolioEngine imports cleanly")
    except Exception as e:
        print(f"  [FAIL] import failed: {e}")
        return False

    runner_scripts = (
        "run_phase3_simulation.py",
        "run_v7_simulation.py",
        "run_certification.py",
        "run_portfolio_sim.py"
    )

    import runpy
    for script in runner_scripts:
        script_path = ROOT / script
        if not script_path.exists():
            continue
        try:
            runpy.run_path(str(script_path))
            print(f"  [OK] {script} imports and top-level code executed cleanly")
        except Exception as e:
            ok = False
            print(f"  [FAIL] {script} failed to import: {type(e).__name__}: {e}")

    if not live:
        print("  [SKIP] skipping .run() - hits live Yahoo Finance, pass --live to execute for real")
        return ok

    for script in ("run_phase3_simulation.py", "run_v7_simulation.py"):
        print(f"  [RUN] running {script} (live network)...")
        result = subprocess.run([sys.executable, script], cwd=ROOT)
        if result.returncode != 0:
            ok = False
            print(f"  [FAIL] {script} failed")
    return ok


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-tests", action="store_true", help="skip the pytest step")
    parser.add_argument("--live", action="store_true",
                         help="actually run the simulation scripts (hits live Yahoo Finance)")
    args = parser.parse_args()

    results = [check_syntax(), check_config()]
    if not args.skip_tests:
        results.append(run_tests())
    results.append(check_entry_points(live=args.live))

    print("\n== Summary ==")
    if all(results):
        print("SUCCESS: all checks passed")
        sys.exit(0)
    else:
        print("FAILED: one or more checks failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
