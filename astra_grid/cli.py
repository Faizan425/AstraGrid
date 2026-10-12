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
    args = parser.parse_args(argv)
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
    else:
        results = run_all(args.output, args.steps); print(f"Completed {len(results)} controller/scenario runs in {args.output}")


if __name__ == "__main__": main()
