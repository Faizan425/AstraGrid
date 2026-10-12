"""Independent baseline, rolling-horizon MILP selector, and final plan validator."""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

import numpy as np

from .config import ScenarioConfig
from .faults import Capability
from .models import Action, EstimatedState, Mode, Plan, TaskStatus, ValidationResult
from .power import backup_for_bus_demand, project_power_balance, source_bus_efficiency
from .tasks import MissionDebtLedger


_EPSILON = 1e-7


def action_demand(config: ScenarioConfig, action: Action, capability: Capability | None = None) -> tuple[float, float]:
    """Return requested critical and optional bus demand in kW for one action."""
    life, power, thermal = config.life_support, config.power, config.thermal
    critical = power.fixed_essential_kw + (power.control_computer_kw if capability is None or capability.computer_available else 0.0)
    critical += life.air_circulation_kw if action.air_circulation_on else 0.0
    critical += life.oxygen_generator_kw if action.oxygen_generator_on else 0.0
    critical += life.co2_removal_kw if action.co2_removal_on else 0.0
    critical += life.water_recovery_kw if action.water_recovery_on else 0.0
    critical += thermal.heater_kw if action.heater_on else 0.0
    task_power = {task.id: task.power_kw for task in config.tasks}
    return critical, sum(task_power.get(task_id, 0.0) for task_id in action.task_ids)


