"""Number entities for Danfoss Ally preset temperatures."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import DanfossLocalConfigEntry, DanfossLocalCoordinator
from .entity import DanfossLocalEntity, mapped


@dataclass(frozen=True, kw_only=True)
class DanfossNumberDescription(NumberEntityDescription):
    """Preset temperature bound to one Danfoss code."""

    code: str


def _preset(key: str, code: str) -> DanfossNumberDescription:
    return DanfossNumberDescription(
        key=key,
        translation_key=key,
        code=code,
        device_class=NumberDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        native_min_value=5.0,
        native_max_value=35.0,
        native_step=0.5,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    )


NUMBERS = (
    _preset("at_home_temperature", "at_home_setting"),
    _preset("away_temperature", "leaving_home_setting"),
    _preset("pause_temperature", "pause_setting"),
)


PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DanfossLocalConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up preset temperature numbers."""
    coordinator = entry.runtime_data
    async_add_entities(
        DanfossLocalNumber(coordinator, cid, desc)
        for cid, dev in coordinator.data.items()
        for desc in NUMBERS
        if mapped(dev, desc.code, writable=True)
    )


class DanfossLocalNumber(DanfossLocalEntity, NumberEntity):
    """A preset temperature."""

    entity_description: DanfossNumberDescription

    def __init__(
        self, coordinator: DanfossLocalCoordinator, cid: str, desc: DanfossNumberDescription
    ) -> None:
        super().__init__(coordinator, cid, desc.key)
        self.entity_description = desc

    @property
    def native_value(self) -> float | None:
        """Return the preset temperature."""
        return self.device.value(self.entity_description.code)

    async def async_set_native_value(self, value: float) -> None:
        """Set the preset temperature."""
        await self.coordinator.async_write(self._cid, {self.entity_description.code: value})
