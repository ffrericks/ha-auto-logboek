"""Car logbook: trips (work/private), refuels and the fuel cost per trip, for any car integration."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN
from .manager import Car

PLATFORMS = [Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

TEXT = vol.Any(None, cv.string, vol.Coerce(str))
NUMBER = vol.Any(None, vol.Coerce(float), cv.string)
CAR = {vol.Optional("auto"): TEXT}

REFUEL_FIELDS = {
    vol.Optional("datum"): TEXT,
    vol.Optional("tijd"): TEXT,
    vol.Optional("pomp"): TEXT,
    vol.Optional("plaats"): TEXT,
    vol.Optional("liters"): NUMBER,
    vol.Optional("bedrag"): NUMBER,
    vol.Optional("literprijs"): NUMBER,
    vol.Optional("km"): NUMBER,
    vol.Optional("notitie"): TEXT,
}
SCHEMAS = {
    "tankbeurt_bijwerken": vol.Schema(
        {**CAR, vol.Required("id"): cv.string, **REFUEL_FIELDS}
    ),
    "tankbeurt_toevoegen": vol.Schema({**CAR, **REFUEL_FIELDS}),
    "tankbeurt_verwijderen": vol.Schema({**CAR, vol.Required("id"): cv.string}),
    "rit_bijwerken": vol.Schema(
        {
            **CAR,
            vol.Required("id"): cv.string,
            vol.Optional("soort"): TEXT,
            vol.Optional("notitie"): TEXT,
        }
    ),
    "rit_toevoegen": vol.Schema(
        {
            **CAR,
            vol.Optional("datum"): TEXT,
            vol.Optional("tijd"): TEXT,
            vol.Optional("eind_tijd"): TEXT,
            vol.Optional("km"): NUMBER,
            vol.Optional("km_start"): NUMBER,
            vol.Optional("km_eind"): NUMBER,
            vol.Optional("van"): TEXT,
            vol.Optional("naar"): TEXT,
            vol.Optional("van_provincie"): TEXT,
            vol.Optional("naar_provincie"): TEXT,
            vol.Optional("soort"): TEXT,
            vol.Optional("notitie"): TEXT,
        }
    ),
    "rit_verwijderen": vol.Schema({**CAR, vol.Required("id"): cv.string}),
}
LIST_SCHEMA = vol.Schema({**CAR, vol.Optional("maand"): TEXT})


def _cars(hass: HomeAssistant) -> list[Car]:
    return [
        e.runtime_data
        for e in hass.config_entries.async_entries(DOMAIN)
        if e.state is ConfigEntryState.LOADED
    ]


def _car(hass: HomeAssistant, which: str | None) -> Car:
    cars = _cars(hass)
    if not cars:
        raise ServiceValidationError("Er is geen auto ingesteld")
    if not which:
        if len(cars) > 1:
            raise ServiceValidationError("Kies een auto (veld auto)")
        return cars[0]
    for car in cars:
        if which in (car.entry.entry_id, car.name) or which.lower() == car.name.lower():
            return car
    raise ServiceValidationError(f"Auto '{which}' ken ik niet")


def _with_response(
    func: Callable[[ServiceCall], Awaitable[dict[str, Any]]],
) -> Callable[[ServiceCall], Awaitable[ServiceResponse]]:
    """With a response requested, report errors as {ok: false, fout} instead of raising."""

    async def handler(call: ServiceCall) -> ServiceResponse:
        if not call.return_response:
            await func(call)
            return None
        try:
            return {"ok": True, **await func(call)}
        except HomeAssistantError as err:
            return {"ok": False, "fout": str(err)}

    return handler


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register the actions once; they find the car by name or entry id."""

    def fields(call: ServiceCall) -> dict[str, Any]:
        return {k: v for k, v in call.data.items() if k not in ("auto", "id")}

    async def tankbeurt_bijwerken(call: ServiceCall) -> dict[str, Any]:
        car = _car(hass, call.data.get("auto"))
        return {"tankbeurt": car.update_refuel(call.data["id"], fields(call))}

    async def tankbeurt_toevoegen(call: ServiceCall) -> dict[str, Any]:
        car = _car(hass, call.data.get("auto"))
        return {"tankbeurt": car.add_refuel(fields(call))}

    async def tankbeurt_verwijderen(call: ServiceCall) -> dict[str, Any]:
        _car(hass, call.data.get("auto")).remove_refuel(call.data["id"])
        return {}

    async def rit_bijwerken(call: ServiceCall) -> dict[str, Any]:
        car = _car(hass, call.data.get("auto"))
        return {"rit": car.update_trip(call.data["id"], fields(call))}

    async def rit_toevoegen(call: ServiceCall) -> dict[str, Any]:
        car = _car(hass, call.data.get("auto"))
        return {"rit": car.add_trip(fields(call))}

    async def rit_verwijderen(call: ServiceCall) -> dict[str, Any]:
        _car(hass, call.data.get("auto")).remove_trip(call.data["id"])
        return {}

    async def lijst(call: ServiceCall) -> ServiceResponse:
        car = _car(hass, call.data.get("auto"))
        month = (call.data.get("maand") or "").strip() or None
        return {
            "auto": car.name,
            "maand": month,
            "ritten": car.trips_in(month),
            "tankbeurten": [
                r for r in car.refuels() if not month or r["t"].startswith(month)
            ],
        }

    handlers = {
        "tankbeurt_bijwerken": tankbeurt_bijwerken,
        "tankbeurt_toevoegen": tankbeurt_toevoegen,
        "tankbeurt_verwijderen": tankbeurt_verwijderen,
        "rit_bijwerken": rit_bijwerken,
        "rit_toevoegen": rit_toevoegen,
        "rit_verwijderen": rit_verwijderen,
    }
    for name, func in handlers.items():
        hass.services.async_register(
            DOMAIN, name, _with_response(func), SCHEMAS[name], SupportsResponse.OPTIONAL
        )
    hass.services.async_register(
        DOMAIN, "lijst", lijst, LIST_SCHEMA, supports_response=SupportsResponse.ONLY
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    car = Car(hass, entry)
    await car.async_load()
    entry.runtime_data = car
    car.start()
    entry.async_on_unload(entry.add_update_listener(_reload))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_stop()
    return unloaded
