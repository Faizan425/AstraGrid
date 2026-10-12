"""Transparent, single-zone discrete-time habitat physics simulator."""
from __future__ import annotations

from dataclasses import dataclass

from .config import ScenarioConfig
from .faults import Capability, FaultEngine
from .models import Action, PhysicalState, Transition
from .tasks import MissionDebtLedger


@dataclass
class LoadBreakdown:
    critical_kw: float
    noncritical_kw: float
    total_kw: float


class HabitatSimulator:
    """Owns true state and performs resource/energy balances one fixed step at a time.

    It uses a prototype one-zone thermal model, not spacecraft-qualified physics.
    """
    def __init__(self, config: ScenarioConfig, faults: FaultEngine) -> None:
        self.config, self.faults = config, faults
        life, thermal, battery = config.life_support, config.thermal, config.battery
        self._state = PhysicalState(0, battery.initial_kwh, life.oxygen_initial_kg, life.co2_initial_kg, life.water_initial_kg, thermal.initial_temperature_c)
        self.last_solar_kw = 0.0
        self.last_load_kw = 0.0

    @property
    def state_for_evaluation(self) -> PhysicalState:
        """Evaluation-only copy. This is intentionally not supplied to controllers."""
        return self._state.copy()

    def capabilities(self) -> Capability: return self.faults.capabilities(self._state.step)

    def solar_kw(self, step: int | None = None) -> float:
        index = self._state.step if step is None else step
        profile = self.config.power.solar_profile
        return self.config.power.solar_rated_kw * profile[index % len(profile)] * self.faults.capabilities(index).solar_factor

    def load_breakdown(self, action: Action, capability: Capability | None = None) -> LoadBreakdown:
        cap = capability or self.capabilities()
        p, life, thermal = self.config.power, self.config.life_support, self.config.thermal
        critical = p.fixed_essential_kw + (p.control_computer_kw if cap.computer_available else 0)
        if action.air_circulation_on: critical += life.air_circulation_kw
        if action.oxygen_generator_on: critical += life.oxygen_generator_kw
        if action.co2_removal_on: critical += life.co2_removal_kw
        if action.water_recovery_on: critical += life.water_recovery_kw
        if action.heater_on: critical += thermal.heater_kw
        noncritical = sum(self._task_power(task_id) for task_id in action.task_ids)
        return LoadBreakdown(critical, noncritical, critical + noncritical)

    def _task_power(self, task_id: str) -> float:
        task = next((item for item in self.config.tasks if item.id == task_id), None)
        if task is None: raise ValueError(f"unknown task id '{task_id}'")
        return task.power_kw

    def advance(self, action: Action) -> Transition:
        """Execute a validated action; report deficits explicitly if a fault made it unsafe."""
        before = self._state.copy(); cap = self.capabilities(); cfg = self.config
        dt, b, p, life, thermal = cfg.dt_hours, cfg.battery, cfg.power, cfg.life_support, cfg.thermal
        usable_capacity = b.capacity_kwh * cap.battery_capacity_factor
        transition_error: str | None = None
        if self._state.battery_kwh > usable_capacity + 1e-9:
            lost = self._state.battery_kwh - usable_capacity
            self._state.battery_kwh = usable_capacity
            transition_error = f"BATTERY_CAPACITY_LOSS:{lost:.3f}kWh"
        if not cap.computer_available:
            transition_error = transition_error or "CONTROL_COMPUTER_UNAVAILABLE"
        loads = self.load_breakdown(action, cap)
        solar = self.solar_kw()
        backup = min(max(action.backup_kw, 0), p.backup_max_kw) if (p.backup_available and cap.backup_available) else 0.0
        source_kw = solar + backup
        # The PDU applies source-to-bus efficiency. Battery meets only remaining bus demand.
        bus_from_source = source_kw * p.pdu_efficiency * cap.pdu_factor if cap.pdu_available else 0.0
        need_kw = max(0.0, loads.total_kw - bus_from_source)
        available_discharge_kw = min(b.max_discharge_kw * cap.battery_discharge_factor, self._state.battery_kwh * b.discharge_efficiency / dt)
        discharge = min(need_kw, available_discharge_kw)
        self._state.battery_kwh -= discharge * dt / b.discharge_efficiency
        supplied = bus_from_source + discharge
        excess_source_bus_kw = max(0.0, bus_from_source - loads.total_kw)
        charge_input_kw = min(excess_source_bus_kw, b.max_charge_kw, (usable_capacity - self._state.battery_kwh) / (dt * b.charge_efficiency))
        self._state.battery_kwh += charge_input_kw * dt * b.charge_efficiency
        # A power deficit is split explicitly: life-support/control demand before optional work.
        critical_shortfall = max(0.0, loads.critical_kw - supplied)
        noncritical_shortfall = max(0.0, loads.total_kw - supplied - critical_shortfall)
        if not action.air_circulation_on or not cap.air_available:
            critical_shortfall = max(critical_shortfall, life.air_circulation_kw)
            transition_error = transition_error or "AIR_CIRCULATION_UNAVAILABLE"

        # Resource balances. Production only occurs when required equipment was powered/requested and healthy.
        requested_oxygen_output = life.oxygen_generation_kgph * cap.oxygen_factor if action.oxygen_generator_on and supplied >= loads.critical_kw else 0.0
        # Electrolysis cannot consume water that is not in the modelled usable inventory.
        oxygen_output = min(requested_oxygen_output, self._state.water_kg / (life.oxygen_water_kg_per_kg * dt)) if life.oxygen_water_kg_per_kg > 0 else requested_oxygen_output
        oxygen_water = oxygen_output * life.oxygen_water_kg_per_kg
        co2_removed = life.co2_removal_kgph * cap.co2_factor if action.co2_removal_on and supplied >= loads.critical_kw else 0.0
        recoverable_stream = life.water_demand_kgph * life.wastewater_return_fraction
        water_recovered = min(life.water_recovery_capacity_kgph * cap.water_factor, recoverable_stream) * life.water_recovery_efficiency if action.water_recovery_on and supplied >= loads.critical_kw else 0.0
        raw_oxygen = self._state.oxygen_kg + (oxygen_output - life.oxygen_demand_kgph) * dt
        raw_co2 = self._state.co2_kg + (life.co2_generation_kgph - co2_removed) * dt
        raw_water = self._state.water_kg + (water_recovered - life.water_demand_kgph - oxygen_water) * dt
        resource_shortfalls = {"oxygen_kg": max(0.0, -raw_oxygen), "water_kg": max(0.0, -raw_water), "co2_over_limit_kg": max(0.0, raw_co2 - life.co2_limit_kg)}
        # Physical inventories have a zero lower bound; any unmet demand is retained as a reported shortfall.
        self._state.oxygen_kg = max(0.0, raw_oxygen)
        self._state.co2_kg = max(0.0, raw_co2)
        self._state.water_kg = max(0.0, raw_water)

        # One-zone linear thermal balance. Electrical heat is counted once through recoverable fraction.
        heater_heat = thermal.heater_kw * thermal.heater_efficiency if action.heater_on and cap.heater_available and supplied >= loads.critical_kw else 0.0
        recoverable_heat = (loads.critical_kw + max(0.0, loads.noncritical_kw - noncritical_shortfall)) * thermal.recoverable_heat_fraction
        heat_loss = thermal.envelope_ua_kw_per_c * (self._state.temperature_c - thermal.external_temperature_c)
        self._state.temperature_c += dt / thermal.thermal_capacity_kwh_per_c * (recoverable_heat + heater_heat - heat_loss)
        self._state.cumulative_critical_shortfall_kwh += critical_shortfall * dt
        self._state.cumulative_noncritical_shortfall_kwh += noncritical_shortfall * dt
        self.last_solar_kw, self.last_load_kw = solar, loads.total_kw
        self._state.step += 1
        if self._state.oxygen_kg <= 0 or self._state.water_kg <= 0 or critical_shortfall > 0:
            self._state.terminal = self._state.terminal or (self._state.oxygen_kg <= 0 or self._state.water_kg <= 0)
        return Transition(before, self._state.copy(), solar, backup, charge_input_kw, discharge, loads.total_kw, supplied, critical_shortfall, noncritical_shortfall, resource_shortfalls, transition_error)
