"""Automated experiment runner for AstraGrid Resilience Lab.

Orchestrates reproducible batch runs across multiple habitat scenarios,
controllers, and seeds with isolated mutable state, failure capture,
and structured multi-format persistence.
"""
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Iterable

from astra_grid.config import ScenarioConfig, built_in_scenarios, scenario_from_id
from astra_grid.runner import SimulationRun

from .diagnostics import DiagnosticRuleEngine
from .evaluator import IndependentEvaluator
from .models import ExperimentConfig, ExperimentManifest, RunRecord
from .replay import MissionReplayBuilder
from .statistics import PairedComparison, ResilienceStatistics, ScenarioControllerSummary


class ExperimentRunner:
    """Orchestrates reproducible evaluation experiments."""

    def __init__(self, config: ExperimentConfig | None = None) -> None:
        self.config = config or ExperimentConfig()
        self.evaluator = IndependentEvaluator()
        self.diagnostics = DiagnosticRuleEngine()

    def run_single(
        self,
        scenario_id: str,
        controller: str,
        seed: int | None = None,
        repetition: int = 1,
        steps: int | None = None,
        save_detailed: bool = True,
        output_dir: Path | None = None,
    ) -> tuple[RunRecord, SimulationRun | None]:
        """Execute one isolated, reproducible controller run."""
        available = built_in_scenarios()
        base_scenario = available.get(scenario_id)
        if base_scenario is None:
            failed_record = RunRecord(
                run_id=f"{scenario_id}_{controller}_s{seed}_r{repetition}_unsupported",
                scenario_id=scenario_id,
                controller=controller,
                seed=seed,
                repetition=repetition,
                duration_steps=0,
                simulated_steps=0,
                simulated_hours=0.0,
                completed=False,
                stop_reason="unsupported_scenario",
                execution_error=f"Scenario '{scenario_id}' not found in built-in scenarios.",
            )
            return failed_record, None

        # Create isolated deep copy
        scen_copy = base_scenario.model_copy(deep=True)
        if seed is not None:
            scen_copy.seed = seed

        # Apply fault variations if specified in config
        if self.config.fault_variations:
            for variation in self.config.fault_variations:
                if variation.get("scenario_id") == scenario_id:
                    for fault in scen_copy.faults:
                        if fault.id == variation.get("fault_id"):
                            fault.parameters.update(variation.get("parameters", {}))

        run_id = f"{scenario_id}_{controller}_s{seed}_r{repetition}"
        sim_run = SimulationRun(scen_copy, controller)
        sim_run.id = run_id

        wall_start = perf_counter()
        execution_error: str | None = None
        try:
            sim_run.run(steps)
        except Exception as exc:
            execution_error = f"{type(exc).__name__}: {exc}"

        wall_runtime = perf_counter() - wall_start

        # Evaluate objectively from recorded outcomes
        record = self.evaluator.evaluate(
            sim_run,
            repetition=repetition,
            wall_runtime_s=wall_runtime,
            execution_error=execution_error,
        )

        # Generate evidence-based diagnostic annotations
        annotations = self.diagnostics.annotate(sim_run)
        record.diagnostic_annotations = annotations

        # Optionally save detailed per-step history and replay
        target_dir = output_dir or self.config.output_dir
        if save_detailed and target_dir:
            self._save_run_details(sim_run, record, annotations, target_dir)

        return record, sim_run

    def run_experiment(self, config: ExperimentConfig | None = None) -> tuple[ExperimentManifest, list[RunRecord], list[ScenarioControllerSummary], list[PairedComparison]]:
        """Execute the full batch experiment across all configured scenarios, controllers, and seeds."""
        cfg = config or self.config
        output_dir = Path(cfg.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        all_records: list[RunRecord] = []
        created_at = datetime.now(timezone.utc).isoformat()

        for scenario_id in cfg.scenario_ids:
            for seed in cfg.seeds:
                for rep in range(1, cfg.repetitions + 1):
                    for controller in cfg.controllers:
                        record, _ = self.run_single(
                            scenario_id=scenario_id,
                            controller=controller,
                            seed=seed,
                            repetition=rep,
                            steps=cfg.steps,
                            save_detailed=cfg.save_detailed_history,
                            output_dir=output_dir,
                        )
                        all_records.append(record)

        # Calculate statistical summaries and paired comparisons
        summaries = ResilienceStatistics.aggregate_runs(all_records)
        comparisons = ResilienceStatistics.compare_paired(all_records)

        # Build manifest
        successful_count = sum(1 for r in all_records if r.completed and not r.execution_error)
        manifest = ExperimentManifest(
            experiment_name=cfg.name,
            description=cfg.description,
            created_at_utc=created_at,
            scenario_ids=list(cfg.scenario_ids),
            controllers=list(cfg.controllers),
            seeds=list(cfg.seeds),
            repetitions=cfg.repetitions,
            total_runs_attempted=len(all_records),
            successful_runs=successful_count,
            failed_runs=len(all_records) - successful_count,
            output_directory=str(output_dir),
            run_ids=[r.run_id for r in all_records],
        )

        # Persist standard outputs
        self._save_experiment_outputs(manifest, all_records, summaries, comparisons, output_dir)

        return manifest, all_records, summaries, comparisons

    def _save_run_details(
        self,
        run: SimulationRun,
        record: RunRecord,
        annotations: list[Any],
        output_dir: Path,
    ) -> None:
        """Save per-step CSV and full replay JSON for a single run."""
        hist_dir = output_dir / "per_step_history"
        replay_dir = output_dir / "replay"
        hist_dir.mkdir(parents=True, exist_ok=True)
        replay_dir.mkdir(parents=True, exist_ok=True)

        # Save per-step CSV
        csv_path = hist_dir / f"{record.run_id}.csv"
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "step", "solar_kw", "backup_kw", "charge_kw", "discharge_kw",
                "demand_kw", "supplied_kw", "critical_shortfall_kw", "noncritical_shortfall_kw",
                "battery_kwh", "soc", "oxygen_kg", "co2_kg", "water_kg", "temperature_c",
            ])
            for i, t in enumerate(run.transitions):
                s = t.after
                writer.writerow([
                    i, t.solar_kw, t.backup_kw, t.battery_charge_kw, t.battery_discharge_kw,
                    t.demand_kw, t.supplied_kw, t.critical_shortfall_kw, t.noncritical_shortfall_kw,
                    s.battery_kwh, s.battery_kwh / run.config.battery.capacity_kwh,
                    s.oxygen_kg, s.co2_kg, s.water_kg, s.temperature_c,
                ])

        # Save replay JSON
        replay_builder = MissionReplayBuilder(run, annotations)
        replay_steps = replay_builder.build_steps()
        timeline = replay_builder.build_timeline()

        replay_payload = {
            "run_id": record.run_id,
            "scenario_id": record.scenario_id,
            "controller": record.controller,
            "seed": record.seed,
            "timeline": timeline,
            "steps": [s.to_dict() for s in replay_steps],
        }
        with open(replay_dir / f"{record.run_id}.json", "w", encoding="utf-8") as f:
            json.dump(replay_payload, f, indent=2)

    def _save_experiment_outputs(
        self,
        manifest: ExperimentManifest,
        records: list[RunRecord],
        summaries: list[ScenarioControllerSummary],
        comparisons: list[PairedComparison],
        output_dir: Path,
    ) -> None:
        """Persist experiment manifest, run summaries, and comparisons in JSON and CSV formats."""
        # 1. Manifest
        with open(output_dir / "experiment_manifest.json", "w", encoding="utf-8") as f:
            json.dump(manifest.to_dict(), f, indent=2)

        # 2. Runs summary (JSON and CSV)
        runs_data = [r.to_dict() for r in records]
        with open(output_dir / "runs_summary.json", "w", encoding="utf-8") as f:
            json.dump(runs_data, f, indent=2)

        # CSV table of runs
        all_metric_keys = sorted({k for r in records for k in r.metrics.keys()})
        csv_runs_path = output_dir / "runs_summary.csv"
        with open(csv_runs_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            header = ["run_id", "scenario_id", "controller", "seed", "repetition", "completed", "stop_reason", "wall_runtime_s", "execution_error"] + all_metric_keys
            writer.writerow(header)
            for r in records:
                row = [
                    r.run_id, r.scenario_id, r.controller, r.seed, r.repetition,
                    r.completed, r.stop_reason, r.wall_runtime_s, r.execution_error or "",
                ] + [r.metrics.get(k, "") for k in all_metric_keys]
                writer.writerow(row)

        # 3. Paired comparisons
        pairs_data = [p.to_dict() for p in comparisons]
        with open(output_dir / "pairs_summary.json", "w", encoding="utf-8") as f:
            json.dump(pairs_data, f, indent=2)

        csv_pairs_path = output_dir / "pairs_summary.csv"
        with open(csv_pairs_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "scenario_id", "seed", "repetition",
                "astragrid_completed", "baseline_completed",
                "delta_critical_shortfall_kwh", "delta_tasks_completed",
                "delta_battery_reserve_violations", "delta_fallback_activations",
            ])
            for p in comparisons:
                writer.writerow([
                    p.scenario_id, p.seed, p.repetition,
                    p.astragrid_completed, p.baseline_completed,
                    p.deltas.get("cumulative_critical_shortfall_kwh", 0.0),
                    p.deltas.get("tasks_completed", 0),
                    p.deltas.get("battery_reserve_violations", 0),
                    p.deltas.get("fallback_activations", 0),
                ])

        # 4. Aggregates
        agg_data = [s.to_dict() for s in summaries]
        with open(output_dir / "aggregate_report.json", "w", encoding="utf-8") as f:
            json.dump(agg_data, f, indent=2)
