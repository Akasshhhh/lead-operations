"""The evaluation gate must fail on skipped tests and fixture/collection failures."""

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from scripts.evaluate import write_report


@pytest.mark.parametrize(
    "source,status",
    [
        ("def test_case(): pass\n", "passed"),
        (
            "import pytest\n@pytest.mark.parametrize('text',['private fixture text'])\n"
            "def test_case(text): pass\n",
            "passed",
        ),
        ("import pytest\ndef test_case(): pytest.skip('private skip data')\n", "skipped"),
        ("def test_case(): raise ValueError('private exception data')\n", "failed"),
        (
            "import pytest\n@pytest.fixture\ndef fixture(): raise ValueError('private setup')\n"
            "def test_case(fixture): pass\n",
            "failed",
        ),
        (
            "import pytest\n@pytest.fixture\ndef fixture():\n yield\n"
            " raise ValueError('private teardown')\ndef test_case(fixture): pass\n",
            "failed",
        ),
        ("raise ValueError('private collection data')\n", "collection"),
    ],
)
def test_actual_pytest_results_are_complete_and_content_free(
    tmp_path: Path,
    source: str,
    status: str,
) -> None:
    case = tmp_path / "test_case.py"
    case.write_text(source, encoding="utf-8")
    output = tmp_path / "report.json"
    code = (
        "from pathlib import Path; from scripts.evaluate import execute; "
        f"raise SystemExit(execute(({str(case)!r},), Path({str(output)!r}), suite='fixture'))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=30,
        env=os.environ | {"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
    )
    assert completed.returncode == (0 if status == "passed" else 1)
    encoded = output.read_text(encoding="utf-8")
    report = json.loads(encoded)
    assert report["passed"] == (status == "passed")
    assert "private" not in encoded
    assert report["duration_seconds"] >= 0
    if status == "collection":
        assert report["collection_errors"] == 1
    else:
        assert report["counts"][status] == 1
        assert report["cases"][0]["status"] == status


def test_cli_missing_database_is_incomplete_and_inherited_filter_cannot_hide_cases(
    tmp_path: Path,
) -> None:
    output = tmp_path / "incomplete.json"
    environment = {key: value for key, value in os.environ.items() if key != "TEST_DATABASE_URL"}
    environment["PYTEST_ADDOPTS"] = "-k no_such_evaluation_case"
    result = subprocess.run(
        [sys.executable, "-m", "scripts.evaluate", "--suite", "core", "--report", str(output)],
        capture_output=True,
        text=True,
        timeout=30,
        env=environment,
    )
    assert result.returncode == 1
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["gate_status"] == "failed_or_incomplete"
    assert report["counts"]["skipped"] > 0
    assert report["counts"]["passed"] > 0  # vendor fixtures still execute without a DB


def test_concurrent_report_writers_each_publish_a_complete_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "report.json"
    barrier = Barrier(2)
    replace = Path.replace

    def simultaneous_replace(source: Path, destination: Path) -> Path:
        barrier.wait(timeout=5)
        return replace(source, destination)

    monkeypatch.setattr(Path, "replace", simultaneous_replace)
    reports = [{"run": run, "cases": [run] * 1000} for run in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(write_report, output, report) for report in reports]
        for future in futures:
            future.result(timeout=10)
    assert json.loads(output.read_text()) in reports
    assert list(tmp_path.iterdir()) == [output]


def test_demo_without_database_fails_instead_of_claiming_a_verified_demo(tmp_path: Path) -> None:
    output = tmp_path / "demo.json"
    environment = {key: value for key, value in os.environ.items() if key != "TEST_DATABASE_URL"}
    environment["PYTEST_ADDOPTS"] = "-k nonexistent"
    result = subprocess.run(
        [sys.executable, "-m", "scripts.demo", "--report", str(output)],
        capture_output=True,
        text=True,
        timeout=30,
        env=environment,
    )
    assert result.returncode == 1
    report = json.loads(output.read_text())
    assert report["suite"] == "demo"
    assert report["gate_status"] == "failed_or_incomplete"
    assert report["counts"]["skipped"] == 1
