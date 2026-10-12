# AstraGrid

> AstraGrid estimates how long a simulated habitat can remain within critical energy, oxygen, water, CO₂ and thermal limits. It coordinates solar and dispatchable backup power, battery usage, life-support equipment, resource inventories, recoverable equipment heat and flexible mission workloads. When tasks must be postponed, it records them in a Mission Debt Ledger and plans their recovery when capacity returns. When hardware or telemetry becomes unreliable, it shifts into an appropriate degraded or emergency mode instead of blindly trusting the optimiser.

AstraGrid is a deterministic simulation and decision-support prototype for a lunar habitat. It is **not flight-qualified software** and must not control a real spacecraft.

## Install and run

```powershell
python -m pip install -e .
astragrid run combined_fault --controller astragrid --trace --output outputs
astragrid compare combined_fault --output outputs
astragrid benchmark nominal_day combined_fault --seed 7 --output outputs\benchmark
astragrid run-all --output outputs
uvicorn astra_grid.api:app --reload
astragrid-gui
```

The command line supports `run`, `compare`, `benchmark`, and `run-all`. `benchmark` accepts one or more built-in scenario IDs (or all scenarios when omitted) and repeatable `--seed` options. It writes `benchmark_report.json`, per-run data, paired deltas, aggregate summaries, and failures as CSV files. The API is process-local: restart loses run state unless it has been exported.

## Desktop dashboard

Run `astragrid-gui` (or `python -m astra_grid.gui`) to open the optional local Tkinter control-room dashboard. It uses the same simulation engine as the CLI/API and includes scenario/controller selection, one-step and auto-run controls, telemetry charts, mission-debt/tasks, an audit log, demo-only fault injection, a raw Synthetic Telemetry Stream tab, and a separate side-by-side AstraGrid-versus-baseline comparison tab. When both comparison runs finish, that tab displays a final benchmark of safety, energy, task completion, fallback, and stopping metrics. Each GUI simulation step prints its raw reported synthetic observation as a JSON line in the launching PowerShell terminal. It requires the standard Python `tkinter` component; no web server is needed.

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

The MILP controller is a multi-step rolling-horizon optimization model: across the planning horizon, it co-optimizes time-indexed binary task schedules, backup generator dispatch, and battery charging and discharging trajectories subject to bus supply, efficiency losses, storage bounds, non-flexible contiguous execution, and hard deadlines. Replanning occurs at every rolling step (every 15 minutes), executing the immediate action while planning ahead. SciPy's `milp` is required for the AstraGrid controller; the baseline uses a priority heuristic. `MissionDebtLedger` records deferrals, completions, and missed deadlines, and never changes a deadline.

## Built-in scenarios

The JSON scenario entry points in `scenarios/` map to validated built-in configurations: `nominal_day`, `prolonged_solar_outage`, `solar_array_damage`, `battery_degradation_or_failure`, `byzantine_soc_sensor`, `combined_fault`, `hard_deadline_under_shortage`, `life_support_resource_shortage`, and `water_recovery_failure`.

Four focused benchmark scenarios are also available through the CLI, API, and dashboard selectors: `constrained_task_selection`, `early_solar_fault_long_workload`, `deadline_release_pressure`, and `early_backup_unavailable`. They are designed to expose scheduler trade-offs and early equipment failures that an easy nominal scenario can hide. See [the benchmark scenario guide](docs/benchmark-scenarios.md) for their exact workload and fault design.

Equipment health is telemetry, not controller truth: the simulator applies a physical fault, emits `equipment_*` status readings, and the controller converts those readings into a conservative `Capability` estimate. An absent status is treated as unavailable. The simulator still enforces its separate true capability during execution, so an unsafe command cannot manufacture backup power or bypass a failed device.

## Metrics

Exports report critical/noncritical electricity shortfall, true/evaluated and estimated SOC, oxygen/CO₂/water extrema, task completions, hard deadline misses, debt, fallback count, and an unsafe-actions target (zero). Resource/thermal models are deliberately simple; metrics are suitable for comparing repeatable prototype policies, not a safety case.

## Tests

```powershell
python -m pytest
```
