"""FastAPI JSON surface. Run state is intentionally process-local/in-memory."""
from __future__ import annotations

from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, ValidationError

from . import __version__
from .config import ScenarioConfig, built_in_scenarios, scenario_from_id
from .evaluation import compare
from .runner import SimulationRun


class CreateRunRequest(BaseModel):
    scenario_id: str
    controller: Literal["astragrid", "baseline", "compare"] = "astragrid"
    overrides: dict[str, object] = Field(default_factory=dict, description="Validated nested ScenarioConfig overrides.")


class RunRequest(BaseModel):
    steps: int = Field(default=1, ge=1, le=1000)


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
    return app


app = create_app()
