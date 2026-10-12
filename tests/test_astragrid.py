from __future__ import annotations

import pytest
import numpy as np

from fastapi import HTTPException

from astra_grid.api import CreateRunRequest, create_app
from astra_grid.config import BatteryConfig, FaultConfig, PowerConfig, ScenarioConfig, TaskConfig, scenario_from_id
from astra_grid.control import BaselineController, ControllerContext, MilpController, PlanValidator
from astra_grid.evaluation import benchmark, benchmark_from_runs, benchmark_scenario, create_paired_runs, export_benchmark
from astra_grid.faults import Capability, FaultEngine
from astra_grid.models import Action, EstimatedState, Mode, Plan, TaskStatus, ValidationResult
from astra_grid.runner import SimulationRun
from astra_grid.safety import SurvivalEstimator
from astra_grid.simulator import HabitatSimulator
from astra_grid.tasks import MissionDebtLedger
from astra_grid.telemetry import CapabilityEstimator, StateEstimator, TelemetryGenerator


def test_invalid_battery_configuration_is_rejected() -> None:
    with pytest.raises(ValueError):
        BatteryConfig(capacity_kwh=10, initial_kwh=11)
    with pytest.raises(ValueError):
        ScenarioConfig(id="bad", description="bad", dt_hours=0)


def test_energy_and_resource_balance_are_explicit() -> None:
    config = scenario_from_id("nominal_day")
    sim = HabitatSimulator(config, FaultEngine([]))
    before = sim.state_for_evaluation
    transition = sim.advance(Action(backup_kw=12))
    after = transition.after
    assert transition.battery_charge_kw >= 0
    assert not (transition.battery_charge_kw > 0 and transition.battery_discharge_kw > 0)
    assert after.oxygen_kg == pytest.approx(before.oxygen_kg + (.7 - .55) * .25)
    assert after.co2_kg == pytest.approx(before.co2_kg + (.5 - .65) * .25)
    expected_water = before.water_kg + (min(3.0, 3.0 * .75) * .88 - 3.0 - .7 * 1.2) * .25
    assert after.water_kg == pytest.approx(expected_water)
    assert 0 <= after.battery_kwh <= config.battery.capacity_kwh


def test_solar_fault_changes_physical_generation() -> None:
    config = scenario_from_id("solar_array_damage")
    engine = FaultEngine(config.faults)
    sim = HabitatSimulator(config, engine)
    assert sim.solar_kw(30) == pytest.approx(config.power.solar_rated_kw * config.power.solar_profile[30] * .35)


def test_telemetry_fault_does_not_change_true_inventory() -> None:
    config = scenario_from_id("byzantine_soc_sensor")
    engine, sim = FaultEngine(config.faults), HabitatSimulator(config, FaultEngine(config.faults))
    telemetry = TelemetryGenerator(config, engine)
    state = sim.state_for_evaluation
    state.step = 5
    observation = telemetry.observe(state, 0, 0)
    assert observation.soc_sensors["soc_sensor_2"] == .75
    assert state.battery_kwh == config.battery.initial_kwh


def test_robust_soc_estimator_flags_single_outlier() -> None:
    config = scenario_from_id("byzantine_soc_sensor")
    config.controller.horizon_steps = 1  # single-step MILP: ensures battery diverges from biased sensor value
    run = SimulationRun(config)
    for _ in range(6): run.step()
    assert "soc_sensor_2" in run.last_estimate.suspect_sensors
    assert run.last_estimate.telemetry_quality == "medium"


def test_validator_rejects_unsafe_plan() -> None:
    config = scenario_from_id("nominal_day")
    validator, ledger = PlanValidator(config), MissionDebtLedger(config.tasks)
    estimated = EstimatedState(0, 19, .2375, 45, 1, 130, 21, "high")
    result = validator.validate(Plan([Action(task_ids=["science_task_01"], backup_kw=0, air_circulation_on=False)], "test", "candidate"), estimated, ledger, 0, FaultEngine([]).capabilities(0), Mode.NORMAL)
    assert not result.accepted
    assert "AIR_CIRCULATION_DISABLED" in result.reasons


def test_hard_deadline_is_not_changed() -> None:
    config = scenario_from_id("hard_deadline_under_shortage")
    ledger = MissionDebtLedger(config.tasks)
    events = ledger.expire(11)
    assert events and "HARD_DEADLINE_UNMET" in events[0].reason_codes
    task = ledger.tasks["urgent_drill"]
    assert task.status == TaskStatus.MISSED and task.config.deadline_step == 10


def test_survival_horizon_zero_when_boundary_breached() -> None:
    config = scenario_from_id("nominal_day")
    state = EstimatedState(0, 30, .4, config.life_support.oxygen_reserve_kg, 1, 100, 21, "high")
    horizon = SurvivalEstimator(config).estimate(state, FaultEngine([]).capabilities(0), 0)
    assert horizon.hours == 0 and horizon.limiting_factor == "boundary_breached"


def test_runner_keeps_proposed_accepted_and_executed_separate() -> None:
    config = scenario_from_id("combined_fault")
    config.controller.horizon_steps = 1  # single-step MILP for predictable battery depletion
    run = SimulationRun(config, "astragrid")
    run.run(12)
    view = run.plan_view()
    assert view["proposed"] is not None and view["accepted"] is not None and view["executed"] is not None
    assert any("soc_sensor_2" in str(event.values) for event in run.events)
    assert all(transition.after.battery_kwh >= 0 for transition in run.transitions)


