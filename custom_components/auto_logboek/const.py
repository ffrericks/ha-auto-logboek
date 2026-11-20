"""Constants for the car logbook integration (trips, refuels, fuel cost per trip)."""

DOMAIN = "auto_logboek"
STORAGE_VERSION = 1
SIGNAL_UPDATED = "auto_logboek_updated_{}"

# Wait after the ignition goes off before closing a trip (odometer arrives late)
TRIP_WAIT_S = 120
# Wait after the fuel level rose before booking a refuel (level can rise in steps)
REFUEL_WAIT_S = 600
# A trip start older than this is stale (missed ignition-off) and is replaced
TRIP_MAX_HOURS = 18
# Trips in the sensor attributes: this month and the previous one; older via action `lijst`
ATTR_MONTHS = 2
# Learn the consumption from the most recent trips up to this distance
LEARN_KM = 300
LEARN_MIN_KM = 80
# Share of provisionally priced liters in the tank above which costs count as provisional
PROVISIONAL_SHARE = 0.05

USER_AGENT = "HomeAssistant-auto_logboek/1.0"

# Config keys (stored in the entry options; the name also in data)
CONF_NAME = "naam"
CONF_FUEL = "brandstof_entiteit"
CONF_TANK = "tankinhoud"
CONF_ODOMETER = "km_entiteit"
CONF_IGNITION = "contact_entiteit"
CONF_IGNITION_OFF = "contact_uit"
CONF_POSITION = "positie_entiteit"
CONF_POSITION_SPARE = "positie_reserve"
CONF_PRIVATE = "prive_entiteit"
CONF_NOTIFY = "melden_aan"
CONF_DASHBOARD = "dashboard"
CONF_REGION = "werk_regio"
CONF_CONSUMPTION = "standaard_verbruik"
CONF_PRICE = "standaard_prijs"
CONF_PRICE_ENTITY = "prijs_entiteit"
CONF_THRESHOLD = "tank_drempel"

DAYS = ("ma", "di", "wo", "do", "vr", "za", "zo")
WORK_KEYS = tuple(f"werk_{d}" for d in DAYS)

DEFAULTS = {
    CONF_TANK: 50,
    CONF_IGNITION_OFF: "off, OFF, Off, false",
    CONF_POSITION_SPARE: "",
    CONF_PRIVATE: "",
    CONF_NOTIFY: "",
    CONF_DASHBOARD: "/lovelace/auto",
    CONF_REGION: "",
    CONF_CONSUMPTION: 6.5,
    CONF_PRICE: 2.0,
    CONF_PRICE_ENTITY: "",
    CONF_THRESHOLD: 3,
    **{k: "" for k in WORK_KEYS},
}

SOORT_WERK = "werk"
SOORT_PRIVE = "prive"
