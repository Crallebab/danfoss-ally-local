"""Diagnostics for Danfoss Ally Local."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_LOCAL_KEY
from .coordinator import DanfossLocalConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: DanfossLocalConfigEntry
) -> dict[str, Any]:
    """Return raw DPs for every sub-device (local key redacted)."""
    coordinator = entry.runtime_data
    return {
        "entry": async_redact_data(dict(entry.data), {CONF_LOCAL_KEY}),
        "subdevices": {
            cid: {
                "online": dev.online,
                "profile": dev.profile.key,
                "profile_confirmed": dev.profile.confirmed,
                "dps": dev.dps,
            }
            for cid, dev in coordinator.devices.items()
        },
    }
