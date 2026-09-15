# Cheapest Time (Home Assistant custom integration)

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

Computes the cheapest time to start an appliance (washing machine, water
heater, bike charger, ...) based on a 15-minute resolution electricity
price entity, and exposes the result as several entities.

## How it works

1. The integration reads the configured **price entity**'s `data`
   attribute: a list of dicts with `start_time`, `end_time`,
   `price_per_kwh`. Today's prices are always assumed known; tomorrow's
   prices typically appear around 1pm.
2. Prices are resampled onto an internal 15-minute grid. If the source is
   not already at 15-minute resolution (e.g. hourly prices), each price
   change is applied as a **step function** to every 15-minute slot it
   covers — there is **no interpolation** between price points.
3. The usage's consumption is expressed either as:
   - a **curve**: a `{"HH:MM": kWh}` dict, `HH:MM` being the elapsed time
     since the start of the run (not a time of day), or
   - a **nominal power** (W) and a **duration** (fixed, in minutes, or
     read from another entity's state).
   Either way it is resampled onto the same 15-minute grid, **preserving
   total energy** regardless of the original step sizes.
4. Two profiles are available:
   - **Manual**: candidate start times are searched between now and a
     configurable horizon (default 12h). Useful for appliances you start
     yourself (washing machine, dishwasher). If the appliance only offers
     a 1-hour step timer, enable "hourly timer only" so candidates are
     restricted to whole-hour offsets from now.
   - **Automatic**: candidate start times cover the full 24h of every day
     for which prices are known (today, and tomorrow once published).
     Useful for appliances you control automatically (water heater).
5. Among all feasible candidates (i.e. those for which the full run's
   price is known), the cheapest one is selected. On equal cost, the
   **earliest** (closest in time) candidate wins.

## Entities created per configured usage

| Entity | Description |
| --- | --- |
| `sensor.<name>_start_time` | Timestamp of the optimal start. Attributes: `run_end_time`, `run_duration_minutes`, `cost` (total cost of the run if started at this optimal time). |
| `binary_sensor.<name>_is_optimal_period` | `on` when now is within the optimal run window (automatic profile) or during the single recommended 15-minute slot (manual profile). |
| `sensor.<name>_cost_at_optimal` | Cost of the usage **if launched right now** (current 15-minute slot). Attribute `forecast_cost`: list of `{start_time, end_time, cost}` — the cost for every later slot, from the one right after "now" up to the last slot for which prices are known (the price horizon). Independent of the optimal start selection. |
| `sensor.<name>_current_consumption` | Expected consumption (kWh) of the current 15-minute slot, assuming the run started (or will start) at the optimal time — unchanged. Attribute `forecast_kwh`: list of `{start_time, end_time, kwh}`, zero-padded outside the actual run window(s), spanning the **same horizon as `forecast_cost`** (from the slot after "now" to the last known price slot) so both attributes line up for charting. **Automatic profile**: shows one consumption placement per calendar day present in the price data (today's cheapest window, and tomorrow's once published), giving visibility on tomorrow's run ahead of time. **Manual profile**: a single placement, anchored on the one recommended start time. |

## Installation

### Via HACS (custom repository)

1. In Home Assistant, go to **HACS > Integrations > ⋮ > Custom repositories**.
2. Add `https://github.com/PrimeJack77/cheapest-time`, category **Integration**.
3. Search for "Cheapest Time" in HACS and install it.
4. Restart Home Assistant.
5. Go to **Settings > Devices & Services > Add Integration** and search for
   "Cheapest Time".

### Manual

Copy the `custom_components/cheapest_time` folder into your Home
Assistant `config/custom_components/` directory, then restart Home
Assistant.

## Configuration

The integration is configured entirely from the UI (**Settings > Devices
& Services > Add Integration > Cheapest Time**), and can be reconfigured
later from the entry's **Configure** button (Options Flow). One config
entry = one usage (add the integration again for a second appliance).

Fields:
- **Name**: friendly name of the usage.
- **Electricity price entity**: entity providing the `data` attribute
  described above.
- **Profile**: `manual` or `automatic`.
- **Consumption mode**: `curve` (JSON dict) or `power` (nominal power +
  duration).
- For `power` mode: nominal power (W), and duration mode (`fixed`
  minutes, or another entity's state).
- For the `manual` profile: search horizon (hours) and whether the
  appliance only supports a 1-hour step timer.

## Notes / assumptions

- Prices and costs are assumed to be in the same currency as reported by
  the price entity's `unit_of_measurement` (e.g. `EUR/kWh`).
- If no candidate start has full price coverage (e.g. tomorrow's prices
  not published yet and the run would extend past midnight), the
  `cheapest_time_time` sensor becomes `unknown` until enough price data
  is available.
- The consumption curve dict keys (`HH:MM`) are **elapsed time offsets**
  from the start of the run, not times of day.
