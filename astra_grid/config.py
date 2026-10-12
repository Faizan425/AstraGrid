"""Validated scenario configuration and bundled scenario factory."""
from __future__ import annotations

import json
from math import isfinite
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class BatteryConfig(BaseModel):
    capacity_kwh: float = Field(80, gt=0)
    initial_kwh: float = Field(60, ge=0)
    safe_reserve_kwh: float = Field(20, ge=0)
    max_charge_kw: float = Field(20, gt=0)
    max_discharge_kw: float = Field(25, gt=0)
    charge_efficiency: float = Field(0.95, gt=0, le=1)
    discharge_efficiency: float = Field(0.95, gt=0, le=1)

    @model_validator(mode="after")
    def valid_energy(self) -> "BatteryConfig":
        if self.initial_kwh > self.capacity_kwh or self.safe_reserve_kwh > self.capacity_kwh:
            raise ValueError("battery initial/safe reserve cannot exceed capacity")
        return self


class PowerConfig(BaseModel):
    solar_rated_kw: float = Field(30, ge=0)
    solar_profile: list[float] = Field(default_factory=lambda: [0.0] * 20 + [0.35] * 8 + [0.7] * 12 + [1.0] * 24 + [0.7] * 12 + [0.35] * 8 + [0.0] * 12)
    backup_max_kw: float = Field(12, ge=0)
    backup_available: bool = True
    pdu_efficiency: float = Field(0.96, gt=0, le=1)
    pdu_available: bool = True
    fixed_essential_kw: float = Field(4.0, ge=0)
    control_computer_kw: float = Field(0.4, ge=0)

    @field_validator("solar_profile")
    @classmethod
    def nonnegative_profile(cls, values: list[float]) -> list[float]:
        if not values or any(not isfinite(value) or value < 0 for value in values):
            raise ValueError("solar profile must be a non-empty finite non-negative fraction list")
        return values


class LifeSupportConfig(BaseModel):
    oxygen_initial_kg: float = Field(45, ge=0)
    oxygen_reserve_kg: float = Field(15, ge=0)
    oxygen_demand_kgph: float = Field(0.55, ge=0)
    oxygen_generation_kgph: float = Field(0.7, ge=0)
    oxygen_generator_kw: float = Field(2.2, ge=0)
    oxygen_water_kg_per_kg: float = Field(1.2, ge=0)
    co2_initial_kg: float = Field(1.0, ge=0)
    co2_limit_kg: float = Field(4.0, gt=0)
    co2_generation_kgph: float = Field(0.5, ge=0)
    co2_removal_kgph: float = Field(0.65, ge=0)
    co2_removal_kw: float = Field(1.8, ge=0)
    air_circulation_kw: float = Field(0.8, ge=0)
    water_initial_kg: float = Field(130, ge=0)
    water_reserve_kg: float = Field(45, ge=0)
    water_demand_kgph: float = Field(3.0, ge=0)
    wastewater_return_fraction: float = Field(0.75, ge=0, le=1)
    water_recovery_efficiency: float = Field(0.88, ge=0, le=1)
    water_recovery_capacity_kgph: float = Field(3.0, ge=0)
    water_recovery_kw: float = Field(1.3, ge=0)


class ThermalConfig(BaseModel):
    initial_temperature_c: float = 21.0
    min_temperature_c: float = 16.0
    max_temperature_c: float = 28.0
    external_temperature_c: float = -70.0
    thermal_capacity_kwh_per_c: float = Field(40, gt=0)
    envelope_ua_kw_per_c: float = Field(0.09, ge=0)
    heater_kw: float = Field(3.0, ge=0)
    heater_efficiency: float = Field(0.95, gt=0, le=1)
    recoverable_heat_fraction: float = Field(0.65, ge=0, le=1)


class TaskConfig(BaseModel):
    id: str
    power_kw: float = Field(gt=0)
    duration_steps: int = Field(ge=1)
    priority: int = Field(ge=1, le=10)
    release_step: int = Field(0, ge=0)
    deadline_step: int = Field(ge=0)
    hard_deadline: bool = False
    flexible: bool = True

    @model_validator(mode="after")
    def release_precedes_deadline(self) -> "TaskConfig":
        if self.release_step > self.deadline_step:
            raise ValueError("task release step cannot follow its deadline")
        return self


class FaultConfig(BaseModel):
    id: str
    category: Literal["physical", "telemetry"]
    target: str
    start_step: int = Field(ge=0)
    end_step: int | None = Field(default=None, ge=0)
    parameters: dict[str, float | str | bool] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_window(self) -> "FaultConfig":
        if self.end_step is not None and self.end_step < self.start_step:
            raise ValueError("fault end_step must not precede start_step")
        for key, value in self.parameters.items():
            if isinstance(value, float) and not isfinite(value):
                raise ValueError(f"fault parameter '{key}' must be finite")
            if key in {"factor", "capacity_factor", "discharge_factor"} and isinstance(value, (int, float)) and not isinstance(value, bool) and not 0 <= value <= 1:
                raise ValueError(f"fault parameter '{key}' must be between zero and one")
        return self


