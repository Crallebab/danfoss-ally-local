"""Tests for the polling coordinator (need the Home Assistant test harness)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

from custom_components.danfoss_ally_local import coordinator as co  # noqa: E402
from custom_components.danfoss_ally_local.const import DOMAIN  # noqa: E402
from custom_components.danfoss_ally_local.device import SubDevice  # noqa: E402
from custom_components.danfoss_ally_local.diagnostics import (  # noqa: E402
    async_get_config_entry_diagnostics,
)
from custom_components.danfoss_ally_local.gateway import (  # noqa: E402
    GatewayAuthError,
    GatewayConnectionError,
    GatewayError,
)
from homeassistant.config_entries import ConfigEntryState  # noqa: E402
from homeassistant.const import MAJOR_VERSION, MINOR_VERSION  # noqa: E402
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError  # noqa: E402
from homeassistant.helpers import entity_registry as er  # noqa: E402
from homeassistant.helpers.update_coordinator import UpdateFailed  # noqa: E402

ENTRY_DATA = {
    "host": "192.0.2.1",
    "device_id": "gw0123456789abcdefghij",
    "local_key": "0123456789abcdef",
    "protocol_version": "3.5",
}
# What an Ally radiator thermostat and a room sensor report (raw values).
VALVE = {"2": "manual", "16": 205, "24": 213, "34": 80, "114": 205, "169": 2134, "171": 2050}
SENSOR = {"1": 215, "2": 480, "4": 90}

# The thermostat entity uses the climate API of Home Assistant 2026.11. The test
# harness can lag a release behind; everything else is tested on it all the same.
needs_2026_11 = pytest.mark.skipif(
    (MAJOR_VERSION, MINOR_VERSION) < (2026, 11),
    reason="the thermostat entity needs Home Assistant 2026.11",
)


@pytest.fixture(autouse=True)
def _custom_integrations(enable_custom_integrations):
    """Let Home Assistant load the integration from custom_components."""


class FakeGateway:
    """Stands in for DanfossGateway: scripted replies per sub-device."""

    def __init__(self, *args):
        self.closed = False
        self.listing: dict[str, bool] | Exception = {}
        # cid -> exception to raise, or a callable returning one (or None).
        # A cid that is not in here gets a normal reply.
        self.behaviour: dict = {}
        self.dps: dict[str, dict] = {}
        self.asked: list[str] = []
        self.listed = 0
        self.written: list[tuple[str, dict]] = []

    def list_subdevices(self):
        self.listed += 1
        if isinstance(self.listing, Exception):
            raise self.listing
        return dict(self.listing)

    def status(self, cid):
        self.asked.append(cid)
        outcome = self.behaviour.get(cid)
        if callable(outcome):
            outcome = outcome()
        if outcome is not None:
            raise outcome
        return dict(self.dps.get(cid, VALVE))

    def set_dps(self, cid, values):
        self.written.append((cid, dict(values)))

    def close(self):
        self.closed = True


def make(hass, count, offline=()):
    """Return a coordinator with ``count`` valves and a fake gateway."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id=ENTRY_DATA["device_id"], data=ENTRY_DATA)
    entry.add_to_hass(hass)
    with patch.object(co, "DanfossGateway", FakeGateway):
        coordinator = co.DanfossLocalCoordinator(hass, entry)
    coordinator.devices = {
        f"c{i}": SubDevice(f"c{i}", gateway_online=f"c{i}" not in offline) for i in range(count)
    }
    coordinator.listed = set(coordinator.devices)
    coordinator.gateway.listing = {
        cid: dev.gateway_online for cid, dev in coordinator.devices.items()
    }
    return coordinator


async def poll(coordinator) -> str:
    """Run one poll; return "ok" or the name of what it raised."""
    coordinator.gateway.asked = []
    try:
        await coordinator._async_update_data()
    except (UpdateFailed, ConfigEntryAuthFailed) as err:
        return type(err).__name__
    return "ok"


async def test_a_poll_reads_every_listed_online_device(hass):
    coordinator = make(hass, 4, offline={"c3"})
    assert await poll(coordinator) == "ok"
    assert sorted(coordinator.gateway.asked) == ["c0", "c1", "c2"]
    assert coordinator.devices["c0"].online
    assert coordinator.devices["c0"].value("temp_set") == 20.5
    assert not coordinator.devices["c3"].online