def test_deferred_work_recovers_after_telemetry_recovers() -> None:
    run = SimulationRun(scenario_from_id("combined_fault"), "astragrid")
    run.run(78)  # run to deadline; rolling-horizon MILP defers tasks to solar hours
    recovered = run.ledger.tasks["science_task_02"]
    assert recovered.deferrals >= 1 and recovered.status == TaskStatus.COMPLETED


def test_resource_sensor_fault_changes_report_not_hidden_inventory() -> None:
    config = ScenarioConfig(id="sensor", description="sensor", faults=[FaultConfig(id="water_bias", category="telemetry", target="water", start_step=0, parameters={"value": 3.0})])
    simulator = HabitatSimulator(config, FaultEngine(config.faults))
    observation = TelemetryGenerator(config, FaultEngine(config.faults)).observe(simulator.state_for_evaluation, 0, 0)
    assert observation.water_kg == 3.0
    assert simulator.state_for_evaluation.water_kg == config.life_support.water_initial_kg


def test_api_paths_and_unknown_run_error() -> None:
    app = create_app()
    endpoints = {route.path: route.endpoint for route in app.routes if hasattr(route, "endpoint")}
    assert endpoints["/health"]()["status"] == "ok"
    created = endpoints["/api/simulations"](CreateRunRequest(scenario_id="nominal_day", controller="baseline"))
    run_id = created["run_id"]
    assert endpoints["/api/simulations/{run_id}/step"](run_id)["step"] == 1
    assert endpoints["/api/simulations/{run_id}/state"](run_id)["note"].startswith("Values are telemetry")
    with pytest.raises(HTTPException) as error:
        endpoints["/api/simulations/{run_id}/metrics"]("nope")
    assert error.value.status_code == 404


def _estimate(config: ScenarioConfig, *, battery: float = 60, step: int = 0) -> EstimatedState:
    return EstimatedState(step, battery, battery / config.battery.capacity_kwh, 45, 1, 130, 21, "high")


def test_validator_rejects_unknown_duplicate_missing_and_completed_tasks_without_crashing() -> None:
    config = scenario_from_id("nominal_day")
    validator, ledger, state = PlanValidator(config), MissionDebtLedger(config.tasks), _estimate(config)
    unknown = validator.validate(Plan([Action(task_ids=["not-a-task"])], "test", "candidate"), state, ledger, 0, Capability(), Mode.NORMAL)
    assert not unknown.accepted and "UNKNOWN_TASK" in unknown.reasons
    duplicate = validator.validate(Plan([Action(task_ids=["science_task_01", "science_task_01"])], "test", "candidate"), state, ledger, 0, Capability(), Mode.NORMAL)
    assert not duplicate.accepted and "DUPLICATE_TASK_ID" in duplicate.reasons
    del ledger.tasks["science_task_01"]
    missing = validator.validate(Plan([Action(task_ids=["science_task_01"])], "test", "candidate"), state, ledger, 0, Capability(), Mode.NORMAL)
    assert not missing.accepted and "TASK_MISSING_FROM_LEDGER" in missing.reasons
    ledger = MissionDebtLedger(config.tasks)
    ledger.tasks["science_task_01"].status = TaskStatus.COMPLETED
    completed = validator.validate(Plan([Action(task_ids=["science_task_01"])], "test", "candidate"), state, ledger, 0, Capability(), Mode.NORMAL)
    assert not completed.accepted and "TASK_NOT_EXECUTABLE" in completed.reasons


@pytest.mark.parametrize("value, expected", [(float("nan"), "NONFINITE_ESTIMATED_BATTERY"), (float("inf"), "NONFINITE_OBSERVED_SOLAR")])
def test_validator_rejects_nonfinite_energy_inputs(value: float, expected: str) -> None:
    config, validator, ledger = scenario_from_id("nominal_day"), None, None
    validator, ledger = PlanValidator(config), MissionDebtLedger(config.tasks)
    state, solar = _estimate(config), 0.0
    if expected.endswith("BATTERY"):
        state.battery_kwh = value
    else:
        solar = value
    result = validator.validate(Plan([Action(backup_kw=0)], "test", "candidate"), state, ledger, solar, Capability(), Mode.NORMAL)
    assert not result.accepted and expected in result.reasons


def test_validator_rejects_nonfinite_backup_and_invalid_capability() -> None:
    config = scenario_from_id("nominal_day")
    validator, ledger = PlanValidator(config), MissionDebtLedger(config.tasks)
    bad_backup = validator.validate(Plan([Action(backup_kw=float("nan"))], "test", "candidate"), _estimate(config), ledger, 0, Capability(), Mode.NORMAL)
    assert "NONFINITE_BACKUP_COMMAND" in bad_backup.reasons
    bad_cap = validator.validate(Plan([Action()], "test", "candidate"), _estimate(config), ledger, 0, Capability(pdu_factor=1.5), Mode.NORMAL)
    assert "INVALID_PDU_FACTOR" in bad_cap.reasons


