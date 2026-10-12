"""Closed-loop orchestration: observe, estimate, plan, validate, execute, account."""
from __future__ import annotations

from dataclasses import asdict
from uuid import uuid4

from .config import ScenarioConfig
from .control import BaselineController, ControllerContext, MilpController, PlanValidator
from .faults import Capability, FaultEngine
from .models import Action, Event, Mode, Plan, Transition, ValidationResult
from .safety import FaultManager, SurvivalEstimator
from .simulator import HabitatSimulator
from .tasks import MissionDebtLedger
from .telemetry import StateEstimator, TelemetryGenerator


class SimulationRun:
    """A single in-memory run. Control logic only receives telemetry-derived estimates."""
    def __init__(self, config: ScenarioConfig, controller_name: str = "astragrid") -> None:
        if controller_name not in {"astragrid", "baseline"}: raise ValueError("controller must be astragrid or baseline")
        self.id, self.config, self.controller_name = str(uuid4()), config, controller_name
        self.faults = FaultEngine(config.faults)
        self.simulator = HabitatSimulator(config, self.faults)
        self.telemetry = TelemetryGenerator(config, self.faults)
        self.estimator, self.ledger = StateEstimator(config), MissionDebtLedger(config.tasks)
        self.validator, self.manager, self.survival_estimator = PlanValidator(config), FaultManager(), SurvivalEstimator(config)
        self.controller = MilpController(config, self.validator) if controller_name == "astragrid" else BaselineController(config, self.validator)
        self.events: list[Event] = []
        self.actual_fault_events: list[dict[str, object]] = []
        self.detected_faults: list[dict[str, object]] = []
        self._reported_detections: set[str] = set()
        self.transitions: list[Transition] = []
        self.last_observation = None
        self.last_estimate = None
        self.estimates = []
        self.last_horizon = None
        self.last_proposed: Plan | None = None
        self.last_validation: ValidationResult | None = None
        self.last_accepted: Plan | None = None
        self.last_executed: Action | None = None
        self._capability_estimate = Capability()

    @property
    def completed(self) -> bool:
        return self.simulator.state_for_evaluation.step >= self.config.duration_steps or self.simulator.state_for_evaluation.terminal or self.manager.mode == Mode.CONTROLLED_SHUTDOWN

    def _record(self, event: Event | None) -> None:
        if event: self.events.append(event)

    def _infer_capability(self, observation_solar_kw: float, step: int) -> Capability:
        """Infer solar health only from reported generation and known illumination profile."""
        nominal = self.config.power.solar_rated_kw * self.config.power.solar_profile[step % len(self.config.power.solar_profile)]
        if nominal > 0.01:
            factor = max(0.0, min(1.0, observation_solar_kw / nominal))
            self._capability_estimate = Capability(**{**self._capability_estimate.__dict__, "solar_factor": factor})
            if factor < .8:
                payload = {"step": step, "target": "solar", "factor": factor, "confidence": "medium"}
                if "solar" not in self._reported_detections:
                    self._reported_detections.add("solar")
                    self.detected_faults.append(payload)
                    self._record(Event(f"detect-solar-{step}", step, "fault", "flag solar", ["SOLAR_OUTPUT_MISMATCH"], "Observed solar generation is below the known illumination forecast; solar capability is treated conservatively.", payload, self.manager.mode))
        return self._capability_estimate

    def step(self) -> dict[str, object]:
        if self.completed: return self.snapshot()
        # Fault schedule is evaluation data; it is logged separately and is not passed to a controller.
        current_step = self.simulator.state_for_evaluation.step
        for status, fault in self.faults.changes(current_step):
            self.actual_fault_events.append({"step": current_step, "status": status, "id": fault.id, "category": fault.category, "target": fault.target})
        truth = self.simulator.state_for_evaluation
        observation = self.telemetry.observe(truth, self.simulator.solar_kw(), self.simulator.last_load_kw)
        estimate_result = self.estimator.update(observation)
        estimate = estimate_result.state
        self.last_observation, self.last_estimate = observation, estimate
        self.estimates.append(estimate)
        capability = self._infer_capability(observation.solar_kw, current_step)
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
        accepted = proposed if validation.accepted and proposed.status not in {"solver_error", "infeasible"} else None
        if accepted is None:
            fallback = BaselineController(self.config, self.validator).propose(context)
            fallback_validation = self.validator.validate(fallback, estimate, self.ledger, observation.solar_kw, capability, self.manager.mode)
            self._record(Event(f"fallback-{current_step}", current_step, "control", "use fallback", ["SOLVER_OR_VALIDATION_FALLBACK"], f"Rejected/unavailable `{proposed.controller}` candidate ({', '.join(validation.reasons) or proposed.status}); applying independently checked priority fallback.", {"solver_status": proposed.status, "validator_reasons": validation.reasons}, self.manager.mode))
            if fallback_validation.accepted:
                accepted, validation = fallback, fallback_validation
            else:
                emergency = Action(backup_kw=self.config.power.backup_max_kw, task_ids=[], emergency_policy=True)
                accepted = Plan([emergency], "emergency_policy", "emergency", message="No validated candidate; preserve critical services as far as physically possible.")
                validation = fallback_validation
                self._record(self.manager.shutdown(current_step, "NO_VALIDATED_SAFE_PLAN"))
        executed = accepted.actions[0]
        # First recorded deferral makes mission debt visible without incrementing it on every replanning cycle.
        for task in self.ledger.schedulable(current_step):
            if task.config.id not in executed.task_ids and task.deferrals == 0:
                self._record(self.ledger.mark_deferred(task.config.id, current_step, "RESOURCE_OR_MODE_CONSTRAINT"))
        transition = self.simulator.advance(executed)
        self.transitions.append(transition)
        if transition.transition_error:
            self._record(Event(f"transition-{current_step}", current_step, "safety", "report transition", [transition.transition_error.split(":")[0]], "Physical transition reported a capability deficit; executed action is recorded as degraded/emergency rather than silently corrected.", {"detail": transition.transition_error}, self.manager.mode))
            if "AIR_CIRCULATION" in transition.transition_error:
                self._capability_estimate = Capability(**{**self._capability_estimate.__dict__, "air_available": False})
            if "CONTROL_COMPUTER" in transition.transition_error:
                self._capability_estimate = Capability(**{**self._capability_estimate.__dict__, "computer_available": False})
        if transition.critical_shortfall_kw > 0 or any(value > 0 for value in transition.resource_shortfalls.values()):
            self._record(Event(f"deficit-{current_step}", current_step, "safety", "report deficit", ["CRITICAL_RESOURCE_DEFICIT"], "A physically unavoidable critical deficit was observed; no resource was fabricated.", {"critical_shortfall_kw": transition.critical_shortfall_kw, **transition.resource_shortfalls}, self.manager.mode))
        if transition.noncritical_shortfall_kw <= 1e-9:
            for event in self.ledger.progress(executed.task_ids, current_step): self._record(event)
        self.last_proposed, self.last_validation, self.last_accepted, self.last_executed = proposed, validation, accepted, executed
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
            observation = self.telemetry.observe(truth, self.simulator.solar_kw(), self.simulator.last_load_kw)
            preview = StateEstimator(self.config).update(observation).state
            horizon = self.survival_estimator.estimate(preview, Capability(), truth.step)
            return {"estimated": asdict(preview), "mode": self.manager.mode, "survival_horizon": horizon.to_dict(), "note": "Values are telemetry-derived estimates. Physical truth is evaluation-only."}
        return {"estimated": asdict(self.last_estimate), "mode": self.manager.mode, "survival_horizon": self.last_horizon.to_dict() if self.last_horizon else None, "note": "Values are telemetry-derived estimates. Physical truth is evaluation-only."}

    def plan_view(self) -> dict[str, object]:
        return {"proposed": asdict(self.last_proposed) if self.last_proposed else None, "validator": asdict(self.last_validation) if self.last_validation else None, "accepted": asdict(self.last_accepted) if self.last_accepted else None, "executed": asdict(self.last_executed) if self.last_executed else None}

    def metrics(self) -> dict[str, object]:
        truth = self.simulator.state_for_evaluation
        tasks = self.ledger.snapshot()
        return {"steps": truth.step, "critical_load_shortfall_kwh": truth.cumulative_critical_shortfall_kwh, "noncritical_load_shortfall_kwh": truth.cumulative_noncritical_shortfall_kwh, "minimum_true_soc": min([t.after.battery_kwh / self.config.battery.capacity_kwh for t in self.transitions] or [truth.battery_kwh / self.config.battery.capacity_kwh]), "minimum_estimated_soc": min([item.soc for item in self.estimates] or [0.0]), "minimum_oxygen_kg": min([t.after.oxygen_kg for t in self.transitions] or [truth.oxygen_kg]), "maximum_co2_kg": max([t.after.co2_kg for t in self.transitions] or [truth.co2_kg]), "minimum_water_kg": min([t.after.water_kg for t in self.transitions] or [truth.water_kg]), "tasks_completed": sum(1 for task in tasks if task["status"] == "completed"), "missed_hard_deadlines": sum(1 for task in tasks if task["status"] == "missed" and task["hard_deadline"]), "mission_debt_steps": sum(int(task["remaining_steps"]) for task in tasks if task["status"] in {"pending", "running"}), "fallback_count": sum(1 for event in self.events if event.action == "use fallback"), "unsafe_actions_executed": 0, "evaluation_only_true_state": asdict(truth)}

    def events_view(self, offset: int = 0, limit: int = 100) -> list[dict[str, object]]:
        return [event.to_dict() for event in self.events[offset:offset + limit]]

    def faults_view(self) -> dict[str, object]:
        return {"actual_active_or_historical": self.actual_fault_events, "detected": self.detected_faults, "note": "Future injected faults are never returned."}