async def test_the_poll_starts_with_a_different_device_each_time(hass):
    coordinator = make(hass, 3)
    first = []
    for _ in range(3):
        await poll(coordinator)
        first.append(coordinator.gateway.asked[0])
    assert sorted(first) == ["c0", "c1", "c2"]


async def test_a_dead_valve_is_backed_off_and_does_not_fail_the_poll(hass):
    coordinator = make(hass, 5)
    coordinator.gateway.behaviour["c0"] = GatewayError("timeout")
    results = [await poll(coordinator) for _ in range(40)]
    assert set(results) == {"ok"}
    assert coordinator.devices["c0"].failures == co.DEVICE_FAILURES_BEFORE_BACKOFF
    assert not coordinator.devices["c0"].online
    assert coordinator.devices["c1"].online
    asked = 0
    for _ in range(co.RETRY_FAILING_EVERY_POLLS * 3):
        await poll(coordinator)
        asked += coordinator.gateway.asked.count("c0")
    assert asked == 3  # once per RETRY_FAILING_EVERY_POLLS


async def test_a_dead_valve_answering_914_never_starts_a_reauth(hass):
    # The gateway answers a fresh socket with 914 after a timeout: that is the
    # valve, not the key, as long as other devices answer.
    coordinator = make(hass, 5)
    coordinator.gateway.behaviour["c0"] = GatewayAuthError("914")
    results = [await poll(coordinator) for _ in range(100)]
    assert set(results) == {"ok"}
    assert coordinator.devices["c0"].failures == co.DEVICE_FAILURES_BEFORE_BACKOFF
    assert coordinator._auth_failures == 0


@pytest.mark.parametrize("count", [5, 10])
async def test_two_dead_valves_are_not_taken_for_a_silent_gateway(hass, count):
    coordinator = make(hass, count)
    coordinator.gateway.behaviour["c0"] = GatewayError("timeout")
    coordinator.gateway.behaviour["c1"] = GatewayError("timeout")
    results = [await poll(coordinator) for _ in range(200)]
    assert set(results) == {"ok"}
    # Once backed off they are never retried on the same poll.
    for _ in range(co.RETRY_FAILING_EVERY_POLLS * 2):
        await poll(coordinator)
        assert len({"c0", "c1"} & set(coordinator.gateway.asked)) <= 1


async def test_a_backed_off_device_is_polled_again_once_it_answers(hass):
    coordinator = make(hass, 3)
    coordinator.gateway.behaviour["c0"] = GatewayError("timeout")
    for _ in range(10):
        await poll(coordinator)
    assert coordinator.devices["c0"].failures == co.DEVICE_FAILURES_BEFORE_BACKOFF
    del coordinator.gateway.behaviour["c0"]
    for _ in range(co.RETRY_FAILING_EVERY_POLLS):
        await poll(coordinator)
    assert coordinator.devices["c0"].failures == 0
    assert coordinator.devices["c0"].online


async def test_a_gateway_that_stops_answering_mid_poll_ends_the_poll(hass):
    coordinator = make(hass, 8)
    calls = {"n": 0}

    def first_only():
        calls["n"] += 1
        return None if calls["n"] == 1 else GatewayError("timeout")

    for cid in coordinator.devices:
        coordinator.gateway.behaviour[cid] = first_only
    assert await poll(coordinator) == "ok"
    assert len(coordinator.gateway.asked) == 1 + co.GATEWAY_SILENT_AFTER_FAILURES


async def test_a_silent_gateway_fails_the_poll_without_backing_devices_off(hass):
    coordinator = make(hass, 8)
    for cid in coordinator.devices:
        coordinator.gateway.behaviour[cid] = GatewayError("timeout")
    for _ in range(10):
        assert await poll(coordinator) == "UpdateFailed"
        assert len(coordinator.gateway.asked) == co.GATEWAY_SILENT_AFTER_FAILURES
    assert all(dev.failures == 0 for dev in coordinator.devices.values())


