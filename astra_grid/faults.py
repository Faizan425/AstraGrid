"""Fault schedules and physical capability derivation."""
from __future__ import annotations

from dataclasses import dataclass

from .config import FaultConfig


@dataclass(frozen=True)
class Capability:
    solar_factor: float = 1.0
    battery_capacity_factor: float = 1.0
    battery_discharge_factor: float = 1.0
    backup_available: bool = True
    pdu_available: bool = True
    pdu_factor: float = 1.0
    oxygen_factor: float = 1.0
    co2_factor: float = 1.0
    air_available: bool = True
    water_factor: float = 1.0
    heater_available: bool = True
    computer_available: bool = True


class FaultEngine:
    """Applies scheduled faults without allowing telemetry faults to alter physics."""
    def __init__(self, faults: list[FaultConfig]) -> None:
        self.faults = faults
        self._previous_active: set[str] = set()

    def active(self, step: int, category: str | None = None) -> list[FaultConfig]:
        return [f for f in self.faults if f.start_step <= step and (f.end_step is None or step < f.end_step) and (category is None or f.category == category)]

    def changes(self, step: int) -> list[tuple[str, FaultConfig]]:
        now = {fault.id for fault in self.active(step)}
        by_id = {fault.id: fault for fault in self.faults}
        entered = [("activated", by_id[fault_id]) for fault_id in sorted(now - self._previous_active)]
        cleared = [("cleared", by_id[fault_id]) for fault_id in sorted(self._previous_active - now)]
        self._previous_active = now
        return entered + cleared

    def capabilities(self, step: int) -> Capability:
        values: dict[str, object] = Capability().__dict__.copy()
        for fault in self.active(step, "physical"):
            p = fault.parameters
            factor = float(p.get("factor", 1.0))
            if fault.target == "solar": values["solar_factor"] = float(values["solar_factor"]) * factor
            elif fault.target == "battery":
                values["battery_capacity_factor"] = float(values["battery_capacity_factor"]) * float(p.get("capacity_factor", factor))
                values["battery_discharge_factor"] = float(values["battery_discharge_factor"]) * float(p.get("discharge_factor", factor))
            elif fault.target == "backup": values["backup_available"] = bool(p.get("available", False))
            elif fault.target == "pdu":
                values["pdu_available"] = bool(p.get("available", False))
                values["pdu_factor"] = float(values["pdu_factor"]) * factor
            elif fault.target == "oxygen_generator": values["oxygen_factor"] = float(values["oxygen_factor"]) * factor
            elif fault.target == "co2_removal": values["co2_factor"] = float(values["co2_factor"]) * factor
            elif fault.target == "air_circulation": values["air_available"] = bool(p.get("available", False))
            elif fault.target == "water_recovery": values["water_factor"] = float(values["water_factor"]) * factor
            elif fault.target == "heater": values["heater_available"] = bool(p.get("available", False))
            elif fault.target == "control_computer": values["computer_available"] = bool(p.get("available", False))
        return Capability(**values)  # type: ignore[arg-type]

    def telemetry_fault(self, step: int, channel: str) -> FaultConfig | None:
        return next((f for f in self.active(step, "telemetry") if f.target == channel), None)
