"""Reproducible, paired controller benchmarking built on ``SimulationRun``."""
from __future__ import annotations

import csv
import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from statistics import mean, pstdev
from time import perf_counter
from typing import Iterable

from .config import ScenarioConfig, built_in_scenarios
from .runner import SimulationRun


def run_experiment(config: ScenarioConfig, controller: str, steps: int | None = None) -> SimulationRun:
    """Run one isolated controller experiment from a fresh scenario copy."""
    run = SimulationRun(config.model_copy(deep=True), controller)
    run.run(steps)
    return run


def create_paired_runs(config: ScenarioConfig, seed: int | None = None, *, trace_telemetry: bool = False) -> dict[str, SimulationRun]:
    """Create independent, equally configured controller runs.

    The current simulator is deterministic, but the configured seed is copied
    into both runs and retained in reports so future stochastic scenarios remain
    pairable without changing the benchmark interface.
    """
    paired_config = config.model_copy(deep=True)
    if seed is not None:
        paired_config.seed = seed
    return {
        name: SimulationRun(paired_config.model_copy(deep=True), name, trace_telemetry=trace_telemetry, trace_label=f"compare:{name}")
        for name in ("astragrid", "baseline")
    }


def compare(config: ScenarioConfig, steps: int | None = None) -> dict[str, SimulationRun]:
    """Run the two controllers against equivalent, independent scenarios."""
    runs = create_paired_runs(config, config.seed)
    for run in runs.values():
        run.run(steps)
    return runs


def _stop_reason(run: SimulationRun) -> str:
    truth = run.simulator.state_for_evaluation
    if run.manager.mode == "controlled_shutdown":
        return "controlled_shutdown"
    if truth.terminal:
        return "terminal_resource_failure"
    if truth.step >= run.config.duration_steps:
        return "duration_complete"
    return "step_limit_or_incomplete"


def collect_run_metrics(run: SimulationRun) -> dict[str, object]:
    """Calculate only metrics represented by the recorded run history."""
    cfg, transitions, tasks = run.config, run.transitions, run.ledger.snapshot()
    states = [transition.after for transition in transitions]
    truth = run.simulator.state_for_evaluation
    states_or_final = states or [truth]
    reserve = cfg.battery.safe_reserve_kwh
    control = run.control_history
    status_counts = Counter(str(item["proposed_status"]) for item in control)
    missed = [task for task in tasks if task["status"] == "missed"]
    completed = [task for task in tasks if task["status"] == "completed"]
    priority_by_id = {task.id: task.priority for task in cfg.tasks}
    total_value = sum(priority_by_id.values())
    completed_value = sum(priority_by_id.get(str(task["id"]), 0) for task in completed)
    resource_shortfall_steps = sum(1 for transition in transitions if any(value > 0 for value in transition.resource_shortfalls.values()))
    emergency_entries = sum(1 for event in run.events if event.action == "enter emergency")
    return {
        "steps_completed": truth.step,
        "simulated_hours": truth.step * cfg.dt_hours,
        "completed": run.completed,
        "stop_reason": _stop_reason(run),
        "critical_shortfall_steps": sum(1 for transition in transitions if transition.critical_shortfall_kw > 0),
        "critical_shortfall_kwh": truth.cumulative_critical_shortfall_kwh,
        "noncritical_shortfall_kwh": truth.cumulative_noncritical_shortfall_kwh,
        "minimum_battery_kwh": min(state.battery_kwh for state in states_or_final),
        "minimum_true_soc": min(state.battery_kwh / cfg.battery.capacity_kwh for state in states_or_final),
        "battery_reserve_violation_steps": sum(1 for state in states if state.battery_kwh < reserve),
        "battery_reserve_violation_hours": sum(cfg.dt_hours for state in states if state.battery_kwh < reserve),
        "minimum_oxygen_kg": min(state.oxygen_kg for state in states_or_final),
        "maximum_co2_kg": max(state.co2_kg for state in states_or_final),
        "minimum_water_kg": min(state.water_kg for state in states_or_final),
        "temperature_violation_steps": sum(1 for state in states if state.temperature_c < cfg.thermal.min_temperature_c or state.temperature_c > cfg.thermal.max_temperature_c),
        "resource_shortfall_steps": resource_shortfall_steps,
        "safety_violation_steps": sum(1 for transition in transitions if transition.critical_shortfall_kw > 1e-9 or any(v > 0 for v in transition.resource_shortfalls.values()) or transition.after.temperature_c < cfg.thermal.min_temperature_c - 1e-9 or transition.after.temperature_c > cfg.thermal.max_temperature_c + 1e-9),
        "backup_energy_kwh": sum(transition.backup_kw * cfg.dt_hours for transition in transitions),
        "total_tasks": len(tasks),
        "tasks_completed": len(completed),
        "task_completion_percent": 100.0 * len(completed) / len(tasks) if tasks else 0.0,
        "weighted_mission_value_completed": completed_value,
        "weighted_mission_value_total": total_value,
        "hard_deadline_misses": sum(1 for task in missed if task["hard_deadline"]),
        "soft_deadline_misses": sum(1 for task in missed if not task["hard_deadline"]),
        "mission_debt_steps": sum(int(task["remaining_steps"]) for task in tasks if task["status"] in {"pending", "running"}),
        "solver_calls": sum(1 for item in control if bool(item["solver_called"])),
        "solver_status_counts": dict(sorted(status_counts.items())),
        "proposed_plan_rejections": sum(1 for item in control if not bool(item["validation_accepted"])),
        "fallback_activations": sum(1 for item in control if bool(item["fallback_attempted"])),
        "emergency_actions": sum(1 for item in control if bool(item["emergency_policy"])),
        "emergency_mode_entries": emergency_entries,
        "invalid_actions_executed": sum(1 for item in control if item["executed_controller"] is not None and not bool(item["final_validation_accepted"])),
    }


