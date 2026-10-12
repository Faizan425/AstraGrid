"""Step-by-step mission replay and timeline extraction for AstraGrid Resilience Lab.

Reconstructs the full 8-part per-timestep operational inspection from
actual recorded histories. Never reruns controllers or reconstructs past
decisions with future hindsight.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from astra_grid.runner import SimulationRun

from .diagnostics import DiagnosticRuleEngine
from .models import DiagnosticAnnotation, ReplayStep


class MissionReplayBuilder:
    """Builds step-by-step replay data and chronological timelines from a completed SimulationRun."""

    def __init__(self, run: SimulationRun, annotations: list[DiagnosticAnnotation] | None = None) -> None:
        self.run = run
        self.annotations = annotations or DiagnosticRuleEngine(run.config).annotate(run)

    def build_steps(self) -> list[ReplayStep]:
        """Extract the full 8-part replay step structure for every simulated timestep."""
        run = self.run
        total_steps = len(run.transitions)
        steps_data: list[ReplayStep] = []

        # Index annotations and events by step
        ann_by_step: dict[int, list[dict[str, Any]]] = {}
        for a in self.annotations:
            ann_by_step.setdefault(a.step, []).append(a.to_dict())

        events_by_step: dict[int, list[dict[str, Any]]] = {}
        for e in run.events:
            events_by_step.setdefault(e.step, []).append(e.to_dict())

        for step in range(total_steps):
            obs = run.telemetry_history[step] if step < len(run.telemetry_history) else None
            est = run.estimates[step] if step < len(run.estimates) else None
            cap_est = (
                run.capability_estimate_history[step]
                if step < len(run.capability_estimate_history)
                else None
            )
            ctrl = run.control_history[step] if step < len(run.control_history) else None
            audit = run.execution_audit[step] if step < len(run.execution_audit) else None
            trans = run.transitions[step] if step < len(run.transitions) else None

            # 1. Observation available to controller
            obs_dict = asdict(obs) if obs is not None else {"status": "unavailable"}

            # 2. Estimated state and capability
            est_state_dict = asdict(est) if est is not None else {"status": "unavailable"}
            est_cap_dict = cap_est if cap_est is not None else {"status": "unavailable"}

            # 3. Proposed plan
            proposed_dict: dict[str, Any] | None = None
            if ctrl is not None:
                proposed_dict = {
                    "controller": ctrl.get("proposed_controller"),
                    "status": ctrl.get("proposed_status"),
                    "solver_called": ctrl.get("solver_called"),
                }

            # 4. Validation result
            validation_dict: dict[str, Any] | None = None
            if ctrl is not None:
                validation_dict = {
                    "accepted": ctrl.get("validation_accepted"),
                    "reasons": ctrl.get("validation_reasons", []),
                    "final_accepted": ctrl.get("final_validation_accepted"),
                }

            # 5. Accepted plan or fallback
            accepted_dict: dict[str, Any] | None = None
            if ctrl is not None:
                accepted_dict = {
                    "executed_controller": ctrl.get("executed_controller"),
                    "fallback_attempted": ctrl.get("fallback_attempted"),
                    "emergency_policy": ctrl.get("emergency_policy"),
                }

            # 6. Action actually executed
            executed_dict: dict[str, Any] | None = None
            if audit is not None:
                executed_dict = {
                    "action": audit.get("action"),
                    "simulator_called": audit.get("simulator_called"),
                    "execution_succeeded": audit.get("execution_succeeded"),
                    "emergency_override": audit.get("emergency_override"),
                    "error_reason": audit.get("error_reason"),
                }

            # 7. Resulting physical state and transition
            trans_dict: dict[str, Any] | None = None
            state_after_dict: dict[str, Any] | None = None
            if trans is not None:
                trans_dict = {
                    "solar_kw": trans.solar_kw,
                    "backup_kw": trans.backup_kw,
                    "charge_kw": trans.battery_charge_kw,
                    "discharge_kw": trans.battery_discharge_kw,
                    "demand_kw": trans.demand_kw,
                    "supplied_kw": trans.supplied_kw,
                    "critical_shortfall_kw": trans.critical_shortfall_kw,
                    "noncritical_shortfall_kw": trans.noncritical_shortfall_kw,
                    "resource_shortfalls": trans.resource_shortfalls,
                    "transition_error": trans.transition_error,
                }
                state_after_dict = asdict(trans.after)

            # 8. Resource shortfalls, fault events, or safety warnings
            shortfalls_and_warnings: dict[str, Any] = {
                "critical_shortfall_kw": trans.critical_shortfall_kw if trans else 0.0,
                "noncritical_shortfall_kw": trans.noncritical_shortfall_kw if trans else 0.0,
                "resource_shortfalls": trans.resource_shortfalls if trans else {},
                "transition_error": trans.transition_error if trans else None,
                "is_battery_reserve_violation": (
                    trans.after.battery_kwh < run.config.battery.safe_reserve_kwh - 1e-9 if trans else False
                ),
                "is_temperature_violation": (
                    (trans.after.temperature_c < run.config.thermal.min_temperature_c - 1e-9 or
                     trans.after.temperature_c > run.config.thermal.max_temperature_c + 1e-9)
                    if trans else False
                ),
            }

            steps_data.append(ReplayStep(
                step=step,
                observation=obs_dict,
                estimated_state=est_state_dict,
                estimated_capability=est_cap_dict,
                proposed_plan=proposed_dict,
                validation_result=validation_dict,
                accepted_plan=accepted_dict,
                executed_action=executed_dict,
                physical_transition=trans_dict,
                physical_state_after=state_after_dict,
                shortfalls_and_warnings=shortfalls_and_warnings,
                events=events_by_step.get(step, []),
                annotations=ann_by_step.get(step, []),
            ))

        return steps_data

    def build_timeline(self) -> list[dict[str, Any]]:
        """Construct a unified chronological timeline distinguishing injected vs detected vs diagnostic events."""
        run = self.run
        timeline: list[dict[str, Any]] = []

        # 1. Injected physical & telemetry faults from scenario schedule (evaluation ground truth)
        for af in run.actual_fault_events:
            timeline.append({
                "step": af.get("step", 0),
                "type": "INJECTED_FAULT",
                "badge": "PHYSICAL FAULT" if af.get("category") == "physical" else "TELEMETRY FAULT",
                "severity": "CRITICAL" if af.get("category") == "physical" else "WARNING",
                "title": f"Injected fault: {af.get('id')} ({af.get('status')})",
                "description": f"Fault target: {af.get('target')}, category: {af.get('category')}",
                "provenance": "EVALUATION_GROUND_TRUTH",
            })

        # 2. Detected faults by controller / estimators
        for df in run.detected_faults:
            timeline.append({
                "step": df.get("step", 0),
                "type": "DETECTED_FAULT",
                "badge": "DETECTED SYMPTOM",
                "severity": "WARNING",
                "title": f"Controller symptom detected: {df.get('target')}",
                "description": f"Detection confidence: {df.get('confidence', 'medium')}",
                "provenance": "CONTROLLER_OBSERVATION",
            })

        # 3. Operational events (mode changes, fallback, emergency, completions, deficits)
        for e in run.events:
            badge = "EVENT"
            severity = "INFO"
            codes = e.reason_codes if hasattr(e, "reason_codes") else []
            if any("FALLBACK" in c for c in codes) or e.action == "use fallback":
                badge = "FALLBACK"
                severity = "WARNING"
            elif any("CRITICAL" in c or "DEFICIT" in c for c in codes):
                badge = "SAFETY DEFICIT"
                severity = "CRITICAL"
            elif any("SHUTDOWN" in c for c in codes) or e.action == "shutdown":
                badge = "SHUTDOWN"
                severity = "CRITICAL"
            elif e.action == "complete task":
                badge = "TASK COMPLETED"
                severity = "SUCCESS"
            elif any("MISSED" in c for c in codes) or e.action == "mark missed":
                badge = "DEADLINE MISSED"
                severity = "WARNING"
            elif getattr(e, "kind", "") == "mode":
                badge = "MODE CHANGE"
                severity = "INFO"

            timeline.append({
                "step": e.step,
                "type": "SIMULATION_EVENT",
                "badge": badge,
                "severity": severity,
                "title": f"{e.action.title()} ({e.event_id})",
                "description": e.message,
                "provenance": "OPERATIONAL_LOG",
            })

        # 4. Diagnostic annotations
        for a in self.annotations:
            timeline.append({
                "step": a.step,
                "type": "DIAGNOSTIC_ANNOTATION",
                "badge": f"DIAGNOSTIC: {a.rule_id}",
                "severity": a.severity,
                "title": a.rule_id,
                "description": a.explanation,
                "provenance": "POST_RUN_DIAGNOSTIC",
            })

        timeline.sort(key=lambda item: (item["step"], 0 if item["type"] == "INJECTED_FAULT" else 1))
        return timeline