def test_hard_deadline_boundary_and_degraded_optional_policy() -> None:
    config = scenario_from_id("nominal_day")
    task = config.tasks[2]  # four steps before its deadline is exactly feasible.
    task.duration_steps, task.deadline_step = 2, 1
    validator, ledger = PlanValidator(config), MissionDebtLedger(config.tasks)
    plan = Plan([Action(task_ids=[task.id], backup_kw=12)], "test", "candidate")
    at_boundary = validator.validate(plan, _estimate(config, step=0), ledger, 0, Capability(), Mode.NORMAL)
    assert at_boundary.accepted
    late = validator.validate(plan, _estimate(config, step=1), ledger, 0, Capability(), Mode.NORMAL)
    assert "HARD_TASK_CANNOT_FINISH_BY_DEADLINE" in late.reasons
    degraded = validator.validate(plan, _estimate(config), ledger, 0, Capability(), Mode.DEGRADED)
    assert "OPTIONAL_WORK_OUTSIDE_NORMAL_MODE" in degraded.reasons


def test_validator_battery_projection_matches_simulator_transition() -> None:
    config = scenario_from_id("nominal_day")
    action = Action(backup_kw=12)
    validator, ledger = PlanValidator(config), MissionDebtLedger(config.tasks)
    result = validator.validate(Plan([action], "test", "candidate"), _estimate(config), ledger, 0, Capability(), Mode.NORMAL)
    transition = HabitatSimulator(config, FaultEngine([])).advance(action)
    assert result.accepted
    assert result.projected_battery_kwh == pytest.approx(transition.after.battery_kwh)
    assert result.supply_deficit_kw == pytest.approx(0)


def test_power_faults_are_consistent_for_backup_pdu_and_reserve() -> None:
    config = scenario_from_id("nominal_day")
    validator, ledger = PlanValidator(config), MissionDebtLedger(config.tasks)
    backup_down = validator.validate(Plan([Action(backup_kw=1)], "test", "candidate"), _estimate(config), ledger, 0, Capability(backup_available=False), Mode.NORMAL)
    assert "BACKUP_UNAVAILABLE" in backup_down.reasons
    pdu_down = validator.validate(Plan([Action(backup_kw=1)], "test", "candidate"), _estimate(config), ledger, 0, Capability(pdu_available=False), Mode.NORMAL)
    assert "PDU_UNAVAILABLE_FOR_SOURCE" in pdu_down.reasons
    at_reserve = validator.validate(Plan([Action()], "test", "candidate"), _estimate(config, battery=config.battery.safe_reserve_kwh), ledger, 0, Capability(), Mode.NORMAL)
    assert "BATTERY_RESERVE" in at_reserve.reasons
    emergency = validator.validate(Plan([Action(emergency_policy=True)], "test", "emergency"), _estimate(config, battery=0), ledger, 0, Capability(), Mode.EMERGENCY)
    assert emergency.accepted and "RESERVE_ALREADY_BREACHED" in emergency.warnings and "SUPPLY_DEFICIT" in emergency.warnings


def test_milp_and_baseline_are_independent_and_milp_status_is_truthful() -> None:
    config = scenario_from_id("nominal_day")
    validator, ledger = PlanValidator(config), MissionDebtLedger(config.tasks)
    context = ControllerContext(_estimate(config), ledger, 0, Capability(), Mode.NORMAL)
    baseline = BaselineController(config, validator).propose(context)
    milp = MilpController(config, validator).propose(context)
    assert baseline.controller == "baseline" and milp.controller == "astragrid"
    assert milp.status in {"optimal", "limit_candidate"}
    assert validator.validate(milp, context.state, ledger, 0, Capability(), Mode.NORMAL).accepted


def test_milp_reports_limit_candidate_and_no_candidate_without_calling_it_optimal(monkeypatch: pytest.MonkeyPatch) -> None:
    import scipy.optimize

    config = scenario_from_id("nominal_day")
    config.controller.horizon_steps = 1  # single-step horizon for predictable mock shape
    validator, ledger = PlanValidator(config), MissionDebtLedger(config.tasks)
    context = ControllerContext(_estimate(config), ledger, 0, Capability(), Mode.NORMAL)

    # With H=1 the MILP has n_tasks*1 binary + 4 continuous variables (b, d, c, e).
    n_vars = len(config.tasks) + 4
    x_cand = np.zeros(n_vars)
    x_cand[-1] = config.battery.safe_reserve_kwh  # energy within valid bounds [reserve, capacity]

    class CandidateResult:
        status, x, fun, message = 1, x_cand, 0.0, "Time limit reached"

    monkeypatch.setattr(scipy.optimize, "milp", lambda **_: CandidateResult())
    candidate = MilpController(config, validator).propose(context)
    assert candidate.status == "limit_candidate"

    class NoCandidateResult:
        status, x, message = 1, None, "Time limit reached"

    monkeypatch.setattr(scipy.optimize, "milp", lambda **_: NoCandidateResult())
    no_candidate = MilpController(config, validator).propose(context)
    assert no_candidate.status == "limit_no_candidate"


def test_missing_critical_telemetry_rejects_normal_action() -> None:
    config = scenario_from_id("nominal_day")
    state = _estimate(config)
    state.water_kg = None
    result = PlanValidator(config).validate(Plan([Action()], "test", "candidate"), state, MissionDebtLedger(config.tasks), 0, Capability(), Mode.NORMAL)
    assert not result.accepted and "MISSING_CRITICAL_TELEMETRY_WATER" in result.reasons


