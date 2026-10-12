"""FastAPI JSON surface and Mission Control web dashboard. Run state is intentionally process-local/in-memory."""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from time import perf_counter
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from . import __version__
from .config import ScenarioConfig, built_in_scenarios, scenario_from_id
from .evaluation import benchmark_from_runs, compare, create_paired_runs
from .runner import SimulationRun


class CreateRunRequest(BaseModel):
    scenario_id: str
    controller: Literal["astragrid", "baseline", "compare"] = "astragrid"
    overrides: dict[str, object] = Field(default_factory=dict, description="Validated nested ScenarioConfig overrides.")


class RunRequest(BaseModel):
    steps: int = Field(default=1, ge=1, le=1000)


class CompareRequest(BaseModel):
    scenario_id: str
    steps: int | None = None
    seed: int | None = None


class RunStore:
    def __init__(self) -> None: self.runs: dict[str, SimulationRun] = {}
    def get(self, run_id: str) -> SimulationRun:
        try: return self.runs[run_id]
        except KeyError: raise HTTPException(404, detail={"code": "RUN_NOT_FOUND", "message": f"Unknown run '{run_id}'."})


def _merge(base: dict[str, object], overrides: dict[str, object]) -> dict[str, object]:
    """Small, explicit recursive merge for validated API scenario overrides."""
    result = dict(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)  # type: ignore[arg-type]
        else:
            result[key] = value
    return result


