"""Repeatable deterministic evaluation using existing pytest integration boundaries."""

import argparse
import json
import os
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

CORE = (
    "tests/integration/test_evaluation_scenarios.py",
    "tests/integration/test_staged_qualification.py",
    "tests/integration/test_workflow_actions.py",
    "tests/integration/test_diagnostics.py",
    "tests/integration/test_llm_providers.py::test_actual_adapters_failover_preserves_context_and_one_response",
    "tests/integration/test_llm_providers.py::test_partial_disconnect_recovery_depends_on_output_exposure",
    "tests/integration/test_speech_providers.py::test_actual_sarvam_to_rumik_fallback_and_no_replay_after_audio",
)
VOICE = (
    "tests/integration/test_dashboard_browser.py",
    "tests/integration/test_conversation_service.py::test_real_webrtc_audio_roundtrip_reconnect_and_transcript_persistence",
    "tests/integration/test_conversation_service.py::test_chrome_browser_voice_page_audio_controls_reconnect_and_end",
)
SUITES = {"core": CORE, "voice": VOICE, "all": CORE + VOICE}


def case_key(nodeid: str) -> str:
    name, separator, parameters = nodeid.partition("[")
    # Pytest's default parameter IDs may contain transcript or other fixture content.
    return name + "[" + sha256(parameters.encode()).hexdigest()[:12] + "]" if separator else name


class Results:
    """One result per collected case; setup/teardown errors override a passing call."""

    def __init__(self) -> None:
        self.cases: dict[str, dict[str, Any]] = {}
        self.collection_errors = 0

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        self.cases = {
            item.nodeid: {
                "case": case_key(item.nodeid),
                "status": "not_run",
                "duration_seconds": 0.0,
            }
            for item in session.items
        }

    def pytest_collectreport(self, report: pytest.CollectReport) -> None:
        self.collection_errors += report.failed

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        case = self.cases[report.nodeid]
        case["duration_seconds"] += report.duration
        if report.failed:
            case["status"] = "failed"
        elif case["status"] != "failed":
            if report.skipped or hasattr(report, "wasxfail"):
                case["status"] = "skipped"
            elif report.when == "call" and case["status"] != "skipped":
                case["status"] = "passed"
        # Never serialize longrepr, stdout/stderr, logs, parameters or fixture data.

    def report(self, exit_code: int) -> dict[str, Any]:
        counts = {
            status: sum(case["status"] == status for case in self.cases.values())
            for status in ("passed", "failed", "skipped", "not_run")
        }
        passed = (
            exit_code == 0
            and bool(self.cases)
            and counts["passed"] == len(self.cases)
            and not self.collection_errors
        )
        return {
            "passed": passed,
            "gate_status": "passed" if passed else "failed_or_incomplete",
            "pytest_exit_code": exit_code,
            "collection_errors": self.collection_errors,
            "counts": counts,
            "cases": [
                {**case, "duration_seconds": round(case["duration_seconds"], 3)}
                for case in self.cases.values()
            ],
        }


def repository_state() -> dict[str, object]:
    try:
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
        dirty = bool(
            subprocess.check_output(
                ["git", "status", "--porcelain"], text=True, stderr=subprocess.DEVNULL
            ).strip()
        )
        return {"revision": revision, "working_tree_dirty": dirty}
    except (OSError, subprocess.CalledProcessError):
        return {"revision": None, "working_tree_dirty": None}


def write_report(output: Path, report: dict[str, Any]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    # Separate files let concurrent writers finish without deleting each other's report.
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=output.parent, prefix=f".{output.name}.", delete=False
    ) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(json.dumps(report, indent=2) + "\n")
            handle.close()
            temporary.replace(output)
        finally:
            temporary.unlink(missing_ok=True)


def execute(paths: tuple[str, ...], output: Path, *, suite: str) -> int:
    started_at = datetime.now(UTC).isoformat()
    started = time.monotonic()
    results = Results()
    code = int(pytest.main([*paths, "-q", "--tb=short", "--show-capture=no"], plugins=[results]))
    report = {
        "format_version": 1,
        "suite": suite,
        **repository_state(),
        "started_at": started_at,
        "completed_at": datetime.now(UTC).isoformat(),
        "duration_seconds": round(time.monotonic() - started, 3),
        "provider_mode": "deterministic_fixtures",
        "scope": "selected_regression_scenarios",
        **results.report(code),
    }
    write_report(output, report)
    print(f"Evaluation: {report['gate_status']} · {output} · {report['counts']}")
    return 0 if report["passed"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=SUITES, default="core")
    parser.add_argument("--report", type=Path, default=Path("test-results/evaluation.json"))
    args = parser.parse_args()
    os.chdir(Path(__file__).resolve().parents[1])
    # An inherited -k/-m/--lf must not silently turn a preset into a partial green gate.
    os.environ.pop("PYTEST_ADDOPTS", None)
    os.environ.update(APP_ENV="test", LLM_MODE="mock", SPEECH_MODE="mock")
    if args.suite in {"voice", "all"}:
        os.environ.update(RUN_DASHBOARD_BROWSER_TESTS="1", RUN_BROWSER_VOICE_TESTS="1")
    return execute(SUITES[args.suite], args.report, suite=args.suite)


if __name__ == "__main__":
    raise SystemExit(main())
