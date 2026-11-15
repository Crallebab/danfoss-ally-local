"""Polling coordinator for Danfoss Ally Local."""

from __future__ import annotations

import logging
import time
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
from .device import SubDevice
from .gateway import DanfossGateway, GatewayAuthError, GatewayConnectionError, GatewayError
from .profiles import PROFILES_BY_KEY, RADIATOR_THERMOSTAT, UNKNOWN, detect_profile

_LOGGER = logging.getLogger(__name__)

type DanfossLocalConfigEntry = ConfigEntry[DanfossLocalCoordinator]

# The gateway answers "check key or version" (914) both for a wrong key and when
# its single connection slot is busy, and a reauth flow stops polling until the
# user acts. So a reauth is only started after the error has persisted for a
# long time: this many setup attempts (Home Assistant retries with backoff, a
# few minutes in total) or this many polls in a row (about ten minutes).
SETUP_AUTH_FAILURES_BEFORE_REAUTH = 5
POLL_AUTH_FAILURES_BEFORE_REAUTH = 20
# Ask the gateway for its sub-device list again every this many polls, or every
# RESCAN_EVERY_POLLS_WHILE_OFFLINE while it lists a device as offline, so a
# valve that checks in again is picked up within a minute.
RESCAN_EVERY_POLLS = 20
RESCAN_EVERY_POLLS_WHILE_OFFLINE = 2
# When this many sub-devices in a row fail in one poll, the gateway itself has
# stopped answering: stop instead of timing out on each remaining device.
# Devices already in backoff are expected to fail and do not count.
GATEWAY_SILENT_AFTER_FAILURES = 2
# A sub-device that failed this many polls in a row is only asked again every
# RETRY_FAILING_EVERY_POLLS polls, so a dead valve does not slow down the rest.
# The retries are spread out, so two dead valves are not retried on the same poll.
DEVICE_FAILURES_BEFORE_BACKOFF = 3
RETRY_FAILING_EVERY_POLLS = 10
# A written value is shown until the device confirms it, or for this long. Until
# then the gateway may still serve the value it cached before the write, and
# battery valves can take a couple of minutes to report.
WRITE_HOLD_SECONDS = 150.0

