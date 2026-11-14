"""Constants for Danfoss Ally Local."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "danfoss_ally_local"

CONF_DEVICE_ID = "device_id"
CONF_LOCAL_KEY = "local_key"
CONF_PROTOCOL_VERSION = "protocol_version"
CONF_NAMES = "names"  # options: {cid: device name}


def default_name(cid: str, profile_name: str) -> str:
    """Name used until the user sets one (the gateway knows no names)."""
    return f"Danfoss {profile_name.lower()} {cid[-4:]}"

DEFAULT_PROTOCOL_VERSION = "3.5"
PROTOCOL_VERSIONS = ["3.5", "3.4", "3.3"]

UPDATE_INTERVAL = timedelta(seconds=30)

# Data points that must never be written, whatever a profile says.
# DP 44 is factory_reset on the Danfoss Icon RT schema.
FORBIDDEN_WRITE_DPS = frozenset({"44"})
