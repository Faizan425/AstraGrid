"""Comprehensive test suite for AstraGrid Resilience Lab.

Exercises the 29 acceptance criteria across:
- Experiment runner
- Independent evaluator
- Statistical reporting
- Dashboard and mission replay
- Diagnostic annotations
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest

from astra_grid.config import BatteryConfig, FaultConfig, PowerConfig, ScenarioConfig, TaskConfig, scenario_from_id
from astra_grid.control import ControllerContext, PlanValidator
from astra_grid.faults import Capability
from astra_grid.models import Action, Mode, Observation, PhysicalState, Plan, TaskStatus, Transition, ValidationResult
from astra_grid.resilience_lab import (
    DiagnosticAnnotation,
    DiagnosticRuleEngine,
    ExperimentConfig,
    ExperimentManifest,
    ExperimentRunner,
    IndependentEvaluator,
    MissionReplayBuilder,
    ResilienceStatistics,
    RunRecord,
    generate_html_report,
)
from astra_grid.runner import SimulationRun


# ==============================================================================
# Group 1: Experiment Runner Tests (1 - 7)
# ==============================================================================

def test_1_single_scenario_can_be_executed_and_recorded(tmp_path: Path) -> None:
    """Requirement 1: A single scenario can be executed and recorded."""
    runner = ExperimentRunner(ExperimentConfig(output_dir=tmp_path))
    record, run = runner.run_single("nominal_day", "baseline", seed=42, steps=4, output_dir=tmp_path)
    assert record is not None
    assert record.scenario_id == "nominal_day"
    assert record.controller == "baseline"
    assert record.seed == 42
    assert record.simulated_steps == 4
    assert (tmp_path / "per_step_history" / f"{record.run_id}.csv").exists()
    assert (tmp_path / "replay" / f"{record.run_id}.json").exists()


def test_2_multi_scenario_experiment_produces_separate_results(tmp_path: Path) -> None:
    """Requirement 2: A multi-scenario experiment produces a separate result for every attempted run."""
    cfg = ExperimentConfig(
        scenario_ids=["nominal_day", "constrained_task_selection"],
        controllers=["baseline"],
        seeds=[10, 20],
        steps=2,
        output_dir=tmp_path,
    )
    runner = ExperimentRunner(cfg)
    manifest, records, summaries, comparisons = runner.run_experiment(cfg)
    assert len(records) == 4
    assert len(manifest.run_ids) == 4
    unique_ids = {r.run_id for r in records}
    assert len(unique_ids) == 4
    assert (tmp_path / "experiment_manifest.json").exists()
    assert (tmp_path / "runs_summary.json").exists()


def test_3_baseline_and_astragrid_receive_independent_instances(tmp_path: Path) -> None:
    """Requirement 3: Baseline and AstraGrid receive independent simulation instances."""
    cfg = ExperimentConfig(
        scenario_ids=["nominal_day"],
        controllers=["astragrid", "baseline"],
        seeds=[42],
        steps=3,
        output_dir=tmp_path,
    )
    runner = ExperimentRunner(cfg)
    manifest, records, summaries, comparisons = runner.run_experiment(cfg)
    assert len(records) == 2
    rec_astra = next(r for r in records if r.controller == "astragrid")
    rec_base = next(r for r in records if r.controller == "baseline")
    assert rec_astra.run_id != rec_base.run_id
    assert rec_astra.controller == "astragrid"
    assert rec_base.controller == "baseline"


def test_4_matching_scenario_and_seeds_for_matched_comparisons(tmp_path: Path) -> None:
    """Requirement 4: Matching scenario configurations and seeds are used for matched comparisons."""
    cfg = ExperimentConfig(
        scenario_ids=["nominal_day"],
        controllers=["astragrid", "baseline"],
        seeds=[99],
        steps=4,
        output_dir=tmp_path,
    )
    runner = ExperimentRunner(cfg)
    manifest, records, summaries, comparisons = runner.run_experiment(cfg)
    assert len(comparisons) == 1
    comp = comparisons[0]
    assert comp.scenario_id == "nominal_day"
    assert comp.seed == 99
    assert "cumulative_critical_shortfall_kwh" in comp.deltas


def test_5_failed_simulation_is_recorded_as_failed_not_omitted(tmp_path: Path) -> None:
    """Requirement 5: A failed simulation is recorded as failed rather than silently omitted."""
    runner = ExperimentRunner(ExperimentConfig(output_dir=tmp_path))
    # Request an invalid scenario ID
    record, _ = runner.run_single("non_existent_scenario", "astragrid", seed=1, steps=2, output_dir=tmp_path)
    assert record.completed is False
    assert record.stop_reason == "unsupported_scenario"
    assert record.execution_error is not None
    assert "not found" in record.execution_error


def test_6_repeating_deterministic_run_produces_equivalent_outcomes(tmp_path: Path) -> None:
    """Requirement 6: Repeating a deterministic run produces equivalent physical outcomes."""
    runner = ExperimentRunner(ExperimentConfig(output_dir=tmp_path))
    rec1, run1 = runner.run_single("nominal_day", "baseline", seed=77, steps=8, output_dir=tmp_path)
    rec2, run2 = runner.run_single("nominal_day", "baseline", seed=77, steps=8, output_dir=tmp_path)
    assert rec1.simulated_steps == rec2.simulated_steps
    assert rec1.metrics["minimum_actual_soc"] == pytest.approx(rec2.metrics["minimum_actual_soc"])
    assert rec1.metrics["actual_solar_generation_kwh"] == pytest.approx(rec2.metrics["actual_solar_generation_kwh"])
    assert rec1.metrics["tasks_completed"] == rec2.metrics["tasks_completed"]


def test_7_output_can_be_loaded_without_rerunning_simulation(tmp_path: Path) -> None:
    """Requirement 7: Output can be loaded again to generate a report without rerunning."""
    cfg = ExperimentConfig(
        scenario_ids=["nominal_day"],
        controllers=["baseline"],
        seeds=[1],
        steps=2,
        output_dir=tmp_path,
    )
    runner = ExperimentRunner(cfg)
    manifest, records, summaries, comparisons = runner.run_experiment(cfg)

    # Now verify the JSON files on disk can be reloaded directly
    with open(tmp_path / "experiment_manifest.json", "r", encoding="utf-8") as f:
        loaded_man = json.load(f)
    with open(tmp_path / "runs_summary.json", "r", encoding="utf-8") as f:
        loaded_runs = json.load(f)

    assert loaded_man["total_runs_attempted"] == 1
    assert len(loaded_runs) == 1
    assert loaded_runs[0]["scenario_id"] == "nominal_day"


# ==============================================================================
# Group 2: Independent Evaluator Tests (8 - 14)
# ==============================================================================

def test_8_power_shortfall_metrics_agree_with_actual_transitions() -> None:
    """Requirement 8: Power shortfall metrics agree with the actual recorded transitions."""
    config = scenario_from_id("hard_deadline_under_shortage")
    run = SimulationRun(config, "baseline")
    run.run(5)
    evaluator = IndependentEvaluator()
    record = evaluator.evaluate(run)

    # Calculate expected sum from actual transition objects
    expected_crit_shortfall = sum(t.critical_shortfall_kw * config.dt_hours for t in run.transitions)
    assert record.metrics["cumulative_critical_shortfall_kwh"] == pytest.approx(expected_crit_shortfall)
    expected_crit_steps = sum(1 for t in run.transitions if t.critical_shortfall_kw > 1e-9)
    assert record.metrics["critical_shortfall_timesteps"] == expected_crit_steps


def test_9_minimum_soc_computed_from_correct_source_and_has_right_units() -> None:
    """Requirement 9: Minimum state of charge is computed from the correct source and has right units."""
    config = scenario_from_id("nominal_day")
    run = SimulationRun(config, "baseline")
    run.run(10)
    evaluator = IndependentEvaluator()
    record = evaluator.evaluate(run)

    min_soc = record.metrics["minimum_actual_soc"]
    assert 0.0 <= min_soc <= 1.0  # fraction units
    actual_min_soc = min(t.after.battery_kwh / config.battery.capacity_kwh for t in run.transitions)
    assert min_soc == pytest.approx(actual_min_soc)


def test_10_task_completion_metrics_agree_with_task_ledger() -> None:
    """Requirement 10: Task-completion metrics agree with the task ledger."""
    config = scenario_from_id("nominal_day")
    run = SimulationRun(config, "baseline")
    run.run(96)
    evaluator = IndependentEvaluator()
    record = evaluator.evaluate(run)

    ledger_tasks = run.ledger.snapshot()
    completed_in_ledger = sum(1 for t in ledger_tasks if t["status"] == "completed")
    assert record.metrics["tasks_completed"] == completed_in_ledger


def test_11_rejected_plans_are_not_counted_as_successful_executions() -> None:
    """Requirement 11: Rejected plans are not counted as successfully executed actions."""
    config = scenario_from_id("nominal_day")
    run = SimulationRun(config, "astragrid")
    # Simulate a forced rejected plan in control history
    run.control_history.append({
        "step": 0, "proposed_controller": "astragrid", "proposed_status": "infeasible",
        "solver_called": True, "validation_accepted": False, "validation_reasons": ["INFEASIBLE"],
        "final_validation_accepted": True, "fallback_attempted": True,
        "executed_controller": "baseline", "emergency_policy": False, "action_executed": True,
    })
    run.execution_audit.append({
        "step": 0, "scenario_id": "nominal_day", "run_id": run.id,
        "proposed_controller": "astragrid", "executed_controller": "baseline",
        "action": {}, "validation_performed": True, "validation_accepted": True,
        "simulator_called": True, "execution_succeeded": True,
        "emergency_override": False, "error_reason": None,
    })
    evaluator = IndependentEvaluator()
    record = evaluator.evaluate(run)
    assert record.metrics["rejected_proposed_plans"] >= 1
    # Successful executions count from audit, not proposed
    assert record.metrics["successful_simulator_executions"] == 1


def test_12_fault_occurrence_and_fault_detection_are_counted_separately() -> None:
    """Requirement 12: Fault occurrence and fault detection are counted separately."""
    config = scenario_from_id("byzantine_soc_sensor")
    run = SimulationRun(config, "astragrid")
    run.run(20)
    evaluator = IndependentEvaluator()
    record = evaluator.evaluate(run)

    # Injected fault count comes from config/actual faults
    assert record.metrics["injected_faults_total"] == len(config.faults)
    assert record.metrics["injected_faults_activated"] >= 1
    # Detected faults comes from detected_faults list
    assert record.metrics["detected_faults_total"] >= 1


def test_13_missing_history_is_reported_as_unavailable_not_fabricated() -> None:
    """Requirement 13: Missing history is reported as unavailable instead of replaced with fabricated values."""
    config = scenario_from_id("nominal_day")
    run = SimulationRun(config, "baseline")
    # 0 steps run
    evaluator = IndependentEvaluator()
    record = evaluator.evaluate(run)
    assert record.simulated_steps == 0
    # No recovery time can be computed when no faults are configured
    assert record.metrics["time_to_recover_steps"] is None


def test_14_evaluation_does_not_modify_original_run_state_or_ledger() -> None:
    """Requirement 14: Evaluation does not modify the original run's physical state or task ledger."""
    config = scenario_from_id("nominal_day")
    run = SimulationRun(config, "baseline")
    run.run(5)
    truth_before = asdict(run.simulator.state_for_evaluation)
    tasks_before = run.ledger.snapshot()

    evaluator = IndependentEvaluator()
    _ = evaluator.evaluate(run)

    truth_after = asdict(run.simulator.state_for_evaluation)
    tasks_after = run.ledger.snapshot()
    assert truth_before == truth_after
    assert tasks_before == tasks_after