# Auth failures during setup, per config entry; survives the coordinator being
# recreated for each setup retry.
_setup_auth_failures: dict[str, int] = {}


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
        # A device type changed or a device was added; kept across failed polls.
        self._needs_reload = False

    async def _async_list_subdevices(self) -> dict[str, bool]:
        return await self.hass.async_add_executor_job(self.gateway.list_subdevices)

    async def _async_setup(self) -> None:
        entry_id = self.config_entry.entry_id
        try:
            found = await self._async_list_subdevices()
        except GatewayAuthError as err:
            failures = _setup_auth_failures.get(entry_id, 0) + 1
            _setup_auth_failures[entry_id] = failures
            if failures >= SETUP_AUTH_FAILURES_BEFORE_REAUTH:
                _setup_auth_failures.pop(entry_id, None)
                raise ConfigEntryAuthFailed(str(err)) from err
            raise UpdateFailed(str(err)) from err
        except GatewayError as err:
            raise UpdateFailed(str(err)) from err
        _setup_auth_failures.pop(entry_id, None)
        if not found:
            # Seen while the gateway's Zigbee side is still starting. Loading
            # with no devices would leave nothing to poll, so retry setup.
            raise UpdateFailed("the gateway reported no sub-devices")
        # Device types seen on earlier runs: a valve that is offline or reports
        # only a few DPs right after a restart still gets its entities.
        stored: dict[str, str] = self.config_entry.data.get(CONF_PROFILES, {})
        self.devices = {
            cid: SubDevice(
                cid,
                online,
                profile=PROFILES_BY_KEY.get(stored.get(cid, ""), UNKNOWN),
                gateway_online=online,
            )
            for cid, online in found.items()
        }
        self.listed = set(found)
        _LOGGER.debug("Gateway has %d sub-devices", len(found))

    async def _async_update_data(self) -> dict[str, SubDevice]:
        self._polls += 1
        # A device the gateway no longer lists keeps its last flag forever, so
        # only listed devices decide how often to rescan.
        any_offline = any(
            not dev.gateway_online for dev in self.devices.values() if dev.cid in self.listed
        )
        rescan_every = RESCAN_EVERY_POLLS_WHILE_OFFLINE if any_offline else RESCAN_EVERY_POLLS
        if self._polls % rescan_every == 0:
            await self._async_rescan()

        # Start with a different device each poll, so a session that drops
        # part-way through does not always starve the same devices.
        order = list(enumerate(self.devices.values()))
        if order:
            start = self._polls % len(order)
            order = order[start:] + order[:start]

        errors: list[str] = []
        failed: list[SubDevice] = []
        succeeded = 0
        streak = 0  # failures in a row, of devices not in backoff
        auth_error: GatewayAuthError | None = None
        for index, dev in order:
            if self.gateway.closed:
                break
            if not dev.gateway_online or dev.cid not in self.listed:
                # Offline or unpaired according to the gateway: asking for its
                # status would only time out and drop the shared connection.
                dev.online = False
                continue
            backed_off = dev.failures >= DEVICE_FAILURES_BEFORE_BACKOFF
            if backed_off and (self._polls + index) % RETRY_FAILING_EVERY_POLLS:
                continue  # keeps failing: stays unavailable, retried now and then
            try:
                dps = await self.hass.async_add_executor_job(self.gateway.status, dev.cid)
            except GatewayConnectionError as err:
                # The gateway itself is unreachable (after a retry): every
                # device is unavailable.
                raise UpdateFailed(str(err)) from err
            except GatewayError as err:
                dev.online = False
                if backed_off:
                    # Still dead, as expected. This says nothing about the
                    # gateway or the key, whatever the error was.
                    continue
                failed.append(dev)
                streak += 1
                if isinstance(err, GatewayAuthError):
                    # The session is gone; the remaining devices would fail the same way.
                    auth_error = err
                    break
                errors.append(str(err))
                if streak >= GATEWAY_SILENT_AFTER_FAILURES:
                    break  # the gateway stopped answering
                continue
            succeeded += 1
            streak = 0
            dev.failures = 0
            dev.online = True
            dev.merge(dps, time.monotonic())
            detected = detect_profile(dev.dps)
            # Upgrade an unknown device, and correct a valve first seen through a
            # status too short to tell it from a room sensor.
            if detected is not dev.profile and (
                dev.profile is UNKNOWN or detected is RADIATOR_THERMOSTAT
            ):
                dev.profile = detected
                self._needs_reload = True

        if succeeded:
            # Some devices answered, so the failures are about those devices and
            # count toward their backoff. When nothing answered it is the
            # gateway, and no device is put into backoff for that.
            for dev in failed:
                dev.failures += 1

        if auth_error is None or succeeded:
            # No auth error, or the key worked earlier in this very poll and the
            # session merely dropped: either way the count "in a row" restarts.
            # The devices not reached keep their last values and are read first
            # on a later poll (the order rotates).
            self._auth_failures = 0
            if auth_error is not None:
                _LOGGER.debug("Poll cut short by the gateway (%s)", auth_error)
        else:
            self._auth_failures += 1
            if self._auth_failures >= POLL_AUTH_FAILURES_BEFORE_REAUTH:
                raise ConfigEntryAuthFailed(str(auth_error)) from auth_error
            if self._auth_failures > 1:
                raise UpdateFailed(str(auth_error)) from auth_error
            # A single occurrence keeps the last values for one poll.
            _LOGGER.debug("Poll cut short by the gateway (%s)", auth_error)

        if errors and not succeeded:
            raise UpdateFailed("; ".join(errors))
        if self._needs_reload and self.reload_on_change:
            self._needs_reload = False
            self.async_persist_profiles()
            self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)
        return self.devices

    def async_entities_created(self) -> None:
        """Called by async_setup_entry once the platforms are set up.

        Device types found during the first refresh already have their
        entities, so only changes from now on reload the entry.
        """
        self._needs_reload = False
        self.reload_on_change = True

    async def _async_rescan(self) -> None:
        """Refresh the sub-device list: online flags, new and unpaired devices."""
        try:
            found = await self._async_list_subdevices()
        except GatewayConnectionError as err:
            # Unreachable: fail the poll now instead of timing out again on
            # the first status.
            raise UpdateFailed(str(err)) from err
        except GatewayError as err:
            _LOGGER.debug("Sub-device rescan failed: %s", err)
            return
        if not found:
            return
        self.listed = set(found)
        for cid, online in found.items():
            dev = self.devices.get(cid)
            if dev is None:
                self.devices[cid] = SubDevice(cid, online, gateway_online=online)
                self._needs_reload = True
            else:
                if online and not dev.gateway_online:
                    dev.failures = 0  # back on the network: poll it right away
                dev.gateway_online = online

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

        shown = dict(raw)
        for code, value in (optimistic or {}).items():
            pair = dev.profile.raw(code, value)
            if pair is not None:
                shown[pair[0]] = pair[1]
        # No refresh here: the gateway would answer from its pre-write cache. The
        # written values are held until the device confirms them on a later poll.
        deadline = time.monotonic() + WRITE_HOLD_SECONDS
        for dp, value in shown.items():
            dev.dps[dp] = value
            dev.pending[dp] = (value, deadline)
        self.async_update_listeners()

    async def async_shutdown(self) -> None:
        """Close the gateway socket."""
        await super().async_shutdown()
        await self.hass.async_add_executor_job(self.gateway.close)
