"""Policy 1 — Fixed-Schedule Baseline Controller.

Follows a documented, predetermined timetable for ordinary activities and water recovery
without reacting to predictive forecasts or optimization algorithms.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..control import ControllerContext, PlanValidator, action_demand
from ..models import Action, Mode, Plan
from ..power import backup_for_bus_demand

if TYPE_CHECKING:
    from ..config import ScenarioConfig


class FixedScheduleController:
    """Predefined timetable baseline.

    Executes a documented schedule:
    - Water recovery runs on a fixed timetable cycle (every even 2-hour window: 4 steps on, 4 steps off).
    - Mission tasks are selected in simple release order as they become available.
    - Backup generator is called whenever static solar profile is dark.
    - Does not inspect future states or optimize knapsacks.
    """
    name = "fixed_schedule"

    def __init__(self, config: "ScenarioConfig", validator: PlanValidator | None = None, water_recovery_rate: float | None = None) -> None:
        self.config = config
        self.validator = validator or PlanValidator(config)
        self.water_recovery_rate = water_recovery_rate

    def propose(self, context: ControllerContext) -> Plan:
        step = context.state.step
        life = self.config.life_support
        power = self.config.power

        # Fixed timetable: water recovery runs in 4-step blocks every 8 steps (1 hour on, 1 hour off)
        # unless water equipment is physically unavailable.
        timetable_water_recovery_on = (step % 8) < 4 and context.capability.water_factor > 0

        # Oxygen and CO2 nominal cycles
        oxygen_on = context.capability.oxygen_factor > 0
        co2_on = context.capability.co2_factor > 0
        air_on = context.capability.air_available
        heater_on = context.state.temperature_c is not None and context.state.temperature_c < self.config.thermal.min_temperature_c + 1.0

        # Fixed task scheduling: pick next eligible released task in static order
        selected_task_ids: list[str] = []
        if context.mode != Mode.CONTROLLED_SHUTDOWN:
            for task in context.ledger.schedulable(step):
                # Only run one task at a time on static schedule
                selected_task_ids = [task.config.id]
                break

        base_action = Action(
            task_ids=selected_task_ids,
            backup_kw=0.0,
            oxygen_generator_on=oxygen_on,
            co2_removal_on=co2_on,
            water_recovery_on=timetable_water_recovery_on,
            air_circulation_on=air_on,
            heater_on=heater_on,
        )

        critical_kw, optional_kw = action_demand(self.config, base_action, context.capability)
        total_demand_kw = critical_kw + optional_kw

        # Backup generator requested based purely on nominal shortfall under static solar
        needed_backup = backup_for_bus_demand(self.config, context.capability, context.observed_solar_kw, total_demand_kw)
        base_action.backup_kw = needed_backup

        return Plan([base_action], self.name, "candidate", message="Static timetable schedule executed without adaptive feedback.")
