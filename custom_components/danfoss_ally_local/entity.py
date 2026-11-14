"""Base entity for Danfoss Ally Local."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import DanfossLocalCoordinator
from .device import SubDevice


def mapped(dev: SubDevice, code: str, writable: bool = False) -> bool:
    """Return whether a code has a confirmed DP on this device (and is writable)."""
    point = dev.profile.codes.get(code)
    if point is None or point.dp is None:
        return False
    return dev.profile.confirmed and point.writable if writable else True


class DanfossLocalEntity(CoordinatorEntity[DanfossLocalCoordinator]):
    """An entity belonging to one sub-device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: DanfossLocalCoordinator, cid: str, key: str) -> None:
        super().__init__(coordinator)
        self._cid = cid
        self._attr_unique_id = f"{cid}_{key}"
        # The device itself (name, model, link to the gateway) is registered in
        # __init__.async_setup_entry; entities only point at it.
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, cid)})

    @property
    def device(self) -> SubDevice:
        """Return the sub-device state."""
        return self.coordinator.data[self._cid]

    @property
    def available(self) -> bool:
        """Return whether the sub-device is reachable."""
        return super().available and self.device.online
