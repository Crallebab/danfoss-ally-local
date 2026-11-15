"""Config flow for Danfoss Ally Local."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_HOST
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_DEVICE_ID,
    CONF_LOCAL_KEY,
    CONF_NAMES,
    CONF_PROTOCOL_VERSION,
    DEFAULT_PROTOCOL_VERSION,
    DOMAIN,
    PROTOCOL_VERSIONS,
    default_name,
)
from .gateway import DanfossGateway, GatewayAuthError, GatewayError

_LOGGER = logging.getLogger(__name__)


def _schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "")): str,
            vol.Required(CONF_DEVICE_ID, default=defaults.get(CONF_DEVICE_ID, "")): str,
            # Never pre-filled: a failed attempt must not echo the key back.
            vol.Required(CONF_LOCAL_KEY): TextSelector(
                TextSelectorConfig(type=TextSelectorType.PASSWORD)
            ),
            vol.Required(
                CONF_PROTOCOL_VERSION,
                default=defaults.get(CONF_PROTOCOL_VERSION, DEFAULT_PROTOCOL_VERSION),
            ): SelectSelector(SelectSelectorConfig(options=PROTOCOL_VERSIONS)),
        }
    )


class DanfossAllyLocalOptionsFlow(OptionsFlowWithReload):
    """Name the sub-devices (the gateway's local protocol carries no names).

    Saving reloads the entry, which applies the names to the device registry.
    """

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show one name field per sub-device, keyed by its Zigbee node id."""
        entry = self.config_entry
        devices = entry.runtime_data.devices if hasattr(entry, "runtime_data") else {}
        names: dict[str, str] = dict(entry.options.get(CONF_NAMES, {}))
        if user_input is not None:
            for cid, dev in devices.items():
                name = (user_input.get(cid) or "").strip()
                # An empty field, or the untouched default, means "no custom name",
                # so the default can follow the device type later.
                if name and name != default_name(cid, dev.profile.name):
                    names[cid] = name
                else:
                    names.pop(cid, None)
            return self.async_create_entry(data={**entry.options, CONF_NAMES: names})

        schema = {
            vol.Optional(
                cid,
                description={
                    "suggested_value": names.get(cid) or default_name(cid, dev.profile.name)
                },
            ): str
            for cid, dev in sorted(devices.items(), key=lambda kv: (kv[1].profile.key, kv[0]))
        }
        return self.async_show_form(step_id="init", data_schema=vol.Schema(schema))


class DanfossAllyLocalConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> DanfossAllyLocalOptionsFlow:
        """Return the options flow."""
        return DanfossAllyLocalOptionsFlow()

    async def _async_test(self, data: dict[str, Any]) -> str | None:
        """Connect with ``data``; return an error key, or None if it works."""
        host = data[CONF_HOST]
        # tinytuya treats these as "discover the device by UDP broadcast".
        if not host or host.lower() == "auto" or host == "0.0.0.0":
            return "cannot_connect"
        if not data[CONF_LOCAL_KEY].isascii():
            return "invalid_auth"
        gateway = DanfossGateway(
            host, data[CONF_DEVICE_ID], data[CONF_LOCAL_KEY], data[CONF_PROTOCOL_VERSION]
        )
        try:
            found = await self.hass.async_add_executor_job(gateway.list_subdevices)
        except GatewayAuthError:
            return "invalid_auth"
        except GatewayError as err:
            _LOGGER.debug("Connection test failed: %s", err)
            return "cannot_connect"
        finally:
            await self.hass.async_add_executor_job(gateway.close)
        return None if found else "no_subdevices"

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for gateway address and local key, then test the connection."""
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input = {
                key: value.strip() if isinstance(value, str) else value
                for key, value in user_input.items()
            }
            await self.async_set_unique_id(user_input[CONF_DEVICE_ID])
            self._abort_if_unique_id_configured()
            error = await self._async_test(user_input)
            if error is None:
                return self.async_create_entry(title="Danfoss Ally Gateway", data=user_input)
            errors["base"] = error

        return self.async_show_form(
            step_id="user", data_schema=_schema(user_input or {}), errors=errors
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Start re-authentication after the gateway rejected the key."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new local key (e.g. after the gateway was re-paired)."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {**entry.data, **user_input, CONF_LOCAL_KEY: user_input[CONF_LOCAL_KEY].strip()}
            error = await self._async_test(data)
            if error is None:
                return self.async_update_reload_and_abort(entry, data=data)
            errors["base"] = error

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_LOCAL_KEY): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                    vol.Required(
                        CONF_PROTOCOL_VERSION,
                        default=entry.data.get(CONF_PROTOCOL_VERSION, DEFAULT_PROTOCOL_VERSION),
                    ): SelectSelector(SelectSelectorConfig(options=PROTOCOL_VERSIONS)),
                }
            ),
            errors=errors,
        )
