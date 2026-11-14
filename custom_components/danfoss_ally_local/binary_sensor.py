"""Binary sensors for Danfoss Ally radiator thermostats."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import DanfossLocalConfigEntry, DanfossLocalCoordinator
from .entity import DanfossLocalEntity, mapped

PARALLEL_UPDATES = 0

WINDOW_STATES = {"close": False, "closed": False, "open": True, "opened": True}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DanfossLocalConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up binary sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        DanfossWindowSensor(coordinator, cid)
        for cid, dev in coordinator.data.items()
        if mapped(dev, "window_state")
    )


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
