"""Tests for the config flow (need the Home Assistant test harness)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

from custom_components.danfoss_ally_local import config_flow  # noqa: E402
from custom_components.danfoss_ally_local.const import DOMAIN  # noqa: E402
from custom_components.danfoss_ally_local.gateway import (  # noqa: E402
    GatewayAuthError,
    GatewayConnectionError,
)
from homeassistant.config_entries import SOURCE_USER  # noqa: E402
from homeassistant.data_entry_flow import FlowResultType  # noqa: E402

USER_INPUT = {
    "host": "192.0.2.1",
    "device_id": "gw0123456789abcdefghij",
    "local_key": "0123456789abcdef",
    "protocol_version": "3.5",
}


@pytest.fixture(autouse=True)
def _custom_integrations(enable_custom_integrations):
    """Let Home Assistant load the integration from custom_components."""


@pytest.fixture(autouse=True)
def _no_setup():
    """A finished flow must not set the entry up against a real gateway."""
    with patch("custom_components.danfoss_ally_local.async_setup_entry", return_value=True):
        yield


def fake_gateway(result):
    """Patch the gateway used by the flow; ``result`` is a list or an exception."""
    made = []

    class Gateway:
        def __init__(self, *args):
            self.args = args
            self.closed = False
            made.append(self)

        def list_subdevices(self):
            if isinstance(result, Exception):
                raise result
            return result

        def close(self):
            self.closed = True

    return patch.object(config_flow, "DanfossGateway", Gateway), made


async def _submit(hass, user_input):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    return await hass.config_entries.flow.async_configure(result["flow_id"], user_input)


async def test_user_flow_creates_the_entry_and_closes_the_test_connection(hass):
    patcher, made = fake_gateway({"a": True})
    with patcher:
        result = await _submit(hass, {**USER_INPUT, "local_key": "  0123456789abcdef "})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == USER_INPUT  # the key was stripped
    assert result["result"].unique_id == USER_INPUT["device_id"]
    assert [gw.closed for gw in made] == [True]


@pytest.mark.parametrize(
    ("outcome", "error"),
    [
        (GatewayAuthError("914"), "invalid_auth"),
        (GatewayConnectionError("901"), "cannot_connect"),
        ({}, "no_subdevices"),
    ],
)
async def test_user_flow_reports_why_the_test_failed(hass, outcome, error):
    patcher, made = fake_gateway(outcome)
    with patcher:
        result = await _submit(hass, USER_INPUT)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}
    assert [gw.closed for gw in made] == [True]


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ({"host": "auto"}, "cannot_connect"),
        ({"host": "0.0.0.0"}, "cannot_connect"),
        ({"local_key": "nøgle56789abcdef"}, "invalid_auth"),
    ],
)
async def test_input_tinytuya_would_mishandle_never_reaches_it(hass, change, error):
    patcher, made = fake_gateway({"a": True})
    with patcher:
        result = await _submit(hass, {**USER_INPUT, **change})
    assert result["errors"] == {"base": error}
    assert made == []


async def test_the_form_never_echoes_the_key_back(hass):
    patcher, _ = fake_gateway(GatewayAuthError("914"))
    with patcher:
        result = await _submit(hass, USER_INPUT)
    defaults = {
        str(key): key.default() for key in result["data_schema"].schema if callable(key.default)
    }
    assert defaults["host"] == USER_INPUT["host"]
    assert "local_key" not in defaults


async def test_a_gateway_can_only_be_added_once(hass):
    MockConfigEntry(domain=DOMAIN, unique_id=USER_INPUT["device_id"], data=USER_INPUT).add_to_hass(
        hass
    )
    patcher, made = fake_gateway({"a": True})
    with patcher:
        result = await _submit(hass, USER_INPUT)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert made == []


async def _reauth(hass, outcome, new_key="fedcba9876543210"):
    entry = MockConfigEntry(domain=DOMAIN, unique_id=USER_INPUT["device_id"], data=USER_INPUT)
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    patcher, _ = fake_gateway(outcome)
    with patcher:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"local_key": new_key, "protocol_version": "3.5"}
        )
        await hass.async_block_till_done()
    return entry, result


async def test_reauth_stores_the_new_key(hass):
    entry, result = await _reauth(hass, {"a": True})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data["local_key"] == "fedcba9876543210"
    assert entry.data["host"] == USER_INPUT["host"]


async def test_reauth_accepts_a_gateway_with_no_subdevices_yet(hass):
    # Right after a reset nothing is paired; the key is right all the same.
    entry, result = await _reauth(hass, {})
    assert result["reason"] == "reauth_successful"
    assert entry.data["local_key"] == "fedcba9876543210"


async def test_reauth_keeps_the_old_key_when_the_new_one_is_rejected(hass):
    entry, result = await _reauth(hass, GatewayAuthError("914"))
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    assert entry.data["local_key"] == USER_INPUT["local_key"]
