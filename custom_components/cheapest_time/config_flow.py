"""Config flow and options flow for the Cheapest Time integration.

Both flows walk through the same sequence of steps, branching depending on
the choices made by the user:

    user/init -> consumption -> [curve | power -> duration] -> [manual_options] -> finish

The shared step logic lives in :class:`CheapestTimeFlowMixin` so that the
initial config flow and the later options flow stay in sync.
"""
from __future__ import annotations

import json
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_CONSUMPTION_CURVE,
    CONF_CONSUMPTION_MODE,
    CONF_DURATION_ENTITY,
    CONF_DURATION_FIXED,
    CONF_DURATION_MODE,
    CONF_HORIZON_HOURS,
    CONF_HOURLY_TIMER_ONLY,
    CONF_NAME,
    CONF_NOMINAL_POWER,
    CONF_PRICE_ENTITY,
    CONF_PROFILE,
    CONSUMPTION_MODE_CURVE,
    CONSUMPTION_MODE_POWER,
    CONSUMPTION_MODES,
    DEFAULT_DURATION_FIXED_MINUTES,
    DEFAULT_HORIZON_HOURS,
    DEFAULT_HOURLY_TIMER_ONLY,
    DEFAULT_NOMINAL_POWER_W,
    DOMAIN,
    DURATION_MODE_ENTITY,
    DURATION_MODE_FIXED,
    DURATION_MODES,
    PROFILE_MANUAL,
    PROFILES,
)


def _validate_curve(raw: str) -> dict[str, float]:
    """Parse and validate the JSON consumption curve provided by the user.

    Expected format: {"00:00": 0.1, "00:15": 0.2, "00:30": 0.1}
    """
    data = json.loads(raw)
    if not isinstance(data, dict) or not data:
        raise ValueError("empty_curve")
    for key, value in data.items():
        hours, _, minutes = key.partition(":")
        if not minutes or not hours.isdigit() or not minutes.isdigit():
            raise ValueError("invalid_time_key")
        float(value)  # raises ValueError if not numeric
    return {k: float(v) for k, v in data.items()}


