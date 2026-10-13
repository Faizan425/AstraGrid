"""Shared serialisable domain models. Units are kW, kWh, kg, °C, and hours."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class Mode(StrEnum):
    NORMAL = "normal"
    DEGRADED = "degraded"
    EMERGENCY = "emergency"
    CONTROLLED_SHUTDOWN = "controlled_shutdown"


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    MISSED = "missed"
    CANCELLED = "cancelled"


@dataclass
class PhysicalState:
    """Simulator-owned state; controllers must never receive this object."""
    step: int
    battery_kwh: float
    oxygen_kg: float
    co2_kg: float
    water_kg: float
    temperature_c: float
    cumulative_critical_shortfall_kwh: float = 0.0
    cumulative_noncritical_shortfall_kwh: float = 0.0
    terminal: bool = False

    def copy(self) -> "PhysicalState":
        return PhysicalState(**asdict(self))


@dataclass
class Observation:
    step: int
    solar_kw: float
    load_kw: float
    temperature_c: float | None
    oxygen_kg: float | None
    co2_kg: float | None
    water_kg: float | None
    soc_sensors: dict[str, float | None]
    # Equipment health is an observed telemetry channel, not direct simulator
    # truth. Values can be missing or corrupted by telemetry faults.
    equipment_status: dict[str, float | bool | None] = field(default_factory=dict)


@dataclass
class EstimatedState:
    step: int
    battery_kwh: float
    soc: float
    oxygen_kg: float | None
    co2_kg: float | None
    water_kg: float | None
    temperature_c: float | None
    telemetry_quality: str
    suspect_sensors: list[str] = field(default_factory=list)


@dataclass
class Action:
    """One approved control step. Supply is resolved by the physical simulator."""
    task_ids: list[str] = field(default_factory=list)
    backup_kw: float = 0.0
    oxygen_generator_on: bool = True
    co2_removal_on: bool = True
    water_recovery_on: bool = True
    air_circulation_on: bool = True
    heater_on: bool = False
    emergency_policy: bool = False


@dataclass
class Plan:
    actions: list[Action]
    controller: str
    status: str
    objective: float | None = None
    message: str = ""
    # Kept separately from status: an "optimal" empty-work plan need not have
    # invoked SciPy, while a time-limited plan may have.
    solver_called: bool = False


@dataclass
class ValidationResult:
    accepted: bool
    reasons: list[str]
    projected_battery_kwh: float | None = None
    warnings: list[str] = field(default_factory=list)
    supply_deficit_kw: float = 0.0


@dataclass
class Event:
    event_id: str
    step: int
    kind: str
    action: str
    reason_codes: list[str]
    message: str
    values: dict[str, Any] = field(default_factory=dict)
    mode: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Transition:
    before: PhysicalState
    after: PhysicalState
    solar_kw: float
    backup_kw: float
    battery_charge_kw: float
    battery_discharge_kw: float
    demand_kw: float
    supplied_kw: float
    critical_shortfall_kw: float
    noncritical_shortfall_kw: float
    resource_shortfalls: dict[str, float]
    transition_error: str | None = None
    water_recovered_kg: float = 0.0
    water_demand_kg: float = 0.0
    water_recovery_power_kw: float = 0.0