class ControllerConfig(BaseModel):
    horizon_steps: int = Field(96, ge=1, le=384)
    solver_time_limit_s: float = Field(1.0, gt=0, le=30)
    minimum_quality_for_normal: str = "medium"


class ScenarioConfig(BaseModel):
    id: str
    description: str
    dt_hours: float = Field(0.25, gt=0, le=2)
    duration_steps: int = Field(96, ge=1, le=2000)
    seed: int = 7
    battery: BatteryConfig = Field(default_factory=BatteryConfig)
    power: PowerConfig = Field(default_factory=PowerConfig)
    life_support: LifeSupportConfig = Field(default_factory=LifeSupportConfig)
    thermal: ThermalConfig = Field(default_factory=ThermalConfig)
    tasks: list[TaskConfig] = Field(default_factory=list)
    faults: list[FaultConfig] = Field(default_factory=list)
    controller: ControllerConfig = Field(default_factory=ControllerConfig)

    @model_validator(mode="after")
    def unique_task_ids_and_finite_temperatures(self) -> "ScenarioConfig":
        ids = [task.id for task in self.tasks]
        if len(ids) != len(set(ids)):
            raise ValueError("task IDs must be unique")
        thermal_values = (
            self.thermal.initial_temperature_c,
            self.thermal.min_temperature_c,
            self.thermal.max_temperature_c,
            self.thermal.external_temperature_c,
        )
        if any(not isfinite(value) for value in thermal_values):
            raise ValueError("thermal temperatures must be finite")
        if self.thermal.min_temperature_c > self.thermal.max_temperature_c:
            raise ValueError("thermal minimum cannot exceed thermal maximum")
        return self


def _tasks() -> list[TaskConfig]:
    return [
        TaskConfig(id="science_task_01", power_kw=3.5, duration_steps=4, priority=3, deadline_step=60),
        TaskConfig(id="science_task_02", power_kw=5.0, duration_steps=6, priority=7, deadline_step=78),
        TaskConfig(id="maintenance_task", power_kw=2.2, duration_steps=3, priority=8, deadline_step=48, hard_deadline=True),
    ]


def _base(scenario_id: str, description: str) -> ScenarioConfig:
    return ScenarioConfig(id=scenario_id, description=description, tasks=_tasks())


def _benchmark_power() -> PowerConfig:
    """Steady daylight leaves an 8 kW optional-work margin at reserve SOC."""
    return PowerConfig(solar_rated_kw=30, solar_profile=[0.65] * 96, backup_max_kw=0)