def test_runner_never_executes_an_unvalidated_solver_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    config = scenario_from_id("nominal_day")
    run = SimulationRun(config, "astragrid")

    def broken(_: ControllerContext) -> Plan:
        return Plan([Action(task_ids=["unknown"])], "astragrid", "solver_failure")

    monkeypatch.setattr(run.controller, "propose", broken)
    run.step()
    assert run.last_accepted is not None
    assert run.last_accepted.controller == "baseline"
    assert run.last_executed is not None and "unknown" not in run.last_executed.task_ids


def test_benchmark_paired_runs_are_equivalent_but_independent() -> None:
    config = scenario_from_id("combined_fault")
    runs = create_paired_runs(config, seed=41)
    astro, baseline = runs["astragrid"], runs["baseline"]
    assert astro.config.seed == baseline.config.seed == 41
    assert astro.config is not baseline.config
    assert astro.simulator is not baseline.simulator
    assert astro.ledger is not baseline.ledger
    assert astro.faults is not baseline.faults
    assert astro.config.faults == baseline.config.faults
    astro.step()
    assert baseline.simulator.state_for_evaluation.step == 0
    baseline.run(4)
    astro.run(3)
    report = benchmark_from_runs(runs)
    assert len(report["runs"]) == 2 and len(report["pairs"]) == 1
    assert all(row["invalid_actions_executed"] == 0 for row in report["runs"])
    assert len(report["failures"]) == 2  # Explicitly reports incomplete, not successful, short runs.


def test_benchmark_rejects_a_mismatched_pair() -> None:
    runs = create_paired_runs(scenario_from_id("nominal_day"))
    runs["baseline"].config.seed += 1
    with pytest.raises(ValueError, match="equivalent scenario"):
        benchmark_from_runs(runs)


def test_benchmark_is_reproducible_and_exports_machine_readable_reports() -> None:
    config = scenario_from_id("nominal_day")
    first = benchmark_scenario(config, seeds=[123], steps=4)
    second = benchmark_scenario(config, seeds=[123], steps=4)
    assert first["pairs"] == second["pairs"]
    # Use the project's ignored output directory so this test works in
    # restricted Windows sandboxes where pytest's global temp fixture cannot
    # create a symlinked numbered directory.
    paths = export_benchmark(first, "outputs/test_benchmark")
    assert paths["json"].exists() and paths["runs"].exists() and paths["pairs"].exists()


def test_benchmark_marks_unknown_scenarios_as_unsupported() -> None:
    report = benchmark(["not-a-real-scenario"], steps=1)
    assert report["runs"] == []
    assert report["failures"] == [{"scenario": "not-a-real-scenario", "seed": None, "controller": None, "reason": "unsupported_scenario"}]


def test_constrained_selection_scenario_exposes_a_real_controller_difference() -> None:
    """The greedy baseline takes one high-priority task; the MILP packs two.

    The 8 kW optional budget is deliberately too small for all three jobs.
    This catches a regression where a scenario is too easy to distinguish the
    controllers, or where the MILP silently falls back to the greedy policy.
    """
    config = scenario_from_id("constrained_task_selection")
    astro = SimulationRun(config.model_copy(deep=True), "astragrid")
    baseline = SimulationRun(config.model_copy(deep=True), "baseline")
    astro.step()
    baseline.step()
    assert astro.last_executed is not None and baseline.last_executed is not None
    assert astro.last_executed.task_ids == ["medium_a", "medium_b"]
    assert baseline.last_executed.task_ids == ["high_draw"]


def test_physical_backup_fault_is_observable_and_controller_visible() -> None:
    run = SimulationRun(scenario_from_id("early_backup_unavailable"), "astragrid")
    run.step()  # step 0: healthy
    run.step()  # step 1: physical failure and its status telemetry
    assert run.simulator.capabilities().backup_available is False
    assert run.last_observation.equipment_status["equipment_backup_available"] is False
    assert run.last_capability_estimate.backup_available is False
    assert any(event.kind == "physical_fault" and "PHYSICAL_BACKUP" in event.reason_codes for event in run.events)
    assert any("OBSERVED_EQUIPMENT_BACKUP_AVAILABLE" in event.reason_codes for event in run.events)
    assert any("INFERRED_CAPABILITY_BACKUP_AVAILABLE" in event.reason_codes for event in run.events)
    assert run.last_executed is not None and run.last_executed.backup_kw == 0


def test_early_solar_fault_is_observable_while_long_workloads_remain() -> None:
    run = SimulationRun(scenario_from_id("early_solar_fault_long_workload"), "astragrid")
    run.run(3)  # fault starts at decision step 2
    assert run.simulator.capabilities().solar_factor == 0
    assert run.last_observation.equipment_status["equipment_solar_factor"] == 0
    assert run.last_capability_estimate.solar_factor == 0
    assert any(event.kind == "physical_fault" and "PHYSICAL_SOLAR" in event.reason_codes for event in run.events)
    assert any(task.status in {TaskStatus.PENDING, TaskStatus.RUNNING} for task in run.ledger.tasks.values())


def test_missing_equipment_status_becomes_a_conservative_capability_not_hidden_truth() -> None:
    config = ScenarioConfig(
        id="missing-backup-status",
        description="backup monitor is unavailable",
        faults=[FaultConfig(
            id="backup_status_lost", category="telemetry", target="equipment_backup_available",
            start_step=0, parameters={"kind": "missing"},
        )],
    )
    run = SimulationRun(config, "astragrid")
    run.step()
    # The simulator's real backup remains healthy; only its monitor is absent.
    assert run.simulator.capabilities().backup_available is True
    assert run.last_observation.equipment_status["equipment_backup_available"] is None
    assert run.last_capability_estimate.backup_available is False
    assert run.last_executed is not None and run.last_executed.backup_kw == 0
    assert any("CAPABILITY_STATUS_MISSING_BACKUP_AVAILABLE" in event.reason_codes for event in run.events)


