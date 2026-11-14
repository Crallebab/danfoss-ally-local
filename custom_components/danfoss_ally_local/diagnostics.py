"""Diagnostics for Danfoss Ally Local."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from .const import CONF_DEVICE_ID, CONF_LOCAL_KEY, CONF_PROFILES
from .coordinator import DanfossLocalConfigEntry

TO_REDACT = {CONF_HOST, CONF_DEVICE_ID, CONF_LOCAL_KEY, CONF_PROFILES}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: DanfossLocalConfigEntry
) -> dict[str, Any]:
    """Return raw DPs for every sub-device, without key, address or full ids."""
    coordinator = entry.runtime_data
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "subdevices": {
            # Only the last four characters of each Zigbee node id.
            cid[-4:]: {
                "online": dev.online,
                "profile": dev.profile.key,
                "profile_confirmed": dev.profile.confirmed,
                "dps": dev.dps,
            }
            for cid, dev in coordinator.devices.items()
        },
    }