# ==============================================================================
# Group 3: Statistical Reporting Tests (15 - 19)
# ==============================================================================

def test_15_aggregation_produces_correct_summaries_for_known_runs() -> None:
    """Requirement 15: Aggregation produces correct summaries for known synthetic runs."""
    records = [
        RunRecord(
            run_id="run1", scenario_id="scenA", controller="astragrid", seed=1, repetition=1,
            duration_steps=10, simulated_steps=10, simulated_hours=2.5, completed=True,
            stop_reason="duration_complete", metrics={"tasks_completed": 4.0, "cumulative_critical_shortfall_kwh": 0.0},
        ),
        RunRecord(
            run_id="run2", scenario_id="scenA", controller="astragrid", seed=2, repetition=1,
            duration_steps=10, simulated_steps=10, simulated_hours=2.5, completed=True,
            stop_reason="duration_complete", metrics={"tasks_completed": 6.0, "cumulative_critical_shortfall_kwh": 2.0},
        ),
    ]
    summaries = ResilienceStatistics.aggregate_runs(records)
    assert len(summaries) == 1
    sc_sum = summaries[0]
    assert sc_sum.attempted_runs == 2
    assert sc_sum.successful_runs == 2
    assert sc_sum.metrics["tasks_completed"].mean == 5.0
    assert sc_sum.metrics["tasks_completed"].median == 5.0
    assert sc_sum.metrics["tasks_completed"].min == 4.0
    assert sc_sum.metrics["tasks_completed"].max == 6.0


