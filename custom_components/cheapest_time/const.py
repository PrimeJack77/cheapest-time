"""Constants for the Cheapest Time integration."""
from __future__ import annotations

DOMAIN = "cheapest_time"

# ---------------------------------------------------------------------------
# Configuration / options keys
# ---------------------------------------------------------------------------
CONF_NAME = "name"
CONF_PRICE_ENTITY = "price_entity"
CONF_PROFILE = "profile"
CONF_CONSUMPTION_MODE = "consumption_mode"
CONF_CONSUMPTION_CURVE = "consumption_curve"
CONF_NOMINAL_POWER = "nominal_power"
CONF_DURATION_MODE = "duration_mode"
CONF_DURATION_FIXED = "duration_fixed"
CONF_DURATION_ENTITY = "duration_entity"
CONF_HORIZON_HOURS = "horizon_hours"
CONF_HOURLY_TIMER_ONLY = "hourly_timer_only"

# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------
PROFILE_MANUAL = "manual"
PROFILE_AUTOMATIC = "automatic"
PROFILES = [PROFILE_MANUAL, PROFILE_AUTOMATIC]

# ---------------------------------------------------------------------------
# Consumption modes
# ---------------------------------------------------------------------------
CONSUMPTION_MODE_CURVE = "curve"
CONSUMPTION_MODE_POWER = "power"
CONSUMPTION_MODES = [CONSUMPTION_MODE_CURVE, CONSUMPTION_MODE_POWER]

# Duration modes (only used when consumption_mode == power)
DURATION_MODE_FIXED = "fixed"
DURATION_MODE_ENTITY = "entity"
DURATION_MODES = [DURATION_MODE_FIXED, DURATION_MODE_ENTITY]

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DEFAULT_HORIZON_HOURS = 12
DEFAULT_HOURLY_TIMER_ONLY = False
DEFAULT_DURATION_FIXED_MINUTES = 60
DEFAULT_NOMINAL_POWER_W = 2000

# Internal computation time step, in minutes. This matches the resolution
# required for the price entity and is used to resample everything
# (prices and consumption curves) onto a common grid.
TIME_STEP_MINUTES = 15

# Coordinator periodic refresh interval (seconds). The coordinator is also
# refreshed reactively whenever the price entity or the duration entity
# (if any) changes state.
UPDATE_INTERVAL_SECONDS = 300

PLATFORMS = ["sensor", "binary_sensor"]

# ---------------------------------------------------------------------------
# Price entity payload keys (as documented by the user: entity attribute
# "data" holding a list of dicts with start_time / end_time / price_per_kwh)
# ---------------------------------------------------------------------------
ATTR_PRICE_DATA = "data"
ATTR_START_TIME = "start_time"
ATTR_END_TIME = "end_time"
ATTR_PRICE_PER_KWH = "price_per_kwh"

# ---------------------------------------------------------------------------
# Output entity attribute keys
# ---------------------------------------------------------------------------
ATTR_FORECAST_COST = "forecast_cost"
ATTR_FORECAST_KWH = "forecast_kwh"
ATTR_COST = "cost"
ATTR_KWH = "kwh"
ATTR_RUN_END_TIME = "run_end_time"
ATTR_RUN_DURATION_MINUTES = "run_duration_minutes"
