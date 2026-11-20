# Auto logboek for Home Assistant

A custom integration that keeps a logbook of your car: every trip (work or private) and every refuel, with the **fuel cost per trip**. It works with any car integration that gives a fuel level, an odometer, an ignition state and a position (FordPass, Volkswagen, Tesla, an OBD dongle, ...). It comes with a dashboard view with totals, charts, refuels and trips.

The integration, its actions and the dashboard are in Dutch (`rit` = trip, `tankbeurt` = refuel, `werk` = work, `privé` = private). An English translation of the setup screens and action descriptions is included. [Nederlandse uitleg staat onderaan.](#nederlands)

## Features

- **Trips are logged automatically**: start when the ignition goes on, end 2 minutes after it goes off (if it goes on again within that time, it is the same trip). Each trip gets the departure and arrival place (OpenStreetMap Nominatim), times, odometer readings and distance. Trips where the odometer did not move are skipped.
- **Work or private**: a trip is *work* when it starts within the work hours you set for that weekday, starts and ends in a region you choose (state or province), and an optional *private signal* (for example Android Auto on your phone) was not on during the trip. Everything else is *private*. One tap on the dashboard switches a trip.
- **Refuels are detected automatically** from a rise of the fuel level. The integration waits 10 minutes (the level can rise in steps), looks up the nearest fuel station and can send a notification to your phone. You fill in the amount later, for example from your bank app.
- **Cost per trip from the tank price**: the tank has one average price per liter. At every refuel, what was still in the tank (at the old price) is mixed with the new liters (at the price paid). Example: 10 L left at 2.109 plus 30 L at 2.049 gives 2.064 per liter. A trip costs its liters times the tank price at departure.
- **Amounts can come later**: until an amount is filled in, a refuel uses an estimated price (a price sensor or a default) and the costs are marked as provisional (`*`). Everything is recalculated when the amount is entered. With both amount and price per liter the liters are exact, which beats the fuel gauge.
- **Consumption is learned** from the drop of the fuel gauge over the last ~300 km (after 80 km); until then a default is used. The liters of a trip are fixed when the trip is saved.
- **More than one car**: one config entry per car, each with its own entities and settings.
- **Sensors per car**: logbook (number of refuels without an amount, plus everything the dashboard needs), fuel used (gas device class in liters, total), fuel cost (EUR, total), price per liter in the tank and consumption. Fuel used and fuel cost can be added as a gas source in the energy dashboard if you want.
- Data is stored locally in `.storage/auto_logboek.<entry_id>`. The lists are kept out of the recorder database.

### Dashboard

- Totals per month and year: km and fuel cost, total, work and private; refuels with liters and amounts paid.
- **Charts**: pick week, month or year (and browse back), and km, cost or number of trips. A donut by work/private, weekday or part of the day (morning 06-12, afternoon 12-18, evening 18-24, night 00-06), and a line or stacked bar chart per day, week or month, with tooltips and a table view.
- Refuels: an open refuel shows fields for the amount and price per liter; edit or remove any refuel.
- Trips per month with a *Werk/Privé* button, notes, and forms to add a refuel or trip by hand.

## Installation

### HACS (custom repository)

1. HACS > three dots > *Custom repositories* > add `https://github.com/ffrericks/ha-auto-logboek` with type *Integration*.
2. Install *Auto logboek* and restart Home Assistant.

### Manual

Copy `custom_components/auto_logboek` to `config/custom_components/auto_logboek` and restart Home Assistant.

### Set up

*Settings > Devices & services > Add integration > Auto logboek*, then pick:

| Field | Example |
| --- | --- |
| Name of the car | `My car` (sensors become `sensor.my_car_*`) |
| Fuel level | a sensor in `%` (with the tank capacity) or in `L` |
| Tank capacity | `52` |
| Odometer | a sensor in km |
| Ignition | any entity; plus the values that mean *off*, e.g. `OFF, off` (unknown and unavailable are ignored) |
| Position | a `device_tracker` (or anything with `latitude`/`longitude` attributes) |

Then *Configure* the entry for:

- **Entities**: a spare position (e.g. your phone, for when the car reports nothing) and the *private signal* (an entity that makes a trip private when it was `on`, e.g. `binary_sensor.<phone>_android_auto`).
- **Work hours and region**: per weekday `08:00-17:00` or empty, and the region as OpenStreetMap names it (e.g. `Utrecht`, `Bavaria`); empty = anywhere.
- **Consumption and price**: default consumption, default price per liter, an optional price sensor, and the rise that counts as a refuel (default 3 L).
- **Notification**: the phone (`notify.mobile_app_*`) that gets a message after a refuel, and the dashboard path to open.

### Dashboard

1. Install [button-card](https://github.com/custom-cards/button-card) (HACS).
2. Open a dashboard > three dots > *Edit dashboard* > three dots > *Raw configuration editor* and paste the view from [`dashboard/auto-logboek-view.yaml`](dashboard/auto-logboek-view.yaml) under `views:`.
3. Replace `my_car` with the slug of your car's name, and add tiles or a map of your car integration to the first section if you like.

## Actions

All actions take an optional `auto` (the car's name or entry id); it is only needed with more than one car.

| Action | Fields | Response |
| --- | --- | --- |
| `auto_logboek.tankbeurt_bijwerken` | `id`, any of `bedrag` (amount), `literprijs`, `liters`, `pomp`, `plaats`, `datum`, `tijd`, `km`, `notitie` | `{ok, tankbeurt}` |
| `auto_logboek.tankbeurt_toevoegen` | `datum`, `tijd`, `pomp`, `plaats`, `liters` and/or `bedrag`, `literprijs`, `km`, `notitie` | `{ok, tankbeurt}` |
| `auto_logboek.tankbeurt_verwijderen` | `id` | `{ok}` |
| `auto_logboek.rit_bijwerken` | `id`, `soort` (`werk`, `prive` or `auto` = back to automatic), `notitie` | `{ok, rit}` |
| `auto_logboek.rit_toevoegen` | `datum`, `tijd`, `eind_tijd`, `km` or `km_start` + `km_eind`, `van`, `naar`, optional `van_provincie`, `naar_provincie`, `soort`, `notitie` | `{ok, rit}` |
| `auto_logboek.rit_verwijderen` | `id` | `{ok}` |
| `auto_logboek.lijst` | optional `maand` (`yyyy-mm`) | trips and refuels |

Numbers accept a comma or a point (`51,23` or `51.23`). Errors are returned as `{ok: false, fout: "..."}` when a response is requested, otherwise they are raised. Error messages are in Dutch.

## Example

```yaml
action: auto_logboek.tankbeurt_bijwerken
data:
  id: t12
  bedrag: "51,23"
  literprijs: "2,079"
```

## Notes

- Addresses and fuel stations come from [Nominatim](https://nominatim.openstreetmap.org) (OpenStreetMap). The integration sends the car's position at the start and end of each trip and at a refuel, at most one request per second, with its own user agent. Without an internet connection a trip is still saved, with the place unknown.
- The work/private split is a help, not tax advice: check the trips before you use them for a declaration.
- How well trips are detected depends on how often your car integration reports the ignition and the odometer.

# Nederlands

**Auto logboek** houdt per auto alle ritten (werk of privé) en tankbeurten bij, met de brandstofkosten per rit. Het werkt met elke auto-integratie die een brandstofniveau, kilometerstand, contact en positie geeft.

- Ritten komen er vanzelf in: begin bij contact aan, eind 2 minuten na contact uit, met vertrek- en aankomstplaats, tijden en km.
- **Werk** = de rit begint binnen de werktijd van die dag, begint en eindigt in de gekozen provincie, en het privé-signaal (bijv. Android Auto) was niet aan. De rest is privé; tik op *Werk/Privé* om een rit om te zetten.
- Tankbeurten worden herkend aan een stijging van het brandstofniveau; het bedrag vul je later in, bijvoorbeeld uit de bank-app.
- **Kosten per rit**: de tank heeft één gemiddelde literprijs. Bij elke tankbeurt wordt wat er nog in zat gemengd met het nieuwe (10 L à 2,109 + 30 L à 2,049 = 2,064 per liter). Een rit kost km x verbruik x die prijs. Zonder bedrag rekent hij met een schatting, gemarkeerd met *, en alles rekent opnieuw zodra het bedrag erin staat.
- Het verbruik leert de integratie uit de daling van de tankmeter.
- Het dashboard toont totalen per maand en jaar, grafieken (taart per werk/privé, weekdag of dagdeel; lijn of staaf per dag, week of maand), de tankbeurten met invulvelden en de ritten per maand.

Installeren en instellen: zie hierboven (*Installation*). Daarna via *Configureren* de werktijden, regio, prijs en melding instellen.
