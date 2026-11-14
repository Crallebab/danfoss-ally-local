"""Tests for DP profiles (pure Python, no Home Assistant needed)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parent.parent / "custom_components" / "danfoss_ally_local" / "profiles.py"
_spec = importlib.util.spec_from_file_location("profiles", _PATH)
profiles = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(profiles)


def test_decode_scales_tenths():
    dps = {"16": 210, "24": 200, "2": "at_home"}
    p = profiles.RADIATOR_THERMOSTAT
    assert p.value(dps, "temp_set") == 21.0
    assert p.value(dps, "temp_current") == 20.0
    assert p.value(dps, "mode") == "at_home"


def test_bool_is_not_scaled():
    assert profiles.DataPoint("30", 10).decode(True) is True


def test_unmapped_code_reads_none():
    assert profiles.RADIATOR_THERMOSTAT.value({"16": 210}, "window_state") is None


def test_encode_rounds_to_raw():
    assert profiles.RADIATOR_THERMOSTAT.encode("manual_mode_fast", 21.5) == ("114", 215)


def test_encode_refuses_read_only_and_unmapped():
    with pytest.raises(KeyError):
        profiles.RADIATOR_THERMOSTAT.encode("temp_current", 20)
    with pytest.raises(KeyError):
        profiles.RADIATOR_THERMOSTAT.encode("window_toggle", True)


def test_detect_profile():
    assert profiles.detect_profile({"1": True, "2": "manual", "16": 200, "24": 190}) is profiles.RADIATOR_THERMOSTAT
    assert profiles.detect_profile({"1": 212, "2": 521, "3": 41}) is profiles.ROOM_SENSOR
    assert profiles.detect_profile({}) is profiles.UNKNOWN


def test_factory_reset_never_writable():
    for prof in profiles.PROFILES:
        assert all(dp.dp != "44" or not dp.writable for dp in prof.codes.values())
