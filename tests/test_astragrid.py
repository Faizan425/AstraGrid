from __future__ import annotations

import pytest

from fastapi import HTTPException

from astra_grid.api import CreateRunRequest, create_app
from astra_grid.config import BatteryConfig, FaultConfig, ScenarioConfig, scenario_from_id
from astra_grid.control import PlanValidator
from astra_grid.faults import FaultEngine
from astra_grid.models import Action, EstimatedState, Mode, Plan, TaskStatus
from astra_grid.runner import SimulationRun
from astra_grid.safety import SurvivalEstimator
from astra_grid.simulator import HabitatSimulator
from astra_grid.tasks import MissionDebtLedger
from astra_grid.telemetry import StateEstimator, TelemetryGenerator


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
    run = SimulationRun(scenario_from_id("combined_fault"), "astragrid")
    run.run(12)
    view = run.plan_view()
    assert view["proposed"] is not None and view["accepted"] is not None and view["executed"] is not None
    assert any("soc_sensor_2" in str(event.values) for event in run.events)
    assert all(transition.after.battery_kwh >= 0 for transition in run.transitions)


def test_deferred_work_recovers_after_telemetry_recovers() -> None:
    run = SimulationRun(scenario_from_id("combined_fault"), "astragrid")
    run.run(30)
    recovered = run.ledger.tasks["science_task_02"]
    assert recovered.deferrals == 1 and recovered.status == TaskStatus.COMPLETED


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
