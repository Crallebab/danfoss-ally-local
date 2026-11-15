"""Switch entities for Danfoss Ally radiator thermostat settings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import DanfossLocalConfigEntry, DanfossLocalCoordinator
from .entity import DanfossLocalEntity, mapped


@dataclass(frozen=True, kw_only=True)
class DanfossSwitchDescription(SwitchEntityDescription):
    """Switch bound to one boolean Danfoss code."""

    code: str


SWITCHES = (
    DanfossSwitchDescription(
        key="preheat",
        translation_key="preheat",
        code="switch",
        entity_category=EntityCategory.CONFIG,
    ),
    DanfossSwitchDescription(
        key="child_lock",
        translation_key="child_lock",
        code="child_lock",
        entity_category=EntityCategory.CONFIG,
    ),
    DanfossSwitchDescription(
        key="window_detection",
        translation_key="window_detection",
        code="window_toggle",
        entity_category=EntityCategory.CONFIG,
    ),
)


PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DanfossLocalConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up switches."""
    coordinator = entry.runtime_data
    async_add_entities(
        DanfossLocalSwitch(coordinator, cid, desc)
        for cid, dev in coordinator.data.items()
        for desc in SWITCHES
        if mapped(dev, desc.code, writable=True)
    )


class DanfossLocalSwitch(DanfossLocalEntity, SwitchEntity):
    """A boolean device setting."""

    entity_description: DanfossSwitchDescription

    def __init__(
        self, coordinator: DanfossLocalCoordinator, cid: str, desc: DanfossSwitchDescription
    ) -> None:
        super().__init__(coordinator, cid, desc.key)
        self.entity_description = desc

    @property
    def is_on(self) -> bool | None:
        """Return the current value."""
        value = self.device.value(self.entity_description.code)
        return None if value is None else bool(value)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the setting on."""
        await self.coordinator.async_write(self._cid, {self.entity_description.code: True})

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the setting off."""
        await self.coordinator.async_write(self._cid, {self.entity_description.code: False})
