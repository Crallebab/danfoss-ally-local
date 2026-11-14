"""Polling coordinator for Danfoss Ally Local."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_DEVICE_ID,
    CONF_LOCAL_KEY,
    CONF_PROTOCOL_VERSION,
    DOMAIN,
    UPDATE_INTERVAL,
)
from .gateway import DanfossGateway, GatewayAuthError, GatewayError
from .profiles import UNKNOWN, Profile, detect_profile

_LOGGER = logging.getLogger(__name__)

type DanfossLocalConfigEntry = ConfigEntry[DanfossLocalCoordinator]


@dataclass
class SubDevice:
    """State of one sub-device."""

    cid: str
    online: bool = True
    dps: dict[str, Any] = field(default_factory=dict)
    profile: Profile = UNKNOWN

    def value(self, code: str) -> Any:
        """Return a decoded value by Danfoss code."""
        return self.profile.value(self.dps, code)

    def has(self, code: str) -> bool:
        """Return whether a code is mapped and reported."""
        return self.profile.has(self.dps, code)


class DanfossLocalCoordinator(DataUpdateCoordinator[dict[str, SubDevice]]):
    """Poll every sub-device behind the gateway."""

    config_entry: DanfossLocalConfigEntry

    def __init__(self, hass: HomeAssistant, entry: DanfossLocalConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self.gateway_id: str = entry.data[CONF_DEVICE_ID]
        self.gateway = DanfossGateway(
            entry.data[CONF_HOST],
            entry.data[CONF_DEVICE_ID],
            entry.data[CONF_LOCAL_KEY],
            entry.data[CONF_PROTOCOL_VERSION],
        )
        self.devices: dict[str, SubDevice] = {}

    async def _async_setup(self) -> None:
        try:
            found = await self.hass.async_add_executor_job(self.gateway.list_subdevices)
        except GatewayAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except GatewayError as err:
            raise UpdateFailed(str(err)) from err
        self.devices = {cid: SubDevice(cid, online) for cid, online in found.items()}
        _LOGGER.debug("Gateway %s has sub-devices %s", self.gateway_id, found)

    async def _async_update_data(self) -> dict[str, SubDevice]:
        errors: list[str] = []
        for dev in self.devices.values():
            try:
                dps = await self.hass.async_add_executor_job(self.gateway.status, dev.cid)
            except GatewayAuthError as err:
                raise ConfigEntryAuthFailed(str(err)) from err
            except GatewayError as err:
                dev.online = False
                errors.append(str(err))
                continue
            dev.online = True
            dev.dps.update(dps)
            if dev.profile is UNKNOWN:
                dev.profile = detect_profile(dev.dps)
        if errors and len(errors) == len(self.devices):
            raise UpdateFailed("; ".join(errors))
        return self.devices

    async def async_write(self, cid: str, values: dict[str, Any]) -> None:
        """Write decoded values (by Danfoss code) to one sub-device."""
        dev = self.devices[cid]
        if not dev.profile.confirmed:
            raise HomeAssistantError(
                f"Writes are disabled for '{dev.profile.name}' until its data point "
                "map is confirmed (see tools/danfoss_probe.py map)"
            )
        raw: dict[str, Any] = {}
        for code, value in values.items():
            try:
                dp, encoded = dev.profile.encode(code, value)
            except KeyError as err:
                raise HomeAssistantError(str(err)) from err
            raw[dp] = encoded
        try:
            await self.hass.async_add_executor_job(self.gateway.set_dps, cid, raw)
        except GatewayError as err:
            raise HomeAssistantError(f"Write to {cid} failed: {err}") from err
        dev.dps.update(raw)
        self.async_update_listeners()
        await self.async_request_refresh()

    async def async_shutdown(self) -> None:
        """Close the gateway socket."""
        await super().async_shutdown()
        await self.hass.async_add_executor_job(self.gateway.close)
