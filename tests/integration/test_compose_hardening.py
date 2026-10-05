"""Opt-in checks of actual deployed identities and authenticated readiness."""

import os
import subprocess

import pytest


@pytest.mark.integration
@pytest.mark.parametrize(
    "service", ["lead-service", "conversation-service", "api-gateway", "event-relay"]
)
def test_deployed_python_services_run_without_root(service: str) -> None:
    if os.getenv("RUN_EVENT_STACK_TESTS") != "1":
        pytest.skip("opt in against a disposable Compose stack with RUN_EVENT_STACK_TESTS=1")
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", service, "id", "-u"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.stdout.strip() == "10001"


@pytest.mark.integration
@pytest.mark.parametrize("service,port", [("lead-service", 8001), ("conversation-service", 8002)])
def test_deployed_domain_health_requires_service_token(service: str, port: int) -> None:
    if os.getenv("RUN_EVENT_STACK_TESTS") != "1":
        pytest.skip("opt in against a disposable Compose stack with RUN_EVENT_STACK_TESTS=1")
    probe = (
        "import os, urllib.request, urllib.error; "
        f"url='http://127.0.0.1:{port}/health'\n"
        "try:\n urllib.request.urlopen(url, timeout=3)\n"
        "except urllib.error.HTTPError as error:\n assert error.code == 401\n"
        "else:\n raise AssertionError('unauthenticated health accepted')\n"
        "request=urllib.request.Request(url, headers={'X-Service-Token': "
        "os.environ['LEAD_SERVICE_AUTH_TOKEN']})\n"
        "assert urllib.request.urlopen(request, timeout=3).status == 200\n"
    )
    subprocess.run(
        ["docker", "compose", "exec", "-T", service, "python", "-c", probe],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
