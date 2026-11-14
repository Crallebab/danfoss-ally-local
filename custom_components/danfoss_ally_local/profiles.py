"""Device profiles: map Danfoss/Tuya status codes to local Tuya data points.

The Danfoss cloud API reports devices by Tuya *code* (``temp_set``), while the
local Tuya protocol only uses data point *numbers* (``"16"``). A profile ties the
two together and records how raw values are scaled.

Numbers marked ``None`` are not confirmed yet. Run ``tools/danfoss_probe.py map``
to derive them by comparing a local snapshot with the cloud snapshot, then fill
them in here and set ``confirmed=True``. Writes are refused for profiles that are
not confirmed, so a wrong guess can never be sent to a radiator valve.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class DataPoint:
    """One Tuya data point."""

    dp: str | None
    scale: float = 1.0
    writable: bool = False

    def decode(self, raw: Any) -> Any:
        """Convert a raw DP value into a natural value."""
        if raw is None or self.scale == 1.0 or isinstance(raw, bool):
            return raw
        if isinstance(raw, (int, float)):
            return raw / self.scale
        return raw

    def encode(self, value: Any) -> Any:
        """Convert a natural value into a raw DP value."""
        if self.scale == 1.0 or isinstance(value, bool):
            return value
        return int(round(float(value) * self.scale))


@dataclass(frozen=True, slots=True)
class Profile:
    """A sub-device type."""

    key: str
    name: str
    # DPs whose presence identifies this profile in a local status dump.
    detect: frozenset[str]
    codes: dict[str, DataPoint] = field(default_factory=dict)
    # True once every DP number used for writing has been verified on a real device.
    confirmed: bool = False

    def value(self, dps: dict[str, Any], code: str) -> Any:
        """Return the decoded value of ``code`` from a raw DP dict."""
        point = self.codes.get(code)
        if point is None or point.dp is None:
            return None
        return point.decode(dps.get(point.dp))

    def has(self, dps: dict[str, Any], code: str) -> bool:
        """Return whether ``code`` is mapped and present in ``dps``."""
        point = self.codes.get(code)
        return point is not None and point.dp is not None and point.dp in dps

    def encode(self, code: str, value: Any) -> tuple[str, Any]:
        """Return ``(dp, raw_value)`` for writing ``code``."""
        point = self.codes.get(code)
        if point is None or point.dp is None:
            raise KeyError(f"{self.key}: code {code!r} has no confirmed data point")
        if not point.writable:
            raise KeyError(f"{self.key}: code {code!r} is read-only")
        return point.dp, point.encode(value)


RADIATOR_THERMOSTAT = Profile(
    key="radiator_thermostat",
    name="Radiator thermostat",
    detect=frozenset({"2", "16", "24"}),
    # Write test 2026-11-14 on one valve: mode=manual + manual_mode_fast=205
    # written locally showed up in the Danfoss cloud; mode restore also worked.
    confirmed=True,
    # Mapped 2026-11-14 on 5 Ally radiator thermostats (Tuya product den6kzk0) by
    # comparing local DPs with the Danfoss cloud snapshot (tools/danfoss_probe.py map).
    # "5/5" = value matched on every valve; "1/1" = only one valve reported the DP.
    codes={
        "mode": DataPoint("2", writable=True),  # 5/5
        "work_state": DataPoint("3"),  # 5/5
        "temp_set": DataPoint("16", 10),  # 5/5, active setpoint
        "upper_temp": DataPoint("18", 10),  # 1/1, matches Icon RT schema
        "temp_current": DataPoint("24", 10),  # 5/5
        "lower_temp": DataPoint("27", 10),  # 1/1, matches Icon RT schema
        "battery_percentage": DataPoint("34"),  # 5/5
        "SetpointChangeSource": DataPoint("111"),  # 5/5
        "manual_mode_fast": DataPoint("114", 10, writable=True),  # 5/5
        "ext_measured_rs": DataPoint("158", 100),  # 3/3, -80.00 = no room sensor
        "OccupiedSetpoint": DataPoint("171", 100),  # 5/5
        # Unresolved: values coincide (all True / all 1) or never reported locally.
        # Candidates noted for the next mapping round.
        "switch": DataPoint(None),
        "child_lock": DataPoint(None),  # 30 / 116 / 161 / 163
        "window_state": DataPoint(None),  # 25 / 117
        "window_toggle": DataPoint(None),
        "window_state_info": DataPoint(None),  # 25 / 117
        "pi_heating_demand": DataPoint(None),  # 170 / 173
        "radiator_covered": DataPoint(None),  # 140 / 159
        "at_home_setting": DataPoint(None, 10),  # 113 / 118
        "leaving_home_setting": DataPoint(None, 10),  # 109 / 112 / 119
        "pause_setting": DataPoint(None, 10),
        "holiday_setting": DataPoint(None, 10),  # 115?
        "fault": DataPoint(None),
        "switch_state": DataPoint(None),
        "mounting_mode_active": DataPoint(None),
        "heat_available": DataPoint(None),
        "load_balance_enable": DataPoint(None),
    },
)

ROOM_SENSOR = Profile(
    key="room_sensor",
    name="Room sensor",
    detect=frozenset({"1", "2", "4"}),
    # Read-only device; 2/2 sensors (Tuya product mxzo5rhf) matched.
    confirmed=True,
    codes={
        "temp_current": DataPoint("1", 10),
        "humidity_value": DataPoint("2", 10),
        "battery_percentage": DataPoint("4"),
    },
)

UNKNOWN = Profile(key="unknown", name="Unknown device", detect=frozenset())

PROFILES = (RADIATOR_THERMOSTAT, ROOM_SENSOR)

# A room sensor reports three DPs; leave a little slack for transport DPs.
_ROOM_SENSOR_MAX_DPS = 6


def detect_profile(dps: dict[str, Any]) -> Profile:
    """Pick a profile for a sub-device from its raw DP dict."""
    keys = set(dps)
    if RADIATOR_THERMOSTAT.detect <= keys:
        return RADIATOR_THERMOSTAT
    if ROOM_SENSOR.detect <= keys and len(keys) <= _ROOM_SENSOR_MAX_DPS:
        return ROOM_SENSOR
    return UNKNOWN
