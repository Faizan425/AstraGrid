# Benchmark scenarios

The built-in benchmark scenarios are deterministic. Paired AstraGrid and
baseline runs start from separate deep copies of the same configuration, seed,
task ledger, illumination profile, and fault schedule.

The original scenarios remain available as nominal and fault-regression cases.
They are intentionally easy in many cases: their three standard tasks finish
before later faults begin, so equal controller results are expected there.

## Added decision scenarios

| Scenario | Initial power and storage | Workload and fault schedule | What it measures |
| --- | --- | --- | --- |
| `constrained_task_selection` | Steady 19.5 kW solar generation, which becomes 18.72 kW at the bus; 10.5 kW critical load; battery at 20.1 kWh with a 20 kWh reserve; no backup. This leaves roughly 8.2 kW optional margin. | At step 0: one 6 kW priority-10 task and two 4 kW priority-7 tasks. | A genuine one-step knapsack choice: greedy priority selects the 6 kW task; the MILP can select both 4 kW tasks for a higher total priority. |
| `early_solar_fault_long_workload` | Default cyclic solar, 60 kWh initial battery, 20 kWh reserve, 12 kW backup. | 20-, 18-, and 16-step workloads are released at step 0. Solar fails at step 2. | Work remains pending when the fault begins; it is not an after-the-fact fault. This does not promise a difference because both policies may make the same early choice. |
| `deadline_release_pressure` | Same steady 18.72 kW bus supply and 20.1/20 kWh battery setup as the constrained case. | Two current tasks have deadlines at steps 1–2; a 6 kW hard task releases at step 2 and must finish by step 3. | Release-time and hard-deadline ledger semantics. The multi-step rolling-horizon MILP forecasts future releases and reserves capacity for upcoming high-priority tasks. |
| `early_backup_unavailable` | Default cyclic solar, 60 kWh initial battery, 20 kWh reserve. | Three long workloads begin at step 0; backup becomes unavailable at step 1. | Controller-visible equipment-status telemetry for a fault while work remains pending. |

## Capability observability

`FaultEngine.capabilities()` remains the simulator's true physical capability.
At every step, `TelemetryGenerator` turns that into equipment-status telemetry
such as `equipment_backup_available` and `equipment_battery_discharge_factor`.
`CapabilityEstimator` turns only those reported statuses into the conservative
capability passed to both controllers. A missing boolean becomes unavailable;
a missing or malformed factor becomes zero. Telemetry faults can target any
`equipment_*` channel with `kind: missing` or a replacement value.

The simulator does not use this estimate: it continues to enforce true
capability. Therefore a corrupted “healthy” status can still lead to a failed
request, which the simulator reports as a transition error; a missing status
instead makes planning conservative.
