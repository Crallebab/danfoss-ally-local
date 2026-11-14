"""Danfoss Ally Local: control a Danfoss Ally gateway over the Tuya LAN protocol."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN
from .coordinator import DanfossLocalConfigEntry, DanfossLocalCoordinator

PLATFORMS = [Platform.CLIMATE, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: DanfossLocalConfigEntry) -> bool:
    """Set up from a config entry."""
    coordinator = DanfossLocalCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    # Register the gateway, then every sub-device linked to it by device id.
    # Entities only reference their device by identifier (see entity.py).
    registry = dr.async_get(hass)
    gateway = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, coordinator.gateway_id)},
        name="Danfoss Ally Gateway",
        manufacturer="Danfoss",
        model="Ally Gateway (Tuya)",
    )
    for cid, dev in coordinator.data.items():
        registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, cid)},
            name=f"Danfoss {dev.profile.name.lower()} {cid[-4:]}",
            manufacturer="Danfoss",
            model=dev.profile.name,
            via_device_id=gateway.id,
        )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: DanfossLocalConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_shutdown()
    return unloaded
