from __future__ import annotations

from pathlib import Path

from lead_service.seed import load_seed_file


def test_seed_fixture_is_deterministic_and_has_required_synthetic_leads() -> None:
    fixture = Path("infrastructure/postgres/seed/leads.json")

    leads = load_seed_file(fixture)
    profile_keys = [lead.synthetic_profile_key for lead in leads]

    assert len(leads) >= 20
    assert len(profile_keys) == len(set(profile_keys))
    assert "human-escalation" in profile_keys
    assert "contradictory-profile" in profile_keys
