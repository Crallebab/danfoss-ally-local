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

    def raw(self, code: str, value: Any) -> tuple[str, Any] | None:
        """Return ``(dp, raw_value)`` for a mapped code, writable or not.

        Used for optimistic state only, never for writing.
        """
        point = self.codes.get(code)
        if point is None or point.dp is None:
            return None
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
        # Mapped 2026-11-15 by toggling each setting in the Danfoss app on one
        # valve and watching which local DP changed (tools/watch_dps.py).
        "switch": DataPoint("1", writable=True),  # app: "Pre-heating"
        "child_lock": DataPoint("30", writable=True),
        "window_state": DataPoint("25"),  # "close" / "open"
        "window_toggle": DataPoint("116", writable=True),  # app: "Open Window" detection
        "mounting_mode": DataPoint("133", writable=True),  # "Vertical" / "Horizontal"
        "at_home_setting": DataPoint("118", 10, writable=True),  # 113 mirrors it
        "leaving_home_setting": DataPoint("119", 10, writable=True),  # 109/112 mirror it
        "pause_setting": DataPoint("120", 10, writable=True),  # 110 mirrors it
        # Local-only: room temperature in 0.01 C (matches DP 24 on 5/5 valves).
        "temp_current_precise": DataPoint("169", 100),
        # Cloud pi_heating_demand matched 170 on 5/5; 173 was contradicted.
        # Only seen at its idle value so far.
        "pi_heating_demand": DataPoint("170"),
        # Unresolved.
        "radiator_covered": DataPoint(None),  # 140 / 159; not in the app
        "display_orientation": DataPoint(None),  # probably 101
        "holiday_setting": DataPoint(None, 10),  # 115? (no holiday UI in the app)
        "window_state_info": DataPoint(None),  # 117?
        "fault": DataPoint(None),
        "switch_state": DataPoint(None),
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
PROFILES_BY_KEY = {profile.key: profile for profile in (*PROFILES, UNKNOWN)}

# A room sensor reports three DPs; leave a little slack for transport DPs.
_ROOM_SENSOR_MAX_DPS = 6
# DPs only the Ally radiator thermostat reports (manual setpoint, 0.01 C
# temperature, occupied setpoint). DPs 2/16/24 alone also fit the Icon RT schema.
_RADIATOR_ONLY_DPS = frozenset({"114", "169", "171"})


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def detect_profile(dps: dict[str, Any]) -> Profile:
    """Pick a profile for a sub-device from its raw DP dict.

    Checks value types as well as DP numbers: a partial status from a valve
    ({1: bool, 2: "manual", ...}) must not look like a room sensor, and another
    Tuya thermostat that happens to share DPs 2/16/24 must not get writes enabled.
    """
    keys = set(dps)
    if (
        RADIATOR_THERMOSTAT.detect <= keys
        and isinstance(dps["2"], str)
        and _is_int(dps["16"])
        and _is_int(dps["24"])
        and keys & _RADIATOR_ONLY_DPS
    ):
        return RADIATOR_THERMOSTAT
    if (
        ROOM_SENSOR.detect <= keys
        and len(keys) <= _ROOM_SENSOR_MAX_DPS
        and all(_is_int(dps[dp]) for dp in ROOM_SENSOR.detect)
    ):
        return ROOM_SENSOR
    return UNKNOWN