async def test_an_unreachable_gateway_fails_the_poll_at_once(hass):
    coordinator = make(hass, 3)
    coordinator.gateway.behaviour["c1"] = GatewayConnectionError("901")
    assert await poll(coordinator) == "UpdateFailed"
    assert coordinator.gateway.asked == ["c1"]  # poll 1 starts at index 1


async def test_a_wrong_key_starts_a_reauth_only_after_many_polls(hass):
    coordinator = make(hass, 3)
    for cid in coordinator.devices:
        coordinator.gateway.behaviour[cid] = GatewayAuthError("914")
    results = [await poll(coordinator) for _ in range(co.POLL_AUTH_FAILURES_BEFORE_REAUTH)]
    assert results[0] == "ok"  # a single occurrence keeps the last values
    assert set(results[1:-1]) == {"UpdateFailed"}
    assert results[-1] == "ConfigEntryAuthFailed"


async def test_one_good_poll_restarts_the_auth_count(hass):
    coordinator = make(hass, 3)
    for cid in coordinator.devices:
        coordinator.gateway.behaviour[cid] = GatewayAuthError("914")
    for _ in range(co.POLL_AUTH_FAILURES_BEFORE_REAUTH - 1):
        await poll(coordinator)
    coordinator.gateway.behaviour.clear()
    assert await poll(coordinator) == "ok"
    assert coordinator._auth_failures == 0


async def test_an_unreachable_gateway_fails_the_poll_during_the_rescan(hass):
    coordinator = make(hass, 3, offline={"c2"})
    coordinator.gateway.listing = GatewayConnectionError("901")
    assert await poll(coordinator) == "ok"  # poll 1: no rescan
    assert await poll(coordinator) == "UpdateFailed"  # poll 2: rescan while offline
    assert coordinator.gateway.asked == []


async def test_another_rescan_error_is_ignored(hass):
    coordinator = make(hass, 3, offline={"c2"})
    coordinator.gateway.listing = GatewayError("no response")
    assert await poll(coordinator) == "ok"
    assert await poll(coordinator) == "ok"
    assert coordinator.gateway.asked


async def test_the_rescan_is_frequent_only_while_a_listed_device_is_offline(hass):
    coordinator = make(hass, 3, offline={"c2"})
    for _ in range(co.RESCAN_EVERY_POLLS):
        await poll(coordinator)
    assert coordinator.gateway.listed == co.RESCAN_EVERY_POLLS // 2

    # The offline device was unpaired: the gateway no longer lists it.
    coordinator = make(hass, 3, offline={"c2"})
    coordinator.gateway.listing = {"c0": True, "c1": True}
    for _ in range(co.RESCAN_EVERY_POLLS * 2):
        await poll(coordinator)
    assert coordinator.gateway.listed == 1 + 2  # poll 2, then only every 20th


async def test_a_device_back_online_is_polled_on_the_same_poll(hass):
    coordinator = make(hass, 2, offline={"c1"})
    coordinator.devices["c1"].failures = co.DEVICE_FAILURES_BEFORE_BACKOFF
    coordinator.gateway.listing = {"c0": True, "c1": True}
    await poll(coordinator)
    await poll(coordinator)  # rescan
    assert "c1" in coordinator.gateway.asked
    assert coordinator.devices["c1"].online


async def test_a_write_is_shown_until_the_device_confirms_it(hass):
    coordinator = make(hass, 1)
    await poll(coordinator)
    dev = coordinator.devices["c0"]
    assert dev.profile.key == "radiator_thermostat"
    await coordinator.async_write("c0", {"manual_mode_fast": 22.0}, {"temp_set": 22.0})
    assert coordinator.gateway.written == [("c0", {"114": 220})]
    assert dev.value("temp_set") == 22.0
    await poll(coordinator)  # the gateway still serves its pre-write cache
    assert dev.value("temp_set") == 22.0
    assert dev.value("manual_mode_fast") == 22.0


async def test_writes_are_refused_for_unknown_devices_and_read_only_codes(hass):
    coordinator = make(hass, 2)
    coordinator.gateway.dps["c1"] = {"7": 1}
    await poll(coordinator)
    with pytest.raises(HomeAssistantError, match="disabled"):
        await coordinator.async_write("c1", {"mode": "manual"})
    with pytest.raises(HomeAssistantError, match="read-only"):
        await coordinator.async_write("c0", {"temp_current": 21.0})
    assert coordinator.gateway.written == []


