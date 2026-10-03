"""Config flow for the EVSE Master UDP integration"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .evse_client import get_evse_client

_LOGGER = logging.getLogger(__name__)

DOMAIN = "evsemasterudp"

DISCOVERY_TIMEOUT = 10  # seconds
MANUAL_OPTION = "__manual__"

# Shown in clear: factory default is usually 123456; protocol also sends it in cleartext.
_PASSWORD_SELECTOR = TextSelector(
    TextSelectorConfig(type=TextSelectorType.TEXT)
)


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configuration flow manager for EVSE EmProto"""

    VERSION = 1

    def __init__(self) -> None:
        self._discovered: dict[str, dict[str, Any]] = {}
        self._selected_serial: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """First screen: show discovery docs, then start UDP search.

        Requires the HA host to receive UDP broadcasts on port 28376
        (same LAN; Docker: host networking or LAN IP / macvlan).
        """
        if user_input is None:
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema(
                    {
                        vol.Required("start_discovery", default=True): bool,
                    }
                ),
            )
        return await self.async_step_discovery()

    async def async_step_discovery(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Discover EVSEs on the local network via UDP broadcasts."""
        self._discovered = await _async_discover_evses(self.hass, DISCOVERY_TIMEOUT)

        # Filter out already configured serials
        configured = {
            entry.unique_id or entry.data.get("serial")
            for entry in self._async_current_entries()
        }
        self._discovered = {
            serial: info
            for serial, info in self._discovered.items()
            if serial not in configured
        }

        if self._discovered:
            return await self.async_step_select()
        return await self.async_step_manual()

    async def async_step_select(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Let the user pick a discovered EVSE and enter the password."""
        errors: dict[str, str] = {}

        options: list[SelectOptionDict] = [
            SelectOptionDict(
                value=serial,
                label=_format_evse_label(serial, info),
            )
            for serial, info in sorted(self._discovered.items())
        ]
        options.append(
            SelectOptionDict(
                value=MANUAL_OPTION,
                label="Enter serial manually",
            )
        )

        if user_input is not None:
            selected = user_input["serial"]
            if selected == MANUAL_OPTION:
                return await self.async_step_manual()

            self._selected_serial = selected
            data = {
                "serial": selected,
                "password": user_input["password"],
                "port": user_input.get("port", 28376),
                "name": user_input.get("name") or "EVSEMaster",
            }
            return await self._async_validate_and_create(data, errors)

        schema = vol.Schema(
            {
                vol.Required("serial"): SelectSelector(
                    SelectSelectorConfig(options=options, mode="dropdown")
                ),
                vol.Required("password", default="123456"): _PASSWORD_SELECTOR,
                vol.Optional("name", default="EVSEMaster"): str,
                vol.Optional("port", default=28376): int,
            }
        )
        return self.async_show_form(
            step_id="select",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "count": str(len(self._discovered)),
            },
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manual entry when discovery finds nothing (or user opts in)."""
        errors: dict[str, str] = {}

        if user_input is not None:
            if user_input.get("rediscover"):
                return await self.async_step_discovery()

            data = {
                "serial": user_input["serial"].strip(),
                "password": user_input["password"],
                "port": user_input.get("port", 28376),
                "name": user_input.get("name") or "EVSEMaster",
            }
            return await self._async_validate_and_create(data, errors)

        schema = vol.Schema(
            {
                vol.Required("serial"): str,
                vol.Required("password", default="123456"): _PASSWORD_SELECTOR,
                vol.Optional("name", default="EVSEMaster"): str,
                vol.Optional("port", default=28376): int,
                vol.Optional("rediscover", default=False): bool,
            }
        )
        return self.async_show_form(
            step_id="manual",
            data_schema=schema,
            errors=errors,
        )

    async def _async_validate_and_create(
        self, data: dict[str, Any], errors: dict[str, str]
    ) -> FlowResult:
        """Validate credentials and create the config entry."""
        serial = data["serial"]

        await self.async_set_unique_id(serial)
        self._abort_if_unique_id_configured()

        try:
            info = await validate_input(self.hass, data)
        except CannotConnect:
            errors["base"] = "cannot_connect"
        except InvalidAuth:
            errors["base"] = "invalid_auth"
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Unexpected error during EVSE validation")
            errors["base"] = "unknown"
        else:
            return self.async_create_entry(title=info["title"], data=data)

        if self._discovered and serial in self._discovered:
            return self._show_select_with_errors(errors, data)
        return self._show_manual_with_errors(errors, data)

    def _show_select_with_errors(
        self, errors: dict[str, str], data: dict[str, Any]
    ) -> FlowResult:
        options: list[SelectOptionDict] = [
            SelectOptionDict(
                value=serial,
                label=_format_evse_label(serial, info),
            )
            for serial, info in sorted(self._discovered.items())
        ]
        options.append(
            SelectOptionDict(
                value=MANUAL_OPTION,
                label="Enter serial manually",
            )
        )
        schema = vol.Schema(
            {
                vol.Required("serial", default=data.get("serial")): SelectSelector(
                    SelectSelectorConfig(options=options, mode="dropdown")
                ),
                vol.Required(
                    "password", default=data.get("password", "123456")
                ): _PASSWORD_SELECTOR,
                vol.Optional("name", default=data.get("name", "EVSEMaster")): str,
                vol.Optional("port", default=data.get("port", 28376)): int,
            }
        )
        return self.async_show_form(
            step_id="select",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "count": str(len(self._discovered)),
            },
        )

    def _show_manual_with_errors(
        self, errors: dict[str, str], data: dict[str, Any]
    ) -> FlowResult:
        schema = vol.Schema(
            {
                vol.Required("serial", default=data.get("serial", "")): str,
                vol.Required(
                    "password", default=data.get("password", "123456")
                ): _PASSWORD_SELECTOR,
                vol.Optional("name", default=data.get("name", "EVSEMaster")): str,
                vol.Optional("port", default=data.get("port", 28376)): int,
                vol.Optional("rediscover", default=False): bool,
            }
        )
        return self.async_show_form(
            step_id="manual",
            data_schema=schema,
            errors=errors,
        )


def _format_evse_label(serial: str, info: dict[str, Any]) -> str:
    brand = info.get("brand") or "EVSE"
    model = info.get("model") or ""
    ip = info.get("ip") or "?"
    parts = [p for p in (brand, model) if p]
    name = " ".join(parts) if parts else "EVSE"
    return f"{name} — {serial} @ {ip}"


async def _async_discover_evses(
    hass: HomeAssistant, timeout: float
) -> dict[str, dict[str, Any]]:
    """Listen for UDP broadcasts and return discovered EVSEs.

    Always listens for the full timeout so siblings are not missed when the
    shared client cache already contains a previously seen charger.
    Leaves the UDP client running so the selected EVSE stays known
    when the user submits the password on the next step.
    """
    client = get_evse_client()

    if not client.running:
        try:
            await client.start()
        except Exception as err:
            _LOGGER.error("Unable to start EVSE client for discovery: %s", err)
            return {}

    await asyncio.sleep(timeout)

    discovered: dict[str, dict[str, Any]] = {}
    for serial, data in client.get_all_evses().items():
        discovered[serial] = {
            "serial": serial,
            "ip": data.get("ip"),
            "port": data.get("port", 28376),
            "brand": data.get("brand"),
            "model": data.get("model"),
            "name": data.get("name"),
        }
    _LOGGER.info("Discovery found %d EVSE(s)", len(discovered))
    return discovered


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate user input data"""

    serial = data["serial"]
    password = data["password"]
    port = data.get("port", 28376)

    client = get_evse_client(port)

    if not client.running:
        try:
            await client.start()
        except Exception as err:
            _LOGGER.error("Unable to start EVSE client: %s", err)
            raise CannotConnect from err

    evse = client.get_evse(serial)
    if not evse:
        # Manual entry path: wait for a broadcast from this serial
        await asyncio.sleep(5)
        evse = client.get_evse(serial)
        if not evse:
            _LOGGER.warning("EVSE %s not found, retrying...", serial)
            await asyncio.sleep(2)
            evse = client.get_evse(serial)

    if not evse:
        _LOGGER.error("EVSE %s not found after waiting for broadcasts", serial)
        raise CannotConnect

    _LOGGER.info("EVSE %s found, attempting connection...", serial)

    success = await client.login(serial, password)
    if not success:
        _LOGGER.warning("First auth attempt failed for %s, retrying...", serial)
        await asyncio.sleep(2)
        success = await client.login(serial, password)

    if not success:
        raise InvalidAuth

    _LOGGER.info("Successfully connected to EVSE %s", serial)

    friendly = data.get("name") or f"EVSE {serial}"
    return {
        "title": friendly,
        "serial": serial,
    }

class CannotConnect(HomeAssistantError):
    """Error indicating that connection could not be established"""


class InvalidAuth(HomeAssistantError):
    """Error indicating invalid authentication"""