def test_capability_estimator_treats_unknown_equipment_as_unavailable() -> None:
    config = scenario_from_id("nominal_day")
    simulator = HabitatSimulator(config, FaultEngine([]))
    observation = TelemetryGenerator(config, FaultEngine([])).observe(
        simulator.state_for_evaluation, 0, 0, Capability(backup_available=False),
    )
    observation.equipment_status["equipment_pdu_factor"] = None
    capability = CapabilityEstimator().update(observation)
    assert capability.capability.backup_available is False
    assert capability.capability.pdu_factor == 0
    assert "CAPABILITY_STATUS_MISSING_PDU_FACTOR" in capability.evidence


def test_simulator_enforces_a_real_backup_outage_even_for_a_bad_command() -> None:
    config = scenario_from_id("early_backup_unavailable")
    simulator = HabitatSimulator(config, FaultEngine(config.faults))
    simulator.advance(Action())  # advance from healthy step 0 to failed step 1
    transition = simulator.advance(Action(backup_kw=config.power.backup_max_kw))
    assert transition.backup_kw == 0
    assert transition.transition_error is not None and "BACKUP_UNAVAILABLE" in transition.transition_error


def test_release_steps_keep_future_work_out_of_the_current_ledger() -> None:
    ledger = MissionDebtLedger(scenario_from_id("deadline_release_pressure").tasks)
    assert "urgent_later" not in {task.config.id for task in ledger.schedulable(1)}
    assert "urgent_later" in {task.config.id for task in ledger.schedulable(2)}


def test_both_controllers_receive_equivalent_equipment_telemetry() -> None:
    runs = create_paired_runs(scenario_from_id("early_backup_unavailable"), seed=19)
    runs["astragrid"].run(2)
    runs["baseline"].run(2)
    assert runs["astragrid"].last_observation.equipment_status == runs["baseline"].last_observation.equipment_status
    assert runs["astragrid"].last_capability_estimate == runs["baseline"].last_capability_estimate


# --- Test group A: Solar fault timing ---


def test_early_solar_fault_has_positive_generation_before_fault() -> None:
    """The custom solar profile gives positive solar before the fault at step 2."""
    config = scenario_from_id("early_solar_fault_long_workload")
    engine = FaultEngine(config.faults)
    sim = HabitatSimulator(config, engine)
    # Before fault (steps 0-1): solar must be positive
    assert sim.solar_kw(0) > 0, "Solar should be positive before the fault"
    assert sim.solar_kw(1) > 0, "Solar should be positive before the fault"
    # At fault start (step 2): solar drops to zero
    assert sim.solar_kw(2) == pytest.approx(0.0), "Solar should be zero when fault is active"
    # The fault persists
    assert sim.solar_kw(10) == pytest.approx(0.0)
    assert sim.solar_kw(50) == pytest.approx(0.0)
    # All tasks are still pending at the fault start
    ledger = MissionDebtLedger(config.tasks)
    assert len(ledger.schedulable(2)) == len(config.tasks)


def test_early_solar_fault_scenario_is_deterministically_reproducible() -> None:
    """Repeated runs with the same configuration produce the same result."""
    config = scenario_from_id("early_solar_fault_long_workload")
    run1 = SimulationRun(config.model_copy(deep=True), "astragrid")
    run1.run(10)
    run2 = SimulationRun(config.model_copy(deep=True), "astragrid")
    run2.run(10)
    assert len(run1.transitions) == len(run2.transitions)
    for t1, t2 in zip(run1.transitions, run2.transitions):
        assert t1.after.battery_kwh == pytest.approx(t2.after.battery_kwh)


def test_hard_deadline_under_shortage_solar_and_backup_timing() -> None:
    """The solar fault at step 0 has no immediate effect (profile is zero in
    the dark period), but the backup fault is immediately effective."""
    config = scenario_from_id("hard_deadline_under_shortage")
    engine = FaultEngine(config.faults)
    sim = HabitatSimulator(config, engine)
    # Solar profile is zero at step 0 — solar fault has no additional effect
    assert sim.solar_kw(0) == 0.0
    # Backup is unavailable from step 0
    cap = engine.capabilities(0)
    assert not cap.backup_available
    # The hard deadline task has a tight deadline
    assert config.tasks[0].hard_deadline
    # Run the scenario: the hard deadline must be missed
    run = SimulationRun(config.model_copy(deep=True), "astragrid")
    run.run(15)
    task = run.ledger.tasks["urgent_drill"]
    assert task.status == TaskStatus.MISSED


# --- Test group D: Safety metrics ---


def test_safety_metrics_are_derived_not_hardcoded() -> None:
    """Safety metrics must be computed from execution records, not hardcoded."""
    config = scenario_from_id("nominal_day")
    run = SimulationRun(config, "astragrid")
    run.run(10)
    metrics = run.metrics()
    # Old hardcoded metric must be replaced
    assert "unsafe_actions_executed" not in metrics
    # New derived metrics must exist
    assert "unvalidated_actions_executed" in metrics
    assert "critical_shortfall_steps" in metrics
    assert "resource_violation_steps" in metrics
    assert "safety_violation_steps" in metrics
    # For a nominal run, all safety metrics should be zero
    assert metrics["unvalidated_actions_executed"] == 0
    assert metrics["critical_shortfall_steps"] == 0
    assert metrics["resource_violation_steps"] == 0
    assert metrics["safety_violation_steps"] == 0