def benchmark_from_runs(runs: dict[str, SimulationRun], *, wall_runtime_s: dict[str, float] | None = None) -> dict[str, object]:
    """Turn completed paired runs into a serialisable, honest comparison."""
    required = {"astragrid", "baseline"}
    if set(runs) != required:
        raise ValueError("a paired benchmark requires astragrid and baseline runs")
    astragrid_config = runs["astragrid"].config.model_dump()
    baseline_config = runs["baseline"].config.model_dump()
    if astragrid_config != baseline_config:
        raise ValueError("paired benchmark runs must use equivalent scenario configuration, seed, and fault schedule")
    records: list[dict[str, object]] = []
    metrics_by_controller: dict[str, dict[str, object]] = {}
    for controller in ("astragrid", "baseline"):
        run = runs[controller]
        metrics = collect_run_metrics(run)
        metrics_by_controller[controller] = metrics
        records.append({
            "scenario": run.config.id,
            "seed": run.config.seed,
            "controller": controller,
            "wall_runtime_s": (wall_runtime_s or {}).get(controller),
            **metrics,
        })
    deltas = {
        key: float(metrics_by_controller["astragrid"][key]) - float(metrics_by_controller["baseline"][key])
        for key in metrics_by_controller["astragrid"]
        if isinstance(metrics_by_controller["astragrid"][key], (int, float))
        and not isinstance(metrics_by_controller["astragrid"][key], bool)
        and isinstance(metrics_by_controller["baseline"].get(key), (int, float))
        and not isinstance(metrics_by_controller["baseline"][key], bool)
    }
    scenario, seed = records[0]["scenario"], records[0]["seed"]
    failures = [
        {"scenario": record["scenario"], "seed": record["seed"], "controller": record["controller"], "reason": record["stop_reason"]}
        for record in records if record["stop_reason"] != "duration_complete"
    ]
    return {
        "runs": records,
        "pairs": [{"scenario": scenario, "seed": seed, "astragrid": metrics_by_controller["astragrid"], "baseline": metrics_by_controller["baseline"], "delta_astragrid_minus_baseline": deltas}],
        "aggregate": _aggregate(records),
        "failures": failures,
        "reproducibility": {
            "paired_initial_conditions": True,
            "independent_mutable_runs": True,
            "configured_seed": seed,
            "note": "The current built-in simulator is deterministic; the seed is retained for future stochastic scenarios.",
        },
    }