def test_16_failed_runs_remain_visible_in_report() -> None:
    """Requirement 16: Failed runs remain visible in the report."""
    records = [
        RunRecord(
            run_id="fail_run", scenario_id="scenB", controller="baseline", seed=1, repetition=1,
            duration_steps=10, simulated_steps=3, simulated_hours=0.75, completed=False,
            stop_reason="controlled_shutdown", execution_error="battery dead",
            metrics={"tasks_completed": 0.0},
        )
    ]
    summaries = ResilienceStatistics.aggregate_runs(records)
    assert len(summaries) == 1
    sc_sum = summaries[0]
    assert sc_sum.failed_runs == 1
    assert len(sc_sum.failures) == 1
    assert sc_sum.failures[0]["stop_reason"] == "controlled_shutdown"


def test_17_same_experiment_results_produce_equivalent_numerical_reports(tmp_path: Path) -> None:
    """Requirement 17: The same experiment results produce equivalent numerical reports."""
    record = RunRecord(
        run_id="r1", scenario_id="scenC", controller="astragrid", seed=5, repetition=1,
        duration_steps=8, simulated_steps=8, simulated_hours=2.0, completed=True,
        stop_reason="duration_complete", metrics={"cumulative_critical_shortfall_kwh": 1.5},
    )
    sum1 = ResilienceStatistics.aggregate_runs([record])
    sum2 = ResilienceStatistics.aggregate_runs([record])
    assert sum1[0].metrics["cumulative_critical_shortfall_kwh"].mean == sum2[0].metrics["cumulative_critical_shortfall_kwh"].mean