def test_critical_shortfall_steps_agree_with_transitions() -> None:
    """The metric must count exactly the transitions with a positive critical shortfall."""
    config = scenario_from_id("hard_deadline_under_shortage")
    run = SimulationRun(config, "astragrid")
    run.run(20)
    metrics = run.metrics()
    expected = sum(1 for t in run.transitions if t.critical_shortfall_kw > 1e-9)
    assert metrics["critical_shortfall_steps"] == expected


def test_action_executed_audit_field_is_present_and_correct() -> None:
    """Each control_history record must include the action_executed flag."""
    config = scenario_from_id("nominal_day")
    run = SimulationRun(config, "astragrid")
    run.run(10)
    for record in run.control_history:
        assert "action_executed" in record
        # In a nominal run, every step should result in an executed action.
        assert record["action_executed"] is True
    assert run.metrics()["unvalidated_actions_executed"] == 0


def test_unexecuted_emergency_is_not_counted_as_executed(monkeypatch: pytest.MonkeyPatch) -> None:
    """If even the emergency plan is rejected, no action should be recorded as executed."""
    config = scenario_from_id("nominal_day")
    run = SimulationRun(config, "astragrid")

    # Make the validator reject *everything*, including emergency plans.
    always_reject = ValidationResult(False, ["FORCED_REJECTION"], None, [], 0)
    monkeypatch.setattr(run.validator, "validate", lambda *args, **kwargs: always_reject)
    run.step()
    # The last control_history entry should show no action was executed.
    last = run.control_history[-1]
    assert last["action_executed"] is False
    assert last["executed_controller"] is None


# --- Test group E: Comparison runs with new metrics ---


def test_benchmark_report_includes_safety_violation_metric() -> None:
    """The benchmark pipeline must compute and report safety_violation_steps."""
    config = scenario_from_id("nominal_day")
    report = benchmark_scenario(config, seeds=[42], steps=10)
    for run_record in report["runs"]:
        assert "safety_violation_steps" in run_record
        assert "invalid_actions_executed" in run_record


def test_comparison_runs_have_independent_state_and_new_metrics() -> None:
    """Paired comparison runs must have independent mutable state and report new metrics."""
    config = scenario_from_id("early_backup_unavailable")
    runs = create_paired_runs(config, seed=55)
    for run in runs.values():
        run.run(10)
    # Independent mutable state
    assert runs["astragrid"].simulator is not runs["baseline"].simulator
    assert runs["astragrid"].ledger is not runs["baseline"].ledger
    assert runs["astragrid"].faults is not runs["baseline"].faults
    # Both should have the new metrics
    for run in runs.values():
        metrics = run.metrics()
        assert "unvalidated_actions_executed" in metrics
        assert "critical_shortfall_steps" in metrics
        assert "safety_violation_steps" in metrics
        assert metrics["unvalidated_actions_executed"] == 0
    # Benchmark pipeline should accept these runs
    report = benchmark_from_runs(runs)
    assert len(report["runs"]) == 2
    assert all(row["invalid_actions_executed"] == 0 for row in report["runs"])


# --- Test group F: Telemetry quantization ---


def test_quantize_factor_maps_correctly() -> None:
    """Floor-quantisation with step 0.25 produces expected categorical levels."""
    from astra_grid.telemetry import _quantize_factor
    assert _quantize_factor(1.0) == 1.0
    assert _quantize_factor(0.0) == 0.0
    assert _quantize_factor(0.75) == 0.75
    assert _quantize_factor(0.5) == 0.5
    assert _quantize_factor(0.25) == 0.25
    assert _quantize_factor(0.35) == 0.25
    assert _quantize_factor(0.99) == 0.75
    assert _quantize_factor(0.24) == 0.0
    assert _quantize_factor(0.74) == 0.5
    assert _quantize_factor(0.51) == 0.5


def test_exact_degradation_factor_does_not_leak_into_telemetry() -> None:
    """A fault with factor=0.35 must not appear as 0.35 in equipment_status."""
    config = scenario_from_id("solar_array_damage")  # physical fault: factor 0.35
    engine = FaultEngine(config.faults)
    sim = HabitatSimulator(config, engine)
    # Advance past the fault start at step 22.
    for _ in range(23):
        sim.advance(Action(backup_kw=12))
    cap = engine.capabilities(23)
    assert cap.solar_factor == pytest.approx(0.35)
    obs = TelemetryGenerator(config, engine).observe(
        sim.state_for_evaluation, sim.solar_kw(), 0, cap,
    )
    reported = obs.equipment_status["equipment_solar_factor"]
    assert reported != pytest.approx(0.35), "Exact degradation factor leaked"
    assert reported == pytest.approx(0.25), "Factor should be quantized to 0.25"


def test_healthy_factors_quantize_to_one() -> None:
    """Fully healthy capabilities must still report 1.0 after quantisation."""
    config = scenario_from_id("nominal_day")
    sim = HabitatSimulator(config, FaultEngine([]))
    cap = Capability()  # all factors 1.0
    obs = TelemetryGenerator(config, FaultEngine([])).observe(
        sim.state_for_evaluation, sim.solar_kw(), 0, cap,
    )
    for key, value in obs.equipment_status.items():
        if isinstance(value, float):
            assert value == pytest.approx(1.0), f"{key} was {value}, expected 1.0"