def create_app() -> FastAPI:
    app, store = FastAPI(title="AstraGrid", version=__version__), RunStore()
    app.state.run_store = store

    static_dir = Path(__file__).resolve().parent / "web" / "static"
    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/")
    @app.get("/dashboard")
    def dashboard() -> FileResponse:
        index_file = static_dir / "index.html"
        if not index_file.is_file():
            raise HTTPException(404, detail="Dashboard index.html not found.")
        return FileResponse(str(index_file))

    @app.get("/health")
    def health() -> dict[str, object]:
        try:
            import scipy.optimize  # noqa: F401
            optimiser = True
        except Exception:
            optimiser = False
        return {"status": "ok", "version": __version__, "optimiser_available": optimiser, "prototype": True}

    @app.get("/api/scenarios")
    def scenarios() -> list[dict[str, str]]:
        return [{"id": cfg.id, "description": cfg.description} for cfg in built_in_scenarios().values()]

    @app.post("/api/simulations")
    def create(request: CreateRunRequest) -> dict[str, object]:
        try: config = scenario_from_id(request.scenario_id)
        except KeyError as exc: raise HTTPException(422, detail={"code": "INVALID_SCENARIO", "message": str(exc)})
        if request.overrides:
            try: config = ScenarioConfig.model_validate(_merge(config.model_dump(), request.overrides))
            except ValidationError as exc: raise HTTPException(422, detail={"code": "INVALID_OVERRIDE", "message": str(exc)})
        if request.controller == "compare":
            runs = compare(config, 0)
            for run in runs.values(): store.runs[run.id] = run
            return {"run_id": runs["astragrid"].id, "comparison_run_ids": {key: run.id for key, run in runs.items()}, "initial_state": runs["astragrid"].state_view()}
        run = SimulationRun(config, request.controller); store.runs[run.id] = run
        return {"run_id": run.id, "initial_state": run.state_view()}

    @app.post("/api/simulations/{run_id}/step")
    def step(run_id: str) -> dict[str, object]: return store.get(run_id).step()

    @app.post("/api/simulations/{run_id}/run")
    def run(run_id: str, request: RunRequest) -> dict[str, object]: return store.get(run_id).run(request.steps)

    @app.get("/api/simulations/{run_id}/state")
    def state(run_id: str) -> dict[str, object]: return store.get(run_id).state_view()

    @app.get("/api/simulations/{run_id}/plan")
    def plan(run_id: str) -> dict[str, object]: return store.get(run_id).plan_view()

    @app.get("/api/simulations/{run_id}/tasks")
    def tasks(run_id: str) -> dict[str, object]:
        run = store.get(run_id); return {"tasks": run.ledger.snapshot(), "ledger_history": run.ledger.history}

    @app.get("/api/simulations/{run_id}/events")
    def events(run_id: str, offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=1000)) -> dict[str, object]:
        run = store.get(run_id); return {"events": run.events_view(offset, limit), "offset": offset, "limit": limit}

    @app.get("/api/simulations/{run_id}/metrics")
    def metrics(run_id: str) -> dict[str, object]: return store.get(run_id).metrics()

    @app.get("/api/simulations/{run_id}/faults")
    def faults(run_id: str) -> dict[str, object]: return store.get(run_id).faults_view()

    @app.get("/api/simulations/{run_id}/full_view")
    def full_view(run_id: str) -> dict[str, object]:
        run = store.get(run_id)
        history: list[dict[str, object]] = []
        for idx, trans in enumerate(run.transitions):
            after = trans.after
            solar = trans.solar_kw
            backup = trans.backup_kw
            shortfall = trans.critical_shortfall_kw
            demand = trans.demand_kw
            history.append({
                "step": idx + 1,
                "solar_kw": solar,
                "backup_kw": backup,
                "battery_kwh": after.battery_kwh,
                "soc": after.battery_kwh / run.config.battery.capacity_kwh,
                "bus_demand_kw": demand,
                "critical_load_kw": 10.5,
                "critical_shortfall_kw": shortfall,
                "oxygen_kg": after.oxygen_kg,
                "co2_kg": after.co2_kg,
                "water_kg": after.water_kg,
                "temperature_c": after.temperature_c,
            })

        last_obs = asdict(run.last_observation) if run.last_observation else None

        return {
            "snapshot": run.snapshot(),
            "state": run.state_view(),
            "plan": run.plan_view(),
            "metrics": run.metrics(),
            "tasks": run.ledger.snapshot(),
            "events": [e.to_dict() for e in run.events],
            "faults": run.faults_view(),
            "last_observation": last_obs,
            "history": history,
            "step_count": len(run.transitions),
            "max_steps": run.config.duration_steps,
            "battery_capacity": run.config.battery.capacity_kwh,
            "battery_reserve": run.config.battery.safe_reserve_kwh,
            "oxygen_reserve": run.config.life_support.oxygen_reserve_kg,
            "co2_limit": run.config.life_support.co2_limit_kg,
            "water_reserve": run.config.life_support.water_reserve_kg,
            "temp_min": run.config.thermal.min_temperature_c,
            "temp_max": run.config.thermal.max_temperature_c,
        }

    @app.get("/api/simulations/{run_id}/replay_data")
    def replay_data(run_id: str) -> dict[str, object]:
        run = store.get(run_id)
        steps_data: list[dict[str, object]] = []
        for idx in range(len(run.transitions)):
            trans = run.transitions[idx]
            obs = asdict(run.telemetry_history[idx]) if idx < len(run.telemetry_history) else None
            est = asdict(run.estimates[idx]) if idx < len(run.estimates) else None
            cap = run.capability_estimate_history[idx] if idx < len(run.capability_estimate_history) else None
            ctrl = run.control_history[idx] if idx < len(run.control_history) else None
            audit = run.execution_audit[idx] if idx < len(run.execution_audit) else None
            step_events = [e.to_dict() for e in run.events if e.step == idx]

            steps_data.append({
                "step": idx,
                "observation": obs,
                "estimated_state": est,
                "capability": cap,
                "control": ctrl,
                "audit": audit,
                "transition": {
                    "solar_kw": trans.solar_kw,
                    "critical_shortfall_kw": trans.critical_shortfall_kw,
                    "noncritical_shortfall_kw": trans.noncritical_shortfall_kw,
                    "resource_shortfalls": trans.resource_shortfalls,
                    "before": asdict(trans.before),
                    "after": asdict(trans.after),
                    "transition_error": trans.transition_error,
                },
                "events": step_events,
            })

        return {
            "total_steps": len(run.transitions),
            "max_steps": run.config.duration_steps,
            "steps": steps_data,
        }

    @app.post("/api/simulations/compare_full")
    def compare_full(request: CompareRequest) -> dict[str, object]:
        try:
            cfg = scenario_from_id(request.scenario_id)
        except KeyError as exc:
            raise HTTPException(422, detail={"code": "INVALID_SCENARIO", "message": str(exc)})
        if request.seed is not None:
            cfg = cfg.model_copy(deep=True)
            cfg.seed = request.seed

        runs = create_paired_runs(cfg, cfg.seed)
        timing: dict[str, float] = {}
        for name, r in runs.items():
            t0 = perf_counter()
            r.run(request.steps)
            timing[name] = perf_counter() - t0
            store.runs[r.id] = r

        benchmark_result = benchmark_from_runs(runs, wall_runtime_s=timing)
        pair = benchmark_result["pairs"][0]
        return {
            "scenario": request.scenario_id,
            "seed": cfg.seed,
            "pair": pair,
            "astragrid_run_id": runs["astragrid"].id,
            "baseline_run_id": runs["baseline"].id,
        }

    return app


app = create_app()

