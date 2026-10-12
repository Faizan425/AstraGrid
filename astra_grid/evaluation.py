"""Repeatable controller comparisons and JSON/CSV artefact export."""
from __future__ import annotations

import csv
import json
from dataclasses import asdict
from pathlib import Path

from .config import ScenarioConfig, built_in_scenarios
from .runner import SimulationRun


def run_experiment(config: ScenarioConfig, controller: str, steps: int | None = None) -> SimulationRun:
    run = SimulationRun(config.model_copy(deep=True), controller)
    run.run(steps)
    return run


def compare(config: ScenarioConfig, steps: int | None = None) -> dict[str, SimulationRun]:
    """Use fresh copies so controllers share exactly the same initial scenario/seed."""
    return {name: run_experiment(config, name, steps) for name in ("astragrid", "baseline")}


def export_run(run: SimulationRun, output_dir: str | Path) -> dict[str, Path]:
    target = Path(output_dir); target.mkdir(parents=True, exist_ok=True)
    stem = f"{run.config.id}_{run.controller_name}"
    json_path, csv_path = target / f"{stem}.json", target / f"{stem}_steps.csv"
    json_path.write_text(json.dumps({"snapshot": run.snapshot(), "events": run.events_view(0, 100000), "tasks": run.ledger.snapshot(), "faults": run.faults_view()}, indent=2, default=str), encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["step", "battery_kwh", "oxygen_kg", "co2_kg", "water_kg", "temperature_c", "solar_kw", "load_kw", "critical_shortfall_kw", "noncritical_shortfall_kw"])
        writer.writeheader()
        for transition in run.transitions:
            writer.writerow({"step": transition.after.step, "battery_kwh": transition.after.battery_kwh, "oxygen_kg": transition.after.oxygen_kg, "co2_kg": transition.after.co2_kg, "water_kg": transition.after.water_kg, "temperature_c": transition.after.temperature_c, "solar_kw": transition.solar_kw, "load_kw": transition.demand_kw, "critical_shortfall_kw": transition.critical_shortfall_kw, "noncritical_shortfall_kw": transition.noncritical_shortfall_kw})
    return {"json": json_path, "csv": csv_path}


def export_comparison(runs: dict[str, SimulationRun], output_dir: str | Path) -> Path:
    target = Path(output_dir); target.mkdir(parents=True, exist_ok=True)
    rows = []
    for name, run in runs.items():
        export_run(run, target)
        rows.append({"controller": name, **{key: value for key, value in run.metrics().items() if not isinstance(value, (dict, list))}})
    path = target / "comparison_summary.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=sorted({key for row in rows for key in row}))
        writer.writeheader(); writer.writerows(rows)
    (target / "comparison_summary.json").write_text(json.dumps(rows, indent=2, default=str), encoding="utf-8")
    return path


def run_all(output_dir: str | Path, steps: int | None = None) -> list[dict[str, object]]:
    summaries = []
    for config in built_in_scenarios().values():
        runs = compare(config, steps)
        export_comparison(runs, Path(output_dir) / config.id)
        summaries.extend({"scenario": config.id, "controller": name, **run.metrics()} for name, run in runs.items())
    return summaries