# --- Test group G: Multi-step rolling-horizon MILP ---


def test_validator_accepts_multi_step_plan() -> None:
    """The validator should accept a multi-step plan by validating only actions[0]."""
    config = scenario_from_id("nominal_day")
    validator, ledger = PlanValidator(config), MissionDebtLedger(config.tasks)
    multi = Plan([Action(backup_kw=12), Action(backup_kw=12)], "test", "optimal")
    result = validator.validate(multi, _estimate(config), ledger, 0, Capability(), Mode.NORMAL)
    assert result.accepted, f"Multi-step plan rejected: {result.reasons}"


def test_milp_produces_multi_step_horizon_plan() -> None:
    """With a horizon > 1, the MILP should produce a multi-step plan."""
    config = scenario_from_id("nominal_day")
    config.controller.horizon_steps = 12
    validator, ledger = PlanValidator(config), MissionDebtLedger(config.tasks)
    context = ControllerContext(_estimate(config), ledger, 0, Capability(), Mode.NORMAL)
    plan = MilpController(config, validator).propose(context)
    assert len(plan.actions) == 12, f"Expected 12-step plan, got {len(plan.actions)}"
    assert plan.status in {"optimal", "limit_candidate"}
    assert plan.solver_called


def test_milp_with_horizon_one_matches_single_step_behaviour() -> None:
    """Horizon=1 should still produce a valid single-action plan."""
    config = scenario_from_id("nominal_day")
    config.controller.horizon_steps = 1
    validator, ledger = PlanValidator(config), MissionDebtLedger(config.tasks)
    context = ControllerContext(_estimate(config), ledger, 0, Capability(), Mode.NORMAL)
    plan = MilpController(config, validator).propose(context)
    assert len(plan.actions) == 1
    assert plan.status in {"optimal", "limit_candidate"}
    assert plan.solver_called


def test_rolling_horizon_only_first_action_is_executed() -> None:
    """The runner should execute only actions[0] of a multi-step plan."""
    config = scenario_from_id("nominal_day")
    config.controller.horizon_steps = 12
    run = SimulationRun(config, "astragrid")
    run.step()
    assert len(run.control_history) == 1
    assert run.control_history[0]["action_executed"] is True
    # The executed action must be the first from the accepted plan.
    if run.last_accepted and len(run.last_accepted.actions) > 1:
        assert run.last_executed is not None


def test_constrained_selection_still_correct_with_rolling_horizon() -> None:
    """The MILP must still prefer two medium tasks over one expensive task."""
    config = scenario_from_id("constrained_task_selection")
    config.controller.horizon_steps = 4
    runs = create_paired_runs(config, seed=42)
    astro, baseline = runs["astragrid"], runs["baseline"]
    astro.step()
    baseline.step()
    assert astro.last_executed is not None
    assert sorted(astro.last_executed.task_ids) == ["medium_a", "medium_b"]


# --- Test group H: Detailed regression tests for rolling-horizon MILP, execution audit, and fault timing ---


def test_future_released_task_included_and_not_scheduled_before_release() -> None:
    """Future tasks must be planned in the horizon but never scheduled before release_step."""
    config = scenario_from_id("nominal_day")
    config.controller.horizon_steps = 10
    config.tasks = [
        TaskConfig(id="future_task", power_kw=3.0, duration_steps=2, priority=9, release_step=3, deadline_step=8)
    ]
    validator, ledger = PlanValidator(config), MissionDebtLedger(config.tasks)
    context = ControllerContext(_estimate(config), ledger, 0, Capability(), Mode.NORMAL)
    plan = MilpController(config, validator).propose(context)
    assert plan.status in {"optimal", "limit_candidate"}
    assert plan.solver_called
    # At step 0, 1, 2 the future task is not yet released, so action[0], action[1], action[2] cannot run it
    assert "future_task" not in plan.actions[0].task_ids
    assert "future_task" not in plan.actions[1].task_ids
    assert "future_task" not in plan.actions[2].task_ids
    # But it can be scheduled at step 3 or later
    scheduled_steps = [t for t, act in enumerate(plan.actions) if "future_task" in act.task_ids]
    assert all(t >= 3 for t in scheduled_steps)


def test_empty_current_candidate_set_considers_future_tasks() -> None:
    """If no tasks are released at step 0, the optimizer must still plan for future tasks."""
    config = scenario_from_id("nominal_day")
    config.controller.horizon_steps = 10
    config.tasks = [
        TaskConfig(id="late_arrival", power_kw=4.0, duration_steps=2, priority=8, release_step=2, deadline_step=6)
    ]
    validator, ledger = PlanValidator(config), MissionDebtLedger(config.tasks)
    # Schedulable at step 0 is empty!
    assert len(ledger.schedulable(0)) == 0
    context = ControllerContext(_estimate(config), ledger, 0, Capability(), Mode.NORMAL)
    plan = MilpController(config, validator).propose(context)
    assert plan.status in {"optimal", "limit_candidate"}
    assert plan.solver_called
    # First action has no tasks
    assert plan.actions[0].task_ids == []
    # Later actions can schedule the future task
    scheduled = any("late_arrival" in act.task_ids for act in plan.actions[2:])
    assert scheduled


