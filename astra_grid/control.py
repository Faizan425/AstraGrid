"""Independent baseline, MILP selector, and candidate-plan validator."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .config import ScenarioConfig
from .faults import Capability
from .models import Action, EstimatedState, Mode, Plan, ValidationResult
from .tasks import MissionDebtLedger


def action_demand(config: ScenarioConfig, action: Action) -> tuple[float, float]:
    life, power, thermal = config.life_support, config.power, config.thermal
    critical = power.fixed_essential_kw + power.control_computer_kw
    critical += life.air_circulation_kw if action.air_circulation_on else 0
    critical += life.oxygen_generator_kw if action.oxygen_generator_on else 0
    critical += life.co2_removal_kw if action.co2_removal_on else 0
    critical += life.water_recovery_kw if action.water_recovery_on else 0
    critical += thermal.heater_kw if action.heater_on else 0
    task_power = {task.id: task.power_kw for task in config.tasks}
    return critical, sum(task_power.get(task_id, 0) for task_id in action.task_ids)


class PlanValidator:
    """Checks plans from either controller without consulting simulator truth."""
    def __init__(self, config: ScenarioConfig) -> None: self.config = config

    def validate(self, plan: Plan, state: EstimatedState, ledger: MissionDebtLedger, observed_solar_kw: float, capability: Capability, mode: Mode) -> ValidationResult:
        if not plan.actions: return ValidationResult(False, ["EMPTY_PLAN"])
        action, cfg = plan.actions[0], self.config
        reasons: list[str] = []
        valid_ids = {task.id for task in cfg.tasks}
        if any(task_id not in valid_ids for task_id in action.task_ids): reasons.append("UNKNOWN_TASK")
        if mode in {Mode.EMERGENCY, Mode.CONTROLLED_SHUTDOWN} and action.task_ids: reasons.append("OPTIONAL_WORK_IN_EMERGENCY")
        for task_id in action.task_ids:
            task = ledger.tasks[task_id]
            if state.step > task.config.deadline_step: reasons.append("TASK_AFTER_DEADLINE")
            if task.config.hard_deadline and state.step + task.remaining_steps - 1 > task.config.deadline_step: reasons.append("HARD_TASK_CANNOT_FINISH_BY_DEADLINE")
        if not (0 <= action.backup_kw <= cfg.power.backup_max_kw): reasons.append("BACKUP_LIMIT")
        if not action.air_circulation_on: reasons.append("AIR_CIRCULATION_DISABLED")
        if state.oxygen_kg is not None and state.oxygen_kg <= cfg.life_support.oxygen_reserve_kg * 1.2 and not action.oxygen_generator_on: reasons.append("OXYGEN_PROTECTION")
        if state.co2_kg is not None and state.co2_kg >= cfg.life_support.co2_limit_kg * .8 and not action.co2_removal_on: reasons.append("CO2_PROTECTION")
        if state.water_kg is not None and state.water_kg <= cfg.life_support.water_reserve_kg * 1.2 and not action.water_recovery_on: reasons.append("WATER_PROTECTION")
        if state.battery_kwh < 0 or state.battery_kwh > cfg.battery.capacity_kwh + 1e-6: reasons.append("INVALID_ESTIMATED_BATTERY")
        critical, optional = action_demand(cfg, action)
        bus_source = (observed_solar_kw + (action.backup_kw if capability.backup_available else 0.0)) * cfg.power.pdu_efficiency * capability.pdu_factor if capability.pdu_available else 0.0
        shortfall = max(0.0, critical + optional - bus_source)
        discharge_limit = min(cfg.battery.max_discharge_kw * capability.battery_discharge_factor, max(0.0, state.battery_kwh - cfg.battery.safe_reserve_kwh) * cfg.battery.discharge_efficiency / cfg.dt_hours)
        projected = state.battery_kwh - min(shortfall, discharge_limit) * cfg.dt_hours / cfg.battery.discharge_efficiency
        if shortfall > discharge_limit + 1e-7: reasons.append("SUPPLY_DEFICIT")
        if projected < cfg.battery.safe_reserve_kwh - 1e-7: reasons.append("BATTERY_RESERVE")
        return ValidationResult(not reasons, reasons, projected)


@dataclass
class ControllerContext:
    state: EstimatedState
    ledger: MissionDebtLedger
    observed_solar_kw: float
    capability: Capability
    mode: Mode


class BaselineController:
    """Independent priority-first policy; no solver or optimizer methods are used."""
    name = "baseline"
    def __init__(self, config: ScenarioConfig, validator: PlanValidator) -> None: self.config, self.validator = config, validator

    def propose(self, context: ControllerContext) -> Plan:
        action = Action(backup_kw=self._backup_for_critical(context))
        if context.mode == Mode.NORMAL:
            candidates = sorted(context.ledger.schedulable(context.state.step), key=lambda item: (-item.config.priority, item.config.deadline_step))
            for task in candidates:
                candidate = Action(**{**action.__dict__, "task_ids": action.task_ids + [task.config.id]})
                plan = Plan([candidate], self.name, "candidate")
                if self.validator.validate(plan, context.state, context.ledger, context.observed_solar_kw, context.capability, context.mode).accepted:
                    action = candidate
        return Plan([action], self.name, "candidate", message="Priority policy schedules only independently safe optional work.")

    def _backup_for_critical(self, context: ControllerContext) -> float:
        critical, _ = action_demand(self.config, Action())
        source_need = critical / max(0.01, self.config.power.pdu_efficiency * context.capability.pdu_factor) - context.observed_solar_kw
        return min(self.config.power.backup_max_kw, max(0.0, source_need)) if context.capability.backup_available else 0.0


class MilpController:
    """One-step binary task MILP within a rolling closed loop; each step replans."""
    name = "astragrid"
    def __init__(self, config: ScenarioConfig, validator: PlanValidator) -> None: self.config, self.validator = config, validator

    def propose(self, context: ControllerContext) -> Plan:
        baseline = BaselineController(self.config, self.validator)
        base = baseline.propose(ControllerContext(context.state, context.ledger, context.observed_solar_kw, context.capability, context.mode))
        action = Action(**{**base.actions[0].__dict__, "task_ids": []})
        if context.mode != Mode.NORMAL:
            return Plan([action], self.name, "conservative", message="Optional work removed in degraded/emergency mode.")
        candidates = context.ledger.schedulable(context.state.step)
        if not candidates: return Plan([action], self.name, "optimal", objective=0.0, message="No schedulable mission work.")
        critical, _ = action_demand(self.config, action)
        source = (context.observed_solar_kw + action.backup_kw) * self.config.power.pdu_efficiency * context.capability.pdu_factor
        battery_rate = min(self.config.battery.max_discharge_kw * context.capability.battery_discharge_factor, max(0, context.state.battery_kwh - self.config.battery.safe_reserve_kwh) * self.config.battery.discharge_efficiency / self.config.dt_hours)
        margin = max(0.0, source + battery_rate - critical)
        try:
            from scipy.optimize import Bounds, LinearConstraint, milp
            powers = np.array([task.config.power_kw for task in candidates])
            values = np.array([task.config.priority + (20 if task.config.hard_deadline else 0) for task in candidates], dtype=float)
            result = milp(c=-values, integrality=np.ones(len(candidates)), bounds=Bounds(0, 1), constraints=LinearConstraint(powers[None, :], -np.inf, np.array([margin])), options={"time_limit": self.config.controller.solver_time_limit_s})
            if result.x is None:
                return Plan([action], self.name, "infeasible", message=f"MILP produced no candidate: {result.message}")
            selected = [task.config.id for task, choice in zip(candidates, result.x) if choice >= .5]
            action.task_ids = selected
            return Plan([action], self.name, "optimal" if result.success else "time_limited_candidate", objective=float(-result.fun) if result.fun is not None else None, message=str(result.message))
        except Exception as exc:  # explicit status lets orchestration select validated fallback
            return Plan([action], self.name, "solver_error", message=f"MILP unavailable/error: {exc}")
