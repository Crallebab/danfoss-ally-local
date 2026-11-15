"""Binary sensors for Danfoss Ally radiator thermostats."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .coordinator import DanfossLocalConfigEntry, DanfossLocalCoordinator
from .entity import DanfossLocalEntity, mapped

PARALLEL_UPDATES = 0

WINDOW_STATES = {"close": False, "closed": False, "open": True, "opened": True}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DanfossLocalConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up binary sensors.

    A valve only reports its window state when it changes, and the gateway hands
    out nothing it has not been told. So the sensor is added once the valve has
    reported, and does not sit there as "unknown" until then.
    """
    coordinator = entry.runtime_data
    registry = er.async_get(hass)
    added: set[str] = set()

    def keep_unreported(cid: str) -> bool:
        """Return whether a sensor from an earlier run stays although unreported."""
        entity_id = registry.async_get_entity_id(Platform.BINARY_SENSOR, DOMAIN, f"{cid}_window")
        if entity_id is None:
            return False
        registered = registry.async_get(entity_id)
        if registered and (
            registered.disabled or registered.name or registered.icon or registered.area_id
        ):
            return True  # the user changed it: removing it would lose that
        registry.async_remove(entity_id)
        return False

    @callback
    def add_reported() -> None:
        new = []
        for cid, dev in coordinator.data.items():
            if cid in added or not mapped(dev, "window_state"):
                continue
            if dev.has("window_state") or keep_unreported(cid):
                added.add(cid)
                new.append(DanfossWindowSensor(coordinator, cid))
        if new:
            async_add_entities(new)

    add_reported()
    entry.async_on_unload(coordinator.async_add_listener(add_reported))


class DanfossWindowSensor(DanfossLocalEntity, BinarySensorEntity):
    """Open window detected by the valve."""

    _attr_device_class = BinarySensorDeviceClass.WINDOW

    def __init__(self, coordinator: DanfossLocalCoordinator, cid: str) -> None:
        super().__init__(coordinator, cid, "window")

    @property
    def is_on(self) -> bool | None:
        """Return True when the valve reports an open window."""
        state = self.device.value("window_state")
        # Only "close" has been seen on real valves so far; an unexpected value
        # reads as unknown, not as an open window.
        return WINDOW_STATES.get(str(state).lower()) if state is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        """Expose the raw value so new states can be identified."""
        state = self.device.value("window_state")
        return None if state is None else {"raw_state": str(state)}