def built_in_scenarios() -> dict[str, ScenarioConfig]:
    """Return fresh configurations so runs cannot mutate shared scenario state."""
    scenarios: dict[str, ScenarioConfig] = {}
    scenarios["nominal_day"] = _base("nominal_day", "Cyclic illumination with healthy equipment.")
    scenarios["prolonged_solar_outage"] = _base("prolonged_solar_outage", "A long temporary loss of solar generation.")
    scenarios["prolonged_solar_outage"].faults = [FaultConfig(id="eclipse", category="physical", target="solar", start_step=20, end_step=65, parameters={"factor": 0.0})]
    scenarios["solar_array_damage"] = _base("solar_array_damage", "Persistent solar-array derating.")
    scenarios["solar_array_damage"].faults = [FaultConfig(id="array_damage", category="physical", target="solar", start_step=22, parameters={"factor": 0.35})]
    scenarios["battery_degradation_or_failure"] = _base("battery_degradation_or_failure", "Battery capacity and discharge degradation.")
    scenarios["battery_degradation_or_failure"].faults = [FaultConfig(id="battery_degradation", category="physical", target="battery", start_step=8, parameters={"capacity_factor": 0.45, "discharge_factor": 0.45})]
    scenarios["byzantine_soc_sensor"] = _base("byzantine_soc_sensor", "One plausible-looking SOC source is arbitrarily misleading.")
    scenarios["byzantine_soc_sensor"].faults = [FaultConfig(id="soc_byzantine", category="telemetry", target="soc_sensor_2", start_step=5, parameters={"value": 0.75})]
    scenarios["combined_fault"] = _base("combined_fault", "Solar damage combined with an outlying SOC sensor.")
    scenarios["combined_fault"].faults = [FaultConfig(id="array_damage", category="physical", target="solar", start_step=8, parameters={"factor": 0.4}), FaultConfig(id="soc_byzantine", category="telemetry", target="soc_sensor_2", start_step=5, end_step=25, parameters={"value": 0.75})]
    scenarios["hard_deadline_under_shortage"] = _base("hard_deadline_under_shortage", "A hard task cannot be completed under the available supply.")
    scenarios["hard_deadline_under_shortage"].tasks = [TaskConfig(id="urgent_drill", power_kw=18, duration_steps=5, priority=10, deadline_step=10, hard_deadline=True)]
    scenarios["hard_deadline_under_shortage"].faults = [FaultConfig(id="solar_out", category="physical", target="solar", start_step=0, end_step=30, parameters={"factor": 0.0}), FaultConfig(id="fission_out", category="physical", target="backup", start_step=0, parameters={"available": False})]
    scenarios["life_support_resource_shortage"] = _base("life_support_resource_shortage", "CO2 removal and oxygen generation lose capability.")
    scenarios["life_support_resource_shortage"].faults = [FaultConfig(id="oxygen_degraded", category="physical", target="oxygen_generator", start_step=10, parameters={"factor": 0.25}), FaultConfig(id="co2_degraded", category="physical", target="co2_removal", start_step=10, parameters={"factor": 0.15})]
    scenarios["water_recovery_failure"] = _base("water_recovery_failure", "Water recovery fails and usable water steadily declines.")
    scenarios["water_recovery_failure"].faults = [FaultConfig(id="recovery_failure", category="physical", target="water_recovery", start_step=12, parameters={"factor": 0.0})]
    scenarios["constrained_task_selection"] = ScenarioConfig(
        id="constrained_task_selection",
        power=_benchmark_power(),
        # A small amount above reserve avoids a zero-hour survival estimate
        # while keeping battery discharge too small to erase the 8 kW choice.
        battery=BatteryConfig(initial_kwh=20.1, safe_reserve_kwh=20),
        tasks=[
            TaskConfig(id="high_draw", power_kw=6, duration_steps=1, priority=10, deadline_step=0),
            TaskConfig(id="medium_a", power_kw=4, duration_steps=1, priority=7, deadline_step=0),
            TaskConfig(id="medium_b", power_kw=4, duration_steps=1, priority=7, deadline_step=0),
        ],
        description="At reserve SOC, 8 kW optional margin: greedy selects 6 kW priority-10 work; the MILP can select two 4 kW priority-7 tasks.",
    )
    scenarios["early_solar_fault_long_workload"] = ScenarioConfig(
        id="early_solar_fault_long_workload",
        description="A solar failure begins while long workloads are still pending; backup remains observable and available.",
        power=PowerConfig(solar_rated_kw=30, solar_profile=[0.8] * 96),
        tasks=[
            TaskConfig(id="mapping", power_kw=6, duration_steps=20, priority=8, deadline_step=70),
            TaskConfig(id="materials", power_kw=7, duration_steps=18, priority=7, deadline_step=76),
            TaskConfig(id="communications", power_kw=8, duration_steps=16, priority=6, deadline_step=82),
        ],
        faults=[FaultConfig(id="early_array_failure", category="physical", target="solar", start_step=2, parameters={"factor": 0.0})],
    )
    scenarios["deadline_release_pressure"] = ScenarioConfig(
        id="deadline_release_pressure",
        description="Released workloads expose deadline pressure; the rolling-horizon MILP forecasts future releases and reserves capacity for upcoming high-priority tasks.",
        power=_benchmark_power(),
        battery=BatteryConfig(initial_kwh=20.1, safe_reserve_kwh=20),
        tasks=[
            TaskConfig(id="low_power_setup", power_kw=4, duration_steps=2, priority=3, release_step=0, deadline_step=2),
            TaskConfig(id="urgent_later", power_kw=6, duration_steps=2, priority=10, release_step=2, deadline_step=3, hard_deadline=True),
            TaskConfig(id="analysis_window", power_kw=4, duration_steps=1, priority=7, release_step=0, deadline_step=1),
        ],
    )
    scenarios["early_backup_unavailable"] = ScenarioConfig(
        id="early_backup_unavailable",
        description="Backup is unavailable from step 1 while long optional workloads remain; availability is reported by equipment telemetry.",
        tasks=[
            TaskConfig(id="survey", power_kw=7, duration_steps=18, priority=8, deadline_step=68),
            TaskConfig(id="experiment", power_kw=8, duration_steps=16, priority=7, deadline_step=74),
            TaskConfig(id="maintenance", power_kw=6, duration_steps=20, priority=6, deadline_step=80),
        ],
        faults=[FaultConfig(id="early_backup_offline", category="physical", target="backup", start_step=1, parameters={"available": False})],
    )
    return scenarios


def scenario_from_id(scenario_id: str) -> ScenarioConfig:
    scenarios = built_in_scenarios()
    if scenario_id not in scenarios:
        raise KeyError(f"unknown scenario '{scenario_id}'")
    return scenarios[scenario_id].model_copy(deep=True)


def load_scenario(path: str | Path) -> ScenarioConfig:
    """Load a scenario document, allowing a built-in ID-only JSON file."""
    raw: dict[str, Any] = json.loads(Path(path).read_text(encoding="utf-8"))
    if set(raw).issubset({"id", "description"}) and "id" in raw:
        return scenario_from_id(str(raw["id"]))
    return ScenarioConfig.model_validate(raw)
