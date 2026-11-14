"""Tests for SubDevice state merging (no Home Assistant needed)."""

from __future__ import annotations

from conftest import load

load("profiles")
device = load("device")


def test_merge_updates_and_keeps_unreported_dps():
    dev = device.SubDevice("cid1", dps={"16": 200, "24": 190})
    dev.merge({"24": 195}, now=0.0)
    assert dev.dps == {"16": 200, "24": 195}


def test_written_value_survives_a_stale_poll_until_confirmed():
    dev = device.SubDevice("cid1", dps={"30": False})
    dev.pending["30"] = (False, 100.0)
    dev.merge({"30": True, "24": 190}, now=10.0)  # gateway still serves the old value
    assert dev.dps == {"30": False, "24": 190}
    dev.merge({"30": False}, now=20.0)  # confirmed
    assert dev.pending == {}
    dev.merge({"30": True}, now=30.0)  # a later real change is accepted
    assert dev.dps["30"] is True


def test_hold_expires_so_a_failed_write_shows_the_real_value():
    dev = device.SubDevice("cid1", dps={"30": False})
    dev.pending["30"] = (False, 100.0)
    dev.merge({"30": True}, now=101.0)
    assert dev.dps["30"] is True
    assert dev.pending == {}
