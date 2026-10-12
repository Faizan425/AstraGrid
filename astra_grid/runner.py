"""Closed-loop orchestration: observe, estimate, plan, validate, execute, account."""
from __future__ import annotations

from dataclasses import asdict
import json
from uuid import uuid4

from .config import ScenarioConfig
from .control import BaselineController, ControllerContext, MilpController, PlanValidator
from .power import backup_for_bus_demand
from .faults import Capability, FaultEngine
from .models import Action, Event, Mode, Observation, Plan, Transition, ValidationResult
from .safety import FaultManager, SurvivalEstimator
from .simulator import HabitatSimulator
from .tasks import MissionDebtLedger
from .telemetry import CapabilityEstimator, StateEstimator, TelemetryGenerator


class SimulationRun:
    """A single in-memory run. Control logic only receives telemetry-derived estimates."""
    def __init__(self, config: ScenarioConfig, controller_name: str = "astragrid", *, trace_telemetry: bool = False, trace_label: str | None = None) -> None:
        if controller_name not in {"astragrid", "baseline"}: raise ValueError("controller must be astragrid or baseline")
        self.id, self.config, self.controller_name = str(uuid4()), config, controller_name
        self.trace_telemetry = trace_telemetry
        self.trace_label = trace_label or controller_name
        self.faults = FaultEngine(config.faults)
        self.simulator = HabitatSimulator(config, self.faults)
        self.telemetry = TelemetryGenerator(config, self.faults)
        self.estimator, self.capability_estimator, self.ledger = StateEstimator(config), CapabilityEstimator(), MissionDebtLedger(config.tasks)
        self.validator, self.manager, self.survival_estimator = PlanValidator(config), FaultManager(), SurvivalEstimator(config)
        self.controller = MilpController(config, self.validator) if controller_name == "astragrid" else BaselineController(config, self.validator)
        self.events: list[Event] = []
        self.actual_fault_events: list[dict[str, object]] = []
        self.detected_faults: list[dict[str, object]] = []
        self._reported_detections: set[str] = set()
        self.transitions: list[Transition] = []
        self.last_observation = None
        self.telemetry_history: list[Observation] = []
        self.last_estimate = None
        self.estimates = []
        self.last_horizon = None
        self.last_proposed: Plan | None = None
        self.last_validation: ValidationResult | None = None
        self.last_accepted: Plan | None = None
        self.last_executed: Action | None = None
        # Benchmarking reads this immutable-per-step audit trail instead of
        # inferring execution from the final plan alone.
        self.control_history: list[dict[str, object]] = []
        self.execution_audit: list[dict[str, object]] = []
        self.last_capability_estimate = Capability()
        self.capability_estimate_history: list[dict[str, object]] = []

    @property
    def completed(self) -> bool:
        return self.simulator.state_for_evaluation.step >= self.config.duration_steps or self.simulator.state_for_evaluation.terminal or self.manager.mode == Mode.CONTROLLED_SHUTDOWN

    def _record(self, event: Event | None) -> None:
        if event: self.events.append(event)

    def _record_control_decision(self, step: int, proposed: Plan, proposed_validation: ValidationResult, final_validation: ValidationResult, accepted: Plan | None, fallback_attempted: bool, emergency_used: bool) -> None:
        self.control_history.append({
            "step": step,
            "proposed_controller": proposed.controller,
            "proposed_status": proposed.status,
            "solver_called": proposed.solver_called,
            "validation_accepted": proposed_validation.accepted,
            "validation_reasons": list(proposed_validation.reasons),
            "final_validation_accepted": final_validation.accepted,
            "fallback_attempted": fallback_attempted,
            "executed_controller": accepted.controller if accepted else None,
            "emergency_policy": emergency_used,
            "action_executed": accepted is not None,
        })

    def _infer_capability(self, observation: Observation, step: int) -> Capability:
        """Infer controller-visible capability from telemetry, never simulator truth.

        Equipment status monitors provide the primary evidence.  Solar output
        is an independent meter: when illumination is known to be non-zero, a
        lower measured output can only reduce the already-observed solar
        capability.  Missing/malformed status is deliberately conservative in
        :class:`CapabilityEstimator` and is not repaired from hidden physics.
        """
        estimate = self.capability_estimator.update(observation)
        capability = estimate.capability
        nominal = self.config.power.solar_rated_kw * self.config.power.solar_profile[step % len(self.config.power.solar_profile)]
        if nominal > 0.01:
            factor = max(0.0, min(1.0, observation.solar_kw / nominal))
            capability = Capability(**{**capability.__dict__, "solar_factor": min(capability.solar_factor, factor)})
            if factor < .8:
                payload = {"step": step, "target": "solar", "factor": factor, "confidence": "medium"}
                if "solar" not in self._reported_detections:
                    self._reported_detections.add("solar")
                    self.detected_faults.append(payload)
                    self._record(Event(f"detect-solar-{step}", step, "fault", "flag solar", ["SOLAR_OUTPUT_MISMATCH"], "Observed solar generation is below the known illumination forecast; solar capability is treated conservatively.", payload, self.manager.mode))
        self.last_capability_estimate = capability
        self.capability_estimate_history.append({"step": step, "capability": asdict(capability), "evidence": list(estimate.evidence)})
        # Keep the audit trail explicit.  A physical fault is ground-truth
        # evaluation data, while these entries are the *observed* symptom and
        # the controller's subsequent inference.  They intentionally use the
        # observation rather than looking back at FaultEngine.capabilities().
        healthy = Capability()
        for field, healthy_value in healthy.__dict__.items():
            observed = observation.equipment_status.get(f"equipment_{field}")
            inferred = getattr(capability, field)
            abnormal = (
                observed is None
                or (isinstance(healthy_value, bool) and observed is not True)
                or (not isinstance(healthy_value, bool) and (not isinstance(observed, (int, float)) or isinstance(observed, bool) or float(observed) < float(healthy_value)))
            )
            report_key = f"equipment:{field}"
            if abnormal and report_key not in self._reported_detections:
                self._reported_detections.add(report_key)
                payload = {"step": step, "field": field, "observed": observed, "inferred": inferred}
                self.detected_faults.append({"step": step, "target": field, "confidence": "high" if observed is not None else "low"})
                self._record(Event(
                    f"equipment-observed-{field}-{step}", step, "observed_symptom", "observe equipment status",
                    [f"OBSERVED_EQUIPMENT_{field.upper()}"], "Equipment-status telemetry reported an unavailable, degraded, or unknown component.", payload, self.manager.mode,
                ))
                self._record(Event(
                    f"equipment-inferred-{field}-{step}", step, "capability", "infer equipment capability",
                    [f"INFERRED_CAPABILITY_{field.upper()}"], "Controller capability was derived conservatively from the observed equipment status.", payload, self.manager.mode,
                ))
        if estimate.evidence:
            self._record(Event(
                f"capability-inference-{step}", step, "capability", "infer conservative capability",
                list(estimate.evidence), "Equipment-status telemetry was missing or invalid; unavailable capability was assumed conservatively.",
                {"observed_status": dict(observation.equipment_status), "estimated_capability": asdict(capability)}, self.manager.mode,
            ))
        return capability

    def step(self) -> dict[str, object]:
        if self.completed: return self.snapshot()
        # Fault schedule is evaluation data; it is logged separately and is not passed to a controller.
        current_step = self.simulator.state_for_evaluation.step
        for status, fault in self.faults.changes(current_step):
            self.actual_fault_events.append({"step": current_step, "status": status, "id": fault.id, "category": fault.category, "target": fault.target})
            if fault.category == "physical":
                self._record(Event(
                    f"physical-{status}-{fault.id}-{current_step}", current_step, "physical_fault", f"{status} physical fault",
                    [f"PHYSICAL_{fault.target.upper()}"], "A physical fault changed simulator capability; controllers receive only subsequent telemetry evidence.",
                    {"id": fault.id, "target": fault.target, "parameters": dict(fault.parameters)}, self.manager.mode,
                ))
        truth = self.simulator.state_for_evaluation
        true_capability = self.simulator.capabilities()
        observation = self.telemetry.observe(truth, self.simulator.solar_kw(), self.simulator.last_load_kw, true_capability)
        self.telemetry_history.append(observation)
        if self.trace_telemetry:
            # Deliberately print reported observations, never simulator truth.
            print(f"[SYNTHETIC TELEMETRY | {self.trace_label}] {json.dumps(asdict(observation), sort_keys=True)}")
        estimate_result = self.estimator.update(observation)
        estimate = estimate_result.state
        self.last_observation, self.last_estimate = observation, estimate
        self.estimates.append(estimate)
        capability = self._infer_capability(observation, current_step)
        newly_suspect = [sensor for sensor in estimate.suspect_sensors if sensor not in self._reported_detections]
        if newly_suspect:
            self._reported_detections.update(newly_suspect)
            payload = {"sensors": estimate.suspect_sensors, "quality": estimate.telemetry_quality}
            self.detected_faults.append({"step": current_step, "target": ",".join(estimate.suspect_sensors), "confidence": "high"})
            self._record(Event(f"isolate-{current_step}", current_step, "telemetry", "isolate sensor", ["SOC_SENSOR_DISAGREEMENT"], f"Marked {', '.join(estimate.suspect_sensors)} suspect: reading disagrees with independent SOC readings.", payload, self.manager.mode))
        horizon = self.survival_estimator.estimate(estimate, capability, current_step)
        self.last_horizon = horizon
        self._record(self.manager.update(estimate, horizon, capability.air_available, current_step))
        for event in self.ledger.expire(current_step): self._record(event)
        context = ControllerContext(estimate, self.ledger, observation.solar_kw, capability, self.manager.mode)
        proposed = self.controller.propose(context)
        validation = self.validator.validate(proposed, estimate, self.ledger, observation.solar_kw, capability, self.manager.mode)
        proposed_validation = validation
        executable_statuses = {"candidate", "optimal", "limit_candidate", "conservative"}
        accepted = proposed if validation.accepted and proposed.status in executable_statuses else None
        fallback_attempted = False
        emergency_used = False
        if accepted is None:
            fallback_attempted = True
            fallback = BaselineController(self.config, self.validator).propose(context)
            fallback_validation = self.validator.validate(fallback, estimate, self.ledger, observation.solar_kw, capability, self.manager.mode)
            self._record(Event(f"fallback-{current_step}", current_step, "control", "use fallback", ["SOLVER_OR_VALIDATION_FALLBACK"], f"Rejected/unavailable `{proposed.controller}` candidate ({', '.join(validation.reasons) or proposed.status}); applying independently checked priority fallback.", {"solver_status": proposed.status, "validator_reasons": validation.reasons}, self.manager.mode))
            if fallback_validation.accepted and fallback.status in executable_statuses:
                accepted, validation = fallback, fallback_validation
            else:
                emergency_action = Action(
                    backup_kw=backup_for_bus_demand(
                        self.config, capability, observation.solar_kw,
                        self.config.power.fixed_essential_kw + self.config.power.control_computer_kw
                        + self.config.life_support.air_circulation_kw + self.config.life_support.oxygen_generator_kw
                        + self.config.life_support.co2_removal_kw + self.config.life_support.water_recovery_kw,
                    ),
                    task_ids=[], emergency_policy=True,
                )
                emergency = Plan([emergency_action], "emergency_policy", "emergency", message="No validated candidate; preserve critical services as far as physically possible.")
                emergency_validation = self.validator.validate(emergency, estimate, self.ledger, observation.solar_kw, capability, self.manager.mode)
                validation = emergency_validation
                emergency_used = True
                self._record(self.manager.shutdown(current_step, "NO_VALIDATED_SAFE_PLAN"))
                if not emergency_validation.accepted:
                    # No action is sent to the simulator when even the emergency
                    # command is malformed or cannot be evaluated safely.
                    audit_record = {
                        "step": current_step,
                        "scenario_id": self.config.id,
                        "run_id": self.id,
                        "proposed_controller": proposed.controller,
                        "executed_controller": None,
                        "action": None,
                        "validation_performed": True,
                        "validation_accepted": False,
                        "simulator_called": False,
                        "execution_succeeded": False,
                        "emergency_override": emergency_used,
                        "error_reason": "; ".join(validation.reasons) if validation else "PLAN_REJECTED",
                    }
                    self.execution_audit.append(audit_record)
                    self.last_proposed, self.last_validation, self.last_accepted = proposed, validation, None
                    self.last_executed = None
                    self._record_control_decision(current_step, proposed, proposed_validation, validation, None, fallback_attempted, emergency_used)
                    return self.snapshot()
                accepted = emergency
        executed = accepted.actions[0]
        # First recorded deferral makes mission debt visible without incrementing it on every replanning cycle.
        for task in self.ledger.schedulable(current_step):
            if task.config.id not in executed.task_ids and task.deferrals == 0:
                self._record(self.ledger.mark_deferred(task.config.id, current_step, "RESOURCE_OR_MODE_CONSTRAINT"))

        audit_record = {
            "step": current_step,
            "scenario_id": self.config.id,
            "run_id": self.id,
            "proposed_controller": proposed.controller,
            "executed_controller": accepted.controller,
            "action": asdict(executed),
            "validation_performed": True,
            "validation_accepted": validation.accepted if validation else False,
            "simulator_called": True,
            "execution_succeeded": False,
            "emergency_override": emergency_used,
            "error_reason": None,
        }
        try:
            transition = self.simulator.advance(executed)
            audit_record["execution_succeeded"] = True
            audit_record["transition_error"] = transition.transition_error
            self.transitions.append(transition)
        except Exception as exc:
            audit_record["execution_succeeded"] = False
            audit_record["error_reason"] = str(exc)
            self.execution_audit.append(audit_record)
            raise

        self.execution_audit.append(audit_record)

        if transition.transition_error:
            error_codes = [detail.split(":")[0] for detail in transition.transition_error.split(";")]
            self._record(Event(f"transition-{current_step}", current_step, "safety", "report transition", error_codes, "Physical transition reported a capability deficit; executed action is recorded as degraded/emergency rather than silently corrected.", {"detail": transition.transition_error}, self.manager.mode))
        if transition.critical_shortfall_kw > 0 or any(value > 0 for value in transition.resource_shortfalls.values()):
            self._record(Event(f"deficit-{current_step}", current_step, "safety", "report deficit", ["CRITICAL_RESOURCE_DEFICIT"], "A physically unavoidable critical deficit was observed; no resource was fabricated.", {"critical_shortfall_kw": transition.critical_shortfall_kw, **transition.resource_shortfalls}, self.manager.mode))
        if transition.noncritical_shortfall_kw <= 1e-9:
            for event in self.ledger.progress(executed.task_ids, current_step): self._record(event)
        self.last_proposed, self.last_validation, self.last_accepted, self.last_executed = proposed, validation, accepted, executed
        self._record_control_decision(current_step, proposed, proposed_validation, validation, accepted, fallback_attempted, emergency_used)
        return self.snapshot()

    def run(self, steps: int | None = None) -> dict[str, object]:
        count = min(steps if steps is not None else self.config.duration_steps, self.config.duration_steps)
        for _ in range(max(0, count)):
            if self.completed: break
            self.step()
        return self.snapshot()

    def snapshot(self) -> dict[str, object]:
        return {"run_id": self.id, "scenario": self.config.id, "controller": self.controller_name, "step": self.simulator.state_for_evaluation.step, "mode": self.manager.mode, "completed": self.completed, "state": self.state_view(), "metrics": self.metrics()}

    def state_view(self) -> dict[str, object]:
        if self.last_estimate is None:
            # A creation/state request receives a telemetry preview, not a physical-state dump.
            truth = self.simulator.state_for_evaluation
            observation = self.telemetry.observe(truth, self.simulator.solar_kw(), self.simulator.last_load_kw, self.simulator.capabilities())
            preview = StateEstimator(self.config).update(observation).state
            horizon = self.survival_estimator.estimate(preview, Capability(), truth.step)
            return {"estimated": asdict(preview), "mode": self.manager.mode, "survival_horizon": horizon.to_dict(), "note": "Values are telemetry-derived estimates. Physical truth is evaluation-only."}
        return {"estimated": asdict(self.last_estimate), "mode": self.manager.mode, "survival_horizon": self.last_horizon.to_dict() if self.last_horizon else None, "note": "Values are telemetry-derived estimates. Physical truth is evaluation-only."}

    def plan_view(self) -> dict[str, object]:
        return {"proposed": asdict(self.last_proposed) if self.last_proposed else None, "validator": asdict(self.last_validation) if self.last_validation else None, "accepted": asdict(self.last_accepted) if self.last_accepted else None, "executed": asdict(self.last_executed) if self.last_executed else None}

    def metrics(self) -> dict[str, object]:
        truth = self.simulator.state_for_evaluation
        tasks = self.ledger.snapshot()
        return {
            "steps": truth.step,
            "critical_load_shortfall_kwh": truth.cumulative_critical_shortfall_kwh,
            "noncritical_load_shortfall_kwh": truth.cumulative_noncritical_shortfall_kwh,
            "minimum_true_soc": min([t.after.battery_kwh / self.config.battery.capacity_kwh for t in self.transitions] or [truth.battery_kwh / self.config.battery.capacity_kwh]),
            "minimum_estimated_soc": min([item.soc for item in self.estimates] or [0.0]),
            "minimum_oxygen_kg": min([t.after.oxygen_kg for t in self.transitions] or [truth.oxygen_kg]),
            "maximum_co2_kg": max([t.after.co2_kg for t in self.transitions] or [truth.co2_kg]),
            "minimum_water_kg": min([t.after.water_kg for t in self.transitions] or [truth.water_kg]),
            "tasks_completed": sum(1 for task in tasks if task["status"] == "completed"),
            "missed_hard_deadlines": sum(1 for task in tasks if task["status"] == "missed" and task["hard_deadline"]),
            "mission_debt_steps": sum(int(task["remaining_steps"]) for task in tasks if task["status"] in {"pending", "running"}),
            "fallback_count": sum(1 for event in self.events if event.action == "use fallback"),
            # Derived from execution-audit records at the simulator boundary.
            "unvalidated_actions_executed": sum(1 for a in self.execution_audit if a.get("execution_succeeded") and not a.get("validation_accepted")),
            "actions_proposed": len(self.control_history),
            "actions_executed": sum(1 for a in self.execution_audit if a.get("execution_succeeded")),
            "actions_rejected": sum(1 for a in self.execution_audit if not a.get("validation_accepted")),
            "emergency_overrides": sum(1 for a in self.execution_audit if a.get("emergency_override")),
            "critical_shortfall_steps": sum(1 for t in self.transitions if t.critical_shortfall_kw > 1e-9),
            "resource_violation_steps": sum(1 for t in self.transitions if any(v > 0 for v in t.resource_shortfalls.values())),
            "battery_reserve_violation_steps": sum(1 for t in self.transitions if t.after.battery_kwh < self.config.battery.safe_reserve_kwh - 1e-9),
            "battery_capacity_violation_steps": sum(1 for t in self.transitions if t.after.battery_kwh > self.config.battery.capacity_kwh + 1e-9),
            "safety_violation_steps": sum(
                1 for t in self.transitions
                if t.critical_shortfall_kw > 1e-9
                or any(v > 0 for v in t.resource_shortfalls.values())
                or t.after.temperature_c < self.config.thermal.min_temperature_c - 1e-9
                or t.after.temperature_c > self.config.thermal.max_temperature_c + 1e-9
                or t.after.battery_kwh < self.config.battery.safe_reserve_kwh - 1e-9
            ),
            "evaluation_only_true_state": asdict(truth),
        }

    def events_view(self, offset: int = 0, limit: int = 100) -> list[dict[str, object]]:
        return [event.to_dict() for event in self.events[offset:offset + limit]]

    def faults_view(self) -> dict[str, object]:
        return {"actual_active_or_historical": self.actual_fault_events, "detected": self.detected_faults, "note": "Future injected faults are never returned."}
