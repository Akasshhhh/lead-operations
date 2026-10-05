"""Run the verified Chrome product scenario with deterministic providers."""

import argparse
import os
from pathlib import Path

from scripts.evaluate import execute

SCENARIO = (
    "tests/integration/test_dashboard_browser.py::"
    "test_production_dashboard_voice_score_reconnect_lost_offer_and_handoff",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=Path("test-results/demo.json"))
    parser.add_argument("--screenshot", type=Path)
    args = parser.parse_args()
    os.chdir(Path(__file__).resolve().parents[1])
    os.environ.pop("PYTEST_ADDOPTS", None)
    os.environ.update(
        APP_ENV="test", LLM_MODE="mock", SPEECH_MODE="mock", RUN_DASHBOARD_BROWSER_TESTS="1"
    )
    if args.screenshot:
        args.screenshot.parent.mkdir(parents=True, exist_ok=True)
        os.environ["DASHBOARD_SCREENSHOT"] = str(args.screenshot.resolve())
    return execute(SCENARIO, args.report, suite="demo")


if __name__ == "__main__":
    raise SystemExit(main())
