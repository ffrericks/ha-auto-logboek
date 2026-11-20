"""Cost model: the tank content has one average price per liter (moving average).

At every refuel the liters that were still in the tank (at the old average price) are
mixed with the new liters (at the price paid): new average = value of both / liters of both.
A trip costs its liters times the average price of the tank at the start of the trip.
A refuel without a known amount gets an estimated price and counts as provisional until
the share of provisionally priced liters in the tank is negligible.

Pure functions without Home Assistant imports, so they can be tested on their own.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .const import PROVISIONAL_SHARE


def _when(value: str) -> datetime:
    return datetime.fromisoformat(value)


def own_price(
    refuel: dict[str, Any], fallback: float
) -> tuple[float | None, float, bool]:
    """(liters, price per liter, provisional) of one refuel.

    Amount and liter price from the bank or receipt beat the measured liters
    (the fuel gauge only knows whole percents).
    """
    amount = refuel.get("bedrag")
    unit_price = refuel.get("literprijs")
    liters = refuel.get("liters")
    if amount and unit_price:
        return amount / unit_price, unit_price, False
    if amount and liters:
        return liters, amount / liters, False
    if unit_price:
        return liters, unit_price, False
    estimate = refuel.get("adviesprijs") or fallback
    if amount:
        return amount / estimate, estimate, True
    return liters, estimate, True


def recalculate(
    refuels: list[dict[str, Any]], trips: list[dict[str, Any]], default_price: float
) -> dict[str, Any]:
    """Annotate refuels and trips in place with prices and costs; return the tank state."""
    ordered = sorted(refuels, key=lambda r: _when(r["t"]))
    if ordered:
        _, first_price, first_prov = own_price(ordered[0], default_price)
    else:
        first_price, first_prov = default_price, True

    events = [(_when(r["t"]), 1, r) for r in ordered]
    events += [(_when(r["start"]), 0, r) for r in trips]
    events.sort(key=lambda e: (e[0], e[1]))

    avg: float | None = None
    share = 1.0  # provisional share of the liters in the tank
    content: float | None = None
    for _, kind, rec in events:
        if kind == 0:
            price = avg if avg is not None else first_price
            prov = share > PROVISIONAL_SHARE if avg is not None else first_prov
            liters = rec.get("liters") or 0.0
            rec["prijs"] = round(price, 4)
            rec["kosten"] = round(liters * price, 2)
            rec["voorlopig"] = prov
            if content is not None:
                content = max(0.0, content - liters)
            continue

        liters, price, prov = own_price(rec, avg if avg is not None else default_price)
        liters = liters or 0.0
        rest = rec.get("voor_l")
        if rest is None:
            rest = content if content is not None else 0.0
        old_avg = avg if avg is not None else price
        old_share = share if avg is not None else (1.0 if prov else 0.0)
        total = rest + liters
        if total > 0:
            avg = (rest * old_avg + liters * price) / total
            share = (rest * old_share + liters * (1.0 if prov else 0.0)) / total
        else:
            avg, share = price, (1.0 if prov else 0.0)
        content = total
        rec["liters_eff"] = round(liters, 2)
        rec["prijs"] = round(price, 4)
        rec["prijs_voorlopig"] = prov
        rec["rest_l"] = round(rest, 1)
        rec["tankprijs"] = round(avg, 4)
        rec["tank_voorlopig"] = share > PROVISIONAL_SHARE
        rec["kosten"] = rec["bedrag"] if rec.get("bedrag") else round(liters * price, 2)

    return {
        "tankprijs": round(avg, 4) if avg is not None else None,
        "voorlopig": share > PROVISIONAL_SHARE if avg is not None else True,
        "inhoud_l": round(content, 1) if content is not None else None,
    }