def benchmark_scenario(config: ScenarioConfig, *, seeds: Iterable[int] | None = None, steps: int | None = None) -> dict[str, object]:
    """Benchmark one scenario over one or more reproducibly paired seeds."""
    seed_values = tuple(seeds) if seeds is not None else (config.seed,)
    all_runs: list[dict[str, object]] = []
    all_pairs: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []
    for seed in seed_values:
        runs = create_paired_runs(config, seed)
        timing: dict[str, float] = {}
        for name, run in runs.items():
            started = perf_counter()
            run.run(steps)
            timing[name] = perf_counter() - started
        pair = benchmark_from_runs(runs, wall_runtime_s=timing)
        all_runs.extend(pair["runs"])
        all_pairs.extend(pair["pairs"])
        failures.extend(pair["failures"])
    return {
        "runs": all_runs,
        "pairs": all_pairs,
        "aggregate": _aggregate(all_runs),
        "failures": failures,
        "scenarios": [config.id],
        "seeds": list(seed_values),
    }


def benchmark(scenario_ids: Iterable[str] | None = None, *, seeds: Iterable[int] | None = None, steps: int | None = None) -> dict[str, object]:
    """Benchmark selected built-in scenarios and retain unsupported requests."""
    seed_values = tuple(seeds) if seeds is not None else None
    available = built_in_scenarios()
    selected = list(scenario_ids) if scenario_ids is not None else sorted(available)
    all_runs: list[dict[str, object]] = []
    all_pairs: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []
    for scenario_id in selected:
        config = available.get(scenario_id)
        if config is None:
            failures.append({"scenario": scenario_id, "seed": None, "controller": None, "reason": "unsupported_scenario"})
            continue
        report = benchmark_scenario(config, seeds=seed_values, steps=steps)
        all_runs.extend(report["runs"])
        all_pairs.extend(report["pairs"])
        failures.extend(report["failures"])
    return {
        "runs": all_runs,
        "pairs": all_pairs,
        "aggregate": _aggregate(all_runs),
        "failures": failures,
        "available_scenarios": sorted(available),
        "selected_scenarios": selected,
        "seeds": list(seed_values) if seed_values is not None else None,
    }


def _aggregate(records: list[dict[str, object]]) -> list[dict[str, object]]:
    """Summarise compatible numeric metrics without hiding worst outcomes."""
    groups: dict[tuple[str, str], list[dict[str, object]]] = {}
    for record in records:
        groups.setdefault((str(record["scenario"]), str(record["controller"])), []).append(record)
    summary: list[dict[str, object]] = []
    for (scenario, controller), rows in sorted(groups.items()):
        item: dict[str, object] = {"scenario": scenario, "controller": controller, "seed_count": len(rows)}
        numeric_keys = {key for row in rows for key, value in row.items() if isinstance(value, (int, float)) and not isinstance(value, bool)}
        for key in sorted(numeric_keys):
            values = [float(row[key]) for row in rows if isinstance(row.get(key), (int, float)) and not isinstance(row.get(key), bool)]
            if values:
                item[f"{key}_mean"] = mean(values)
                lower_is_worse = key.startswith("minimum_") or key in {"tasks_completed", "task_completion_percent", "weighted_mission_value_completed"}
                item[f"{key}_worst"] = min(values) if lower_is_worse else max(values)
                item[f"{key}_stdev"] = pstdev(values) if len(values) > 1 else 0.0
        summary.append(item)
    return summary


