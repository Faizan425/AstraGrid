from __future__ import annotations

from typing import Any, Callable
import pytest
from fastapi import HTTPException

from astra_grid.api import AddTaskRequest, CreateRunRequest, InjectFaultRequest, RunRequest, create_app
from astra_grid.cascade import CascadeAnalyzer
from astra_grid.config import scenario_from_id
from astra_grid.faults import FaultEngine
from astra_grid.models import Action, Plan
from astra_grid.policies import FixedScheduleController, RuleBasedResourceAwareController
from astra_grid.runner import SimulationRun
from astra_grid.simulator import HabitatSimulator


def get_endpoint(app: Any, path: str, method: str = "POST") -> Callable[..., Any]:
    for route in app.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", []):
            return route.endpoint  # type: ignore[no-any-return]
    raise KeyError(f"No route for {method} {path}")


def test_water_mass_balance_tracking_in_transition() -> None:
    config = scenario_from_id("nominal_day")
    sim = HabitatSimulator(config, FaultEngine([]))
    before = sim.state_for_evaluation
    transition = sim.advance(Action(backup_kw=0.0, water_recovery_on=True))
    after = transition.after

    assert transition.water_recovered_kg is not None
    assert transition.water_demand_kg is not None
    assert transition.water_recovery_power_kw is not None
    assert transition.water_recovered_kg >= 0.0
    assert transition.water_demand_kg > 0.0

    # Strict conservation of water mass: water_after = water_before + recovered - demanded
    expected_water = before.water_kg + transition.water_recovered_kg - transition.water_demand_kg
    assert after.water_kg == pytest.approx(expected_water, abs=1e-5)


def test_water_metrics_in_simulation_runner() -> None:
    config = scenario_from_id("nominal_day")
    run = SimulationRun(config, controller="astragrid")
    run.run(steps=12)
    metrics = run.metrics()

    assert "initial_water_kg" in metrics
    assert "final_water_kg" in metrics
    assert "total_water_recovered_kg" in metrics
    assert "total_water_consumed_kg" in metrics
    assert "water_reserve_violations" in metrics
    assert "water_reserve_deficit_kgh" in metrics
    assert "water_recovery_energy_kwh" in metrics

    # Conservation across multiple timesteps
    conserved_final = (
        metrics["initial_water_kg"]
        + metrics["total_water_recovered_kg"]
        - metrics["total_water_consumed_kg"]
    )
    assert metrics["final_water_kg"] == pytest.approx(conserved_final, abs=1e-4)


def test_fixed_schedule_controller() -> None:
    config = scenario_from_id("nominal_day")
    controller = FixedScheduleController(config, water_recovery_rate=0.75)
    run = SimulationRun(config, controller="fixed_schedule")
    assert isinstance(run.controller, FixedScheduleController)

    run.step()
    assert len(run.transitions) == 1
    plan = run.last_proposed
    assert isinstance(plan, Plan)
    assert plan.actions[0].water_recovery_on is True


def test_rule_based_resource_aware_controller() -> None:
    config = scenario_from_id("nominal_day")
    controller = RuleBasedResourceAwareController(config, target_soc=0.6)
    run = SimulationRun(config, controller="rule_based")
    assert isinstance(run.controller, RuleBasedResourceAwareController)

    run.run(steps=8)
    assert len(run.transitions) == 8
    metrics = run.metrics()
    assert metrics["controller"] == "rule_based"
    assert metrics["tasks_completed"] >= 0


def test_cascade_analyzer_detects_energy_water_cascade() -> None:
    config = scenario_from_id("combined_fault")
    run = SimulationRun(config, controller="baseline")
    run.run(steps=24)

    analyzer = CascadeAnalyzer(run)
    graph = analyzer.build_graph()
    summaries = analyzer.summarize_cascades()

    assert "nodes" in graph
    assert "edges" in graph
    assert isinstance(summaries, list)
    for s in summaries:
        d = s.to_dict()
        assert "root_cause" in d
        assert "impacted_subsystems" in d
        assert "depth" in d
        assert "narrative" in d


def test_api_manual_fault_injection_and_clear() -> None:
    app = create_app()
    create_fn = get_endpoint(app, "/api/simulations", "POST")
    inject_fn = get_endpoint(app, "/api/simulations/{run_id}/faults/inject", "POST")
    clear_fn = get_endpoint(app, "/api/simulations/{run_id}/faults/clear", "POST")
    step_fn = get_endpoint(app, "/api/simulations/{run_id}/step", "POST")

    create_res = create_fn(CreateRunRequest(scenario_id="nominal_day", controller="astragrid"))
    run_id = str(create_res["run_id"])

    # Inject solar drop fault
    inject_res = inject_fn(
        run_id,
        InjectFaultRequest(
            target="solar",
            parameters={"solar_factor": 0.5},
            category="physical",
            duration_steps=10,
        ),
    )
    assert inject_res["status"] == "ok"
    assert "fault_id" in inject_res

    # Inject Byzantine sensor fault with category='sensor' and target='battery_soc'
    inject_sensor_res = inject_fn(
        run_id,
        InjectFaultRequest(
            target="battery_soc",
            parameters={"reported_soc": 0.75},
            category="sensor",
            duration_steps=10,
        ),
    )
    assert inject_sensor_res["status"] == "ok"
    assert inject_sensor_res["category"] == "telemetry"
    assert inject_sensor_res["target"] == "soc_sensor_2"

    # Step simulation to observe fault taking effect
    step_res = step_fn(run_id)
    assert "step" in step_res

    # Clear faults
    clear_res = clear_fn(run_id)
    assert clear_res["status"] == "ok"
    assert clear_res["cleared_count"] >= 2


def test_api_dynamic_task_addition() -> None:
    app = create_app()
    create_fn = get_endpoint(app, "/api/simulations", "POST")
    add_task_fn = get_endpoint(app, "/api/simulations/{run_id}/tasks/add", "POST")

    create_res = create_fn(CreateRunRequest(scenario_id="nominal_day", controller="astragrid"))
    run_id = str(create_res["run_id"])

    task_payload = AddTaskRequest(
        id="operator_drill_01",
        power_kw=3.0,
        duration_steps=2,
        priority=2,
        deadline_step=15,
        hard_deadline=False,
    )
    add_res = add_task_fn(run_id, task_payload)
    assert add_res["status"] == "ok"
    assert add_res["task_id"] == "operator_drill_01"

    # Duplicate should fail with 400
    with pytest.raises(HTTPException) as exc_info:
        add_task_fn(run_id, task_payload)
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["code"] == "DUPLICATE_TASK_ID"


def test_api_cascade_endpoint() -> None:
    app = create_app()
    create_fn = get_endpoint(app, "/api/simulations", "POST")
    run_fn = get_endpoint(app, "/api/simulations/{run_id}/run", "POST")
    cascades_fn = get_endpoint(app, "/api/simulations/{run_id}/cascades", "GET")

    create_res = create_fn(CreateRunRequest(scenario_id="combined_fault", controller="baseline"))
    run_id = str(create_res["run_id"])

    run_fn(run_id, RunRequest(steps=16))

    casc_res = cascades_fn(run_id)
    assert "graph" in casc_res
    assert "summaries" in casc_res