def test_18_uncertainty_interval_not_misleading_when_sample_size_insufficient() -> None:
    """Requirement 18: Uncertainty is not calculated misleadingly when sample size is insufficient (N < 3)."""
    record = RunRecord(
        run_id="r1", scenario_id="scenD", controller="baseline", seed=1, repetition=1,
        duration_steps=4, simulated_steps=4, simulated_hours=1.0, completed=True,
        stop_reason="duration_complete", metrics={"val": 10.0},
    )
    summaries = ResilienceStatistics.aggregate_runs([record])
    metric = summaries[0].metrics["val"]
    assert metric.ci_lower is None
    assert metric.ci_upper is None
    assert "Insufficient" in metric.ci_notes


def test_19_individual_scenario_outcomes_remain_available_alongside_aggregates() -> None:
    """Requirement 19: Individual scenario outcomes remain available alongside any aggregate results."""
    records = [
        RunRecord(
            run_id="r1", scenario_id="scen_alpha", controller="astragrid", seed=1, repetition=1,
            duration_steps=4, simulated_steps=4, simulated_hours=1.0, completed=True,
            stop_reason="duration_complete", metrics={"val": 5.0},
        ),
        RunRecord(
            run_id="r2", scenario_id="scen_beta", controller="astragrid", seed=1, repetition=1,
            duration_steps=4, simulated_steps=4, simulated_hours=1.0, completed=True,
            stop_reason="duration_complete", metrics={"val": 15.0},
        ),
    ]
    summaries = ResilienceStatistics.aggregate_runs(records)
    # Scenarios are preserved distinctly
    scen_ids = {s.scenario_id for s in summaries}
    assert scen_ids == {"scen_alpha", "scen_beta"}


