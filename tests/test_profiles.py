"""Tests for DP profiles (pure Python, no Home Assistant needed)."""

from __future__ import annotations

import pytest

from conftest import load

profiles = load("profiles")


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
        profiles.RADIATOR_THERMOSTAT.encode("radiator_covered", True)
    assert profiles.RADIATOR_THERMOSTAT.encode("window_toggle", False) == ("116", False)


VALVE = {"2": "manual", "16": 200, "24": 190, "114": 200, "169": 1900}


def test_detect_profile():
    assert profiles.detect_profile(VALVE) is profiles.RADIATOR_THERMOSTAT
    assert profiles.detect_profile({"1": 212, "2": 521, "4": 41}) is profiles.ROOM_SENSOR
    assert profiles.ROOM_SENSOR.value({"1": 218, "2": 517, "4": 41}, "humidity_value") == 51.7
    assert profiles.detect_profile({"1": 212, "2": 521, "3": 41}) is profiles.UNKNOWN
    assert profiles.detect_profile({}) is profiles.UNKNOWN


def test_detect_needs_an_ally_only_dp():
    # DPs 2/16/24 alone also fit other Danfoss/Tuya thermostats (Icon RT).
    shared = {"2": "manual", "16": 200, "24": 190}
    assert profiles.detect_profile(shared) is profiles.UNKNOWN


def test_partial_valve_status_is_not_a_room_sensor():
    assert profiles.detect_profile({"1": True, "2": "manual", "4": 0}) is profiles.UNKNOWN


def test_detect_checks_value_types():
    assert profiles.detect_profile({**VALVE, "16": "200"}) is profiles.UNKNOWN
    assert profiles.detect_profile({**VALVE, "2": 3}) is profiles.UNKNOWN


def test_raw_ignores_writable_flag_but_not_mapping():
    assert profiles.RADIATOR_THERMOSTAT.raw("temp_set", 20.5) == ("16", 205)
    assert profiles.RADIATOR_THERMOSTAT.raw("radiator_covered", True) is None


def test_factory_reset_never_writable():
    for prof in profiles.PROFILES:
        assert all(dp.dp != "44" or not dp.writable for dp in prof.codes.values())
