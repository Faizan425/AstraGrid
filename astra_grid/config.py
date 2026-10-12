"""Validated scenario configuration and bundled scenario factory."""
from __future__ import annotations

import json
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
        if not values or any(value < 0 for value in values):
            raise ValueError("solar profile must be a non-empty non-negative fraction list")
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
    deadline_step: int = Field(ge=0)
    hard_deadline: bool = False
    flexible: bool = True


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


def _tasks() -> list[TaskConfig]:
    return [
        TaskConfig(id="science_task_01", power_kw=3.5, duration_steps=4, priority=3, deadline_step=60),
        TaskConfig(id="science_task_02", power_kw=5.0, duration_steps=6, priority=7, deadline_step=78),
        TaskConfig(id="maintenance_task", power_kw=2.2, duration_steps=3, priority=8, deadline_step=48, hard_deadline=True),
    ]


def _base(scenario_id: str, description: str) -> ScenarioConfig:
    return ScenarioConfig(id=scenario_id, description=description, tasks=_tasks())


def built_in_scenarios() -> dict[str, ScenarioConfig]:
    """Return fresh configurations so runs cannot mutate shared scenario state."""
    scenarios: dict[str, ScenarioConfig] = {}
    scenarios["nominal_day"] = _base("nominal_day", "Cyclic illumination with healthy equipment.")
    scenarios["prolonged_solar_outage"] = _base("prolonged_solar_outage", "A long temporary loss of solar generation.")
    scenarios["prolonged_solar_outage"].faults = [FaultConfig(id="eclipse", category="physical", target="solar", start_step=20, end_step=65, parameters={"factor": 0.0})]
    scenarios["solar_array_damage"] = _base("solar_array_damage", "Persistent solar-array derating.")
    scenarios["solar_array_damage"].faults = [FaultConfig(id="array_damage", category="physical", target="solar", start_step=12, parameters={"factor": 0.35})]
    scenarios["battery_degradation_or_failure"] = _base("battery_degradation_or_failure", "Battery capacity and discharge degradation.")
    scenarios["battery_degradation_or_failure"].faults = [FaultConfig(id="battery_degradation", category="physical", target="battery", start_step=8, parameters={"capacity_factor": 0.45, "discharge_factor": 0.45})]
    scenarios["byzantine_soc_sensor"] = _base("byzantine_soc_sensor", "One plausible-looking SOC source is arbitrarily misleading.")
    scenarios["byzantine_soc_sensor"].faults = [FaultConfig(id="soc_byzantine", category="telemetry", target="soc_sensor_2", start_step=5, parameters={"value": 0.75})]
    scenarios["combined_fault"] = _base("combined_fault", "Solar damage combined with an outlying SOC sensor.")
    scenarios["combined_fault"].faults = [FaultConfig(id="array_damage", category="physical", target="solar", start_step=8, parameters={"factor": 0.4}), FaultConfig(id="soc_byzantine", category="telemetry", target="soc_sensor_2", start_step=5, end_step=18, parameters={"value": 0.75})]
    scenarios["hard_deadline_under_shortage"] = _base("hard_deadline_under_shortage", "A hard task cannot be completed under the available supply.")
    scenarios["hard_deadline_under_shortage"].tasks = [TaskConfig(id="urgent_drill", power_kw=18, duration_steps=5, priority=10, deadline_step=10, hard_deadline=True)]
    scenarios["hard_deadline_under_shortage"].faults = [FaultConfig(id="solar_out", category="physical", target="solar", start_step=0, end_step=30, parameters={"factor": 0.0}), FaultConfig(id="fission_out", category="physical", target="backup", start_step=0, parameters={"available": False})]
    scenarios["life_support_resource_shortage"] = _base("life_support_resource_shortage", "CO2 removal and oxygen generation lose capability.")
    scenarios["life_support_resource_shortage"].faults = [FaultConfig(id="oxygen_degraded", category="physical", target="oxygen_generator", start_step=10, parameters={"factor": 0.25}), FaultConfig(id="co2_degraded", category="physical", target="co2_removal", start_step=10, parameters={"factor": 0.15})]
    scenarios["water_recovery_failure"] = _base("water_recovery_failure", "Water recovery fails and usable water steadily declines.")
    scenarios["water_recovery_failure"].faults = [FaultConfig(id="recovery_failure", category="physical", target="water_recovery", start_step=12, parameters={"factor": 0.0})]
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