class CheapestTimeFlowMixin:
    """Shared step implementations for the config flow and the options flow."""

    _data: dict[str, Any]

    # -- step: general info (name / price entity / profile) ----------------
    def _schema_general(self, defaults: dict[str, Any]) -> vol.Schema:
        return vol.Schema(
            {
                vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, "")): str,
                vol.Required(
                    CONF_PRICE_ENTITY, default=defaults.get(CONF_PRICE_ENTITY)
                ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
                vol.Required(
                    CONF_PROFILE, default=defaults.get(CONF_PROFILE, PROFILE_MANUAL)
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=PROFILES,
                        translation_key="profile",
                        mode=selector.SelectSelectorMode.LIST,
                    )
                ),
            }
        )

    async def _async_step_general(
        self, step_id: str, user_input: dict[str, Any] | None
    ):
        errors: dict[str, str] = {}
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_consumption()

        return self.async_show_form(
            step_id=step_id,
            data_schema=self._schema_general(self._data),
            errors=errors,
        )

    # -- step: consumption mode selection -----------------------------------
    async def async_step_consumption(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            self._data.update(user_input)
            if self._data[CONF_CONSUMPTION_MODE] == CONSUMPTION_MODE_CURVE:
                return await self.async_step_curve()
            return await self.async_step_power()

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_CONSUMPTION_MODE,
                    default=self._data.get(CONF_CONSUMPTION_MODE, CONSUMPTION_MODE_CURVE),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=CONSUMPTION_MODES,
                        translation_key="consumption_mode",
                        mode=selector.SelectSelectorMode.LIST,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="consumption", data_schema=schema, errors=errors)

    # -- step: consumption curve (JSON dict) --------------------------------
    async def async_step_curve(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        current = self._data.get(CONF_CONSUMPTION_CURVE)
        default_raw = json.dumps(current) if current else (
            'Ex Washing Machine : {"00:00":0.210,"00:15":0.240,"00:30":0.015,"00:45":0.023}\n'
            'Ex Dish Washer : {"00:00":0.007,"00:15":0.167,"00:30":0.333,"00:45":0.008,'
            '"01:00":0.011,"01:15":0.013,"01:30":0.007,"01:45":0.325,"02:00":0.008}'
        )

        if user_input is not None:
            try:
                curve = _validate_curve(user_input["consumption_curve_raw"])
            except (json.JSONDecodeError, ValueError):
                errors["consumption_curve_raw"] = "invalid_curve"
            else:
                self._data[CONF_CONSUMPTION_CURVE] = curve
                return await self._async_step_after_consumption()

        schema = vol.Schema(
            {
                vol.Required("consumption_curve_raw", default=default_raw): selector.TextSelector(
                    selector.TextSelectorConfig(multiline=True)
                ),
            }
        )
        return self.async_show_form(step_id="curve", data_schema=schema, errors=errors)

    # -- step: nominal power ------------------------------------------------
    async def async_step_power(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_duration()

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_NOMINAL_POWER,
                    default=self._data.get(CONF_NOMINAL_POWER, DEFAULT_NOMINAL_POWER_W),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1, max=100000, step=1, unit_of_measurement="W",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_DURATION_MODE,
                    default=self._data.get(CONF_DURATION_MODE, DURATION_MODE_FIXED),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=DURATION_MODES,
                        translation_key="duration_mode",
                        mode=selector.SelectSelectorMode.LIST,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="power", data_schema=schema, errors=errors)

    # -- step: duration (fixed minutes or source entity) --------------------
    async def async_step_duration(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            self._data.update(user_input)
            return await self._async_step_after_consumption()

        if self._data.get(CONF_DURATION_MODE) == DURATION_MODE_ENTITY:
            schema = vol.Schema(
                {
                    vol.Required(
                        CONF_DURATION_ENTITY, default=self._data.get(CONF_DURATION_ENTITY)
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain=["sensor", "input_number", "number"])
                    ),
                }
            )
        else:
            schema = vol.Schema(
                {
                    vol.Required(
                        CONF_DURATION_FIXED,
                        default=self._data.get(
                            CONF_DURATION_FIXED, DEFAULT_DURATION_FIXED_MINUTES
                        ),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=1, max=1440, step=1, unit_of_measurement="min",
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                }
            )
        return self.async_show_form(step_id="duration", data_schema=schema, errors=errors)

    async def _async_step_after_consumption(self):
        """Branch to the manual-profile options step, or finish."""
        if self._data.get(CONF_PROFILE) == PROFILE_MANUAL:
            return await self.async_step_manual_options()
        return await self._async_finish()

    # -- step: manual profile specific options -------------------------------
    async def async_step_manual_options(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            self._data.update(user_input)
            return await self._async_finish()

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_HORIZON_HOURS,
                    default=self._data.get(CONF_HORIZON_HOURS, DEFAULT_HORIZON_HOURS),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1, max=48, step=1, unit_of_measurement="h",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_HOURLY_TIMER_ONLY,
                    default=self._data.get(
                        CONF_HOURLY_TIMER_ONLY, DEFAULT_HOURLY_TIMER_ONLY
                    ),
                ): selector.BooleanSelector(),
            }
        )
        return self.async_show_form(step_id="manual_options", data_schema=schema, errors=errors)

    async def _async_finish(self):
        """Implemented by the config flow and the options flow."""
        raise NotImplementedError


class CheapestTimeConfigFlow(config_entries.ConfigFlow, CheapestTimeFlowMixin, domain=DOMAIN):
    """Handle the initial configuration of an Cheapest Time usage."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        return await self._async_step_general("user", user_input)

    async def _async_finish(self):
        return self.async_create_entry(title=self._data[CONF_NAME], data=self._data)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        return CheapestTimeOptionsFlow(config_entry)


class CheapestTimeOptionsFlow(config_entries.OptionsFlow, CheapestTimeFlowMixin):
    """Handle updating an existing Cheapest Time usage."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        # Start from the current effective configuration (options override
        # the initial data) so the forms are pre-filled with current values.
        self._data: dict[str, Any] = {**config_entry.data, **config_entry.options}

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        return await self._async_step_general("init", user_input)

    async def _async_finish(self):
        # Name and price entity are stored in options too so that the
        # coordinator's _conf() lookup (options-first) reflects the update.
        return self.async_create_entry(title="", data=self._data)