def _finite(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(float(value))


def _add(items: list[str], value: str) -> None:
    if value not in items:
        items.append(value)


class PlanValidator:
    """Final validation for controller proposals using only estimates.

    For multi-step rolling-horizon plans the validator checks only the first
    action (which is the one the runner will execute).  Advisory future
    actions are informational and are not individually validated.
    """
    def __init__(self, config: ScenarioConfig) -> None:
        self.config = config

    def _numeric_reasons(self, state: EstimatedState, solar_kw: float, cap: Capability) -> list[str]:
        cfg, b, p = self.config, self.config.battery, self.config.power
        reasons: list[str] = []
        values = {
            "ESTIMATED_BATTERY": state.battery_kwh, "ESTIMATED_SOC": state.soc,
            "OBSERVED_SOLAR": solar_kw, "BATTERY_CAPACITY": b.capacity_kwh,
            "BATTERY_RESERVE": b.safe_reserve_kwh, "BATTERY_MAX_DISCHARGE": b.max_discharge_kw,
            "BATTERY_MAX_CHARGE": b.max_charge_kw, "BATTERY_DISCHARGE_EFFICIENCY": b.discharge_efficiency,
            "BATTERY_CHARGE_EFFICIENCY": b.charge_efficiency, "PDU_EFFICIENCY": p.pdu_efficiency,
            "DT_HOURS": cfg.dt_hours,
        }
        for name, value in values.items():
            if not _finite(value): _add(reasons, f"NONFINITE_{name}")
        factors = {
            "SOLAR_FACTOR": cap.solar_factor, "BATTERY_CAPACITY_FACTOR": cap.battery_capacity_factor,
            "BATTERY_DISCHARGE_FACTOR": cap.battery_discharge_factor, "PDU_FACTOR": cap.pdu_factor,
            "OXYGEN_FACTOR": cap.oxygen_factor, "CO2_FACTOR": cap.co2_factor, "WATER_FACTOR": cap.water_factor,
        }
        for name, value in factors.items():
            if not _finite(value) or not 0 <= float(value) <= 1: _add(reasons, f"INVALID_{name}")
        if _finite(solar_kw) and solar_kw < 0: _add(reasons, "INVALID_OBSERVED_SOLAR")
        if _finite(state.soc) and not 0 <= state.soc <= 1: _add(reasons, "INVALID_ESTIMATED_SOC")
        if _finite(b.capacity_kwh) and b.capacity_kwh <= 0: _add(reasons, "INVALID_BATTERY_CAPACITY")
        if _finite(b.safe_reserve_kwh) and b.safe_reserve_kwh < 0: _add(reasons, "INVALID_BATTERY_RESERVE")
        if _finite(b.discharge_efficiency) and not 0 < b.discharge_efficiency <= 1: _add(reasons, "INVALID_BATTERY_DISCHARGE_EFFICIENCY")
        if _finite(b.charge_efficiency) and not 0 < b.charge_efficiency <= 1: _add(reasons, "INVALID_BATTERY_CHARGE_EFFICIENCY")
        if _finite(p.pdu_efficiency) and not 0 < p.pdu_efficiency <= 1: _add(reasons, "INVALID_PDU_EFFICIENCY")
        if _finite(cfg.dt_hours) and cfg.dt_hours <= 0: _add(reasons, "INVALID_DT_HOURS")
        return reasons

    def _resource_reasons(self, action: Action, state: EstimatedState, cap: Capability, deficit_kw: float, warnings: list[str]) -> list[str]:
        """One-step resource checks, matching simulator rates without cloning it."""
        cfg, life, thermal, dt = self.config, self.config.life_support, self.config.thermal, self.config.dt_hours
        reasons: list[str] = []
        readings = {"OXYGEN": state.oxygen_kg, "CO2": state.co2_kg, "WATER": state.water_kg, "TEMPERATURE": state.temperature_c}
        missing = [name for name, value in readings.items() if not _finite(value)]
        if missing:
            target = warnings if action.emergency_policy else reasons
            for name in missing: _add(target, f"MISSING_CRITICAL_TELEMETRY_{name}")
            return reasons
        assert state.oxygen_kg is not None and state.co2_kg is not None and state.water_kg is not None and state.temperature_c is not None
        if not cap.air_available: _add(warnings if action.emergency_policy else reasons, "AIR_CIRCULATION_UNAVAILABLE")
        if not cap.computer_available: _add(warnings if action.emergency_policy else reasons, "CONTROL_COMPUTER_UNAVAILABLE")
        if action.oxygen_generator_on and cap.oxygen_factor <= _EPSILON: _add(warnings, "OXYGEN_GENERATOR_UNAVAILABLE")
        if action.co2_removal_on and cap.co2_factor <= _EPSILON: _add(warnings, "CO2_REMOVAL_UNAVAILABLE")
        if action.water_recovery_on and cap.water_factor <= _EPSILON: _add(warnings, "WATER_RECOVERY_UNAVAILABLE")
        if action.heater_on and not cap.heater_available: _add(warnings, "HEATER_UNAVAILABLE")
        critical_supplied = deficit_kw <= _EPSILON
        oxygen_out = life.oxygen_generation_kgph * cap.oxygen_factor if action.oxygen_generator_on and critical_supplied else 0.0
        oxygen_out = min(oxygen_out, state.water_kg / (life.oxygen_water_kg_per_kg * dt)) if life.oxygen_water_kg_per_kg > 0 else oxygen_out
        oxygen_next = state.oxygen_kg + (oxygen_out - life.oxygen_demand_kgph) * dt
        co2_out = life.co2_removal_kgph * cap.co2_factor if action.co2_removal_on and critical_supplied else 0.0
        co2_next = state.co2_kg + (life.co2_generation_kgph - co2_out) * dt
        recovered = min(life.water_recovery_capacity_kgph * cap.water_factor, life.water_demand_kgph * life.wastewater_return_fraction) * life.water_recovery_efficiency if action.water_recovery_on and critical_supplied else 0.0
        water_next = state.water_kg + (recovered - life.water_demand_kgph - oxygen_out * life.oxygen_water_kg_per_kg) * dt
        critical, optional = action_demand(cfg, action, cap)
        heater_heat = thermal.heater_kw * thermal.heater_efficiency if action.heater_on and cap.heater_available and critical_supplied else 0.0
        recoverable_heat = (critical + optional) * thermal.recoverable_heat_fraction if critical_supplied else 0.0
        heat_loss = thermal.envelope_ua_kw_per_c * (state.temperature_c - thermal.external_temperature_c)
        temperature_next = state.temperature_c + dt / thermal.thermal_capacity_kwh_per_c * (recoverable_heat + heater_heat - heat_loss)
        checks = {
            "OXYGEN_RESERVE": oxygen_next < life.oxygen_reserve_kg - _EPSILON,
            "CO2_LIMIT": co2_next > life.co2_limit_kg + _EPSILON,
            "WATER_RESERVE": water_next < life.water_reserve_kg - _EPSILON,
            "THERMAL_ENVELOPE": temperature_next < thermal.min_temperature_c - _EPSILON or temperature_next > thermal.max_temperature_c + _EPSILON,
        }
        for reason, unsafe in checks.items():
            if unsafe: _add(warnings if action.emergency_policy else reasons, reason)
        if state.oxygen_kg <= life.oxygen_reserve_kg * 1.2 and not action.oxygen_generator_on: _add(reasons, "OXYGEN_PROTECTION")
        if state.co2_kg >= life.co2_limit_kg * .8 and not action.co2_removal_on: _add(reasons, "CO2_PROTECTION")
        if state.water_kg <= life.water_reserve_kg * 1.2 and not action.water_recovery_on: _add(reasons, "WATER_PROTECTION")
        return reasons

    def validate(self, plan: Plan, state: EstimatedState, ledger: MissionDebtLedger, observed_solar_kw: float, capability: Capability, mode: Mode) -> ValidationResult:
        if not plan.actions: return ValidationResult(False, ["EMPTY_PLAN"])
        action, cfg = plan.actions[0], self.config
        reasons, warnings = self._numeric_reasons(state, observed_solar_kw, capability), []
        if not _finite(action.backup_kw): _add(reasons, "NONFINITE_BACKUP_COMMAND")
        elif not 0 <= action.backup_kw <= cfg.power.backup_max_kw: _add(reasons, "BACKUP_LIMIT")
        if _finite(action.backup_kw) and action.backup_kw > 0 and (not cfg.power.backup_available or not capability.backup_available): _add(reasons, "BACKUP_UNAVAILABLE")
        if _finite(action.backup_kw) and action.backup_kw > 0 and not capability.pdu_available: _add(reasons, "PDU_UNAVAILABLE_FOR_SOURCE")
        if not action.air_circulation_on: _add(reasons, "AIR_CIRCULATION_DISABLED")
        if mode != Mode.NORMAL and action.task_ids: _add(reasons, "OPTIONAL_WORK_OUTSIDE_NORMAL_MODE")
        if len(action.task_ids) != len(set(action.task_ids)): _add(reasons, "DUPLICATE_TASK_ID")
        configured = {task.id: task for task in cfg.tasks}
        schedulable_ids = {task.config.id for task in ledger.schedulable(state.step)}
        for task_id in action.task_ids:
            task_cfg, task = configured.get(task_id), ledger.tasks.get(task_id)
            if task_cfg is None:
                _add(reasons, "UNKNOWN_TASK"); continue
            if task is None:
                _add(reasons, "TASK_MISSING_FROM_LEDGER"); continue
            if task.status not in {TaskStatus.PENDING, TaskStatus.RUNNING}:
                _add(reasons, "TASK_NOT_EXECUTABLE"); continue
            # A task cannot run before release. A soft task may run on its
            # deadline; a hard task must finish on it.
            if state.step < task_cfg.release_step: _add(reasons, "TASK_BEFORE_RELEASE")
            if state.step > task_cfg.deadline_step: _add(reasons, "TASK_AFTER_DEADLINE")
            if task_cfg.hard_deadline and state.step + task.remaining_steps - 1 > task_cfg.deadline_step: _add(reasons, "HARD_TASK_CANNOT_FINISH_BY_DEADLINE")
            if task_id not in schedulable_ids: _add(reasons, "TASK_INELIGIBLE")
            if not _finite(task_cfg.power_kw) or task_cfg.power_kw < 0: _add(reasons, "INVALID_TASK_POWER")
        if reasons: return ValidationResult(False, reasons, warnings=warnings)
        usable_capacity = cfg.battery.capacity_kwh * capability.battery_capacity_factor
        if state.battery_kwh < 0 or state.battery_kwh > usable_capacity + _EPSILON:
            return ValidationResult(False, ["INVALID_ESTIMATED_BATTERY"], warnings=warnings)
        critical, optional = action_demand(cfg, action, capability)
        if not _finite(critical) or not _finite(optional) or critical < 0 or optional < 0:
            return ValidationResult(False, ["INVALID_ACTION_DEMAND"], warnings=warnings)
        balance = project_power_balance(cfg, capability, battery_kwh=state.battery_kwh, solar_kw=observed_solar_kw, requested_backup_kw=action.backup_kw, demand_kw=critical + optional)
        if balance.supply_deficit_kw > _EPSILON: _add(warnings if action.emergency_policy else reasons, "SUPPLY_DEFICIT")
        if state.battery_kwh < cfg.battery.safe_reserve_kwh - _EPSILON: _add(warnings, "RESERVE_ALREADY_BREACHED")
        elif balance.battery_after_kwh < cfg.battery.safe_reserve_kwh - _EPSILON: _add(reasons, "BATTERY_RESERVE")
        reasons.extend(self._resource_reasons(action, state, capability, balance.supply_deficit_kw, warnings))
        return ValidationResult(not reasons, reasons, balance.battery_after_kwh, warnings, balance.supply_deficit_kw)


@dataclass
class ControllerContext:
    state: EstimatedState
    ledger: MissionDebtLedger
    observed_solar_kw: float
    capability: Capability
    mode: Mode


class _ControllerPower:
    """Shared physical calculation only; it does not share controller policy."""
    def _critical_action(self, context: ControllerContext) -> Action:
        critical, _ = action_demand(self.config, Action(), context.capability)
        return Action(backup_kw=backup_for_bus_demand(self.config, context.capability, context.observed_solar_kw, critical))


class BaselineController(_ControllerPower):
    """Independent greedy priority policy; it never calls the MILP."""
    name = "baseline"
    def __init__(self, config: ScenarioConfig, validator: PlanValidator) -> None: self.config, self.validator = config, validator

    def propose(self, context: ControllerContext) -> Plan:
        action = self._critical_action(context)
        if context.mode == Mode.NORMAL:
            candidates = sorted(context.ledger.schedulable(context.state.step), key=lambda item: (-item.config.priority, item.config.deadline_step))
            for task in candidates:
                candidate = Action(**{**action.__dict__, "task_ids": action.task_ids + [task.config.id]})
                if self.validator.validate(Plan([candidate], self.name, "candidate"), context.state, context.ledger, context.observed_solar_kw, context.capability, context.mode).accepted:
                    action = candidate
        return Plan([action], self.name, "candidate", message="Priority policy schedules only independently safe optional work.")


class MilpController(_ControllerPower):
    """Rolling-horizon binary task MILP inside a repeated closed control loop.

    The controller optimises task selection over a configurable planning
    horizon *H* using a time-indexed battery energy trajectory that tracks
    charge/discharge at every timestep.  The full *H*-step schedule is
    returned in the ``Plan``; the runner executes only ``actions[0]``.

    Key physical modelling decisions (all consistent with ``simulator.py``):

    * Solar and backup are generation-side sources that pass through the PDU
      before reaching the habitat bus.  Battery is on the bus side.
    * The optimizer forecasts backup at each step using ``backup_for_bus_demand``
      for the *total* demand (critical + tasks), so the backup command in the
      returned action matches what the MILP assumed was available.
    * Battery energy is tracked per-step with charging from solar surplus.
    * Tasks whose ``release_step`` falls inside the horizon are included but
      cannot be scheduled before their release.
    """
    name = "astragrid"

    def __init__(self, config: ScenarioConfig, validator: PlanValidator) -> None:
        self.config, self.validator = config, validator

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _horizon_candidates(ledger: MissionDebtLedger, current_step: int,
                            horizon_end_step: int) -> list:
        """Gather tasks eligible now *or* released within the horizon."""
        out = []
        for task in ledger.tasks.values():
            if task.status in {TaskStatus.COMPLETED, TaskStatus.MISSED,
                               TaskStatus.CANCELLED}:
                continue
            if task.remaining_steps <= 0:
                continue
            # Task window must overlap with the planning horizon.
            if task.config.deadline_step < current_step:
                continue
            if task.config.release_step > horizon_end_step:
                continue
            out.append(task)
        return out

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def propose(self, context: ControllerContext) -> Plan:
        action = self._critical_action(context)
        if context.mode != Mode.NORMAL:
            return Plan([action], self.name, "conservative",
                        message="Optional work is disabled outside normal mode.")
        numeric = self.validator._numeric_reasons(
            context.state, context.observed_solar_kw, context.capability)
        if numeric:
            return Plan([action], self.name, "invalid_input",
                        message="; ".join(numeric))

        cfg = self.config
        current_step = context.state.step
        cap = context.capability

        # --- Planning horizon -------------------------------------------------
        # Extend the horizon to cover the latest relevant deadline/release
        # among all live tasks, not just currently schedulable ones.
        all_live = self._horizon_candidates(
            context.ledger, current_step,
            current_step + cfg.controller.horizon_steps - 1)

        if not all_live:
            return Plan([action], self.name, "optimal", objective=0.0,
                        message="No schedulable or future mission work.")

        # Validate task inputs.
        if any(not _finite(t.config.power_kw) or t.config.power_kw < 0
               or not _finite(t.config.priority) for t in all_live):
            return Plan([action], self.name, "invalid_input",
                        message="Invalid task power or objective coefficient.")

        max_deadline = max(t.config.deadline_step for t in all_live)
        H = max(1, min(cfg.controller.horizon_steps,
                       cfg.duration_steps - current_step,
                       max_deadline - current_step + 1))
        n_tasks = len(all_live)

        # --- Physical parameters -----------------------------------------------
        critical, _ = action_demand(cfg, action, cap)
        profile = cfg.power.solar_profile
        pdu_eff = source_bus_efficiency(cfg, cap)
        backup_avail = (cfg.power.backup_available and cap.backup_available)
        backup_max = cfg.power.backup_max_kw if backup_avail else 0.0
        dt = cfg.dt_hours
        batt = cfg.battery
        charge_eff = max(batt.charge_efficiency, 1e-9)
        discharge_eff = max(batt.discharge_efficiency, 1e-9)
        max_discharge_kw = batt.max_discharge_kw * cap.battery_discharge_factor
        max_charge_kw = batt.max_charge_kw
        usable_capacity = batt.capacity_kwh * cap.battery_capacity_factor
        reserve = batt.safe_reserve_kwh
        battery_now = min(context.state.battery_kwh, usable_capacity)

        # Pre-compute per-step solar and source-bus power.
        solar_forecast = np.empty(H, dtype=float)
        bus_from_source = np.empty(H, dtype=float)
        for t in range(H):
            fs = current_step + t
            s = cfg.power.solar_rated_kw * profile[fs % len(profile)] * cap.solar_factor
            solar_forecast[t] = s
            # Backup dispatched for total demand is computed later per-task-set,
            # but the source bus *excluding backup for tasks* gives us the
            # critical-only bus contribution.
            bus_from_source[t] = s * pdu_eff

        # For the source margin we use backup dispatched for critical demand
        # only.  Task power is covered by the d (discharge) variable or any
        # remaining source margin after critical loads.
        critical_backup = np.empty(H, dtype=float)
        source_margin_no_tasks = np.empty(H, dtype=float)
        for t in range(H):
            fs = current_step + t
            cb = backup_for_bus_demand(cfg, cap, solar_forecast[t], critical)
            critical_backup[t] = cb
            bus = (solar_forecast[t] + cb) * pdu_eff
            source_margin_no_tasks[t] = max(0.0, bus - critical)

        try:
            from scipy.optimize import Bounds, LinearConstraint, milp
        except ImportError as exc:
            return Plan([action], self.name, "solver_unavailable",
                        message=f"SciPy MILP unavailable: {exc}")

        # --- Variable layout ---------------------------------------------------
        #   x[i*H+t]    – binary, task i active at horizon step t
        #   b[idx_b+t]  – continuous (kW), backup dispatch at step t
        #   d[idx_d+t]  – continuous (kW), battery discharge at step t
        #   c[idx_c+t]  – continuous (kW), battery charging at step t
        #   e[idx_e+t]  – continuous (kWh), battery energy after step t
        #   s[idx_s...] – continuous auxiliary start indicators for non-flexible tasks
        n_x = n_tasks * H
        idx_b = n_x
        idx_d = n_x + H
        idx_c = n_x + 2 * H
        idx_e = n_x + 3 * H

        # Identify non-flexible pending tasks for start-indicator constraints
        nf_pending_indices = [
            i for i, task in enumerate(all_live)
            if not task.config.flexible and task.status == TaskStatus.PENDING
        ]
        n_s = len(nf_pending_indices) * H
        idx_s = n_x + 4 * H

        n_vars = n_x + 4 * H + n_s

        # --- Objective: minimise cost (maximise priority-weighted task work) ---
        # A small per-step discount (1e-4) avoids procrastination.
        # Small penalties on backup and discharge encourage energy conservation
        # and prevent simultaneous charging and discharging.
        _HORIZON_DISCOUNT = 1e-4
        c_obj = np.zeros(n_vars)
        for i, task in enumerate(all_live):
            remaining = max(1, task.remaining_steps)
            value = (
                (task.config.priority + (20 if task.config.hard_deadline else 0))
                / remaining
            )
            for t in range(H):
                fs = current_step + t
                if task.config.release_step <= fs <= task.config.deadline_step:
                    c_obj[i * H + t] = -value * max(1e-6, 1.0 - t * _HORIZON_DISCOUNT)

        for t in range(H):
            c_obj[idx_b + t] = 1e-4   # penalise unneeded backup dispatch
            c_obj[idx_d + t] = 1e-5   # penalise battery discharge throughput

        # Prefer retaining higher state of charge at the end of the horizon
        c_obj[idx_e + H - 1] = -1e-5

        # --- Integrality -------------------------------------------------------
        integ = np.zeros(n_vars)
        integ[:n_x] = 1  # binary task variables

        # --- Bounds ------------------------------------------------------------
        lb = np.zeros(n_vars)
        ub = np.zeros(n_vars)

        # Task variable bounds: [0, 1] within release–deadline window, else 0
        for i, task in enumerate(all_live):
            for t in range(H):
                fs = current_step + t
                if task.config.release_step <= fs <= task.config.deadline_step:
                    ub[i * H + t] = 1.0

        # Backup generation bounds
        for t in range(H):
            ub[idx_b + t] = backup_max

        # Battery discharge rate bounds
        for t in range(H):
            ub[idx_d + t] = max_discharge_kw

        # Battery charge rate bounds
        for t in range(H):
            ub[idx_c + t] = max_charge_kw

        # Battery energy bounds: [reserve, usable_capacity]
        for t in range(H):
            lb[idx_e + t] = reserve
            ub[idx_e + t] = usable_capacity

        # Non-flexible start indicator bounds: [0, 1]
        for k in range(n_s):
            ub[idx_s + k] = 1.0

        # --- Constraints -------------------------------------------------------
        constraints_rows = []
        constraints_lhs = []
        constraints_rhs = []

        # (A) Task duration cap: Σ_t x_{i,t} ≤ remaining_i
        for i, task in enumerate(all_live):
            row = np.zeros(n_vars)
            row[i * H : (i + 1) * H] = 1.0
            constraints_rows.append(row)
            constraints_lhs.append(-np.inf)
            constraints_rhs.append(float(task.remaining_steps))

        # (B) Non-flexible task contiguous execution
        for i, task in enumerate(all_live):
            if not task.config.flexible:
                if task.status == TaskStatus.RUNNING:
                    # Already running: cannot restart once stopped (x_{i,t} ≤ x_{i,t-1})
                    for t in range(1, H):
                        row = np.zeros(n_vars)
                        row[i * H + t] = 1.0
                        row[i * H + t - 1] = -1.0
                        constraints_rows.append(row)
                        constraints_lhs.append(-np.inf)
                        constraints_rhs.append(0.0)
                elif task.status == TaskStatus.PENDING:
                    # Pending: can start at most once across the horizon
                    s_offset = idx_s + nf_pending_indices.index(i) * H
                    # s_{i,0} ≥ x_{i,0}  -->  x_{i,0} - s_{i,0} ≤ 0
                    row0 = np.zeros(n_vars)
                    row0[i * H] = 1.0
                    row0[s_offset] = -1.0
                    constraints_rows.append(row0)
                    constraints_lhs.append(-np.inf)
                    constraints_rhs.append(0.0)

                    for t in range(1, H):
                        # s_{i,t} ≥ x_{i,t} - x_{i,t-1}  -->  x_{i,t} - x_{i,t-1} - s_{i,t} ≤ 0
                        row = np.zeros(n_vars)
                        row[i * H + t] = 1.0
                        row[i * H + t - 1] = -1.0
                        row[s_offset + t] = -1.0
                        constraints_rows.append(row)
                        constraints_lhs.append(-np.inf)
                        constraints_rhs.append(0.0)

                    # Σ_t s_{i,t} ≤ 1
                    row_sum = np.zeros(n_vars)
                    row_sum[s_offset : s_offset + H] = 1.0
                    constraints_rows.append(row_sum)
                    constraints_lhs.append(-np.inf)
                    constraints_rhs.append(1.0)

        # (C) Per-step power balance on the habitat bus:
        #     critical + Σ_i power_i · x_{i,t} + c_t ≤ (solar_t + b_t)·pdu_eff + d_t
        #  => Σ_i power_i · x_{i,t} - pdu_eff · b_t - d_t + c_t ≤ solar_t · pdu_eff - critical
        for t in range(H):
            row = np.zeros(n_vars)
            for i, task in enumerate(all_live):
                row[i * H + t] = task.config.power_kw
            row[idx_b + t] = -pdu_eff
            row[idx_d + t] = -1.0
            row[idx_c + t] = 1.0
            constraints_rows.append(row)
            constraints_lhs.append(-np.inf)
            constraints_rhs.append(solar_forecast[t] * pdu_eff - critical)

        # (D) Time-indexed battery energy trajectory:
        #     t=0:  e_0 + d_0·(dt/η_d) - c_0·(dt·η_c) = battery_now
        #     t>0:  e_t - e_{t-1} + d_t·(dt/η_d) - c_t·(dt·η_c) = 0
        for t in range(H):
            row = np.zeros(n_vars)
            row[idx_e + t] = 1.0
            if t > 0:
                row[idx_e + t - 1] = -1.0
            row[idx_d + t] = dt / discharge_eff
            row[idx_c + t] = -dt * charge_eff
            constraints_rows.append(row)
            if t == 0:
                constraints_lhs.append(battery_now)
                constraints_rhs.append(battery_now)
            else:
                constraints_lhs.append(0.0)
                constraints_rhs.append(0.0)

        A_mat = np.array(constraints_rows)
        lhs_vec = np.array(constraints_lhs)
        rhs_vec = np.array(constraints_rhs)

        try:
            result = milp(
                c=c_obj, integrality=integ,
                bounds=Bounds(lb, ub),
                constraints=LinearConstraint(A_mat, lhs_vec, rhs_vec),
                options={"time_limit": cfg.controller.solver_time_limit_s},
            )
        except Exception as exc:
            return Plan([action], self.name, "solver_failure",
                        message=f"MILP exception: {exc!r}", solver_called=True)

        status = int(getattr(result, "status", 4))
        message = str(getattr(result, "message", "MILP returned no diagnostic."))
        x_raw = getattr(result, "x", None)

        if status == 2:
            return Plan([action], self.name, "infeasible",
                        message=message, solver_called=True)
        if status == 3:
            return Plan([action], self.name, "unbounded",
                        message=message, solver_called=True)
        if x_raw is None:
            return Plan([action], self.name,
                        "limit_no_candidate" if status == 1 else "solver_failure",
                        message=message, solver_called=True)

        x_vec = np.asarray(x_raw, dtype=float)
        if x_vec.shape[0] != n_vars or not np.isfinite(x_vec).all():
            return Plan([action], self.name, "solver_failure",
                        message=f"Invalid MILP result vector: {message}",
                        solver_called=True)

        # Validate task decision variables
        x_tasks = x_vec[:n_x]
        if (np.any(np.abs(x_tasks - np.rint(x_tasks)) > 1e-5)
                or np.any(x_tasks < -1e-5)
                or np.any(x_tasks > 1 + 1e-5)):
            return Plan([action], self.name, "solver_failure",
                        message=f"Non-integral task variables: {message}",
                        solver_called=True)

        # Validate continuous decision variables against bounds
        b_vars = x_vec[idx_b : idx_b + H]
        d_vars = x_vec[idx_d : idx_d + H]
        c_vars = x_vec[idx_c : idx_c + H]
        e_vars = x_vec[idx_e : idx_e + H]

        if np.any(b_vars < -1e-5) or np.any(b_vars > backup_max + 1e-5):
            return Plan([action], self.name, "solver_failure",
                        message=f"Backup dispatch variable out of bounds: {message}",
                        solver_called=True)

        if np.any(d_vars < -1e-5) or np.any(d_vars > max_discharge_kw + 1e-5):
            return Plan([action], self.name, "solver_failure",
                        message=f"Discharge variable out of bounds: {message}",
                        solver_called=True)

        if np.any(c_vars < -1e-5) or np.any(c_vars > max_charge_kw + 1e-5):
            return Plan([action], self.name, "solver_failure",
                        message=f"Charge variable out of bounds: {message}",
                        solver_called=True)

        if np.any(e_vars < reserve - 1e-4) or np.any(e_vars > usable_capacity + 1e-4):
            return Plan([action], self.name, "solver_failure",
                        message=f"Battery energy variable out of bounds: {message}",
                        solver_called=True)

        if status not in {0, 1}:
            return Plan([action], self.name, "solver_failure",
                        message=message, solver_called=True)

        # --- Build multi-step action schedule -----------------------------------
        actions: list[Action] = []
        for t in range(H):
            task_ids = [
                all_live[i].config.id
                for i in range(n_tasks)
                if x_tasks[i * H + t] >= 0.5
            ]
            step_backup = max(0.0, min(backup_max, round(float(b_vars[t]), 4)))
            if step_backup < 1e-5:
                step_backup = 0.0
            actions.append(Action(backup_kw=step_backup, task_ids=task_ids))

        objective = (-float(result.fun)
                     if _finite(getattr(result, "fun", None)) else None)
        return Plan(
            actions, self.name,
            "optimal" if status == 0 else "limit_candidate",
            objective=objective, message=message, solver_called=True,
        )