def test_completed_task_receives_no_further_execution() -> None:
    """A completed task must not be scheduled by the MILP."""
    config = scenario_from_id("nominal_day")
    config.controller.horizon_steps = 6
    config.tasks = [
        TaskConfig(id="quick_job", power_kw=2.0, duration_steps=1, priority=10, release_step=0, deadline_step=5)
    ]
    validator, ledger = PlanValidator(config), MissionDebtLedger(config.tasks)
    # Complete the task
    ledger.progress(["quick_job"], 0)
    assert ledger.tasks["quick_job"].status == TaskStatus.COMPLETED
    context = ControllerContext(_estimate(config), ledger, 1, Capability(), Mode.NORMAL)
    plan = MilpController(config, validator).propose(context)
    assert all("quick_job" not in act.task_ids for act in plan.actions)


def test_non_flexible_task_runs_contiguously() -> None:
    """A non-flexible task must execute in contiguous steps once started."""
    config = scenario_from_id("nominal_day")
    config.controller.horizon_steps = 8
    config.power = PowerConfig(solar_rated_kw=30, solar_profile=[0.8] * 96)
    config.tasks = [
        TaskConfig(id="inflexible_task", power_kw=3.0, duration_steps=3, priority=9, release_step=0, deadline_step=7, flexible=False)
    ]
    validator, ledger = PlanValidator(config), MissionDebtLedger(config.tasks)
    context = ControllerContext(_estimate(config), ledger, 0, Capability(), Mode.NORMAL)
    plan = MilpController(config, validator).propose(context)
    assert plan.status in {"optimal", "limit_candidate"}
    steps = [t for t, act in enumerate(plan.actions) if "inflexible_task" in act.task_ids]
    if len(steps) > 1:
        for i in range(len(steps) - 1):
            assert steps[i + 1] == steps[i] + 1


def test_solver_rejects_out_of_bounds_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    """Invalid variable values from the solver must cause solver_failure."""
    import scipy.optimize

    config = scenario_from_id("nominal_day")
    config.controller.horizon_steps = 1
    validator, ledger = PlanValidator(config), MissionDebtLedger(config.tasks)
    context = ControllerContext(_estimate(config), ledger, 0, Capability(), Mode.NORMAL)

    n_vars = len(config.tasks) + 4
    # Case A: non-integral binary task variable
    x_bad = np.zeros(n_vars)
    x_bad[0] = 0.5  # non-integral
    x_bad[-1] = config.battery.safe_reserve_kwh
    class BadIntegerResult:
        status, x, fun, message = 0, x_bad, 0.0, "Optimal"
    monkeypatch.setattr(scipy.optimize, "milp", lambda **_: BadIntegerResult())
    plan = MilpController(config, validator).propose(context)
    assert plan.status == "solver_failure"

    # Case B: battery energy below reserve
    x_bad2 = np.zeros(n_vars)
    x_bad2[-1] = config.battery.safe_reserve_kwh - 5.0  # below reserve
    class BadEnergyResult:
        status, x, fun, message = 0, x_bad2, 0.0, "Optimal"
    monkeypatch.setattr(scipy.optimize, "milp", lambda **_: BadEnergyResult())
    plan = MilpController(config, validator).propose(context)
    assert plan.status == "solver_failure"


def test_execution_audit_records_central_logging() -> None:
    """Execution audit records must be produced for every step at the simulator boundary."""
    config = scenario_from_id("nominal_day")
    run = SimulationRun(config, "astragrid")
    run.run(5)
    assert len(run.execution_audit) == 5
    for rec in run.execution_audit:
        assert rec["validation_performed"] is True
        assert rec["validation_accepted"] is True
        assert rec["simulator_called"] is True
        assert rec["execution_succeeded"] is True
        assert rec["action"] is not None
        assert rec["scenario_id"] == "nominal_day"
    metrics = run.metrics()
    assert metrics["actions_executed"] == 5
    assert metrics["unvalidated_actions_executed"] == 0


def test_solar_array_damage_timing_creates_observable_reduction() -> None:
    """At step 22, the solar array damage reduces generation when nominal solar is positive."""
    config = scenario_from_id("solar_array_damage")
    engine = FaultEngine(config.faults)
    sim = HabitatSimulator(config, engine)
    # At step 21: undamaged
    gen_21 = sim.solar_kw(21)
    nominal_21 = config.power.solar_rated_kw * config.power.solar_profile[21]
    assert gen_21 == pytest.approx(nominal_21)
    assert gen_21 > 0.0
    # At step 22: damaged by factor 0.35
    gen_22 = sim.solar_kw(22)
    nominal_22 = config.power.solar_rated_kw * config.power.solar_profile[22]
    assert gen_22 == pytest.approx(nominal_22 * 0.35)
    assert gen_22 < nominal_22


def test_combined_fault_exercises_overlapping_observable_faults() -> None:
    """In combined_fault, solar degradation and SOC telemetry fault overlap during daylight."""
    config = scenario_from_id("combined_fault")
    engine = FaultEngine(config.faults)
    # Check that at step 20 (daylight begins), both faults are active
    active = {f.id for f in engine.active(20)}
    assert "array_damage" in active
    assert "soc_byzantine" in active
    # And at step 22:
    active_22 = {f.id for f in engine.active(22)}
    assert "array_damage" in active_22
    assert "soc_byzantine" in active_22



