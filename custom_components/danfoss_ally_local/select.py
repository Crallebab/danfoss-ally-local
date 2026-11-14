"""Select entities for Danfoss Ally radiator thermostat settings."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import DanfossLocalConfigEntry, DanfossLocalCoordinator
from .entity import DanfossLocalEntity, mapped

# Raw DP value -> option key (translated in strings.json).
MOUNTING = {"Horizontal": "horizontal", "Vertical": "vertical"}


PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DanfossLocalConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up selects."""
    coordinator = entry.runtime_data
    async_add_entities(
        DanfossMountingSelect(coordinator, cid)
        for cid, dev in coordinator.data.items()
        if mapped(dev, "mounting_mode", writable=True)
    )


class DanfossMountingSelect(DanfossLocalEntity, SelectEntity):
    """Valve mounting orientation (app: "Vertical mounting")."""

    _attr_translation_key = "mounting"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = list(MOUNTING.values())

    def __init__(self, coordinator: DanfossLocalCoordinator, cid: str) -> None:
        super().__init__(coordinator, cid, "mounting")

    @property
    def current_option(self) -> str | None:
        """Return the current orientation."""
        return MOUNTING.get(self.device.value("mounting_mode"))

    async def async_select_option(self, option: str) -> None:
        """Set the orientation."""
        raw = next(k for k, v in MOUNTING.items() if v == option)
        await self.coordinator.async_write(self._cid, {"mounting_mode": raw})