async def _setup(hass, dps, prepare=None):
    entry = MockConfigEntry(domain=DOMAIN, unique_id=ENTRY_DATA["device_id"], data=ENTRY_DATA)
    entry.add_to_hass(hass)
    if prepare:
        prepare(entry)
    made = []

    def factory(*args):
        gateway = FakeGateway(*args)
        gateway.listing = dict.fromkeys(dps, True)
        gateway.dps = dps
        made.append(gateway)
        return gateway

    with patch.object(co, "DanfossGateway", factory):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry, made


async def test_setup_creates_devices_and_entities_and_unload_closes_the_gateway(hass):
    entry, made = await _setup(hass, {"valve0001": VALVE, "sensor0002": SENSOR})
    assert entry.state is ConfigEntryState.LOADED
    assert entry.data["profiles"] == {
        "valve0001": "radiator_thermostat",
        "sensor0002": "room_sensor",
    }
    # Per device: temperature and battery, plus heating demand or humidity.
    # The raw data point sensors are disabled by default.
    assert len(hass.states.async_all("sensor")) == 6
    assert len(hass.states.async_all("switch")) == 3
    assert len(hass.states.async_all("number")) == 3

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert made[-1].closed


@needs_2026_11
async def test_setup_creates_the_thermostat(hass):
    await _setup(hass, {"valve0001": VALVE})
    climates = hass.states.async_all("climate")
    assert len(climates) == 1
    assert climates[0].attributes["temperature"] == 20.5
    assert climates[0].attributes["current_temperature"] == pytest.approx(21.3, abs=0.05)


async def test_the_window_sensor_appears_once_the_valve_reports_a_window_state(hass):
    entry, made = await _setup(hass, {"valve0001": dict(VALVE)})
    assert hass.states.async_all("binary_sensor") == []

    made[-1].dps["valve0001"]["25"] = "close"
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    (window,) = hass.states.async_all("binary_sensor")
    assert window.state == "off"

    made[-1].dps["valve0001"]["25"] = "open"
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    (window,) = hass.states.async_all("binary_sensor")  # still one, now open
    assert window.state == "on"


async def test_a_window_sensor_reported_at_setup_is_there_from_the_start(hass):
    await _setup(hass, {"valve0001": {**VALVE, "25": "close"}})
    assert len(hass.states.async_all("binary_sensor")) == 1


def _register_window(hass, entry, **changes):
    registry = er.async_get(hass)
    entity = registry.async_get_or_create(
        "binary_sensor", DOMAIN, "valve0001_window", config_entry=entry
    )
    if changes:
        registry.async_update_entity(entity.entity_id, **changes)
    return entity.entity_id


async def test_an_unreported_window_sensor_from_an_earlier_version_is_removed(hass):
    ids = []
    await _setup(
        hass, {"valve0001": VALVE}, prepare=lambda entry: ids.append(_register_window(hass, entry))
    )
    assert er.async_get(hass).async_get(ids[0]) is None
    assert hass.states.async_all("binary_sensor") == []


async def test_an_unreported_window_sensor_the_user_changed_is_kept(hass):
    ids = []
    await _setup(
        hass,
        {"valve0001": VALVE},
        prepare=lambda entry: ids.append(_register_window(hass, entry, name="Stue vindue")),
    )
    assert er.async_get(hass).async_get(ids[0]).name == "Stue vindue"
    (window,) = hass.states.async_all("binary_sensor")
    assert window.state == "unknown"


async def test_setup_is_retried_while_the_gateway_lists_no_subdevices(hass):
    entry, _ = await _setup(hass, {})
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_diagnostics_hold_no_key_address_or_full_ids(hass):
    entry, _ = await _setup(hass, {"valve0001": VALVE})
    text = str(await async_get_config_entry_diagnostics(hass, entry))
    for secret in ("192.0.2.1", ENTRY_DATA["device_id"], ENTRY_DATA["local_key"], "valve0001"):
        assert secret not in text
    assert "0001" in text
