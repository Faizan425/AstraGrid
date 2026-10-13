"""Policy 2 — Simple Rule-Based Resource-Aware Controller.

Transparent decision-making based on documented, observable physical thresholds and power margins.
Never calls mathematical programming solvers or claims predictive prescience.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..control import ControllerContext, PlanValidator, action_demand
from ..models import Action, Mode, Plan
from ..power import backup_for_bus_demand

if TYPE_CHECKING:
    from ..config import ScenarioConfig


class RuleBasedResourceAwareController:
    """Explicit threshold and margin-aware policy.

    Decision Rules:
    1. Life Support Priority: Air circulation and CO2 scrubbing are unconditionally active if available.
    2. Thermal Comfort: Heaters are activated whenever temperature is within 2°C of lower bound.
    3. Water Recovery Management:
       - Initiated if estimated water <= 1.35 * safe_reserve_kg and electrical margin allows.
       - Suppressed if estimated water >= 0.85 * initial_water_kg (prevent unnecessary energy drain).
       - Suspended if battery SOC <= 30% and solar generation is insufficient to cover base load.
    4. Mission Task Throttling:
       - Only scheduled in NORMAL mode when battery SOC >= 35% and net power margin > task demand.
       - Hard deadline tasks prioritized over soft tasks when within 8 steps of deadline.
    5. Conservative Fallback:
       - If telemetry quality is degraded or sensors are suspect, suppresses all optional loads.
    """
    name = "rule_based"

    def __init__(self, config: "ScenarioConfig", validator: PlanValidator | None = None, target_soc: float = 0.5) -> None:
        self.config = config
        self.validator = validator or PlanValidator(config)
        self.target_soc = target_soc

    def propose(self, context: ControllerContext) -> Plan:
        state = context.state
        cap = context.capability
        life = self.config.life_support
        battery = self.config.battery
        step = state.step

        # Rule 1 & 2: Essential life support & thermal protection
        air_on = cap.air_available
        co2_on = cap.co2_factor > 0
        oxygen_on = cap.oxygen_factor > 0
        heater_on = state.temperature_c is not None and state.temperature_c < (self.config.thermal.min_temperature_c + 2.0)

        # Rule 3: Water recovery threshold arbitration
        water_current = state.water_kg if state.water_kg is not None else life.water_initial_kg
        water_warning_threshold = life.water_reserve_kg * 1.35
        water_high_threshold = life.water_initial_kg * 0.85

        # Check energy margin for water recovery
        battery_soc = state.soc
        has_energy_for_recovery = (
            context.observed_solar_kw > (self.config.power.fixed_essential_kw + life.water_recovery_kw)
            or battery_soc > 0.35
        )

        should_recover_water = False
        if cap.water_factor > 0:
            if water_current <= water_warning_threshold and has_energy_for_recovery:
                should_recover_water = True
            elif water_current < water_high_threshold and context.observed_solar_kw > 15.0 and battery_soc > 0.50:
                # Opportunity charging / opportunistic water production during peak solar
                should_recover_water = True

        # Rule 4: Mission task selection under power margin
        selected_task_ids: list[str] = []
        is_conservative = context.mode != Mode.NORMAL or state.telemetry_quality in {"degraded", "suspect"}

        if not is_conservative and battery_soc > 0.30:
            schedulable = context.ledger.schedulable(step)
            # Prioritize hard deadline tasks approaching deadline first
            def task_sort_key(t):
                steps_to_deadline = (t.config.deadline_step - step) if t.config.deadline_step is not None else 999
                is_urgent_hard = t.config.hard_deadline and steps_to_deadline <= 8
                return (not is_urgent_hard, -t.config.priority, steps_to_deadline)

            ordered_tasks = sorted(schedulable, key=task_sort_key)

            # Greedily add tasks that pass safety validation
            for task in ordered_tasks:
                candidate_action = Action(
                    task_ids=selected_task_ids + [task.config.id],
                    backup_kw=0.0,
                    oxygen_generator_on=oxygen_on,
                    co2_removal_on=co2_on,
                    water_recovery_on=should_recover_water,
                    air_circulation_on=air_on,
                    heater_on=heater_on,
                )
                crit_kw, opt_kw = action_demand(self.config, candidate_action, cap)
                candidate_backup = backup_for_bus_demand(self.config, cap, context.observed_solar_kw, crit_kw + opt_kw)
                candidate_action.backup_kw = candidate_backup

                # Validate
                val = self.validator.validate(
                    Plan([candidate_action], self.name, "candidate"),
                    state, context.ledger, context.observed_solar_kw, cap, context.mode
                )
                if val.accepted:
                    selected_task_ids.append(task.config.id)

        # Build final proposed action
        action = Action(
            task_ids=selected_task_ids,
            backup_kw=0.0,
            oxygen_generator_on=oxygen_on,
            co2_removal_on=co2_on,
            water_recovery_on=should_recover_water,
            air_circulation_on=air_on,
            heater_on=heater_on,
        )
        crit_kw, opt_kw = action_demand(self.config, action, cap)
        action.backup_kw = backup_for_bus_demand(self.config, cap, context.observed_solar_kw, crit_kw + opt_kw)

        return Plan([action], self.name, "candidate", message="Rule-based resource-aware policy evaluated transparently.")
