"""One car: detects trips and refuels from its entities, keeps the logbook, computes costs."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from . import geo
from .const import (
    ATTR_MONTHS,
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
    DAYS,
    DEFAULTS,
    DOMAIN,
    LEARN_KM,
    LEARN_MIN_KM,
    REFUEL_WAIT_S,
    SIGNAL_UPDATED,
    SOORT_PRIVE,
    SOORT_WERK,
    STORAGE_VERSION,
    TRIP_MAX_HOURS,
    TRIP_WAIT_S,
)
from .kosten import recalculate

_LOGGER = logging.getLogger(__name__)
IGNORED = (STATE_UNKNOWN, STATE_UNAVAILABLE, "", None)
DAY_NAMES = ("ma", "di", "wo", "do", "vr", "za", "zo")


def parse_number(value: Any, label: str) -> float | None:
    """'51,23', '€ 51.23', 2.079 -> float; empty -> None."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("€", "").replace("EUR", "").strip()
    if not text:
        return None
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        return float(text)
    except ValueError as err:
        raise ServiceValidationError(f"Ongeldig getal voor {label}: '{value}'") from err


def parse_when(date_text: Any, time_text: Any) -> datetime:
    """yyyy-mm-dd or dd/mm/yyyy plus hh:mm (local time); empty date = now."""
    date_text = str(date_text or "").strip()
    time_text = str(time_text or "").strip() or "12:00"
    if not date_text:
        return dt_util.now().replace(second=0, microsecond=0)
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            day = datetime.strptime(date_text[:10], fmt).date()
            break
        except ValueError:
            continue
    else:
        raise ServiceValidationError(f"Ongeldige datum '{date_text}'")
    match = re.fullmatch(r"(\d{1,2})[:.](\d{2})", time_text)
    if not match:
        raise ServiceValidationError(f"Ongeldige tijd '{time_text}'")
    return datetime(
        day.year,
        day.month,
        day.day,
        int(match.group(1)),
        int(match.group(2)),
        tzinfo=dt_util.get_default_time_zone(),
    )


def work_window(text: str) -> tuple[str, str] | None:
    """'08:00-17:00' -> ('08:00', '17:00'); empty or invalid -> None."""
    match = re.fullmatch(
        r"\s*(\d{1,2})[:.](\d{2})\s*-\s*(\d{1,2})[:.](\d{2})\s*", text or ""
    )
    if not match:
        return None
    h1, m1, h2, m2 = match.groups()
    return f"{int(h1):02d}:{m1}", f"{int(h2):02d}:{m2}"