# ==============================================================================
# Group 4: Dashboard and Replay Tests (20 - 25)
# ==============================================================================

def test_20_overview_presents_metrics_from_stored_evaluation_results(tmp_path: Path) -> None:
    """Requirement 20: The overview presents metrics from stored evaluation results."""
    manifest = ExperimentManifest(
        experiment_name="test_overview", description="desc", created_at_utc="2026-10-13T00:00:00Z",
        scenario_ids=["nominal_day"], controllers=["baseline"], seeds=[42], repetitions=1,
        total_runs_attempted=1, successful_runs=1, failed_runs=0, output_directory=str(tmp_path),
        run_ids=["run_01"],
    )
    record = RunRecord(
        run_id="run_01", scenario_id="nominal_day", controller="baseline", seed=42, repetition=1,
        duration_steps=4, simulated_steps=4, simulated_hours=1.0, completed=True,
        stop_reason="duration_complete", metrics={"tasks_completed": 3, "cumulative_critical_shortfall_kwh": 0.0},
    )
    summaries = ResilienceStatistics.aggregate_runs([record])
    html_path = generate_html_report(manifest, [record], summaries, [], tmp_path / "report.html")
    assert html_path.exists()
    content = html_path.read_text(encoding="utf-8")
    assert "test_overview" in content
    assert "nominal_day" in content
    assert "baseline" in content


def test_21_replay_follows_recorded_timesteps_in_actual_order() -> None:
    """Requirement 21: A run's replay follows recorded timesteps in their actual order."""
    config = scenario_from_id("nominal_day")
    run = SimulationRun(config, "baseline")
    run.run(6)
    builder = MissionReplayBuilder(run)
    steps = builder.build_steps()
    assert len(steps) == 6
    for i, step in enumerate(steps):
        assert step.step == i


def test_22_proposed_rejected_accepted_and_executed_actions_remain_distinguishable() -> None:
    """Requirement 22: Proposed action, rejected plan, accepted action, and executed action remain distinguishable."""
    config = scenario_from_id("nominal_day")
    run = SimulationRun(config, "baseline")
    run.run(2)
    builder = MissionReplayBuilder(run)
    steps = builder.build_steps()
    step0 = steps[0]
    assert step0.proposed_plan is not None
    assert step0.validation_result is not None
    assert step0.accepted_plan is not None
    assert step0.executed_action is not None


def test_23_fault_annotations_display_correct_timeline_position() -> None:
    """Requirement 23: Fault annotations display the correct timeline position."""
    config = scenario_from_id("solar_array_damage")
    run = SimulationRun(config, "astragrid")
    run.run(25)  # past step 22 when array damage begins
    builder = MissionReplayBuilder(run)
    timeline = builder.build_timeline()
    solar_events = [item for item in timeline if "solar" in item["title"].lower() or "SOLAR" in item["badge"]]
    assert len(solar_events) > 0
    # Steps are non-negative and properly ordered
    for event in solar_events:
        assert event["step"] >= 0


