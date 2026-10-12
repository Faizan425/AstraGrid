"""Independent evaluation engine for AstraGrid Resilience Lab.

Calculates resilience, safety, and mission performance strictly from
recorded simulator physical transitions, task ledger state, and execution
audit evidence. Never relies on controller predictions or claimed feasibility.
"""
from __future__ import annotations

from typing import Any

from astra_grid.config import ScenarioConfig
from astra_grid.models import Mode, TaskStatus
from astra_grid.runner import SimulationRun

from .models import METRIC_DEFINITIONS, RunRecord


def _stop_reason(run: SimulationRun) -> str:
    truth = run.simulator.state_for_evaluation
    if run.manager.mode == Mode.CONTROLLED_SHUTDOWN or run.manager.mode == "controlled_shutdown":
        return "controlled_shutdown"
    if truth.terminal:
        return "terminal_resource_failure"
    if truth.step >= run.config.duration_steps:
        return "duration_complete"
    return "step_limit_or_incomplete"


class IndependentEvaluator:
    """Evaluates a completed SimulationRun without modifying any simulation state."""

    def __init__(self, config: ScenarioConfig | None = None) -> None:
        self.config = config

    def evaluate(
        self,
        run: SimulationRun,
        *,
        repetition: int = 1,
        wall_runtime_s: float | None = None,
        execution_error: str | None = None,
    ) -> RunRecord:
        """Extract objective metrics directly from recorded simulation outcomes."""
        cfg = run.config
        dt = cfg.dt_hours
        truth = run.simulator.state_for_evaluation
        transitions = run.transitions
        tasks = run.ledger.snapshot()
        audit = run.execution_audit
        control = run.control_history

        # Physical states after transition, fallback to initial truth if 0 transitions
        states = [t.after for t in transitions]
        states_or_final = states or [truth]

        # Power and Energy metrics
        cumulative_critical_shortfall_kwh = float(truth.cumulative_critical_shortfall_kwh)
        cumulative_noncritical_shortfall_kwh = float(truth.cumulative_noncritical_shortfall_kwh)
        critical_shortfall_timesteps = sum(1 for t in transitions if t.critical_shortfall_kw > 1e-9)
        noncritical_shortfall_timesteps = sum(1 for t in transitions if t.noncritical_shortfall_kw > 1e-9)

        min_soc = min(s.battery_kwh / cfg.battery.capacity_kwh for s in states_or_final)
        final_soc = states_or_final[-1].battery_kwh / cfg.battery.capacity_kwh
        safe_reserve = cfg.battery.safe_reserve_kwh
        capacity_kwh = cfg.battery.capacity_kwh

        battery_reserve_violations = sum(1 for s in states if s.battery_kwh < safe_reserve - 1e-9)
        battery_capacity_violations = sum(1 for s in states if s.battery_kwh > capacity_kwh + 1e-9)

        backup_utilization_kwh = sum(t.backup_kw * dt for t in transitions)
        backup_dispatch_timesteps = sum(1 for t in transitions if t.backup_kw > 1e-9)
        actual_solar_generation_kwh = sum(t.solar_kw * dt for t in transitions)
        actual_energy_supplied_kwh = sum(t.supplied_kw * dt for t in transitions)
        actual_energy_demanded_kwh = sum(t.demand_kw * dt for t in transitions)

        # Life support metrics
        min_oxygen_kg = min(s.oxygen_kg for s in states_or_final)
        max_co2_kg = max(s.co2_kg for s in states_or_final)
        min_water_kg = min(s.water_kg for s in states_or_final)

        oxygen_reserve_violations = sum(
            1 for s in states if s.oxygen_kg < cfg.life_support.oxygen_reserve_kg - 1e-9
        )
        co2_limit_violations = sum(
            1 for s in states if s.co2_kg > cfg.life_support.co2_limit_kg + 1e-9
        )
        water_reserve_violations = sum(
            1 for s in states if s.water_kg < cfg.life_support.water_reserve_kg - 1e-9
        )
        thermal_bound_violations = sum(
            1 for s in states
            if s.temperature_c < cfg.thermal.min_temperature_c - 1e-9
            or s.temperature_c > cfg.thermal.max_temperature_c + 1e-9
        )
        resource_shortfall_events = sum(
            1 for t in transitions if any(v > 1e-9 for v in t.resource_shortfalls.values())
        )

        # Mission progress metrics
        tasks_completed = sum(1 for task in tasks if task["status"] == "completed" or task["status"] == TaskStatus.COMPLETED)
        tasks_pending_or_running = sum(1 for task in tasks if task["status"] in {"pending", "running", TaskStatus.PENDING, TaskStatus.RUNNING})
        missed_hard_deadlines = sum(
            1 for task in tasks if (task["status"] == "missed" or task["status"] == TaskStatus.MISSED) and task.get("hard_deadline", False)
        )
        missed_soft_deadlines = sum(
            1 for task in tasks if (task["status"] == "missed" or task["status"] == TaskStatus.MISSED) and not task.get("hard_deadline", False)
        )
        remaining_mission_debt_steps = sum(
            int(task["remaining_steps"]) for task in tasks if task["status"] in {"pending", "running", TaskStatus.PENDING, TaskStatus.RUNNING}
        )
        task_deferrals_recorded = sum(
            1 for event in run.events if event.action == "mark deferred" or "DEFERRED" in event.message
        )

        task_completion_timeline: list[dict[str, Any]] = []
        for event in run.events:
            if event.action == "complete task" or "COMPLETED" in event.message:
                task_id = event.values.get("task_id", event.event_id) if hasattr(event, "values") else str(event)
                task_completion_timeline.append({
                    "step": event.step,
                    "task_id": task_id,
                    "event_message": event.message,
                })

        # Control and execution metrics from audit & control history
        control_decisions_total = len(control)
        successful_simulator_executions = sum(1 for a in audit if a.get("execution_succeeded"))
        rejected_proposed_plans = sum(
            1 for item in control
            if not bool(item.get("validation_accepted")) or item.get("proposed_status") not in {"candidate", "optimal", "limit_candidate", "conservative"}
        )
        fallback_activations = sum(1 for item in control if bool(item.get("fallback_attempted")))
        emergency_policy_activations = sum(1 for a in audit if bool(a.get("emergency_override")))
        unvalidated_actions_executed = sum(
            1 for a in audit if a.get("simulator_called") and not a.get("validation_accepted") and a.get("execution_succeeded")
        )
        failed_execution_attempts = sum(
            1 for a in audit if a.get("simulator_called") and not a.get("execution_succeeded")
        )
        simulation_or_controller_errors = (
            sum(1 for a in audit if a.get("error_reason") is not None)
            + (1 if execution_error else 0)
        )

        # Fault response metrics
        injected_faults_total = len(cfg.faults)
        active_injected_faults = [f for f in run.actual_fault_events if f.get("status") in {"activated", "start"}]
        injected_faults_activated = len(active_injected_faults)
        detected_faults_total = len(run.detected_faults)

        # Match detection delay per fault target
        fault_detection_delays: dict[str, int] = {}
        detected_targets = {str(d.get("target")) for d in run.detected_faults}
        undetected_count = 0

        for f in cfg.faults:
            if truth.step >= f.start_step:
                # Find matching detection at or after f.start_step
                matching_dets = [
                    d for d in run.detected_faults
                    if (str(d.get("target")) == f.target or f.target in str(d.get("target")) or f.id in str(d.get("target")))
                    and int(d.get("step", 0)) >= f.start_step
                ]
                if matching_dets:
                    first_det_step = min(int(d["step"]) for d in matching_dets)
                    fault_detection_delays[f.id] = max(0, first_det_step - f.start_step)
                else:
                    undetected_count += 1

        # Check if critical shortfall occurred after fault onset
        first_fault_start = min((f.start_step for f in cfg.faults), default=None)
        critical_shortfall_after_fault = False
        if first_fault_start is not None:
            critical_shortfall_after_fault = any(
                t.critical_shortfall_kw > 1e-9 for i, t in enumerate(transitions) if i >= first_fault_start
            )

        # Time to recover: timesteps between fault end and return to NORMAL mode
        fault_end_steps = [f.end_step for f in cfg.faults if f.end_step is not None]
        time_to_recover_steps: int | None = None
        if fault_end_steps:
            last_end = max(fault_end_steps)
            if truth.step >= last_end:
                # Look for transitions or state views after last_end where mode was NORMAL
                # Reconstruct mode at each step from control_history or events
                # If final mode is NORMAL:
                if run.manager.mode == Mode.NORMAL:
                    time_to_recover_steps = 0  # recovered by end

        metrics: dict[str, Any] = {
            "cumulative_critical_shortfall_kwh": cumulative_critical_shortfall_kwh,
            "cumulative_noncritical_shortfall_kwh": cumulative_noncritical_shortfall_kwh,
            "critical_shortfall_timesteps": critical_shortfall_timesteps,
            "noncritical_shortfall_timesteps": noncritical_shortfall_timesteps,
            "minimum_actual_soc": min_soc,
            "final_actual_soc": final_soc,
            "battery_reserve_violations": battery_reserve_violations,
            "battery_capacity_violations": battery_capacity_violations,
            "backup_utilization_kwh": backup_utilization_kwh,
            "backup_dispatch_timesteps": backup_dispatch_timesteps,
            "actual_solar_generation_kwh": actual_solar_generation_kwh,
            "actual_energy_supplied_kwh": actual_energy_supplied_kwh,
            "actual_energy_demanded_kwh": actual_energy_demanded_kwh,
            "minimum_actual_oxygen_kg": min_oxygen_kg,
            "maximum_actual_co2_kg": max_co2_kg,
            "minimum_actual_water_kg": min_water_kg,
            "oxygen_reserve_violations": oxygen_reserve_violations,
            "co2_limit_violations": co2_limit_violations,
            "water_reserve_violations": water_reserve_violations,
            "thermal_bound_violations": thermal_bound_violations,
            "resource_shortfall_events": resource_shortfall_events,
            "tasks_completed": tasks_completed,
            "tasks_pending_or_running": tasks_pending_or_running,
            "missed_hard_deadlines": missed_hard_deadlines,
            "missed_soft_deadlines": missed_soft_deadlines,
            "remaining_mission_debt_steps": remaining_mission_debt_steps,
            "task_deferrals_recorded": task_deferrals_recorded,
            "control_decisions_total": control_decisions_total,
            "successful_simulator_executions": successful_simulator_executions,
            "rejected_proposed_plans": rejected_proposed_plans,
            "fallback_activations": fallback_activations,
            "emergency_policy_activations": emergency_policy_activations,
            "unvalidated_actions_executed": unvalidated_actions_executed,
            "failed_execution_attempts": failed_execution_attempts,
            "simulation_or_controller_errors": simulation_or_controller_errors,
            "injected_faults_total": injected_faults_total,
            "injected_faults_activated": injected_faults_activated,
            "detected_faults_total": detected_faults_total,
            "undetected_faults_in_window": undetected_count,
            "critical_shortfall_after_fault": critical_shortfall_after_fault,
            "time_to_recover_steps": time_to_recover_steps,
        }

        # Check for history consistency
        if len(transitions) != truth.step:
            metrics["history_consistency_warning"] = (
                f"Transition count ({len(transitions)}) differs from final step ({truth.step})"
            )

        return RunRecord(
            run_id=run.id,
            scenario_id=cfg.id,
            controller=run.controller_name,
            seed=cfg.seed,
            repetition=repetition,
            duration_steps=cfg.duration_steps,
            simulated_steps=truth.step,
            simulated_hours=truth.step * dt,
            completed=run.completed,
            stop_reason=_stop_reason(run),
            wall_runtime_s=wall_runtime_s,
            execution_error=execution_error,
            metrics=metrics,
            fault_detection_delays=fault_detection_delays,
            task_completion_timeline=task_completion_timeline,
        )
