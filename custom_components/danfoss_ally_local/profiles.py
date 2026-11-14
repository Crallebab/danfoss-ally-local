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


# Numbers come from the Danfoss Icon RT schema (same Danfoss/Tuya code names),
# published at github.com/Virki-Workspace/danfoss-ally-is-tuya. They are a
# strong hint for the Ally radiator thermostat, not proof.
RADIATOR_THERMOSTAT = Profile(
    key="radiator_thermostat",
    name="Radiator thermostat",
    detect=frozenset({"2", "16", "24"}),
    confirmed=False,
    codes={
        "switch": DataPoint("1", writable=True),
        "mode": DataPoint("2", writable=True),
        "work_state": DataPoint("3"),
        "temp_set": DataPoint("16", 10, writable=True),
        "upper_temp": DataPoint("18", 10),
        "temp_current": DataPoint("24", 10),
        "lower_temp": DataPoint("27", 10),
        "child_lock": DataPoint("30", writable=True),
        "battery_percentage": DataPoint("34"),
        "fault": DataPoint("45"),
        "SetpointChangeSource": DataPoint("111"),
        "manual_mode_fast": DataPoint("114", 10, writable=True),
        "at_home_setting": DataPoint("118", 10, writable=True),
        "leaving_home_setting": DataPoint("119", 10, writable=True),
        "pause_setting": DataPoint("120", 10, writable=True),
        "holiday_setting": DataPoint("121", 10, writable=True),
        "switch_state": DataPoint("123"),
        # Radiator-only codes: numbers unknown until mapped.
        "window_state": DataPoint(None),
        "window_toggle": DataPoint(None),
        "window_state_info": DataPoint(None),
        "mounting_mode_active": DataPoint(None),
        "ext_measured_rs": DataPoint(None, 100),
        "radiator_covered": DataPoint(None),
        "heat_available": DataPoint(None),
        "load_balance_enable": DataPoint(None),
        "pi_heating_demand": DataPoint(None),
        "OccupiedSetpoint": DataPoint(None, 100),
    },
)

ROOM_SENSOR = Profile(
    key="room_sensor",
    name="Room sensor",
    detect=frozenset(),  # matched by size, see detect_profile()
    confirmed=False,
    codes={
        "temp_current": DataPoint(None, 10),
        "humidity_value": DataPoint(None, 10),
        "battery_percentage": DataPoint(None),
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
    if 0 < len(keys) <= _ROOM_SENSOR_MAX_DPS:
        return ROOM_SENSOR
    return UNKNOWN
