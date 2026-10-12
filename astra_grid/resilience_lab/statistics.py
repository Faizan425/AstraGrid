"""Statistical resilience aggregation for AstraGrid Resilience Lab.

Aggregates metrics across runs and seeds, calculates dispersion metrics,
computes Student-t confidence intervals when sample sizes permit (N >= 3),
and generates paired controller comparisons without biased scoring.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from statistics import mean, median, stdev
from typing import Any

from .models import RunRecord


@dataclass
class MetricSummary:
    """Summary statistics for a single metric across independent runs."""
    metric_name: str
    count: int
    mean: float
    median: float
    min: float
    max: float
    stdev: float
    ci_lower: float | None = None
    ci_upper: float | None = None
    ci_level: float = 0.95
    ci_notes: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ScenarioControllerSummary:
    """Aggregated resilience summary for a specific (scenario, controller) pair."""
    scenario_id: str
    controller: str
    attempted_runs: int
    successful_runs: int
    failed_runs: int
    unique_seeds: list[int]
    metrics: dict[str, MetricSummary] = field(default_factory=dict)
    failures: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "controller": self.controller,
            "attempted_runs": self.attempted_runs,
            "successful_runs": self.successful_runs,
            "failed_runs": self.failed_runs,
            "unique_seeds": self.unique_seeds,
            "metrics": {k: v.to_dict() for k, v in self.metrics.items()},
            "failures": self.failures,
        }


@dataclass
class PairedComparison:
    """Matched comparison between AstraGrid and Baseline on equivalent conditions."""
    scenario_id: str
    seed: int | None
    repetition: int
    astragrid_metrics: dict[str, Any]
    baseline_metrics: dict[str, Any]
    deltas: dict[str, float]  # AstraGrid minus Baseline
    astragrid_completed: bool
    baseline_completed: bool
    trade_off_summary: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _student_t_critical(df: int, alpha: float = 0.05) -> float:
    """Return two-tailed critical t-value for given degrees of freedom."""
    # Common critical values for 95% CI (alpha=0.05, two-tailed)
    t_table = {
        1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571,
        6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228,
        15: 2.131, 20: 2.086, 25: 2.060, 30: 2.042, 50: 2.009, 100: 1.984,
    }
    if df in t_table:
        return t_table[df]
    if df > 100:
        return 1.960
    # Linear interpolation between known table keys
    sorted_dfs = sorted(t_table.keys())
    for i in range(len(sorted_dfs) - 1):
        d1, d2 = sorted_dfs[i], sorted_dfs[i + 1]
        if d1 <= df <= d2:
            frac = (df - d1) / (d2 - d1)
            return t_table[d1] + frac * (t_table[d2] - t_table[d1])
    return 1.960


def summarize_values(metric_name: str, values: list[float]) -> MetricSummary:
    """Compute robust sample statistics with Student-t confidence interval."""
    n = len(values)
    if n == 0:
        return MetricSummary(
            metric_name=metric_name, count=0, mean=0.0, median=0.0,
            min=0.0, max=0.0, stdev=0.0, ci_notes="no data",
        )
    val_mean = mean(values)
    val_median = median(values)
    val_min = min(values)
    val_max = max(values)
    val_stdev = stdev(values) if n > 1 else 0.0

    ci_lower: float | None = None
    ci_upper: float | None = None
    ci_notes: str | None = None

    if n >= 3 and val_stdev > 1e-9:
        t_crit = _student_t_critical(n - 1, 0.05)
        margin = t_crit * (val_stdev / math.sqrt(n))
        ci_lower = val_mean - margin
        ci_upper = val_mean + margin
    elif n < 3:
        ci_notes = f"Insufficient independent replications for 95% CI (N={n} < 3)"
    else:
        ci_notes = "Zero sample variance (all runs identical)"

    return MetricSummary(
        metric_name=metric_name,
        count=n,
        mean=val_mean,
        median=val_median,
        min=val_min,
        max=val_max,
        stdev=val_stdev,
        ci_lower=ci_lower,
        ci_upper=ci_upper,
        ci_level=0.95,
        ci_notes=ci_notes,
    )


class ResilienceStatistics:
    """Aggregates RunRecord objects across experimental batches."""

    @staticmethod
    def aggregate_runs(records: list[RunRecord]) -> list[ScenarioControllerSummary]:
        """Group records by (scenario_id, controller) and aggregate metrics."""
        groups: dict[tuple[str, str], list[RunRecord]] = defaultdict(list)
        for r in records:
            groups[(r.scenario_id, r.controller)].append(r)

        summaries: list[ScenarioControllerSummary] = []
        for (scen_id, ctrl), run_list in sorted(groups.items()):
            attempted = len(run_list)
            successful = sum(1 for r in run_list if r.completed and not r.execution_error)
            failed = attempted - successful
            unique_seeds = sorted({r.seed for r in run_list if r.seed is not None})

            failures: list[dict[str, Any]] = [
                {
                    "run_id": r.run_id,
                    "seed": r.seed,
                    "stop_reason": r.stop_reason,
                    "error": r.execution_error,
                }
                for r in run_list if not r.completed or r.execution_error
            ]

            # Collect metric values across all runs
            metric_values: dict[str, list[float]] = defaultdict(list)
            for r in run_list:
                for k, v in r.metrics.items():
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        metric_values[k].append(float(v))

            metrics_summary = {
                k: summarize_values(k, vals) for k, vals in sorted(metric_values.items())
            }

            summaries.append(ScenarioControllerSummary(
                scenario_id=scen_id,
                controller=ctrl,
                attempted_runs=attempted,
                successful_runs=successful,
                failed_runs=failed,
                unique_seeds=unique_seeds,
                metrics=metrics_summary,
                failures=failures,
            ))

        return summaries

    @staticmethod
    def compare_paired(records: list[RunRecord]) -> list[PairedComparison]:
        """Align records with identical (scenario_id, seed, repetition) for matched comparison."""
        runs_by_key: dict[tuple[str, int | None, int], dict[str, RunRecord]] = defaultdict(dict)
        for r in records:
            runs_by_key[(r.scenario_id, r.seed, r.repetition)][r.controller] = r

        comparisons: list[PairedComparison] = []
        for (scen_id, seed, rep), ctrl_map in sorted(runs_by_key.items()):
            if "astragrid" in ctrl_map and "baseline" in ctrl_map:
                astra_run = ctrl_map["astragrid"]
                base_run = ctrl_map["baseline"]

                deltas: dict[str, float] = {}
                for k, a_val in astra_run.metrics.items():
                    b_val = base_run.metrics.get(k)
                    if (
                        isinstance(a_val, (int, float))
                        and not isinstance(a_val, bool)
                        and isinstance(b_val, (int, float))
                        and not isinstance(b_val, bool)
                    ):
                        deltas[k] = float(a_val) - float(b_val)

                # Generate qualitative trade-off summary notes
                trade_offs: dict[str, str] = {}
                crit_delta = deltas.get("cumulative_critical_shortfall_kwh", 0.0)
                if abs(crit_delta) < 1e-6:
                    trade_offs["critical_power"] = "Identical critical power shortfall outcome"
                elif crit_delta < 0:
                    trade_offs["critical_power"] = f"AstraGrid reduced critical power shortfall by {abs(crit_delta):.2f} kWh"
                else:
                    trade_offs["critical_power"] = f"Baseline experienced {crit_delta:.2f} kWh less critical power shortfall"

                task_delta = deltas.get("tasks_completed", 0)
                if task_delta > 0:
                    trade_offs["mission_tasks"] = f"AstraGrid completed {int(task_delta)} more mission tasks"
                elif task_delta < 0:
                    trade_offs["mission_tasks"] = f"Baseline completed {int(abs(task_delta))} more mission tasks"
                else:
                    trade_offs["mission_tasks"] = "Equal mission task completion"

                comparisons.append(PairedComparison(
                    scenario_id=scen_id,
                    seed=seed,
                    repetition=rep,
                    astragrid_metrics=astra_run.metrics,
                    baseline_metrics=base_run.metrics,
                    deltas=deltas,
                    astragrid_completed=astra_run.completed,
                    baseline_completed=base_run.completed,
                    trade_off_summary=trade_offs,
                ))

        return comparisons
