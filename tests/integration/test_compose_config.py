"""Compose application credentials must match PostgreSQL's configured identity."""

import json
import os
import shutil
import subprocess

import pytest
from sqlalchemy.engine import make_url
from voice_platform_config.settings import database_url_from_env


@pytest.mark.integration
def test_compose_passes_custom_database_credentials_to_every_database_client() -> None:
    if not shutil.which("docker"):
        pytest.skip("Docker Compose required")
    credentials = {
        "POSTGRES_USER": "audit_user",
        "POSTGRES_DB": "audit_database",
        "POSTGRES_PASSWORD": " spaces/@:$+% secret ",
    }
    result = subprocess.run(
        ["docker", "compose", "config", "--format", "json"],
        env=os.environ | credentials,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    services = json.loads(result.stdout)["services"]
    for name in ("postgres", "db-migrate", "lead-service", "event-relay"):
        env = services[name]["environment"]
        # `compose config` escapes dollars so its output can be re-used as YAML.
        env = {key: value.replace("$$", "$") for key, value in env.items()}
        for key, value in credentials.items():
            assert env[key] == value
        if name != "postgres":
            url = make_url(database_url_from_env(env))
            assert url.username == credentials["POSTGRES_USER"]
            assert url.password == credentials["POSTGRES_PASSWORD"]
            assert url.database == credentials["POSTGRES_DB"]
            assert url.host == "postgres" and url.port == 5432
