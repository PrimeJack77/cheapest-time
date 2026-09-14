"""The Cheapest Time integration.

This integration computes the cheapest time to start an appliance (washing
machine, water heater, bike charger, ...) based on a 15-minute resolution
electricity price entity, and exposes the result as several entities:

- the optimal start time
- a boolean telling whether "now" is the right moment to run the appliance
- the total cost of the run if started at the optimal time (with a
  ``forecast_cost`` attribute detailing the cost for every candidate start)
- the expected consumption for the current 15-minute slot (with a
  ``forecast_kwh`` attribute detailing the full load curve anchored at the
  optimal start time)

All the heavy lifting happens in :class:`OptimalStartCoordinator` below.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
import homeassistant.util.dt as dt_util

from .const import (
    ATTR_COST,
    ATTR_END_TIME,
    ATTR_KWH,
    ATTR_PRICE_DATA,
    ATTR_PRICE_PER_KWH,
    ATTR_START_TIME,
    CONF_CONSUMPTION_CURVE,
    CONF_CONSUMPTION_MODE,
    CONF_DURATION_ENTITY,
    CONF_DURATION_FIXED,
    CONF_DURATION_MODE,
    CONF_HORIZON_HOURS,
    CONF_HOURLY_TIMER_ONLY,
    CONF_NOMINAL_POWER,
    CONF_PRICE_ENTITY,
    CONF_PROFILE,
    CONSUMPTION_MODE_CURVE,
    CONSUMPTION_MODE_POWER,
    DEFAULT_HORIZON_HOURS,
    DEFAULT_HOURLY_TIMER_ONLY,
    DOMAIN,
    DURATION_MODE_ENTITY,
    DURATION_MODE_FIXED,
    PLATFORMS,
    PROFILE_AUTOMATIC,
    PROFILE_MANUAL,
    TIME_STEP_MINUTES,
    UPDATE_INTERVAL_SECONDS,
)

_LOGGER = logging.getLogger(__name__)

STEP = timedelta(minutes=TIME_STEP_MINUTES)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------
@dataclass
class OptimalStartResult:
    """Result of a computation cycle, consumed by the entities."""

    cheapest_time: datetime | None = None
    optimal_cost: float | None = None
    run_duration_minutes: int = 0
    is_optimal_period: bool = False
    current_consumption_kwh: float = 0.0
    forecast_cost: list[dict] = field(default_factory=list)
    forecast_kwh: list[dict] = field(default_factory=list)
    currency: str | None = None


# ---------------------------------------------------------------------------
# Helpers: time / price grid handling
# ---------------------------------------------------------------------------
def _floor_to_step(moment: datetime, step_minutes: int = TIME_STEP_MINUTES) -> datetime:
    """Floor a datetime to the internal computation grid (15 minutes)."""
    discard = timedelta(
        minutes=moment.minute % step_minutes,
        seconds=moment.second,
        microseconds=moment.microsecond,
    )
    return moment - discard


def _parse_dt(value) -> datetime | None:
    """Parse a start_time / end_time value coming from the price entity."""
    if isinstance(value, datetime):
        return dt_util.as_local(value)
    if isinstance(value, str):
        parsed = dt_util.parse_datetime(value)
        if parsed is not None:
            return dt_util.as_local(parsed)
    return None


def _build_price_grid(raw_data: list[dict]) -> dict[datetime, float]:
    """Build a mapping of 15-minute slot start -> price (per kWh).

    The price entity is expected to already be on a 15-minute grid. If a
    given entry does not match that resolution (e.g. hourly prices), its
    price is applied as a step function to every 15-minute slot it
    overlaps, i.e. we take the last known price change and do NOT
    interpolate between price points.
    """
    grid: dict[datetime, float] = {}
    for entry in raw_data:
        start = _parse_dt(entry.get(ATTR_START_TIME))
        end = _parse_dt(entry.get(ATTR_END_TIME))
        price = entry.get(ATTR_PRICE_PER_KWH)
        if start is None or end is None or price is None:
            continue
        try:
            price = float(price)
        except (TypeError, ValueError):
            continue
        slot = _floor_to_step(start)
        # Step function: repeat this price for every 15-minute slot until
        # the next known price change (end of this entry).
        while slot < end:
            grid[slot] = price
            slot += STEP
    return grid


# ---------------------------------------------------------------------------
# Helpers: consumption curve handling
# ---------------------------------------------------------------------------
def _slots_from_intervals(intervals: list[tuple[int, int, float]]) -> list[float]:
    """Resample piecewise-constant-power intervals onto the 15-minute grid.

    ``intervals`` is a list of (offset_minutes, duration_minutes,
    average_power_kw) tuples, relative to the start of the run. The
    resulting list gives, for each successive 15-minute slot of the run,
    the energy (kWh) consumed during that slot. Total energy is preserved
    regardless of the original interval boundaries.
    """
    if not intervals:
        return []

    total_minutes = max(offset + duration for offset, duration, _ in intervals)
    n_slots = math.ceil(total_minutes / TIME_STEP_MINUTES)
    slots = [0.0] * n_slots

    for offset, duration, avg_power_kw in intervals:
        remaining = duration
        pos = offset
        while remaining > 0:
            slot_index = pos // TIME_STEP_MINUTES
            slot_start_min = slot_index * TIME_STEP_MINUTES
            slot_end_min = slot_start_min + TIME_STEP_MINUTES
            overlap = min(remaining, slot_end_min - pos)
            if 0 <= slot_index < n_slots:
                slots[slot_index] += avg_power_kw * (overlap / 60)
            pos += overlap
            remaining -= overlap
    return slots


def _curve_from_dict(curve: dict[str, float]) -> list[float]:
    """Convert a {"HH:MM": kWh} curve into 15-minute resampled kWh slots.

    Keys are elapsed-time offsets from the start of the run (00:00 = start).
    The energy given for a key is assumed to be consumed between that key
    and the next one (the last key is assumed to cover an interval of the
    same length as the previous one, or one 15-minute step if there is only
    a single point).
    """
    points: list[tuple[int, float]] = []
    for key, kwh in curve.items():
        try:
            hours, minutes = key.split(":")
            offset = int(hours) * 60 + int(minutes)
            points.append((offset, float(kwh)))
        except (ValueError, AttributeError):
            _LOGGER.warning("Ignoring invalid consumption curve entry: %s=%s", key, kwh)

    if not points:
        return []

    points.sort(key=lambda p: p[0])
    intervals: list[tuple[int, int, float]] = []
    for idx, (offset, kwh) in enumerate(points):
        if idx + 1 < len(points):
            duration = points[idx + 1][0] - offset
        elif idx > 0:
            duration = offset - points[idx - 1][0]
        else:
            duration = TIME_STEP_MINUTES
        duration = max(duration, 1)
        avg_power_kw = kwh / (duration / 60)
        intervals.append((offset, duration, avg_power_kw))

    return _slots_from_intervals(intervals)


def _curve_from_power(power_w: float, duration_minutes: float) -> list[float]:
    """Build a constant-power consumption curve resampled to 15-minute slots."""
    if duration_minutes <= 0 or power_w <= 0:
        return []
    power_kw = power_w / 1000
    return _slots_from_intervals([(0, int(round(duration_minutes)), power_kw)])


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------
class OptimalStartCoordinator(DataUpdateCoordinator[OptimalStartResult]):
    """Coordinator computing the optimal start time for one usage."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.entry = entry
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=timedelta(seconds=UPDATE_INTERVAL_SECONDS),
        )
        self._unsub_price: list = []

    # -- config helpers -----------------------------------------------
    def _conf(self, key: str, default=None):
        """Read a config value, options taking precedence over initial data."""
        return self.entry.options.get(key, self.entry.data.get(key, default))

    @property
    def price_entity_id(self) -> str:
        return self._conf(CONF_PRICE_ENTITY)

    @property
    def profile(self) -> str:
        return self._conf(CONF_PROFILE)

    # -- lifecycle -------------------------------------------------------
    async def async_setup(self) -> None:
        """Subscribe to the entities this coordinator depends on."""
        entities_to_track = [self.price_entity_id]
        duration_entity = self._conf(CONF_DURATION_ENTITY)
        if self._conf(CONF_DURATION_MODE) == DURATION_MODE_ENTITY and duration_entity:
            entities_to_track.append(duration_entity)

        @callback
        def _handle_source_change(event) -> None:
            self.hass.async_create_task(self.async_request_refresh())

        self._unsub_price.append(
            async_track_state_change_event(self.hass, entities_to_track, _handle_source_change)
        )
        # Refresh periodically so that "now" keeps moving even without a
        # state change on the tracked entities.
        self._unsub_price.append(
            async_track_time_interval(
                self.hass, lambda now: self.hass.async_create_task(self.async_request_refresh()),
                timedelta(minutes=TIME_STEP_MINUTES),
            )
        )

    @callback
    def async_unload(self) -> None:
        for unsub in self._unsub_price:
            unsub()
        self._unsub_price.clear()

    # -- computation -------------------------------------------------------
    def _get_price_grid(self) -> tuple[dict[datetime, float], str | None]:
        state = self.hass.states.get(self.price_entity_id)
        if state is None:
            raise UpdateFailed(f"Price entity {self.price_entity_id} not found")

        raw_data = state.attributes.get(ATTR_PRICE_DATA)
        if not raw_data:
            raise UpdateFailed(
                f"Price entity {self.price_entity_id} has no '{ATTR_PRICE_DATA}' attribute"
            )

        currency = state.attributes.get("unit_of_measurement")
        return _build_price_grid(raw_data), currency

    def _get_duration_minutes(self) -> float:
        mode = self._conf(CONF_DURATION_MODE, DURATION_MODE_FIXED)
        if mode == DURATION_MODE_ENTITY:
            entity_id = self._conf(CONF_DURATION_ENTITY)
            state = self.hass.states.get(entity_id) if entity_id else None
            if state is None or state.state in ("unknown", "unavailable", None):
                raise UpdateFailed(f"Duration entity {entity_id} is not available")
            try:
                return float(state.state)
            except ValueError as err:
                raise UpdateFailed(f"Duration entity {entity_id} state is not numeric") from err
        return float(self._conf(CONF_DURATION_FIXED, 60))

    def _get_consumption_curve(self) -> list[float]:
        """Return the run's consumption curve as 15-minute kWh slots."""
        mode = self._conf(CONF_CONSUMPTION_MODE)
        if mode == CONSUMPTION_MODE_CURVE:
            curve = self._conf(CONF_CONSUMPTION_CURVE) or {}
            return _curve_from_dict(curve)
        if mode == CONSUMPTION_MODE_POWER:
            power_w = float(self._conf(CONF_NOMINAL_POWER, 0))
            duration_minutes = self._get_duration_minutes()
            return _curve_from_power(power_w, duration_minutes)
        raise UpdateFailed(f"Unknown consumption mode: {mode}")

    def _get_candidate_starts(
        self, now_floor: datetime, price_grid: dict[datetime, float]
    ) -> list[datetime]:
        profile = self._conf(CONF_PROFILE)
        known_slots = sorted(price_grid.keys())

        if profile == PROFILE_AUTOMATIC:
            # Every known price slot from now until the end of available
            # price data (today, plus tomorrow once published, usually
            # around 1pm).
            return [slot for slot in known_slots if slot >= now_floor]

        # Manual profile: bounded by the configured horizon.
        horizon_hours = float(self._conf(CONF_HORIZON_HOURS, DEFAULT_HORIZON_HOURS))
        horizon_end = now_floor + timedelta(hours=horizon_hours)
        hourly_timer_only = bool(
            self._conf(CONF_HOURLY_TIMER_ONLY, DEFAULT_HOURLY_TIMER_ONLY)
        )

        if hourly_timer_only:
            # The appliance can only be scheduled in whole-hour steps from
            # now (e.g. "start in 3 hours"), not on an arbitrary 15-minute
            # grid aligned to the clock.
            candidates = []
            hours = 0
            while now_floor + timedelta(hours=hours) <= horizon_end:
                candidates.append(_floor_to_step(now_floor + timedelta(hours=hours)))
                hours += 1
            return candidates

        return [slot for slot in known_slots if now_floor <= slot <= horizon_end]

    @staticmethod
    def _compute_optimal(
        price_grid: dict[datetime, float],
        curve_slots: list[float],
        candidate_starts: list[datetime],
    ) -> tuple[datetime | None, float | None, list[dict]]:
        """Find the cheapest feasible start among the candidates.

        Candidates are expected to be sorted chronologically. Ties are
        naturally resolved in favour of the earliest (closest in time)
        candidate because we only replace the current best on a strictly
        lower cost.
        """
        best_start: datetime | None = None
        best_cost: float | None = None
        forecast: list[dict] = []
        n = len(curve_slots)

        for start in candidate_starts:
            cost = 0.0
            feasible = True
            for i, kwh in enumerate(curve_slots):
                slot = start + i * STEP
                price = price_grid.get(slot)
                if price is None:
                    feasible = False
                    break
                cost += kwh * price
            if not feasible:
                continue

            end = start + n * STEP
            forecast.append(
                {
                    ATTR_START_TIME: start.isoformat(),
                    ATTR_END_TIME: end.isoformat(),
                    ATTR_COST: round(cost, 4),
                }
            )
            if best_cost is None or cost < best_cost - 1e-9:
                best_cost = cost
                best_start = start

        return best_start, best_cost, forecast

    async def _async_update_data(self) -> OptimalStartResult:
        now = dt_util.now()
        now_floor = _floor_to_step(now)

        price_grid, currency = await self.hass.async_add_executor_job(self._get_price_grid)
        curve_slots = await self.hass.async_add_executor_job(self._get_consumption_curve)

        if not curve_slots:
            raise UpdateFailed("Unable to build a consumption curve for this usage")

        candidates = self._get_candidate_starts(now_floor, price_grid)
        cheapest_time, optimal_cost, forecast_cost = self._compute_optimal(
            price_grid, curve_slots, candidates
        )

        result = OptimalStartResult(currency=currency)
        result.forecast_cost = forecast_cost
        result.run_duration_minutes = len(curve_slots) * TIME_STEP_MINUTES

        if cheapest_time is None:
            _LOGGER.debug(
                "No feasible start time found for %s (missing price data for the full run)",
                self.entry.title,
            )
            return result

        result.cheapest_time = cheapest_time
        result.optimal_cost = optimal_cost

        # Anchor the consumption curve on the optimal start to build
        # forecast_kwh and derive the current slot's expected consumption.
        forecast_kwh = []
        current_kwh = 0.0
        for i, kwh in enumerate(curve_slots):
            slot_start = cheapest_time + i * STEP
            slot_end = slot_start + STEP
            forecast_kwh.append(
                {
                    ATTR_START_TIME: slot_start.isoformat(),
                    ATTR_END_TIME: slot_end.isoformat(),
                    ATTR_KWH: round(kwh, 4),
                }
            )
            if slot_start <= now < slot_end:
                current_kwh = kwh
        result.forecast_kwh = forecast_kwh
        result.current_consumption_kwh = current_kwh

        run_end = cheapest_time + len(curve_slots) * STEP
        profile = self._conf(CONF_PROFILE)
        if profile == PROFILE_AUTOMATIC:
            result.is_optimal_period = cheapest_time <= now < run_end
        else:
            # Manual profile: true only during the recommended 15-minute
            # slot (i.e. "now" is the minute to press start).
            result.is_optimal_period = now_floor == _floor_to_step(cheapest_time)

        return result


# ---------------------------------------------------------------------------
# Integration setup
# ---------------------------------------------------------------------------
async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Cheapest Time from a config entry."""
    coordinator = OptimalStartCoordinator(hass, entry)
    await coordinator.async_setup()

    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception as err:  # noqa: BLE001
        coordinator.async_unload()
        raise ConfigEntryNotReady from err

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    entry.async_on_unload(entry.add_update_listener(async_update_options))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when its options are updated via the Options Flow."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator: OptimalStartCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        coordinator.async_unload()
    return unload_ok
