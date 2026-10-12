# AstraGrid

> AstraGrid estimates how long a simulated habitat can remain within critical energy, oxygen, water, CO₂ and thermal limits. It coordinates solar and dispatchable backup power, battery usage, life-support equipment, resource inventories, recoverable equipment heat and flexible mission workloads. When tasks must be postponed, it records them in a Mission Debt Ledger and plans their recovery when capacity returns. When hardware or telemetry becomes unreliable, it shifts into an appropriate degraded or emergency mode instead of blindly trusting the optimiser.

AstraGrid is a deterministic simulation and decision-support prototype for a lunar habitat. It is **not flight-qualified software** and must not control a real spacecraft.

## Install and run

```powershell
python -m pip install -e .
astragrid run combined_fault --controller astragrid --trace --output outputs
astragrid compare combined_fault --output outputs
astragrid benchmark nominal_day combined_fault --seed 7 --output outputs\benchmark
astragrid lab batch nominal_day solar_array_damage combined_fault --seed 42 --output outputs\resilience_lab
astragrid run-all --output outputs
uvicorn astra_grid.api:app --reload
astragrid-gui
```

The command line supports `run`, `compare`, `benchmark`, `run-all`, and `lab`. `lab` invokes **AstraGrid Resilience Lab** for automated batch experiments, independent evaluations, step-by-step mission replay, and interactive HTML reporting. See [the Resilience Lab guide](docs/resilience-lab.md) for full details.

## AstraGrid Resilience Lab

Run `astragrid lab` commands for systematic evaluation and mission replay:
- `astragrid lab list`: List all built-in scenarios, descriptions, durations, and fault targets.
- `astragrid lab run <scenario> [--controller] [--seed] [--output]`: Run a single evaluated scenario, generating full per-step telemetry/state CSV and replay JSON.
- `astragrid lab compare <scenario> [--seed] [--output]`: Run matched controller comparison (AstraGrid vs Baseline) on identical initial conditions with deltas and interactive HTML report.
- `astragrid lab batch [scenarios...] [--controllers] [--seed] [--output]`: Run batch experiment with multi-seed aggregation and Student-t confidence intervals.
- `astragrid lab report <directory>`: Regenerate standalone interactive HTML dashboard (`report.html`) from saved results without rerunning simulations.

## Mission Control Web Interface

Launch the modern browser-based Mission Control cockpit at `http://127.0.0.1:8000`:

```powershell
astragrid web
# or custom host/port:
astragrid web --host 127.0.0.1 --port 8000
```

Features included in the web interface:
- **Cockpit HUD & Radial Gauges**: Real-time animated SOC radial gauge, battery kWh trajectory, power bus meters (solar, backup, load, critical shortfall), life-support inventories (O₂, CO₂, water, temperature), and mission debt KPIs.
- **Power & Life Support Flow Schematic**: Interactive SVG diagram showing live energy flows between solar array, backup generator, battery bank, central PDU, and subsystems.
- **Independent Validation Inspector**: Real-time display of solver proposed status, safety validator audit reasons, executed actions, and fallback policy notifications.
- **Live Telemetry Charts**: Zero-dependency HTML5 Canvas time-series charts for power balance, battery reserve envelopes, atmosphere/water consumables, and thermal envelope.
- **Mission Task Ledger**: Interactive status table showing task priorities, release/deadline horizons, progress bars, and deferral counts.
- **Matched Controller Comparison**: One-click side-by-side evaluation of AstraGrid MILP vs. Baseline Heuristic with live delta cards and trade-off metrics.
- **Event Timeline & Telemetry Stream**: Chronological event feed categorized by safety, control, fault, and telemetry events, plus raw observation JSON stream.
- **8-Panel Post-Mortem Mission Replay**: Scrub step-by-step through any completed mission with operational telemetry, state estimates, proposed plans, validator audits, executed actions, and physical transitions.

## Desktop dashboard (Tkinter)

Run `astragrid-gui` (or `python -m astra_grid.gui`) to open the local Tkinter control-room dashboard. It uses the same simulation engine as the CLI/API and includes scenario/controller selection, one-step and auto-run controls, telemetry charts, mission-debt/tasks, an audit log, demo-only fault injection, a raw Synthetic Telemetry Stream tab, and a separate side-by-side AstraGrid-versus-baseline comparison tab. It requires the standard Python `tkinter` component; no web server is needed.

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
