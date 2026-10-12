# AstraGrid

> AstraGrid estimates how long a simulated habitat can remain within critical energy, oxygen, water, CO₂ and thermal limits. It coordinates solar and dispatchable backup power, battery usage, life-support equipment, resource inventories, recoverable equipment heat and flexible mission workloads. When tasks must be postponed, it records them in a Mission Debt Ledger and plans their recovery when capacity returns. When hardware or telemetry becomes unreliable, it shifts into an appropriate degraded or emergency mode instead of blindly trusting the optimiser.

AstraGrid is a deterministic simulation and decision-support prototype for a lunar habitat. It is **not flight-qualified software** and must not control a real spacecraft.

## Install and run

```powershell
python -m pip install -e .
astragrid run combined_fault --controller astragrid --trace --output outputs
astragrid compare combined_fault --output outputs
astragrid run-all --output outputs
uvicorn astra_grid.api:app --reload
```

The command line supports `run`, `compare`, and `run-all`. Output contains a JSON run record, a per-step CSV, and comparison CSV/JSON summaries. The API is process-local: restart loses run state unless it has been exported.

## API

- `GET /health` – version and SciPy optimiser availability.
- `GET /api/scenarios` – built-in scenario IDs.
- `POST /api/simulations` with `{"scenario_id":"combined_fault","controller":"astragrid"}` creates a run (`baseline` and `compare` are also supported). An optional `overrides` object is recursively merged then validated as `ScenarioConfig`, e.g. `{"overrides":{"battery":{"safe_reserve_kwh":25}}}`.
- `POST /api/simulations/{id}/step` and `/run` (`{"steps": 20}`) advance a run.
- `GET /api/simulations/{id}/state`, `/plan`, `/tasks`, `/events`, `/metrics`, and `/faults` inspect it.

`state` is explicitly telemetry-derived. Metrics include an `evaluation_only_true_state` field solely for post-run evaluation; it is never handed to either controller. Unknown IDs and invalid scenario IDs receive structured 404/422 errors.

## Model and assumptions

The fixed step is 0.25 hours (15 minutes); the standard horizon/scenario duration is 96 steps (24 hours). Units are kW, kWh, kg, °C, and hours.

- Solar follows a configurable repeating illumination profile. A dispatchable backup source is capped and can fail.
- Battery storage has capacity, reserve, rates, and efficiencies. A degradation fault can strand energy above a newly reduced usable capacity; this is reported as a transition event, never hidden.
- Oxygen is consumed/produced in kg; oxygen generation also consumes water. CO₂ is a simplified burden in kg. Water recovery is limited by recoverable wastewater, capacity, and efficiency.
- The thermal model is one-zone linear: `T_next = T + dt/C * (recoverable_heat + heater_heat - UA*(T-external))`. It is intentionally not a detailed lunar thermal model.
- Three SOC sensors are median-fused; readings that disagree by more than 0.12 SOC are marked suspect. “Byzantine” means a misleading telemetry source, not distributed Byzantine consensus.

The survival horizon is a conservative linear projection across energy reserve, oxygen reserve, CO₂ limit, water reserve, and thermal bound. Its limiting factor and assumptions are returned with each step. A zero-hour result means a configured boundary is already breached or required telemetry is absent.

## Safety and decision path

Each step obtains telemetry, updates estimates and telemetry quality, estimates capability from observations, calculates a horizon, updates the deterministic fault mode, obtains a plan, independently validates it, and executes only its first action. A solver error, infeasible candidate, or rejected proposal triggers an independently validated priority-policy fallback. If none can preserve configured critical services, the run declares controlled shutdown and logs any unavoidable deficit.

The MILP is intentionally small: it selects binary optional mission tasks at the current rolling step, subject to bus supply, battery reserve/rate and hard task deadlines. Replanning occurs every 15 minutes. SciPy's `milp` is required for the AstraGrid controller; the baseline does not use it. `MissionDebtLedger` records deferrals, completions and missed deadlines, and never changes a deadline.

## Built-in scenarios

The nine JSON scenario entry points in `scenarios/` map to validated built-in configurations: `nominal_day`, `prolonged_solar_outage`, `solar_array_damage`, `battery_degradation_or_failure`, `byzantine_soc_sensor`, `combined_fault`, `hard_deadline_under_shortage`, `life_support_resource_shortage`, and `water_recovery_failure`.

## Metrics

Exports report critical/noncritical electricity shortfall, true/evaluated and estimated SOC, oxygen/CO₂/water extrema, task completions, hard deadline misses, debt, fallback count, and an unsafe-actions target (zero). Resource/thermal models are deliberately simple; metrics are suitable for comparing repeatable prototype policies, not a safety case.

## Tests

```powershell
python -m pytest
```
