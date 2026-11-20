"""Config flow: one entry per car, with the entities of any car integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector as sel

from .const import (
    CONF_CONSUMPTION,
    CONF_DASHBOARD,
    CONF_FUEL,
    CONF_IGNITION,
    CONF_IGNITION_OFF,
    CONF_NAME,
    CONF_NOTIFY,
    CONF_ODOMETER,
    CONF_POSITION,
    CONF_POSITION_SPARE,
    CONF_PRICE,
    CONF_PRICE_ENTITY,
    CONF_PRIVATE,
    CONF_REGION,
    CONF_TANK,
    CONF_THRESHOLD,
    DEFAULTS,
    DOMAIN,
    WORK_KEYS,
)
from .manager import work_window

TEXT = sel.TextSelector()
SENSOR = sel.EntitySelector(sel.EntitySelectorConfig(domain="sensor"))
ANY = sel.EntitySelector(sel.EntitySelectorConfig())
POSITION = sel.EntitySelector(
    sel.EntitySelectorConfig(domain=["device_tracker", "person", "sensor"])
)
OPTIONAL_ENTITIES = (CONF_POSITION_SPARE, CONF_PRIVATE, CONF_PRICE_ENTITY)


def _number(lo: float, hi: float, step: float, unit: str) -> sel.NumberSelector:
    return sel.NumberSelector(
        sel.NumberSelectorConfig(
            min=lo,
            max=hi,
            step=step,
            mode=sel.NumberSelectorMode.BOX,
            unit_of_measurement=unit,
        )
    )


def _car_schema(full: bool) -> vol.Schema:
    fields: dict[Any, Any] = {}
    if not full:
        fields[vol.Required(CONF_NAME)] = TEXT
    fields.update(
        {
            vol.Required(CONF_FUEL): SENSOR,
            vol.Required(CONF_TANK): _number(5, 200, 1, "L"),
            vol.Required(CONF_ODOMETER): SENSOR,
            vol.Required(CONF_IGNITION): ANY,
            vol.Required(CONF_IGNITION_OFF): TEXT,
            vol.Required(CONF_POSITION): POSITION,
        }
    )
    if full:
        fields[vol.Optional(CONF_POSITION_SPARE)] = POSITION
        fields[vol.Optional(CONF_PRIVATE)] = ANY
    return vol.Schema(fields)


def _check_car(hass, user_input: dict[str, Any]) -> dict[str, str]:
    errors = {}
    for key in (CONF_FUEL, CONF_ODOMETER, CONF_IGNITION, CONF_POSITION):
        if hass.states.get(user_input.get(key, "")) is None:
            errors[key] = "entity_missing"
    if not any(s.strip() for s in user_input.get(CONF_IGNITION_OFF, "").split(",")):
        errors[CONF_IGNITION_OFF] = "no_off_state"
    return errors


class AutoLogboekConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _check_car(self.hass, user_input)
            name = user_input[CONF_NAME].strip()
            if not name:
                errors[CONF_NAME] = "name_missing"
            if not errors:
                await self.async_set_unique_id(name.lower())
                self._abort_if_unique_id_configured()
                options = {**DEFAULTS, **user_input, CONF_NAME: name}
                return self.async_create_entry(
                    title=name, data={CONF_NAME: name}, options=options
                )
        schema = self.add_suggested_values_to_schema(
            _car_schema(False), user_input or DEFAULTS
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return AutoLogboekOptionsFlow()


class AutoLogboekOptionsFlow(OptionsFlow):
    """Menu with one form per subject; every form saves right away."""

    @property
    def _opts(self) -> dict[str, Any]:
        return {**DEFAULTS, **self.config_entry.data, **self.config_entry.options}

    def _save(self, changes: dict[str, Any]):
        return self.async_create_entry(data={**self._opts, **changes})

    async def async_step_init(self, user_input=None):
        return self.async_show_menu(
            step_id="init", menu_options=["auto", "werk", "kosten", "meldingen"]
        )

    async def async_step_auto(self, user_input=None):
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _check_car(self.hass, user_input)
            if not errors:
                return self._save(
                    {
                        **{k: user_input.get(k, "") for k in OPTIONAL_ENTITIES[:2]},
                        **user_input,
                    }
                )
        schema = self.add_suggested_values_to_schema(
            _car_schema(True), user_input or self._clean(self._opts)
        )
        return self.async_show_form(step_id="auto", data_schema=schema, errors=errors)

    async def async_step_werk(self, user_input=None):
        errors: dict[str, str] = {}
        if user_input is not None:
            values = {k: (user_input.get(k) or "").strip() for k in WORK_KEYS}
            for key, value in values.items():
                if value and not work_window(value):
                    errors[key] = "bad_window"
            if not errors:
                values[CONF_REGION] = (user_input.get(CONF_REGION) or "").strip()
                return self._save(values)
        schema = vol.Schema(
            {
                **{vol.Optional(k): TEXT for k in WORK_KEYS},
                vol.Optional(CONF_REGION): TEXT,
            }
        )
        return self.async_show_form(
            step_id="werk",
            data_schema=self.add_suggested_values_to_schema(
                schema, user_input or self._opts
            ),
            errors=errors,
        )

    async def async_step_kosten(self, user_input=None):
        if user_input is not None:
            return self._save(
                {
                    CONF_CONSUMPTION: float(user_input[CONF_CONSUMPTION]),
                    CONF_PRICE: float(user_input[CONF_PRICE]),
                    CONF_THRESHOLD: float(user_input[CONF_THRESHOLD]),
                    CONF_PRICE_ENTITY: user_input.get(CONF_PRICE_ENTITY, ""),
                }
            )
        schema = vol.Schema(
            {
                vol.Required(CONF_CONSUMPTION): _number(1, 30, 0.1, "L/100 km"),
                vol.Required(CONF_PRICE): _number(0.5, 5, 0.001, "EUR/L"),
                vol.Optional(CONF_PRICE_ENTITY): SENSOR,
                vol.Required(CONF_THRESHOLD): _number(1, 50, 0.5, "L"),
            }
        )
        return self.async_show_form(
            step_id="kosten",
            data_schema=self.add_suggested_values_to_schema(
                schema, self._clean(self._opts)
            ),
        )

    async def async_step_meldingen(self, user_input=None):
        if user_input is not None:
            return self._save(
                {
                    CONF_NOTIFY: user_input.get(CONF_NOTIFY, ""),
                    CONF_DASHBOARD: (user_input.get(CONF_DASHBOARD) or "").strip(),
                }
            )
        phones = sorted(
            s
            for s in self.hass.services.async_services().get("notify", {})
            if s.startswith("mobile_app_")
        )
        choices = [sel.SelectOptionDict(value="", label="(geen melding)")] + [
            sel.SelectOptionDict(
                value=p, label=p.removeprefix("mobile_app_").replace("_", " ")
            )
            for p in phones
        ]
        opts = self._opts
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_NOTIFY,
                    default=opts[CONF_NOTIFY] if opts[CONF_NOTIFY] in phones else "",
                ): sel.SelectSelector(
                    sel.SelectSelectorConfig(
                        options=choices, mode=sel.SelectSelectorMode.DROPDOWN
                    )
                ),
                vol.Optional(CONF_DASHBOARD, default=opts[CONF_DASHBOARD]): TEXT,
            }
        )
        return self.async_show_form(step_id="meldingen", data_schema=schema)

    @staticmethod
    def _clean(opts: dict[str, Any]) -> dict[str, Any]:
        """Entity selectors do not accept an empty string as a suggestion."""
        return {k: v for k, v in opts.items() if not (k in OPTIONAL_ENTITIES and not v)}
