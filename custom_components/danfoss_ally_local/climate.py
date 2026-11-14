"""Climate entities for Danfoss Ally radiator thermostats."""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import DanfossLocalConfigEntry, DanfossLocalCoordinator
from .entity import DanfossLocalEntity
from .profiles import RADIATOR_THERMOSTAT

# Danfoss modes, exposed one-to-one as presets.
# "holiday" is reported by the device but not offered: its setpoint DP is unmapped.
PRESETS = ["at_home", "leaving_home", "manual", "pause"]
SCHEDULE_MODES = {"at_home", "leaving_home"}
# The external room-sensor temperature reports -80 °C when no sensor is linked.
NO_EXTERNAL_SENSOR = -80.0


PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DanfossLocalConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up climate entities."""
    coordinator = entry.runtime_data
    async_add_entities(
        DanfossLocalClimate(coordinator, cid)
        for cid, dev in coordinator.data.items()
        if dev.profile is RADIATOR_THERMOSTAT
    )


class DanfossLocalClimate(DanfossLocalEntity, ClimateEntity):
    """A Danfoss Ally radiator thermostat."""

    _attr_name = None
    _attr_translation_key = "thermostat"
    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 0.5
    _attr_hvac_modes = [HVACMode.AUTO, HVACMode.HEAT, HVACMode.OFF]
    _attr_preset_modes = PRESETS
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TURN_OFF
        | ClimateEntityFeature.TURN_ON
    )

    def __init__(self, coordinator: DanfossLocalCoordinator, cid: str) -> None:
        super().__init__(coordinator, cid, "climate")

    @property
    def native_current_temperature(self) -> float | None:
        """Return the room temperature, preferring a linked room sensor."""
        external = self.device.value("ext_measured_rs")
        if external is not None and external > NO_EXTERNAL_SENSOR:
            return external
        # DP 169 carries the valve's own reading in 0.01 C; DP 24 in 0.1 C.
        precise = self.device.value("temp_current_precise")
        return precise if precise is not None else self.device.value("temp_current")

    @property
    def native_target_temperature(self) -> float | None:
        """Return the active setpoint."""
        return self.device.value("temp_set")

    # min_temp / max_temp have no native_ variant in Home Assistant.
    @property
    def min_temp(self) -> float:
        """Return the lowest allowed setpoint."""
        value = self.device.value("lower_temp")
        return 5.0 if value is None else value

    @property
    def max_temp(self) -> float:
        """Return the highest allowed setpoint."""
        value = self.device.value("upper_temp")
        return 35.0 if value is None else value

    @property
    def preset_mode(self) -> str | None:
        """Return the Danfoss mode."""
        mode = self.device.value("mode")
        return mode if mode in PRESETS else None

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Map the Danfoss mode to an HVAC mode."""
        mode = self.device.value("mode")
        if mode in SCHEDULE_MODES:
            return HVACMode.AUTO
        if mode == "pause":
            return HVACMode.OFF
        if mode in {"manual", "holiday"}:
            return HVACMode.HEAT
        return None

    @property
    def hvac_action(self) -> HVACAction | None:
        """Return whether the valve is currently heating."""
        if self.device.value("mode") == "pause":
            return HVACAction.OFF
        work_state = self.device.value("work_state")
        if work_state is None:
            return None
        state = str(work_state).lower()
        if state == "noheat":
            return HVACAction.IDLE
        if not state.startswith("heat"):
            return None
        if state.endswith("_active"):
            return HVACAction.HEATING
        demand = self.device.value("pi_heating_demand")
        if demand is not None:
            return HVACAction.HEATING if demand > 1 else HVACAction.IDLE
        return HVACAction.HEATING

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose a few useful raw values."""
        attrs = {}
        for code in ("window_state", "SetpointChangeSource", "pi_heating_demand"):
            value = self.device.value(code)
            if value is not None:
                attrs[code.lower()] = value
        return attrs

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set a manual setpoint (overrides the schedule, like the Danfoss app)."""
        if ATTR_TEMPERATURE not in kwargs:
            return
        temperature = float(kwargs[ATTR_TEMPERATURE])
        await self.coordinator.async_write(
            self._cid,
            {"mode": "manual", "manual_mode_fast": temperature},
            # In manual mode the active setpoint follows the manual setpoint.
            optimistic={"temp_set": temperature},
        )

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Switch Danfoss mode."""
        await self.coordinator.async_write(self._cid, {"mode": preset_mode})

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Map an HVAC mode to a Danfoss mode."""
        if hvac_mode == HVACMode.AUTO and self.device.value("mode") in SCHEDULE_MODES:
            return  # already on the schedule; don't override "away" with "at home"
        mode = {HVACMode.AUTO: "at_home", HVACMode.HEAT: "manual", HVACMode.OFF: "pause"}[
            hvac_mode
        ]
        await self.coordinator.async_write(self._cid, {"mode": mode})
