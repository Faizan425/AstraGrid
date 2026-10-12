"""Command-line demo and experiment entry points."""
from __future__ import annotations

import argparse
from pathlib import Path

from .config import built_in_scenarios, scenario_from_id
from .evaluation import benchmark, compare, export_benchmark, export_comparison, export_run, run_all
from .runner import SimulationRun


def _trace(run: SimulationRun) -> None:
    prior_events = len(run.events)
    run.step()
    state, event = run.state_view().get("estimated", {}), (run.events[-1] if len(run.events) > prior_events else None)
    horizon = run.last_horizon.hours if run.last_horizon else 0
    print(f"t={run.simulator.state_for_evaluation.step:03d} mode={run.manager.mode:>18} soc={state.get('soc', 0):.2f} quality={state.get('telemetry_quality', 'n/a'):>6} solar={run.last_observation.solar_kw if run.last_observation else 0:.1f}kW horizon={horizon:.1f}h {event.message if event else ''}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="astragrid", description="AstraGrid lunar habitat prototype")
    subs = parser.add_subparsers(dest="command", required=True)
    run_p = subs.add_parser("run"); run_p.add_argument("scenario", choices=sorted(built_in_scenarios())); run_p.add_argument("--controller", choices=["astragrid", "baseline"], default="astragrid"); run_p.add_argument("--steps", type=int, default=None); run_p.add_argument("--output", type=Path, default=Path("outputs")); run_p.add_argument("--trace", action="store_true")
    cmp_p = subs.add_parser("compare"); cmp_p.add_argument("scenario", choices=sorted(built_in_scenarios())); cmp_p.add_argument("--steps", type=int, default=None); cmp_p.add_argument("--output", type=Path, default=Path("outputs"))
    bench_p = subs.add_parser("benchmark", help="reproducible paired controller benchmark")
    bench_p.add_argument("scenarios", nargs="*", help="built-in scenario IDs; omit to benchmark all")
    bench_p.add_argument("--seed", type=int, action="append", help="repeat each selected scenario for this seed (may be supplied more than once)")
    bench_p.add_argument("--steps", type=int, default=None)
    bench_p.add_argument("--output", type=Path, default=Path("outputs") / "benchmark")
    all_p = subs.add_parser("run-all"); all_p.add_argument("--steps", type=int, default=None); all_p.add_argument("--output", type=Path, default=Path("outputs"))

    # Mission Control Web Dashboard subcommand
    web_p = subs.add_parser("web", help="Launch AstraGrid Mission Control web interface")
    web_p.add_argument("--host", default="127.0.0.1", help="Host interface (default: 127.0.0.1)")
    web_p.add_argument("--port", type=int, default=8000, help="Port to bind (default: 8000)")

    # Resilience Lab subcommands
    lab_p = subs.add_parser("lab", help="AstraGrid Resilience Lab operations")
    lab_subs = lab_p.add_subparsers(dest="lab_command", required=True)

    l_run = lab_subs.add_parser("run", help="Run single evaluated scenario")
    l_run.add_argument("scenario", choices=sorted(built_in_scenarios()))
    l_run.add_argument("--controller", choices=["astragrid", "baseline"], default="astragrid")
    l_run.add_argument("--seed", type=int, default=42)
    l_run.add_argument("--steps", type=int, default=None)
    l_run.add_argument("--output", type=Path, default=Path("outputs") / "resilience_lab")

    l_cmp = lab_subs.add_parser("compare", help="Run matched controller comparison")
    l_cmp.add_argument("scenario", choices=sorted(built_in_scenarios()))
    l_cmp.add_argument("--seed", type=int, default=42)
    l_cmp.add_argument("--steps", type=int, default=None)
    l_cmp.add_argument("--output", type=Path, default=Path("outputs") / "resilience_lab")

    l_batch = lab_subs.add_parser("batch", help="Run batch resilience experiment")
    l_batch.add_argument("scenarios", nargs="*", help="scenarios to run (omit for default suite)")
    l_batch.add_argument("--controllers", nargs="+", choices=["astragrid", "baseline"], default=["astragrid", "baseline"])
    l_batch.add_argument("--seed", type=int, action="append", help="seed values")
    l_batch.add_argument("--repetitions", type=int, default=1)
    l_batch.add_argument("--steps", type=int, default=None)
    l_batch.add_argument("--output", type=Path, default=Path("outputs") / "resilience_lab")

    l_rep = lab_subs.add_parser("report", help="Generate HTML report from completed results directory")
    l_rep.add_argument("directory", type=Path, help="directory containing completed experiment results")
    l_rep.add_argument("--output", type=Path, default=None, help="output HTML report path")

    lab_subs.add_parser("list", help="List available scenarios and intended behavior")

    args = parser.parse_args(argv)
    if args.command == "web":
        import uvicorn
        print(f"Starting AstraGrid Lunar Habitat Mission Control web interface on http://{args.host}:{args.port}")
        uvicorn.run("astra_grid.api:app", host=args.host, port=args.port, reload=False)
        return
    if args.command == "lab":
        _handle_lab_command(args)
        return
    if args.command == "run":
        run = SimulationRun(scenario_from_id(args.scenario), args.controller)
        if args.trace:
            for _ in range(args.steps or run.config.duration_steps):
                if run.completed: break
                _trace(run)
        else: run.run(args.steps)
        paths = export_run(run, args.output); print(f"Finished {args.scenario} with {args.controller}. Artefacts: {paths['json']}, {paths['csv']}")
    elif args.command == "compare":
        runs = compare(scenario_from_id(args.scenario), args.steps); path = export_comparison(runs, args.output); print(f"Comparison written to {path}")
    elif args.command == "benchmark":
        report = benchmark(args.scenarios or None, seeds=args.seed, steps=args.steps)
        paths = export_benchmark(report, args.output)
        print(f"Benchmark completed: {len(report['runs'])} runs, {len(report['failures'])} non-duration-complete/unsupported entries. Report: {paths['json']}")
    elif args.command == "run-all":
        results = run_all(args.output, args.steps); print(f"Completed {len(results)} controller/scenario runs in {args.output}")



def _handle_lab_command(args: argparse.Namespace) -> None:
    import json
    from .resilience_lab import (
        ExperimentConfig,
        ExperimentRunner,
        generate_html_report,
    )
    from .resilience_lab.models import ExperimentManifest, RunRecord
    from .resilience_lab.statistics import MetricSummary, PairedComparison, ScenarioControllerSummary

    if args.lab_command == "list":
        scenarios = built_in_scenarios()
        print("Available AstraGrid Scenarios:")
        print("=" * 80)
        for sid, sc in sorted(scenarios.items()):
            print(f"[{sid}]")
            print(f"  Description: {sc.description}")
            print(f"  Duration:    {sc.duration_steps} steps ({sc.duration_steps * sc.dt_hours:.1f} hours)")
            print(f"  Faults:      {len(sc.faults)} scheduled (targets: {', '.join(f.target for f in sc.faults) or 'none'})")
            print(f"  Tasks:       {len(sc.tasks)} configured")
            print()
        return

    if args.lab_command == "run":
        runner = ExperimentRunner(ExperimentConfig(output_dir=args.output))
        record, _ = runner.run_single(
            scenario_id=args.scenario,
            controller=args.controller,
            seed=args.seed,
            steps=args.steps,
            save_detailed=True,
            output_dir=args.output,
        )
        print(f"Resilience Lab run finished: {record.run_id}")
        print(f"  Completed:      {record.completed} (reason: {record.stop_reason})")
        print(f"  Crit shortfall: {record.metrics.get('cumulative_critical_shortfall_kwh', 0.0):.2f} kWh")
        print(f"  Tasks done:     {record.metrics.get('tasks_completed', 0)}")
        print(f"  Artifacts saved in: {args.output}")
        return

    if args.lab_command == "compare":
        cfg = ExperimentConfig(
            scenario_ids=[args.scenario],
            controllers=["astragrid", "baseline"],
            seeds=[args.seed],
            steps=args.steps,
            output_dir=args.output,
        )
        runner = ExperimentRunner(cfg)
        manifest, records, summaries, comparisons = runner.run_experiment(cfg)
        html_path = generate_html_report(manifest, records, summaries, comparisons, args.output / "report.html")
        print(f"Matched comparison finished: {manifest.experiment_name}")
        print(f"  Runs completed: {manifest.successful_runs}/{manifest.total_runs_attempted}")
        if comparisons:
            comp = comparisons[0]
            print(f"  Delta critical shortfall: {comp.deltas.get('cumulative_critical_shortfall_kwh', 0):+.2f} kWh")
            print(f"  Delta tasks completed:    {int(comp.deltas.get('tasks_completed', 0)):+d}")
        print(f"  Dashboard report: {html_path}")
        return

    if args.lab_command == "batch":
        scen_ids = args.scenarios or ExperimentConfig().scenario_ids
        seeds = args.seed or [42]
        cfg = ExperimentConfig(
            scenario_ids=scen_ids,
            controllers=args.controllers,
            seeds=seeds,
            repetitions=args.repetitions,
            steps=args.steps,
            output_dir=args.output,
        )
        runner = ExperimentRunner(cfg)
        manifest, records, summaries, comparisons = runner.run_experiment(cfg)
        html_path = generate_html_report(manifest, records, summaries, comparisons, args.output / "report.html")
        print(f"Resilience Lab batch completed: {manifest.total_runs_attempted} runs")
        print(f"  Successful: {manifest.successful_runs}")
        print(f"  Failures:   {manifest.failed_runs}")
        print(f"  Manifest:   {args.output / 'experiment_manifest.json'}")
        print(f"  Report:     {html_path}")
        return

    if args.lab_command == "report":
        manifest_p = args.directory / "experiment_manifest.json"
        runs_p = args.directory / "runs_summary.json"
        pairs_p = args.directory / "pairs_summary.json"
        agg_p = args.directory / "aggregate_report.json"
        if not (manifest_p.exists() and runs_p.exists()):
            print(f"Error: manifest or runs summary not found in {args.directory}")
            return
        with open(manifest_p, "r", encoding="utf-8") as f:
            man_data = json.load(f)
        with open(runs_p, "r", encoding="utf-8") as f:
            runs_data = json.load(f)
        pairs_data = []
        if pairs_p.exists():
            with open(pairs_p, "r", encoding="utf-8") as f:
                pairs_data = json.load(f)
        agg_data = []
        if agg_p.exists():
            with open(agg_p, "r", encoding="utf-8") as f:
                agg_data = json.load(f)

        manifest = ExperimentManifest(**man_data)
        records = [
            RunRecord(
                run_id=r["run_id"],
                scenario_id=r["scenario_id"],
                controller=r["controller"],
                seed=r.get("seed"),
                repetition=r.get("repetition", 1),
                duration_steps=r["duration_steps"],
                simulated_steps=r["simulated_steps"],
                simulated_hours=r["simulated_hours"],
                completed=r["completed"],
                stop_reason=r["stop_reason"],
                wall_runtime_s=r.get("wall_runtime_s"),
                execution_error=r.get("execution_error"),
                metrics=r.get("metrics", {}),
                fault_detection_delays=r.get("fault_detection_delays", {}),
                task_completion_timeline=r.get("task_completion_timeline", []),
            )
            for r in runs_data
        ]
        summaries = [
            ScenarioControllerSummary(
                scenario_id=s["scenario_id"],
                controller=s["controller"],
                attempted_runs=s["attempted_runs"],
                successful_runs=s["successful_runs"],
                failed_runs=s["failed_runs"],
                unique_seeds=s["unique_seeds"],
                metrics={k: MetricSummary(**v) for k, v in s["metrics"].items()},
                failures=s.get("failures", []),
            )
            for s in agg_data
        ]
        comparisons = [
            PairedComparison(
                scenario_id=c["scenario_id"],
                seed=c.get("seed"),
                repetition=c.get("repetition", 1),
                astragrid_metrics=c["astragrid_metrics"],
                baseline_metrics=c["baseline_metrics"],
                deltas=c["deltas"],
                astragrid_completed=c["astragrid_completed"],
                baseline_completed=c["baseline_completed"],
                trade_off_summary=c.get("trade_off_summary", {}),
            )
            for c in pairs_data
        ]
        out_path = args.output or (args.directory / "report.html")
        html_path = generate_html_report(manifest, records, summaries, comparisons, out_path)
        print(f"Report regenerated: {html_path}")


if __name__ == "__main__": main()
