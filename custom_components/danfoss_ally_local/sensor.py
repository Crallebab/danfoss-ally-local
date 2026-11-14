"""Sensor entities for Danfoss Ally Local."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import DanfossLocalConfigEntry, DanfossLocalCoordinator
from .entity import DanfossLocalEntity
from .profiles import RADIATOR_THERMOSTAT, ROOM_SENSOR


@dataclass(frozen=True, kw_only=True)
class DanfossSensorDescription(SensorEntityDescription):
    """Sensor bound to one Danfoss code."""

    code: str


TEMPERATURE = DanfossSensorDescription(
    key="temperature",
    code="temp_current",
    device_class=SensorDeviceClass.TEMPERATURE,
    native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    state_class=SensorStateClass.MEASUREMENT,
)
HUMIDITY = DanfossSensorDescription(
    key="humidity",
    code="humidity_value",
    device_class=SensorDeviceClass.HUMIDITY,
    native_unit_of_measurement=PERCENTAGE,
    state_class=SensorStateClass.MEASUREMENT,
)
BATTERY = DanfossSensorDescription(
    key="battery",
    code="battery_percentage",
    device_class=SensorDeviceClass.BATTERY,
    native_unit_of_measurement=PERCENTAGE,
    state_class=SensorStateClass.MEASUREMENT,
    entity_category=EntityCategory.DIAGNOSTIC,
)
HEATING_DEMAND = DanfossSensorDescription(
    key="heating_demand",
    translation_key="heating_demand",
    code="pi_heating_demand",
    native_unit_of_measurement=PERCENTAGE,
    state_class=SensorStateClass.MEASUREMENT,
)

VALVE_TEMPERATURE = DanfossSensorDescription(
    key="temperature",
    code="temp_current_precise",  # DP 169, 0.01 C
    device_class=SensorDeviceClass.TEMPERATURE,
    native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=1,
)

SENSORS_BY_PROFILE = {
    RADIATOR_THERMOSTAT.key: (VALVE_TEMPERATURE, BATTERY, HEATING_DEMAND),
    ROOM_SENSOR.key: (TEMPERATURE, HUMIDITY, BATTERY),
}


PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DanfossLocalConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors."""
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = []
    for cid, dev in coordinator.data.items():
        entities.extend(
            DanfossLocalSensor(coordinator, cid, desc)
            for desc in SENSORS_BY_PROFILE.get(dev.profile.key, ())
            if (point := dev.profile.codes.get(desc.code)) and point.dp is not None
        )
        entities.append(DanfossRawSensor(coordinator, cid))
    async_add_entities(entities)


class DanfossLocalSensor(DanfossLocalEntity, SensorEntity):
    """A value read from one Danfoss code."""

    entity_description: DanfossSensorDescription

    def __init__(
        self, coordinator: DanfossLocalCoordinator, cid: str, desc: DanfossSensorDescription
    ) -> None:
        super().__init__(coordinator, cid, desc.key)
        self.entity_description = desc

    @property
    def native_value(self) -> Any:
        """Return the decoded value."""
        return self.device.value(self.entity_description.code)


class DanfossRawSensor(DanfossLocalEntity, SensorEntity):
    """All raw data points, for mapping unknown DPs. State is the DP count."""

    _attr_translation_key = "raw_dps"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    # Every DP lands in the state attributes on each poll; opt-in for debugging.
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DanfossLocalCoordinator, cid: str) -> None:
        super().__init__(coordinator, cid, "raw_dps")

    @property
    def native_value(self) -> int:
        """Return how many DPs the device reports."""
        return len(self.device.dps)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the raw DPs."""
        return {
            "cid": self._cid,
            "profile": self.device.profile.key,
            "dps": dict(sorted(self.device.dps.items(), key=lambda kv: int(kv[0]))),
        }