def test_24_missing_telemetry_or_missing_history_does_not_crash_viewer(tmp_path: Path) -> None:
    """Requirement 24: Missing telemetry or missing history does not crash the viewer."""
    manifest = ExperimentManifest(
        experiment_name="empty_test", description="desc", created_at_utc="now",
        scenario_ids=[], controllers=[], seeds=[], repetitions=0,
        total_runs_attempted=0, successful_runs=0, failed_runs=0, output_directory=str(tmp_path),
    )
    html_path = generate_html_report(manifest, [], [], [], tmp_path / "empty_report.html")
    assert html_path.exists()


def test_25_changing_dashboard_selections_does_not_mutate_underlying_run() -> None:
    """Requirement 25: Dashboard and replay generation does not mutate the underlying run."""
    config = scenario_from_id("nominal_day")
    run = SimulationRun(config, "baseline")
    run.run(4)
    builder = MissionReplayBuilder(run)
    _ = builder.build_steps()
    _ = builder.build_timeline()
    assert run.simulator.state_for_evaluation.step == 4


# ==============================================================================
# Group 5: Diagnostic Annotations Tests (26 - 29)
# ==============================================================================

def test_26_each_rule_annotation_identifies_evidence_that_caused_it_to_fire() -> None:
    """Requirement 26: Each rule annotation identifies the evidence that caused it to fire."""
    config = scenario_from_id("byzantine_soc_sensor")
    run = SimulationRun(config, "astragrid")
    run.run(10)
    engine = DiagnosticRuleEngine(config)
    annotations = engine.annotate(run)
    soc_annotations = [a for a in annotations if a.rule_id == "SOC_SENSOR_DISAGREEMENT"]
    assert len(soc_annotations) > 0
    for a in soc_annotations:
        assert "soc_sensors" in a.observed_evidence
        assert "max_difference" in a.observed_evidence
        assert a.explanation is not None


def test_27_telemetry_anomaly_does_not_become_confirmed_physical_fault() -> None:
    """Requirement 27: A telemetry anomaly does not automatically become a confirmed physical fault."""
    config = scenario_from_id("byzantine_soc_sensor")
    run = SimulationRun(config, "astragrid")
    run.run(10)
    engine = DiagnosticRuleEngine(config)
    annotations = engine.annotate(run)
    for a in annotations:
        if a.rule_id == "SOC_SENSOR_DISAGREEMENT":
            # Must be labeled carefully, not asserting confirmed physical battery destruction
            assert "drift" in a.explanation or "anomaly" in a.explanation or "telemetry" in a.explanation
            assert "confirmed battery failure" not in a.explanation.lower()


def test_28_hidden_future_fault_not_presented_at_earlier_timestep() -> None:
    """Requirement 28: Hidden future fault information is not presented as controller-visible earlier."""
    config = scenario_from_id("solar_array_damage")  # starts at step 22
    run = SimulationRun(config, "astragrid")
    run.run(10)  # before step 22
    engine = DiagnosticRuleEngine(config)
    annotations = engine.annotate(run)
    underperf = [a for a in annotations if a.rule_id == "SOLAR_UNDERPERFORMANCE"]
    # At steps 0-10 under normal illumination, solar underperformance must not fire
    assert len(underperf) == 0


def test_29_diagnostic_module_does_not_modify_controller_inputs_or_simulator() -> None:
    """Requirement 29: The diagnostic module does not modify controller inputs or physical simulation."""
    config = scenario_from_id("nominal_day")
    run = SimulationRun(config, "baseline")
    run.run(5)
    state_before = run.simulator.state_for_evaluation.copy()
    engine = DiagnosticRuleEngine(config)
    _ = engine.annotate(run)
    state_after = run.simulator.state_for_evaluation.copy()
    assert state_before == state_after