class Car:
    """Logbook of one car (one config entry)."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.opts: dict[str, Any] = {**DEFAULTS, **entry.data, **entry.options}
        self.name: str = self.opts.get(CONF_NAME) or entry.title
        self.signal = SIGNAL_UPDATED.format(entry.entry_id)
        self._store: Store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self.data: dict[str, Any] = {}
        self.state: dict[str, Any] = {}
        self.version = 0
        self._unsubs: list[CALLBACK_TYPE] = []
        self._trip_timer: CALLBACK_TYPE | None = None
        self._refuel_timer: CALLBACK_TYPE | None = None

    # ---- lifecycle -------------------------------------------------------

    async def async_load(self) -> None:
        self.data = await self._store.async_load() or {}
        for key, value in (
            ("teller", 0),
            ("basis_l", None),
            ("rit", None),
            ("tank_wacht", None),
            ("tankbeurten", {}),
            ("ritten", {}),
        ):
            self.data.setdefault(key, value)
        self._recalc()

    @callback
    def start(self) -> None:
        watch = [self.opts[CONF_IGNITION], self.opts[CONF_FUEL]]
        if self.opts.get(CONF_PRIVATE):
            watch.append(self.opts[CONF_PRIVATE])
        self._unsubs.append(
            async_track_state_change_event(self.hass, watch, self._on_state)
        )
        now = dt_util.now()
        trip = self.data["rit"]
        if trip and trip.get("uit"):
            left = (
                TRIP_WAIT_S
                - (now - dt_util.parse_datetime(trip["uit"])).total_seconds()
            )
            self._trip_timer = async_call_later(
                self.hass, max(5, left), self._finish_trip_cb
            )
        wait = self.data["tank_wacht"]
        if wait:
            left = (
                REFUEL_WAIT_S
                - (now - dt_util.parse_datetime(wait["laatst"])).total_seconds()
            )
            self._refuel_timer = async_call_later(
                self.hass, max(5, left), self._finish_refuel_cb
            )
        if self.data["basis_l"] is None:
            self.data["basis_l"] = self._level_l()
            self._save()

    async def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        for timer in (self._trip_timer, self._refuel_timer):
            if timer:
                timer()
        await self._store.async_save(self.data)

    @callback
    def _save(self) -> None:
        self._store.async_delay_save(lambda: self.data, 2)

    @callback
    def _changed(self) -> None:
        self._recalc()
        self._save()
        self.version += 1
        async_dispatcher_send(self.hass, self.signal)

    # ---- reading the car's entities --------------------------------------

    def _float(self, entity_id: str) -> float | None:
        state = self.hass.states.get(entity_id) if entity_id else None
        if state is None or state.state in IGNORED:
            return None
        try:
            return float(state.state)
        except ValueError:
            return None

    def _level_l(self) -> float | None:
        """Fuel in the tank in liters (sensor in % of the tank or in L)."""
        entity_id = self.opts[CONF_FUEL]
        value = self._float(entity_id)
        if value is None:
            return None
        unit = str(
            self.hass.states.get(entity_id).attributes.get("unit_of_measurement") or "%"
        )
        if unit.lower() in ("l", "liter", "liters"):
            return value
        return value * float(self.opts[CONF_TANK]) / 100

    def _km(self) -> float | None:
        return self._float(self.opts[CONF_ODOMETER])

    def _position(self) -> tuple[float, float] | None:
        for entity_id in (self.opts[CONF_POSITION], self.opts.get(CONF_POSITION_SPARE)):
            state = self.hass.states.get(entity_id) if entity_id else None
            if state is None:
                continue
            lat, lon = (
                state.attributes.get("latitude"),
                state.attributes.get("longitude"),
            )
            if lat is not None and lon is not None:
                return float(lat), float(lon)
        return None

    def _off_states(self) -> set[str]:
        return {
            s.strip() for s in str(self.opts[CONF_IGNITION_OFF]).split(",") if s.strip()
        }

    def _private_on(self) -> bool:
        entity_id = self.opts.get(CONF_PRIVATE)
        state = self.hass.states.get(entity_id) if entity_id else None
        return bool(state and state.state == "on")

    def _private_label(self) -> str:
        entity_id = self.opts.get(CONF_PRIVATE)
        state = self.hass.states.get(entity_id) if entity_id else None
        return (
            state.attributes.get("friendly_name") if state else None
        ) or "privé-signaal"

    # ---- state changes ---------------------------------------------------

    @callback
    def _on_state(self, event: Event) -> None:
        entity_id = event.data["entity_id"]
        new = event.data.get("new_state")
        if new is None or new.state in IGNORED:
            return
        if entity_id == self.opts[CONF_IGNITION]:
            self._on_ignition(new.state)
        elif entity_id == self.opts[CONF_FUEL]:
            self._on_fuel()
        elif entity_id == self.opts.get(CONF_PRIVATE) and new.state == "on":
            trip = self.data["rit"]
            if trip and not trip.get("prive"):
                trip["prive"] = True
                self._save()

    @callback
    def _on_ignition(self, value: str) -> None:
        now = dt_util.now()
        trip = self.data["rit"]
        if value in self._off_states():
            if trip and not trip.get("uit"):
                trip["uit"] = now.isoformat()
                trip["eind_tijd"] = now.strftime("%H:%M")
                self._save()
                self._trip_timer = async_call_later(
                    self.hass, TRIP_WAIT_S, self._finish_trip_cb
                )
            return
        if trip and trip.get("uit"):
            # back on within the waiting time: same trip (short stop)
            if self._trip_timer:
                self._trip_timer()
                self._trip_timer = None
            trip.pop("uit", None)
            trip.pop("eind_tijd", None)
            self._save()
            return
        if trip and now - dt_util.parse_datetime(trip["start"]) < timedelta(
            hours=TRIP_MAX_HOURS
        ):
            return
        pos = self._position()
        self.data["rit"] = {
            "start": now.isoformat(),
            "km": self._km(),
            "lat": pos[0] if pos else None,
            "lon": pos[1] if pos else None,
            "niveau_l": self._level_l(),
            "prive": self._private_on(),
        }
        self._save()

    @callback
    def _finish_trip_cb(self, _now: datetime) -> None:
        self._trip_timer = None
        self.hass.async_create_task(self._finish_trip())

    async def _finish_trip(self) -> None:
        trip = self.data["rit"]
        self.data["rit"] = None
        self._save()
        if not trip:
            return
        km_end = self._km()
        km_start = trip.get("km")
        if km_start is None or km_end is None or km_end <= km_start:
            _LOGGER.info(
                "%s: rit van %s overgeslagen (km %s -> %s)",
                self.name,
                trip["start"],
                km_start,
                km_end,
            )
            return
        pos = self._position()
        start = await self._address(trip.get("lat"), trip.get("lon"))
        end = await self._address(*(pos or (None, None)))
        level_end = self._level_l()
        drop = None
        if trip.get("niveau_l") is not None and level_end is not None:
            drop = round(trip["niveau_l"] - level_end, 2)
        begin = dt_util.parse_datetime(trip["start"])
        end_time = trip.get("eind_tijd") or dt_util.now().strftime("%H:%M")
        self._add_trip(
            begin,
            end_time,
            km_start,
            km_end,
            start,
            end,
            private=bool(trip.get("prive")),
            drop=drop,
        )

    async def _address(self, lat: float | None, lon: float | None) -> dict[str, str]:
        if lat is None or lon is None:
            return {"straat": "", "plaats": "", "provincie": ""}
        return await geo.reverse(self.hass, lat, lon)

    @callback
    def _on_fuel(self) -> None:
        level = self._level_l()
        if level is None:
            return
        wait = self.data["tank_wacht"]
        if wait:
            if level > wait["na_l"]:
                wait["na_l"] = level
                wait["laatst"] = dt_util.now().isoformat()
                if self._refuel_timer:
                    self._refuel_timer()
                self._refuel_timer = async_call_later(
                    self.hass, REFUEL_WAIT_S, self._finish_refuel_cb
                )
                self._save()
            return
        base = self.data["basis_l"]
        if base is None or level < base:
            self.data["basis_l"] = level
            self._save()
            return
        if level - base >= float(self.opts[CONF_THRESHOLD]):
            pos = self._position()
            now = dt_util.now().isoformat()
            self.data["tank_wacht"] = {
                "t": now,
                "laatst": now,
                "voor_l": base,
                "na_l": level,
                "km": self._km(),
                "lat": pos[0] if pos else None,
                "lon": pos[1] if pos else None,
                "adviesprijs": self._float(self.opts.get(CONF_PRICE_ENTITY) or ""),
            }
            self._refuel_timer = async_call_later(
                self.hass, REFUEL_WAIT_S, self._finish_refuel_cb
            )
            self._save()

    @callback
    def _finish_refuel_cb(self, _now: datetime) -> None:
        self._refuel_timer = None
        self.hass.async_create_task(self._finish_refuel())

    async def _finish_refuel(self) -> None:
        wait = self.data["tank_wacht"]
        if not wait:
            return
        level = self._level_l()
        after = max(wait["na_l"], level or 0)
        self.data["tank_wacht"] = None
        self.data["basis_l"] = after
        liters = round(after - wait["voor_l"], 1)
        station = None
        address = {"straat": "", "plaats": "", "provincie": ""}
        if wait.get("lat") is not None:
            station = await geo.fuel_station(self.hass, wait["lat"], wait["lon"])
            if station is None:
                address = await geo.reverse(self.hass, wait["lat"], wait["lon"])
        rec = self._new_refuel(
            dt_util.parse_datetime(wait["t"]),
            pomp=(station or {}).get("naam") or "Onbekende pomp",
            straat=(station or address).get("straat", ""),
            plaats=(station or address).get("plaats", ""),
            liters=liters,
            km=wait.get("km"),
            voor_l=round(wait["voor_l"], 1),
            na_l=round(after, 1),
            adviesprijs=wait.get("adviesprijs"),
            bron="auto",
        )
        self._changed()
        await self._notify_refuel(rec)

    async def _notify_refuel(self, rec: dict[str, Any]) -> None:
        service = self.opts.get(CONF_NOTIFY)
        if not service or not self.hass.services.has_service("notify", service):
            return
        where = " ".join(x for x in (rec["pomp"], rec["plaats"]) if x)
        await self.hass.services.async_call(
            "notify",
            service,
            {
                "title": f"Getankt: {self.name}",
                "message": f"{where}, ca. {fmt(rec['liters'], 1)} L. Vul het bedrag in als je het weet.",
                "data": {
                    "clickAction": self.opts.get(CONF_DASHBOARD) or "/lovelace/auto",
                    "tag": f"{DOMAIN}_{self.entry.entry_id}_tank",
                },
            },
            blocking=False,
        )

    # ---- records ---------------------------------------------------------

    def _next_id(self, prefix: str) -> str:
        self.data["teller"] += 1
        return f"{prefix}{self.data['teller']}"

    def _new_refuel(self, when: datetime, **fields: Any) -> dict[str, Any]:
        rec = {
            "id": self._next_id("t"),
            "t": when.isoformat(),
            "pomp": "",
            "straat": "",
            "plaats": "",
            "liters": None,
            "km": None,
            "voor_l": None,
            "na_l": None,
            "bedrag": None,
            "literprijs": None,
            "adviesprijs": None,
            "notitie": "",
            "bron": "handmatig",
        }
        rec.update(fields)
        self.data["tankbeurten"][rec["id"]] = rec
        return rec

    def classify(
        self, begin: datetime, start: dict[str, str], end: dict[str, str], private: bool
    ) -> tuple[str, str]:
        """(werk|prive, reason) from the work hours, the region and the private signal."""
        window = work_window(self.opts.get(f"werk_{DAYS[begin.weekday()]}", ""))
        hhmm = begin.strftime("%H:%M")
        if not window or not window[0] <= hhmm < window[1]:
            return SOORT_PRIVE, "buiten werktijd"
        region = str(self.opts.get(CONF_REGION) or "").strip().lower()
        if region:
            provinces = [start.get("provincie", ""), end.get("provincie", "")]
            if not all(provinces):
                return SOORT_PRIVE, "provincie onbekend"
            if any(p.lower() != region for p in provinces):
                return SOORT_PRIVE, "buiten " + self.opts[CONF_REGION]
        if private:
            return SOORT_PRIVE, self._private_label() + " aan"
        return SOORT_WERK, "werktijd" + (
            f", {self.opts[CONF_REGION]}" if region else ""
        )

    def _add_trip(
        self,
        begin: datetime,
        end_time: str,
        km_start: float | None,
        km_end: float | None,
        start: dict[str, str],
        end: dict[str, str],
        *,
        private: bool = False,
        drop: float | None = None,
        distance: float | None = None,
        soort: str | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        if distance is None:
            distance = (km_end or 0) - (km_start or 0)
        auto_soort, reason = self.classify(begin, start, end, private)
        consumption = self.state.get("verbruik") or float(self.opts[CONF_CONSUMPTION])
        rec = {
            "id": self._next_id("r"),
            "start": begin.isoformat(),
            "eind": end_time,
            "km_start": km_start,
            "km_eind": km_end,
            "km": round(distance, 1),
            "van": start.get("plaats") or start.get("straat") or "onbekend",
            "naar": end.get("plaats") or end.get("straat") or "onbekend",
            "van_straat": start.get("straat", ""),
            "naar_straat": end.get("straat", ""),
            "van_provincie": start.get("provincie", ""),
            "naar_provincie": end.get("provincie", ""),
            "prive_signaal": private,
            "daling_l": drop,
            "liters": round(distance * consumption / 100, 2),
            "verbruik": round(consumption, 2),
            "soort_auto": auto_soort,
            "reden": reason,
            "soort": soort,
            "notitie": note,
        }
        self.data["ritten"][rec["id"]] = rec
        self._changed()
        return rec

    # ---- costs -----------------------------------------------------------

    def learned_consumption(self) -> float | None:
        """L/100 km from the fuel gauge drop of the most recent trips."""
        trips = sorted(
            (
                t
                for t in self.data["ritten"].values()
                if t.get("daling_l") is not None and t["daling_l"] >= 0
            ),
            key=lambda t: t["start"],
            reverse=True,
        )
        km = liters = 0.0
        for trip in trips:
            km += trip["km"]
            liters += trip["daling_l"]
            if km >= LEARN_KM:
                break
        if km < LEARN_MIN_KM or liters <= 0:
            return None
        value = liters / km * 100
        return round(value, 2) if 2 <= value <= 30 else None

    def _recalc(self) -> None:
        refuels = list(self.data["tankbeurten"].values())
        trips = list(self.data["ritten"].values())
        tank = recalculate(refuels, trips, float(self.opts[CONF_PRICE]))
        learned = self.learned_consumption()
        self.state = {
            **tank,
            "verbruik": learned or float(self.opts[CONF_CONSUMPTION]),
            "verbruik_geleerd": learned is not None,
            "liters_totaal": round(sum(t.get("liters") or 0 for t in trips), 2),
            "kosten_totaal": round(sum(t.get("kosten") or 0 for t in trips), 2),
            "open_bedragen": sum(1 for r in refuels if not r.get("bedrag")),
        }

    # ---- actions (called by the services) --------------------------------

    def _refuel(self, refuel_id: str) -> dict[str, Any]:
        rec = self.data["tankbeurten"].get(refuel_id)
        if rec is None:
            raise ServiceValidationError(f"Tankbeurt {refuel_id} bestaat niet")
        return rec

    def _trip(self, trip_id: str) -> dict[str, Any]:
        rec = self.data["ritten"].get(trip_id)
        if rec is None:
            raise ServiceValidationError(f"Rit {trip_id} bestaat niet")
        return rec

    def update_refuel(self, refuel_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        rec = self._refuel(refuel_id)
        for key in ("bedrag", "literprijs", "liters", "km"):
            if key in fields:
                value = parse_number(fields[key], key)
                if value is not None and value <= 0:
                    raise ServiceValidationError(f"{key} moet groter dan 0 zijn")
                rec[key] = value
        for key in ("pomp", "plaats", "straat", "notitie"):
            if key in fields:
                rec[key] = str(fields[key] or "").strip()
        if "datum" in fields or "tijd" in fields:
            old = dt_util.parse_datetime(rec["t"])
            rec["t"] = parse_when(
                fields.get("datum") or old.date().isoformat(),
                fields.get("tijd") or old.strftime("%H:%M"),
            ).isoformat()
        self._changed()
        return self.public_refuel(rec)

    def add_refuel(self, fields: dict[str, Any]) -> dict[str, Any]:
        when = parse_when(fields.get("datum"), fields.get("tijd"))
        values = {
            k: parse_number(fields.get(k), k)
            for k in ("liters", "bedrag", "literprijs", "km")
        }
        if not (values["liters"] or values["bedrag"]):
            raise ServiceValidationError("Vul minstens de liters of het bedrag in")
        rec = self._new_refuel(
            when,
            pomp=str(fields.get("pomp") or "").strip() or "Tankbeurt",
            plaats=str(fields.get("plaats") or "").strip(),
            notitie=str(fields.get("notitie") or "").strip(),
            **values,
        )
        self._changed()
        return self.public_refuel(rec)

    def remove_refuel(self, refuel_id: str) -> None:
        self._refuel(refuel_id)
        del self.data["tankbeurten"][refuel_id]
        self._changed()

    def update_trip(self, trip_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        rec = self._trip(trip_id)
        if "soort" in fields:
            soort = str(fields["soort"] or "").strip().lower().replace("é", "e")
            if soort in ("", "auto", "automatisch"):
                rec["soort"] = None
            elif soort in (SOORT_WERK, SOORT_PRIVE):
                rec["soort"] = None if soort == rec["soort_auto"] else soort
            else:
                raise ServiceValidationError("Soort moet werk, prive of auto zijn")
        if "notitie" in fields:
            rec["notitie"] = str(fields["notitie"] or "").strip()
        self._changed()
        return self.public_trip(rec)

    def add_trip(self, fields: dict[str, Any]) -> dict[str, Any]:
        begin = parse_when(fields.get("datum"), fields.get("tijd"))
        km_start = parse_number(fields.get("km_start"), "km_start")
        km_end = parse_number(fields.get("km_eind"), "km_eind")
        distance = parse_number(fields.get("km"), "km")
        if distance is None:
            if km_start is None or km_end is None or km_end <= km_start:
                raise ServiceValidationError("Vul de afstand of de km-standen in")
            distance = km_end - km_start
        if distance <= 0:
            raise ServiceValidationError("De afstand moet groter dan 0 zijn")
        soort = str(fields.get("soort") or "").strip().lower().replace("é", "e") or None
        if soort not in (None, SOORT_WERK, SOORT_PRIVE):
            raise ServiceValidationError("Soort moet werk of prive zijn")
        start = {
            "plaats": str(fields.get("van") or "").strip(),
            "provincie": str(fields.get("van_provincie") or "").strip(),
        }
        end = {
            "plaats": str(fields.get("naar") or "").strip(),
            "provincie": str(fields.get("naar_provincie") or "").strip(),
        }
        rec = self._add_trip(
            begin,
            str(fields.get("eind_tijd") or "").strip(),
            km_start,
            km_end,
            start,
            end,
            distance=distance,
            soort=soort,
            note=str(fields.get("notitie") or "").strip(),
        )
        if soort and soort == rec["soort_auto"]:
            rec["soort"] = None
        if soort:
            rec["reden"] = "handmatig toegevoegd"
        self._changed()
        return self.public_trip(rec)

    def remove_trip(self, trip_id: str) -> None:
        self._trip(trip_id)
        del self.data["ritten"][trip_id]
        self._changed()

    # ---- output for sensors and the dashboard ----------------------------

    @staticmethod
    def public_refuel(rec: dict[str, Any]) -> dict[str, Any]:
        return dict(rec)

    @staticmethod
    def public_trip(rec: dict[str, Any]) -> dict[str, Any]:
        out = dict(rec)
        out["soort_def"] = rec.get("soort") or rec["soort_auto"]
        out["handmatig"] = bool(rec.get("soort"))
        return out

    def months(self) -> dict[str, dict[str, Any]]:
        """Totals per month (yyyy-mm) and per year (yyyy)."""
        out: dict[str, dict[str, Any]] = {}

        def bucket(key: str) -> dict[str, Any]:
            return out.setdefault(
                key,
                {
                    "ritten": 0,
                    "km": 0.0,
                    "km_werk": 0.0,
                    "km_prive": 0.0,
                    "kosten": 0.0,
                    "kosten_werk": 0.0,
                    "kosten_prive": 0.0,
                    "liters": 0.0,
                    "voorlopig": False,
                    "tank_aantal": 0,
                    "tank_liters": 0.0,
                    "tank_bedrag": 0.0,
                    "tank_open": 0,
                },
            )

        for trip in self.data["ritten"].values():
            soort = trip.get("soort") or trip["soort_auto"]
            for key in (trip["start"][:7], trip["start"][:4]):
                b = bucket(key)
                b["ritten"] += 1
                b["km"] += trip["km"]
                b["km_" + soort] += trip["km"]
                b["kosten"] += trip.get("kosten") or 0
                b["kosten_" + soort] += trip.get("kosten") or 0
                b["liters"] += trip.get("liters") or 0
                b["voorlopig"] = b["voorlopig"] or bool(trip.get("voorlopig"))
        for rec in self.data["tankbeurten"].values():
            for key in (rec["t"][:7], rec["t"][:4]):
                b = bucket(key)
                b["tank_aantal"] += 1
                b["tank_liters"] += rec.get("liters_eff") or rec.get("liters") or 0
                if rec.get("bedrag"):
                    b["tank_bedrag"] += rec["bedrag"]
                else:
                    b["tank_open"] += 1
        for b in out.values():
            for key, value in b.items():
                if isinstance(value, float):
                    b[key] = round(value, 2)
        return out

    def trips_in(self, month: str | None) -> list[dict[str, Any]]:
        trips = [
            self.public_trip(t)
            for t in self.data["ritten"].values()
            if not month or t["start"].startswith(month)
        ]
        return sorted(trips, key=lambda t: t["start"], reverse=True)

    def refuels(self) -> list[dict[str, Any]]:
        return sorted(
            (self.public_refuel(r) for r in self.data["tankbeurten"].values()),
            key=lambda r: r["t"],
            reverse=True,
        )

    def recent_months(self) -> list[str]:
        today = dt_util.now().date().replace(day=1)
        months = []
        for _ in range(ATTR_MONTHS):
            months.append(today.strftime("%Y-%m"))
            today = (today - timedelta(days=1)).replace(day=1)
        return months

    def snapshot(self) -> dict[str, Any]:
        months = self.recent_months()
        trip = self.data["rit"]
        return {
            "auto": self.name,
            "entry_id": self.entry.entry_id,
            "versie": self.version,
            **self.state,
            "rijdt": bool(trip and not trip.get("uit")),
            "maanden": self.months(),
            "geladen_maanden": months,
            "ritten": [t for t in self.trips_in(None) if t["start"][:7] in months],
            "tankbeurten": self.refuels()[:60],
        }


def fmt(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "?"
    return f"{value:.{digits}f}".replace(".", ",")
