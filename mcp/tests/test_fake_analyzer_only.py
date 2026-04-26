"""Tier 4: tests that PIN the FAKE analyzer's specific behavior.

WARNING: These tests will FAIL once we swap fake_analyzer for the real
analyzer. THAT IS EXPECTED. Delete this entire file at swap time --
do NOT 'fix' the assertions to match the real analyzer's numbers.

Marked with `fake_only` so they can be skipped via:
    pytest -m "not fake_only"

The conftest prints a banner whenever any of these are collected so
nobody is surprised by the eventual failure.
"""

import pytest

pytestmark = pytest.mark.fake_only


def test_fake_snapshot_has_eleven_modules(snapshot):
    assert len(snapshot.modules) == 11


def test_fake_snapshot_includes_known_modules(snapshot):
    assert "handlers.user" in snapshot.modules
    assert "db.session" in snapshot.modules


def test_fake_handlers_user_is_god_module(snapshot):
    m = snapshot.modules["handlers.user"]
    assert "GOD_MODULE" in m.violations
    assert m.cc_max == 21


def test_fake_db_session_has_sdp_violation(snapshot):
    assert "SDP" in snapshot.modules["db.session"].violations


def test_fake_incremental_check_resolves_god_module(analyzer):
    result = analyzer.incremental_check("/fake", ["handlers/user.py"])
    assert result["verdict"] == "green"
    assert "GOD_MODULE" in result["resolved_violations"]
