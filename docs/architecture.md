# Architecture

`HabitatSimulator` owns physical truth. `TelemetryGenerator` alone observes it, applies telemetry faults, and produces `Observation`. `StateEstimator`, controllers, validator, fault manager, and survival estimator receive observations/previous estimates/configuration only. `SimulationRun` keeps actual fault/evaluation records separate from controller-visible estimates.

`FaultEngine` keeps physical and telemetry faults distinct. Physical failures change capabilities and resource trajectories; telemetry faults change only measurements. The runner infers solar degradation from reported generation versus the known illumination profile and treats sensor disagreement conservatively.

The in-memory FastAPI store is intentionally small: it is a demo API, not durable operations infrastructure.
