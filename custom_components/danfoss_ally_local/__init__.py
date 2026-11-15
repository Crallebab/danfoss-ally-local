"""Danfoss Ally Local: control a Danfoss Ally gateway over the Tuya LAN protocol."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import CONF_DEVICE_ID, CONF_NAMES, DOMAIN, default_name
from .coordinator import DanfossLocalConfigEntry, DanfossLocalCoordinator

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.CLIMATE,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]


async def async_setup_entry(hass: HomeAssistant, entry: DanfossLocalConfigEntry) -> bool:
    """Set up from a config entry."""
    coordinator = DanfossLocalCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    coordinator.async_persist_profiles()

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
    names: dict[str, str] = entry.options.get(CONF_NAMES, {})
    for cid, dev in coordinator.data.items():
        name = names.get(cid) or default_name(cid, dev.profile.name)
        device = registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, cid)},
            name=name,
            manufacturer="Danfoss",
            model=dev.profile.name,
            serial_number=cid,
            via_device_id=gateway.id,
        )
        if device.name != name or device.model != dev.profile.name:
            registry.async_update_device(device.id, name=name, model=dev.profile.name)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # From here on a device that turns out to be a different type, or a newly
    # paired device, reloads the entry so its entities get created.
    coordinator.async_entities_created()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: DanfossLocalConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_shutdown()
    return unloaded


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: DanfossLocalConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Allow deleting a sub-device the gateway no longer lists (unpaired)."""
    ids = {identifier[1] for identifier in device.identifiers if identifier[0] == DOMAIN}
    if entry.data[CONF_DEVICE_ID] in ids:
        return False  # the gateway itself goes with the config entry
    coordinator = getattr(entry, "runtime_data", None)
    if coordinator is None:
        # Not loaded (e.g. gateway offline): the device is recreated on the next
        # successful setup if it is still paired.
        return True
    return not ids & coordinator.listed
