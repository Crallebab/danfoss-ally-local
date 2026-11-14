"""Polling coordinator for Danfoss Ally Local."""

from __future__ import annotations

import asyncio
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
    CONF_PROFILES,
    CONF_PROTOCOL_VERSION,
    DOMAIN,
    UPDATE_INTERVAL,
)
from .gateway import DanfossGateway, GatewayAuthError, GatewayError
from .profiles import PROFILES_BY_KEY, RADIATOR_THERMOSTAT, UNKNOWN, Profile, detect_profile

_LOGGER = logging.getLogger(__name__)

type DanfossLocalConfigEntry = ConfigEntry[DanfossLocalCoordinator]

# The gateway answers "check key or version" (914) both for a wrong key and when
# it is merely busy, so only this many failures in a row start a reauth flow.
AUTH_FAILURES_BEFORE_REAUTH = 3
SETUP_AUTH_RETRY_DELAY = 5.0
# Ask the gateway for its sub-device list again every this many polls.
RESCAN_EVERY_POLLS = 20


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
        # cids the gateway listed on its last sub-device query.
        self.listed: set[str] = set()
        # Set by async_setup_entry once entities exist; from then on a device
        # whose type changes (or a new device) reloads the entry.
        self.reload_on_change = False
        self._auth_failures = 0
        self._polls = 0

    async def _async_list_subdevices(self) -> dict[str, bool]:
        return await self.hass.async_add_executor_job(self.gateway.list_subdevices)

    async def _async_setup(self) -> None:
        for attempt in range(1, AUTH_FAILURES_BEFORE_REAUTH + 1):
            try:
                found = await self._async_list_subdevices()
                break
            except GatewayAuthError as err:
                if attempt == AUTH_FAILURES_BEFORE_REAUTH:
                    raise ConfigEntryAuthFailed(str(err)) from err
                await asyncio.sleep(SETUP_AUTH_RETRY_DELAY)
            except GatewayError as err:
                raise UpdateFailed(str(err)) from err
        # Device types seen on earlier runs: a valve that is offline or reports
        # only a few DPs right after a restart still gets its entities.
        stored: dict[str, str] = self.config_entry.data.get(CONF_PROFILES, {})
        self.devices = {
            cid: SubDevice(cid, online, profile=PROFILES_BY_KEY.get(stored.get(cid, ""), UNKNOWN))
            for cid, online in found.items()
        }
        self.listed = set(found)
        _LOGGER.debug("Gateway has %d sub-devices", len(found))

    async def _async_update_data(self) -> dict[str, SubDevice]:
        self._polls += 1
        changed = False
        if self._polls % RESCAN_EVERY_POLLS == 0:
            changed = await self._async_rescan()

        errors: list[str] = []
        for dev in list(self.devices.values()):
            if self.gateway.closed:
                break
            try:
                dps = await self.hass.async_add_executor_job(self.gateway.status, dev.cid)
            except GatewayAuthError as err:
                self._auth_failures += 1
                if self._auth_failures >= AUTH_FAILURES_BEFORE_REAUTH:
                    raise ConfigEntryAuthFailed(str(err)) from err
                # The session is gone; the remaining devices would fail the same way.
                raise UpdateFailed(str(err)) from err
            except GatewayError as err:
                dev.online = False
                errors.append(str(err))
                continue
            self._auth_failures = 0
            dev.online = True
            dev.dps.update(dps)
            detected = detect_profile(dev.dps)
            # Upgrade an unknown device, and correct a valve first seen through a
            # status too short to tell it from a room sensor.
            if detected is not dev.profile and (
                dev.profile is UNKNOWN or detected is RADIATOR_THERMOSTAT
            ):
                dev.profile = detected
                changed = True

        if errors and len(errors) == len(self.devices):
            raise UpdateFailed("; ".join(errors))
        if changed and self.reload_on_change:
            self.async_persist_profiles()
            self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)
        return self.devices

    async def _async_rescan(self) -> bool:
        """Refresh the sub-device list; return True if a new device appeared."""
        try:
            found = await self._async_list_subdevices()
        except GatewayError as err:
            _LOGGER.debug("Sub-device rescan failed: %s", err)
            return False
        if not found:
            return False
        self.listed = set(found)
        new = [cid for cid in found if cid not in self.devices]
        for cid in new:
            self.devices[cid] = SubDevice(cid, found[cid])
        return bool(new)

    def async_persist_profiles(self) -> None:
        """Store each device's detected type in the config entry."""
        profiles = {
            cid: dev.profile.key for cid, dev in self.devices.items() if dev.profile is not UNKNOWN
        }
        stored = self.config_entry.data.get(CONF_PROFILES, {})
        merged = {**stored, **profiles}
        if merged != stored:
            self.hass.config_entries.async_update_entry(
                self.config_entry, data={**self.config_entry.data, CONF_PROFILES: merged}
            )

    async def async_write(
        self, cid: str, values: dict[str, Any], optimistic: dict[str, Any] | None = None
    ) -> None:
        """Write decoded values (by Danfoss code) to one sub-device.

        ``optimistic`` holds further codes the write is known to change on the
        device (e.g. the active setpoint), shown until the device reports them.
        """
        dev = self.devices[cid]
        if not dev.profile.confirmed:
            raise HomeAssistantError(
                f"Writes are disabled for '{dev.profile.name}' until its data point map is confirmed"
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
            raise HomeAssistantError(f"Write to {cid[-4:]} failed: {err}") from err
        dev.dps.update(raw)
        for code, value in (optimistic or {}).items():
            pair = dev.profile.raw(code, value)
            if pair is not None:
                dev.dps[pair[0]] = pair[1]
        self.async_update_listeners()
        await self.async_request_refresh()

    async def async_shutdown(self) -> None:
        """Close the gateway socket."""
        await super().async_shutdown()
        await self.hass.async_add_executor_job(self.gateway.close)