def format_pair_summary(pair: dict[str, object]) -> str:
    """Compact human-readable dashboard result; it deliberately declares ties."""
    astra = pair["astragrid"]
    baseline = pair["baseline"]
    delta = pair["delta_astragrid_minus_baseline"]
    assert isinstance(astra, dict) and isinstance(baseline, dict) and isinstance(delta, dict)
    def value(source: dict[str, object], key: str, digits: int = 2) -> str:
        raw = source.get(key, "—")
        return f"{float(raw):.{digits}f}" if isinstance(raw, (int, float)) and not isinstance(raw, bool) else str(raw)
    return (
        f"FINAL BENCHMARK - {pair['scenario']} (seed {pair['seed']})\n"
        f"                      AstraGrid     Baseline     Delta Astro-Base\n"
        f"Critical shortfall:  {value(astra, 'critical_shortfall_kwh')} kWh       {value(baseline, 'critical_shortfall_kwh')} kWh       {value(delta, 'critical_shortfall_kwh')} kWh\n"
        f"Minimum battery:     {value(astra, 'minimum_battery_kwh')} kWh       {value(baseline, 'minimum_battery_kwh')} kWh       {value(delta, 'minimum_battery_kwh')} kWh\n"
        f"Tasks completed:     {value(astra, 'tasks_completed', 0)}/{value(astra, 'total_tasks', 0)}          {value(baseline, 'tasks_completed', 0)}/{value(baseline, 'total_tasks', 0)}          {value(delta, 'tasks_completed', 0)}\n"
        f"Hard misses / fallbacks: {value(astra, 'hard_deadline_misses', 0)} / {value(astra, 'fallback_activations', 0)}      {value(baseline, 'hard_deadline_misses', 0)} / {value(baseline, 'fallback_activations', 0)}\n"
        f"Stop reason: {astra['stop_reason']} | {baseline['stop_reason']}"
    )


def export_run(run: SimulationRun, output_dir: str | Path) -> dict[str, Path]:
    target = Path(output_dir); target.mkdir(parents=True, exist_ok=True)
    stem = f"{run.config.id}_{run.controller_name}"
    json_path, csv_path = target / f"{stem}.json", target / f"{stem}_steps.csv"
    json_path.write_text(json.dumps({"snapshot": run.snapshot(), "events": run.events_view(0, 100000), "tasks": run.ledger.snapshot(), "faults": run.faults_view(), "control_history": run.control_history}, indent=2, default=str), encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["step", "battery_kwh", "oxygen_kg", "co2_kg", "water_kg", "temperature_c", "solar_kw", "backup_kw", "load_kw", "critical_shortfall_kw", "noncritical_shortfall_kw"])
        writer.writeheader()
        for transition in run.transitions:
            writer.writerow({"step": transition.after.step, "battery_kwh": transition.after.battery_kwh, "oxygen_kg": transition.after.oxygen_kg, "co2_kg": transition.after.co2_kg, "water_kg": transition.after.water_kg, "temperature_c": transition.after.temperature_c, "solar_kw": transition.solar_kw, "backup_kw": transition.backup_kw, "load_kw": transition.demand_kw, "critical_shortfall_kw": transition.critical_shortfall_kw, "noncritical_shortfall_kw": transition.noncritical_shortfall_kw})
    return {"json": json_path, "csv": csv_path}


def _write_rows(path: Path, rows: list[dict[str, object]]) -> None:
    fields = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, sort_keys=True) if isinstance(value, (dict, list)) else value for key, value in row.items()})


def export_benchmark(report: dict[str, object], output_dir: str | Path) -> dict[str, Path]:
    """Write raw run rows, paired deltas, aggregate data, and failures."""
    target = Path(output_dir); target.mkdir(parents=True, exist_ok=True)
    json_path = target / "benchmark_report.json"
    json_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    paths = {"json": json_path}
    for key, filename in (("runs", "benchmark_runs.csv"), ("pairs", "benchmark_pairs.csv"), ("aggregate", "benchmark_aggregate.csv"), ("failures", "benchmark_failures.csv")):
        rows = report.get(key, [])
        if isinstance(rows, list):
            path = target / filename
            _write_rows(path, rows)
            paths[key] = path
    return paths


def export_comparison(runs: dict[str, SimulationRun], output_dir: str | Path) -> Path:
    """Backwards-compatible single-pair export used by the existing CLI."""
    target = Path(output_dir); target.mkdir(parents=True, exist_ok=True)
    for run in runs.values():
        export_run(run, target)
    report = benchmark_from_runs(runs)
    export_benchmark(report, target)
    path = target / "comparison_summary.csv"
    _write_rows(path, list(report["runs"]))
    return path


def run_all(output_dir: str | Path, steps: int | None = None) -> list[dict[str, object]]:
    report = benchmark(steps=steps)
    export_benchmark(report, output_dir)
    return list(report["runs"])
