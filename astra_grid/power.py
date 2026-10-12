"""Shared one-step electrical balance used by planning and simulation.

Solar and backup are generation-side sources: they pass through the PDU before
reaching the habitat bus.  The prototype battery is connected on the bus side,
so it covers the remaining bus demand and is not additionally derated by the
PDU.  This is deliberately the same convention used by ``HabitatSimulator``.
"""
from __future__ import annotations

from dataclasses import dataclass

from .config import ScenarioConfig
from .faults import Capability


@dataclass(frozen=True)
class PowerBalance:
    """Predicted electrical transition for one timestep (kW and kWh)."""

    solar_kw: float
    backup_kw: float
    bus_from_source_kw: float
    discharge_kw: float
    charge_kw: float
    supplied_kw: float
    supply_deficit_kw: float
    battery_after_kwh: float
    usable_capacity_kwh: float
    available_discharge_kw: float


def source_bus_efficiency(config: ScenarioConfig, capability: Capability) -> float:
    """Return generation-to-bus efficiency, or zero when the PDU is offline."""
    return config.power.pdu_efficiency * capability.pdu_factor if capability.pdu_available else 0.0


def backup_for_bus_demand(config: ScenarioConfig, capability: Capability, solar_kw: float, demand_kw: float) -> float:
    """Generation-side backup required for a bus demand, respecting availability."""
    efficiency = source_bus_efficiency(config, capability)
    if not capability.backup_available or not config.power.backup_available or efficiency <= 0:
        return 0.0
    return min(config.power.backup_max_kw, max(0.0, demand_kw / efficiency - solar_kw))


def project_power_balance(
    config: ScenarioConfig,
    capability: Capability,
    *,
    battery_kwh: float,
    solar_kw: float,
    requested_backup_kw: float,
    demand_kw: float,
) -> PowerBalance:
    """Project the simulator's electrical equations without mutating state.

    Callers validate values before invoking this function.  An unavailable
    source delivers zero here; the validator separately rejects a command that
    requested it, while this function remains useful for reporting the actual
    physical consequence.
    """
    battery, power, dt = config.battery, config.power, config.dt_hours
    usable_capacity = battery.capacity_kwh * capability.battery_capacity_factor
    backup_kw = requested_backup_kw if power.backup_available and capability.backup_available else 0.0
    bus_from_source = (solar_kw + backup_kw) * source_bus_efficiency(config, capability)
    need_kw = max(0.0, demand_kw - bus_from_source)
    available_discharge_kw = min(
        battery.max_discharge_kw * capability.battery_discharge_factor,
        battery_kwh * battery.discharge_efficiency / dt,
    )
    discharge_kw = min(need_kw, available_discharge_kw)
    after_discharge = battery_kwh - discharge_kw * dt / battery.discharge_efficiency
    excess_source_bus_kw = max(0.0, bus_from_source - demand_kw)
    charge_kw = min(
        excess_source_bus_kw,
        battery.max_charge_kw,
        max(0.0, usable_capacity - after_discharge) / (dt * battery.charge_efficiency),
    )
    battery_after = after_discharge + charge_kw * dt * battery.charge_efficiency
    supplied_kw = bus_from_source + discharge_kw
    return PowerBalance(
        solar_kw=solar_kw,
        backup_kw=backup_kw,
        bus_from_source_kw=bus_from_source,
        discharge_kw=discharge_kw,
        charge_kw=charge_kw,
        supplied_kw=supplied_kw,
        supply_deficit_kw=max(0.0, demand_kw - supplied_kw),
        battery_after_kwh=battery_after,
        usable_capacity_kwh=usable_capacity,
        available_discharge_kw=available_discharge_kw,
    )
