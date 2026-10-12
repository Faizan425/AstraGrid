"""Local Tkinter dashboard for interactively demonstrating AstraGrid runs."""
from __future__ import annotations

import tkinter as tk
from dataclasses import asdict
from tkinter import messagebox, ttk

from .config import FaultConfig, TaskConfig, built_in_scenarios, scenario_from_id
from .evaluation import benchmark_from_runs, create_paired_runs, format_pair_summary
from .models import TaskStatus
from .runner import SimulationRun
from .tasks import MissionTask


BG = "#0d131e"
PANEL = "#162234"
PANEL_2 = "#1b2a40"
TEXT = "#dce8f7"
MUTED = "#94a9bf"
CYAN = "#00d9ff"
GREEN = "#18dc7f"
YELLOW = "#ffbd16"
RED = "#ef3a42"
BLUE = "#2678ff"


class AstraGridApp(tk.Tk):
    """A compact, dependency-free control-room view over :class:`SimulationRun`."""
    def __init__(self) -> None:
        super().__init__()
        self.title("AstraGrid — Lunar Habitat Survival Console")
        # The standard dashboard deliberately fits inside a 768 px laptop
        # workspace; users can still enlarge it for wider charts.
        self.geometry("1400x800")
        self.minsize(1180, 640)
        self.configure(bg=BG)
        self.scenario_var = tk.StringVar(value="combined_fault")
        self.controller_var = tk.StringVar(value="astragrid")
        self.mode_var = tk.StringVar(value="NORMAL")
        self.auto_running = False
        self.compare_auto_running = False
        self._fault_sequence = 0
        self.run = SimulationRun(scenario_from_id(self.scenario_var.get()), self.controller_var.get(), trace_telemetry=True, trace_label="dashboard")
        self.compare_runs: dict[str, SimulationRun] | None = None
        self.compare_report: dict[str, object] | None = None
        self._configure_style()
        self._build_header()
        self._build_tabs()
        self.refresh()

    def _configure_style(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("TLabel", background=BG, foreground=TEXT, font=("Segoe UI", 10))
        style.configure("Title.TLabel", background=BG, foreground=CYAN, font=("Segoe UI", 21, "bold"))
        style.configure("Sub.TLabel", background=BG, foreground=MUTED, font=("Segoe UI", 8, "bold"))
        style.configure("Value.TLabel", background=PANEL, foreground=GREEN, font=("Consolas", 10, "bold"))
        style.configure("Key.TLabel", background=PANEL, foreground=MUTED, font=("Segoe UI", 9, "bold"))
        style.configure("Header.TLabel", background=PANEL, foreground=CYAN, font=("Segoe UI", 11, "bold"))
        style.configure("TButton", background="#263a52", foreground=TEXT, padding=(9, 5), font=("Segoe UI", 9, "bold"))
        style.map("TButton", background=[("active", BLUE)])
        style.configure("Accent.TButton", background=BLUE, foreground="white")
        style.configure("Good.TButton", background="#187d3a", foreground="white")
        style.configure("Danger.TButton", background="#a82229", foreground="white")
        style.configure("TCombobox", fieldbackground="#203148", background="#203148", foreground=TEXT, arrowcolor=TEXT)
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background="#182536", foreground=MUTED, padding=(18, 8), font=("Segoe UI", 10, "bold"))
        style.map("TNotebook.Tab", background=[("selected", BLUE)], foreground=[("selected", "white")])
        style.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=TEXT, rowheight=27, font=("Segoe UI", 9))
        style.configure("Treeview.Heading", background="#203148", foreground=CYAN, font=("Segoe UI", 9, "bold"))
        style.map("Treeview", background=[("selected", BLUE)])

    def _build_header(self) -> None:
        header = ttk.Frame(self, style="Panel.TFrame", padding=(18, 7))
        self.header = header
        header.pack(fill="x")
        brand = ttk.Frame(header, style="Panel.TFrame")
        brand.pack(side="left", anchor="w")
        ttk.Label(brand, text="ASTRAGRID", style="Title.TLabel").pack(anchor="w")
        ttk.Label(brand, text="LUNAR HABITAT SURVIVAL & MISSION SCHEDULER", style="Sub.TLabel").pack(anchor="w")
        controls = ttk.Frame(header, style="Panel.TFrame")
        controls.pack(side="right", anchor="e", padx=(14, 0))
        ttk.Label(controls, text="SCENARIO", style="Sub.TLabel").grid(row=0, column=0, padx=(0, 5))
        self.scenario_box = ttk.Combobox(controls, width=23, state="readonly", values=sorted(built_in_scenarios()), textvariable=self.scenario_var)
        self.scenario_box.grid(row=0, column=1, padx=(0, 10))
        ttk.Label(controls, text="CONTROLLER", style="Sub.TLabel").grid(row=0, column=2, padx=(0, 5))
        ttk.Combobox(controls, width=10, state="readonly", values=["astragrid", "baseline"], textvariable=self.controller_var).grid(row=0, column=3, padx=(0, 10))
        ttk.Button(controls, text="▶ Step (15m)", style="Accent.TButton", command=self.step_once).grid(row=0, column=4, padx=3)
        self.auto_button = ttk.Button(controls, text="▣ Auto Run", style="Good.TButton", command=self.toggle_auto)
        self.auto_button.grid(row=0, column=5, padx=3)
        ttk.Button(controls, text="+10 Steps", command=lambda: self.run_steps(10)).grid(row=1, column=0, columnspan=2, pady=(5, 0), sticky="ew")
        ttk.Button(controls, text="⇄ Compare", command=self.start_comparison).grid(row=1, column=2, columnspan=2, padx=3, pady=(5, 0), sticky="ew")
        ttk.Button(controls, text="↻ Reset", command=self.reset_run).grid(row=1, column=4, pady=(5, 0), sticky="ew")
        self.mode_label = tk.Label(controls, textvariable=self.mode_var, bg="#13652a", fg="white", font=("Segoe UI", 9, "bold"), padx=10, pady=3)
        self.mode_label.grid(row=1, column=5, padx=(8, 0), pady=(5, 0), sticky="e")

    def _build_tabs(self) -> None:
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        self.overview = ttk.Frame(self.notebook)
        self.tasks_tab = ttk.Frame(self.notebook)
        self.audit_tab = ttk.Frame(self.notebook)
        self.telemetry_tab = ttk.Frame(self.notebook)
        self.compare_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.overview, text="◈  Telemetry & Power Overview")
        self.notebook.add(self.tasks_tab, text="▣  Mission Debt Ledger & Tasks")
        self.notebook.add(self.audit_tab, text="▤  Audit Trail & Decision Logs")
        self.notebook.add(self.telemetry_tab, text="⌁  Synthetic Telemetry Stream")
        self.notebook.add(self.compare_tab, text="⇄  Optimizer vs Baseline")
        self._build_overview()
        self._build_tasks()
        self._build_audit()
        self._build_telemetry_stream()
        self._build_comparison()

    def _panel(self, parent: tk.Misc, title: str) -> ttk.Frame:
        panel = ttk.Frame(parent, style="Panel.TFrame", padding=12)
        ttk.Label(panel, text=title.upper(), style="Header.TLabel").pack(anchor="w", pady=(0, 8))
        return panel

    def _build_overview(self) -> None:
        # A direct responsive grid avoids the Canvas/Frame sizing mismatch that
        # left unused space beside the charts. The compact dimensions fit the
        # complete operational console on a typical 768 px-tall laptop display.
        self.overview.columnconfigure(0, weight=0, minsize=330)
        self.overview.columnconfigure(1, weight=1)
        self.overview.rowconfigure(0, weight=1)
        left = ttk.Frame(self.overview)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=8)
        charts = self._panel(self.overview, "Real-time telemetry charts")
        charts.grid(row=0, column=1, sticky="nsew", pady=8)
        info = self._panel(left, "Critical telemetry & margins")
        info.pack(fill="x", pady=(0, 8))
        self.metric_vars: dict[str, tk.StringVar] = {}
        for label, key in [("MISSION TIME", "time"), ("BATTERY SOC", "soc"), ("BATTERY FLOW", "flow"), ("SOLAR GENERATION", "solar"), ("TOTAL LOAD", "load"), ("HABITAT TEMP", "temp"), ("SURVIVAL HORIZON", "horizon"), ("LIMITING FACTOR", "limit")]:
            row = ttk.Frame(info, style="Panel.TFrame")
            row.pack(fill="x", pady=3)
            ttk.Label(row, text=f"{label}:", style="Key.TLabel").pack(side="left")
            var = tk.StringVar(value="—"); self.metric_vars[key] = var
            ttk.Label(row, textvariable=var, style="Value.TLabel").pack(side="right")
        consensus = self._panel(left, "Telemetry consensus & validation")
        consensus.pack(fill="x", pady=8)
        self.sensor_var = tk.StringVar(value="Awaiting telemetry")
        self.quality_var = tk.StringVar(value="—")
        ttk.Label(consensus, textvariable=self.sensor_var, style="Value.TLabel", wraplength=300).pack(anchor="w", pady=3)
        ttk.Label(consensus, textvariable=self.quality_var, style="Key.TLabel", wraplength=300).pack(anchor="w", pady=3)
        faults = self._panel(left, "Interactive fault injection")
        faults.pack(fill="x", pady=8)
        button_grid = ttk.Frame(faults, style="Panel.TFrame"); button_grid.pack(fill="x")
        ttk.Button(button_grid, text="⚡ Solar drop 50%", command=lambda: self.inject_fault("solar", {"factor": .5})).grid(row=0, column=0, padx=3, pady=3, sticky="ew")
        ttk.Button(button_grid, text="◉ Byzantine SOC", command=lambda: self.inject_fault("soc_sensor_2", {"value": .75}, "telemetry")).grid(row=0, column=1, padx=3, pady=3, sticky="ew")
        ttk.Button(button_grid, text="▱ Battery degrade", command=lambda: self.inject_fault("battery", {"capacity_factor": .5, "discharge_factor": .5})).grid(row=1, column=0, padx=3, pady=3, sticky="ew")
        ttk.Button(button_grid, text="Clear injected", style="Danger.TButton", command=self.clear_injected_faults).grid(row=1, column=1, padx=3, pady=3, sticky="ew")
        button_grid.columnconfigure((0, 1), weight=1)
        self.charts: list[tuple[str, tk.Canvas, str, str, float | None]] = []
        for title, color, second, bound in [("Battery SOC (%) — 20% safe reserve", GREEN, "", 20.0), ("Power flow — solar vs load (kW)", YELLOW, "#ff7b45", None), ("Habitat temperature (°C) — 16–28°C envelope", CYAN, "", 16.0)]:
            ttk.Label(charts, text=title, style="Header.TLabel").pack(anchor="w", pady=(8, 1))
            canvas = tk.Canvas(charts, bg=PANEL_2, highlightthickness=0, height=96)
            canvas.pack(fill="both", expand=True, pady=(0, 4))
            self.charts.append((title, canvas, color, second, bound))

    def _build_tasks(self) -> None:
        # Keep the entry form visible. Task history is bounded in height and
        # independently scrollable instead of consuming the whole tab.
        self.tasks_tab.rowconfigure(1, weight=0); self.tasks_tab.columnconfigure(0, weight=1)
        self.debt_var = tk.StringVar(value="MISSION DEBT BACKLOG: 0 step(s)")
        tk.Label(self.tasks_tab, textvariable=self.debt_var, bg=PANEL, fg=YELLOW, font=("Segoe UI", 15, "bold"), padx=18, pady=12).grid(row=0, column=0, sticky="ew", padx=8, pady=8)
        columns = ("id", "power", "duration", "deadline", "type", "priority", "status", "progress", "deferrals")
        self.task_tree = ttk.Treeview(self.tasks_tab, columns=columns, show="headings", height=9)
        headings = {"id":"Task ID", "power":"Power kW", "duration":"Duration", "deadline":"Deadline", "type":"Type", "priority":"Priority", "status":"Ledger status", "progress":"Progress", "deferrals":"Deferrals"}
        for key in columns:
            self.task_tree.heading(key, text=headings[key]); self.task_tree.column(key, width=105, anchor="center")
        self.task_tree.column("id", width=180, anchor="w"); self.task_tree.column("status", width=150)
        task_scroll = ttk.Scrollbar(self.tasks_tab, orient="vertical", command=self.task_tree.yview)
        self.task_tree.configure(yscrollcommand=task_scroll.set)
        self.task_tree.grid(row=1, column=0, sticky="ew", padx=(8, 0))
        task_scroll.grid(row=1, column=1, sticky="ns", padx=(0, 8))
        # `_panel` uses pack for its title, so use an inner frame for grid-based
        # form controls. Tk does not allow pack and grid in the same parent.
        form = self._panel(self.tasks_tab, "Add new mission task")
        self.task_form = form
        form.grid(row=2, column=0, sticky="ew", padx=8, pady=8)
        fields = ttk.Frame(form, style="Panel.TFrame")
        fields.pack(fill="x")
        self.new_task_vars = {"id": tk.StringVar(value="experiment_01"), "power": tk.StringVar(value="3.0"), "duration": tk.StringVar(value="4"), "priority": tk.StringVar(value="5"), "deadline": tk.StringVar(value="70")}
        for index, (label, key) in enumerate([("Task ID", "id"), ("Power kW", "power"), ("Duration steps", "duration"), ("Priority", "priority"), ("Deadline step", "deadline")]):
            ttk.Label(fields, text=label, style="Key.TLabel").grid(row=0, column=index * 2, padx=(0, 4), sticky="w")
            ttk.Entry(fields, textvariable=self.new_task_vars[key], width=13).grid(row=0, column=index * 2 + 1, padx=(0, 12))
        ttk.Button(fields, text="+ Add task", style="Good.TButton", command=self.add_task).grid(row=0, column=10, padx=5)

    def _build_audit(self) -> None:
        self.audit_tab.rowconfigure(0, weight=1); self.audit_tab.columnconfigure(0, weight=1)
        columns = ("step", "kind", "action", "reason", "message")
        self.event_tree = ttk.Treeview(self.audit_tab, columns=columns, show="headings")
        for key, width in [("step", 70), ("kind", 105), ("action", 180), ("reason", 180), ("message", 720)]:
            self.event_tree.heading(key, text=key.upper()); self.event_tree.column(key, width=width, anchor="w")
        scroll = ttk.Scrollbar(self.audit_tab, orient="vertical", command=self.event_tree.yview)
        self.event_tree.configure(yscrollcommand=scroll.set)
        self.event_tree.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=8); scroll.grid(row=0, column=1, sticky="ns", pady=8)

    def _build_telemetry_stream(self) -> None:
        """Show raw reported readings before they are fused into an estimate."""
        self.telemetry_tab.rowconfigure(1, weight=1); self.telemetry_tab.columnconfigure(0, weight=1)
        note = "Raw synthetic sensor reports produced each step. Fault injection changes these reported values; they are not hidden simulator truth. The same JSON is printed in the terminal."
        tk.Label(self.telemetry_tab, text=note, bg=PANEL, fg=MUTED, font=("Segoe UI", 10), anchor="w", padx=14, pady=10).grid(row=0, column=0, columnspan=2, sticky="ew", padx=8, pady=(8, 0))
        columns = ("step", "solar", "load", "temperature", "oxygen", "co2", "water", "soc_1", "soc_2", "soc_3")
        self.telemetry_tree = ttk.Treeview(self.telemetry_tab, columns=columns, show="headings")
        headings = {"step":"Step", "solar":"Solar kW", "load":"Load kW", "temperature":"Temp °C", "oxygen":"O₂ kg", "co2":"CO₂ kg", "water":"Water kg", "soc_1":"SOC 1", "soc_2":"SOC 2", "soc_3":"SOC 3"}
        for key in columns:
            self.telemetry_tree.heading(key, text=headings[key]); self.telemetry_tree.column(key, width=105, anchor="center")
        self.telemetry_tree.column("step", width=75)
        scroll = ttk.Scrollbar(self.telemetry_tab, orient="vertical", command=self.telemetry_tree.yview)
        self.telemetry_tree.configure(yscrollcommand=scroll.set)
        self.telemetry_tree.grid(row=1, column=0, sticky="nsew", padx=(8, 0), pady=8)
        scroll.grid(row=1, column=1, sticky="ns", padx=(0, 8), pady=8)

    def _build_comparison(self) -> None:
        """Build a compact live, side-by-side controller comparison view."""
        self.compare_tab.columnconfigure((0, 1), weight=1, uniform="comparison")
        self.compare_tab.rowconfigure(1, weight=1)
        controls = ttk.Frame(self.compare_tab, style="Panel.TFrame", padding=(10, 8))
        controls.grid(row=0, column=0, columnspan=2, sticky="ew", padx=8, pady=8)
        ttk.Label(controls, text="Fair comparison: identical initial state, seed and fault schedule; each controller receives telemetry from its own cloned habitat.", style="Key.TLabel").pack(side="left", padx=(2, 18))
        ttk.Button(controls, text="▶ Step both", style="Accent.TButton", command=self.compare_step).pack(side="left", padx=4)
        self.compare_auto_button = ttk.Button(controls, text="▣ Auto compare", style="Good.TButton", command=self.toggle_compare_auto)
        self.compare_auto_button.pack(side="left", padx=4)
        ttk.Button(controls, text="+10 both", command=lambda: self.compare_steps(10)).pack(side="left", padx=4)
        ttk.Button(controls, text="↻ Reset comparison", command=self.start_comparison).pack(side="left", padx=4)
        self.compare_text: dict[str, tk.Text] = {}
        for column, (name, title, accent) in enumerate((("astragrid", "ASTRAGRID OPTIMIZER", CYAN), ("baseline", "PRIORITY BASELINE", YELLOW))):
            panel = ttk.Frame(self.compare_tab, style="Panel.TFrame", padding=12)
            panel.grid(row=1, column=column, sticky="nsew", padx=(8, 4) if column == 0 else (4, 8), pady=(0, 8))
            tk.Label(panel, text=title, bg=PANEL, fg=accent, font=("Segoe UI", 15, "bold")).pack(anchor="w", pady=(0, 8))
            text = tk.Text(panel, bg="#101b2b", fg=TEXT, insertbackground=TEXT, relief="flat", font=("Consolas", 10), wrap="word", padx=12, pady=12, state="disabled")
            text.pack(fill="both", expand=True)
            self.compare_text[name] = text
        summary_panel = ttk.Frame(self.compare_tab, style="Panel.TFrame", padding=10)
        summary_panel.grid(row=2, column=0, columnspan=2, sticky="ew", padx=8, pady=(0, 8))
        ttk.Label(summary_panel, text="REPRODUCIBLE FINAL BENCHMARK", style="Header.TLabel").pack(anchor="w")
        self.compare_summary = tk.Text(summary_panel, height=7, bg="#101b2b", fg=TEXT, insertbackground=TEXT, relief="flat", font=("Consolas", 9), wrap="none", padx=10, pady=8, state="disabled")
        self.compare_summary.pack(fill="x", expand=True, pady=(6, 0))
        self._set_compare_summary("Run both controllers to completion. The final paired benchmark will appear here.\nIt uses independent cloned runs with the same configured scenario, seed, and fault schedule.")

    def _set_compare_summary(self, value: str) -> None:
        self.compare_summary.configure(state="normal")
        self.compare_summary.delete("1.0", "end")
        self.compare_summary.insert("1.0", value)
        self.compare_summary.configure(state="disabled")

    def reset_run(self) -> None:
        self.auto_running = False; self.auto_button.configure(text="▣ Auto Run")
        self.run = SimulationRun(scenario_from_id(self.scenario_var.get()), self.controller_var.get(), trace_telemetry=True, trace_label="dashboard")
        self._fault_sequence = 0
        self.refresh()

    def start_comparison(self) -> None:
        """Reset fair lockstep runs and show the non-clipping comparison tab."""
        self.compare_auto_running = False
        self.compare_auto_button.configure(text="▣ Auto compare")
        self.compare_runs = create_paired_runs(scenario_from_id(self.scenario_var.get()), trace_telemetry=True)
        self.compare_report = None
        self._set_compare_summary("Comparison reset. Run both controllers to completion to calculate the final paired benchmark.")
        self._refresh_comparison()
        self.notebook.select(self.compare_tab)

    def compare_step(self) -> None:
        if self.compare_runs is None: self.start_comparison()
        assert self.compare_runs is not None
        for run in self.compare_runs.values():
            if not run.completed: run.step()
        self._refresh_comparison()

    def compare_steps(self, count: int) -> None:
        for _ in range(count):
            if self.compare_runs is None or all(run.completed for run in self.compare_runs.values()): break
            self.compare_step()

    def toggle_compare_auto(self) -> None:
        if self.compare_runs is None: self.start_comparison()
        self.compare_auto_running = not self.compare_auto_running
        self.compare_auto_button.configure(text="■ Stop comparison" if self.compare_auto_running else "▣ Auto compare")
        if self.compare_auto_running: self._compare_auto_tick()

    def _compare_auto_tick(self) -> None:
        if not self.compare_auto_running: return
        if self.compare_runs is None or all(run.completed for run in self.compare_runs.values()):
            self.compare_auto_running = False; self.compare_auto_button.configure(text="▣ Auto compare")
            self._refresh_comparison()
            return
        self.compare_step()
        self.after(550, self._compare_auto_tick)

    def step_once(self) -> None:
        if not self.run.completed: self.run.step()
        self.refresh()

    def run_steps(self, count: int) -> None:
        self.run.run(count); self.refresh()

    def toggle_auto(self) -> None:
        self.auto_running = not self.auto_running
        self.auto_button.configure(text="■ Stop" if self.auto_running else "▣ Auto Run")
        if self.auto_running: self._auto_tick()

    def _auto_tick(self) -> None:
        if not self.auto_running: return
        if self.run.completed:
            self.auto_running = False; self.auto_button.configure(text="▣ Auto Run"); return
        self.step_once()
        self.after(550, self._auto_tick)

    def inject_fault(self, target: str, parameters: dict[str, float], category: str = "physical") -> None:
        self._fault_sequence += 1
        fault = FaultConfig(id=f"gui_{target}_{self._fault_sequence}", category=category, target=target, start_step=self.run.simulator.state_for_evaluation.step, parameters=parameters)
        self.run.faults.faults.append(fault)
        self.refresh()

    def clear_injected_faults(self) -> None:
        # End rather than remove a live fault so FaultEngine can emit a clean
        # "cleared" transition without losing its previous-ID bookkeeping.
        now = self.run.simulator.state_for_evaluation.step
        for fault in self.run.faults.faults:
            if fault.id.startswith("gui_"):
                fault.end_step = now
        self.refresh()

    def add_task(self) -> None:
        try:
            task = TaskConfig(id=self.new_task_vars["id"].get().strip(), power_kw=float(self.new_task_vars["power"].get()), duration_steps=int(self.new_task_vars["duration"].get()), priority=int(self.new_task_vars["priority"].get()), deadline_step=int(self.new_task_vars["deadline"].get()))
            if task.id in self.run.ledger.tasks: raise ValueError("Task ID already exists")
        except ValueError as exc:
            messagebox.showerror("Invalid task", str(exc)); return
        self.run.config.tasks.append(task)
        self.run.ledger.tasks[task.id] = MissionTask(task)
        self.refresh()

    def refresh(self) -> None:
        view = self.run.state_view(); estimated = view["estimated"]
        step = self.run.simulator.state_for_evaluation.step
        transition = self.run.transitions[-1] if self.run.transitions else None
        horizon = view["survival_horizon"]
        self.metric_vars["time"].set(f"T+ {step * self.run.config.dt_hours:05.2f}h  (Step {step}/{self.run.config.duration_steps})")
        self.metric_vars["soc"].set(f"{estimated['soc'] * 100:.1f}%  ({estimated['battery_kwh']:.1f} kWh)")
        flow = 0.0 if transition is None else transition.battery_charge_kw - transition.battery_discharge_kw
        self.metric_vars["flow"].set(f"{flow:+.2f} kW")
        self.metric_vars["solar"].set(f"{(self.run.last_observation.solar_kw if self.run.last_observation else self.run.simulator.solar_kw()):.2f} kW")
        self.metric_vars["load"].set(f"{(transition.demand_kw if transition else 0):.2f} kW")
        self.metric_vars["temp"].set(f"{estimated['temperature_c']:.1f} °C  [16–28°C]")
        self.metric_vars["horizon"].set(f"{horizon['hours']:.1f} h")
        self.metric_vars["limit"].set(str(horizon["limiting_factor"]))
        suspects = estimated["suspect_sensors"]
        self.sensor_var.set("SOC sensors: " + ("; ".join(f"{key}={value:.0%}" for key, value in self.run.last_observation.soc_sensors.items()) if self.run.last_observation else "waiting"))
        self.quality_var.set(f"Confidence: {estimated['telemetry_quality'].upper()}  |  Isolated: {', '.join(suspects) if suspects else 'none'}")
        mode = str(self.run.manager.mode).upper(); self.mode_var.set(mode)
        self.mode_label.configure(bg={"NORMAL":"#13652a", "DEGRADED":"#8a6510", "EMERGENCY":"#a82229", "CONTROLLED_SHUTDOWN":"#73232a"}.get(mode, "#13652a"))
        self._refresh_tasks(); self._refresh_events(); self._refresh_telemetry_stream(); self._draw_charts()

    def _refresh_tasks(self) -> None:
        for item in self.task_tree.get_children(): self.task_tree.delete(item)
        tasks = self.run.ledger.snapshot()
        debt = sum(int(task["remaining_steps"]) for task in tasks if task["status"] in {TaskStatus.PENDING, TaskStatus.RUNNING})
        completed = sum(task["status"] == TaskStatus.COMPLETED for task in tasks)
        deferred = sum(int(task["deferrals"]) > 0 for task in tasks)
        self.debt_var.set(f"MISSION DEBT BACKLOG: {debt} step(s)    |    Completed: {completed}    |    Deferred: {deferred}    |    Total: {len(tasks)}")
        for task in tasks:
            config = self.run.ledger.tasks[str(task["id"])].config
            progress = 100 * (config.duration_steps - int(task["remaining_steps"])) / config.duration_steps
            self.task_tree.insert("", "end", values=(task["id"], f"{config.power_kw:.1f}", config.duration_steps, config.deadline_step, "HARD" if config.hard_deadline else "SOFT", config.priority, str(task["status"]).upper(), f"{progress:.0f}%", task["deferrals"]))

    def _refresh_events(self) -> None:
        for item in self.event_tree.get_children(): self.event_tree.delete(item)
        for event in self.run.events[-200:]:
            self.event_tree.insert("", "end", values=(event.step, event.kind, event.action, ", ".join(event.reason_codes), event.message))

    def _refresh_telemetry_stream(self) -> None:
        for item in self.telemetry_tree.get_children(): self.telemetry_tree.delete(item)
        for reading in self.run.telemetry_history[-500:]:
            sensors = reading.soc_sensors
            def value(item: float | None, digits: int = 2) -> str:
                return "missing" if item is None else f"{item:.{digits}f}"
            self.telemetry_tree.insert("", "end", values=(reading.step, value(reading.solar_kw), value(reading.load_kw), value(reading.temperature_c, 1), value(reading.oxygen_kg, 2), value(reading.co2_kg, 2), value(reading.water_kg, 2), value(sensors.get("soc_sensor_1"), 3), value(sensors.get("soc_sensor_2"), 3), value(sensors.get("soc_sensor_3"), 3)))
        children = self.telemetry_tree.get_children()
        if children: self.telemetry_tree.see(children[-1])

    def _refresh_comparison(self) -> None:
        if self.compare_runs is None: return
        for name, run in self.compare_runs.items():
            text = self.compare_text[name]
            estimate_view = run.state_view()
            estimated = estimate_view["estimated"]
            horizon = estimate_view["survival_horizon"]
            metrics = run.metrics()
            plan = run.plan_view()
            action = plan["executed"] or {"task_ids": [], "backup_kw": 0.0}
            last_event = run.events[-1].message if run.events else "Waiting for first control step."
            lines = [
                f"SCENARIO       {run.config.id}",
                f"TIME           T+ {run.simulator.state_for_evaluation.step * run.config.dt_hours:.2f} h  (step {run.simulator.state_for_evaluation.step}/{run.config.duration_steps})",
                f"MODE           {str(run.manager.mode).upper()}",
                "",
                "TELEMETRY / SAFETY",
                f"SOC            {estimated['soc'] * 100:.1f}%  ({estimated['battery_kwh']:.1f} kWh)",
                f"QUALITY        {estimated['telemetry_quality'].upper()}",
                f"SURVIVAL       {horizon['hours']:.1f} h  | limiting: {horizon['limiting_factor']}",
                f"OXYGEN         {estimated['oxygen_kg']:.1f} kg    CO₂: {estimated['co2_kg']:.2f} kg",
                f"WATER          {estimated['water_kg']:.1f} kg    TEMP: {estimated['temperature_c']:.1f} °C",
                "",
                "EXECUTED ACTION",
                f"TASKS          {', '.join(action['task_ids']) if action['task_ids'] else 'none'}",
                f"BACKUP POWER   {action['backup_kw']:.1f} kW",
                "",
                "RUN SCORECARD",
                f"COMPLETED      {metrics['tasks_completed']} task(s)",
                f"MISSION DEBT   {metrics['mission_debt_steps']} step(s)",
                f"FALLBACKS      {metrics['fallback_count']}",
                f"CRITICAL GAP   {metrics['critical_load_shortfall_kwh']:.2f} kWh",
                "",
                "LATEST DECISION",
                last_event,
            ]
            text.configure(state="normal")
            text.delete("1.0", "end")
            text.insert("1.0", "\n".join(lines))
            text.configure(state="disabled")
        if all(run.completed for run in self.compare_runs.values()) and self.compare_report is None:
            # The report is computed only after both independently evolving
            # runs reach their normal stopping condition.
            self.compare_report = benchmark_from_runs(self.compare_runs)
        if self.compare_report is not None:
            pairs = self.compare_report.get("pairs", [])
            failures = self.compare_report.get("failures", [])
            if isinstance(pairs, list) and pairs:
                summary = format_pair_summary(pairs[0])
                if isinstance(failures, list) and failures:
                    summary += "\nBenchmark status: one or more runs ended before duration completion; see stop reason above."
                else:
                    summary += "\nBenchmark status: both runs reached configured duration."
                self._set_compare_summary(summary)

    def _draw_charts(self) -> None:
        transitions = self.run.transitions[-64:]
        series = [
            ([transition.after.battery_kwh / self.run.config.battery.capacity_kwh * 100 for transition in transitions], None, 20.0),
            ([transition.solar_kw for transition in transitions], [transition.demand_kw for transition in transitions], None),
            ([transition.after.temperature_c for transition in transitions], None, 16.0),
        ]
        for (_, canvas, color, second_color, _), (first, second, reference) in zip(self.charts, series):
            canvas.delete("all")
            width, height = max(canvas.winfo_width(), 100), max(canvas.winfo_height(), 100)
            # Before the first step, there is no history to plot. Show a clear
            # empty-state panel rather than calling create_line() with no points.
            if not first:
                canvas.create_text(width / 2, height / 2, text="Advance the simulation to draw telemetry", fill=MUTED, font=("Segoe UI", 10))
                if reference is not None:
                    canvas.create_text(12, height - 12, text=f"safe reference: {reference:g}", anchor="sw", fill=YELLOW)
                continue
            all_values = first + (second or []) + ([reference] if reference is not None else [])
            low, high = min(all_values), max(all_values)
            padding = max(1.0, (high - low) * .18); low -= padding; high += padding
            def points(values: list[float]) -> list[float]:
                if len(values) == 1: return [18, height / 2, width - 18, height / 2]
                result: list[float] = []
                for index, value in enumerate(values):
                    x = 18 + index * (width - 36) / max(1, len(values) - 1)
                    y = height - 16 - (value - low) / (high - low) * (height - 32)
                    result.extend((x, y))
                return result
            for fraction in (.25, .5, .75):
                y = 12 + fraction * (height - 24); canvas.create_line(0, y, width, y, fill="#29384d")
            if reference is not None:
                y = height - 16 - (reference - low) / (high - low) * (height - 32)
                canvas.create_line(0, y, width, y, fill=YELLOW, dash=(5, 4)); canvas.create_text(48, y - 8, text=f"limit {reference:g}", fill=YELLOW)
            canvas.create_line(*points(first), fill=color, width=2)
            if second: canvas.create_line(*points(second), fill=second_color, width=2)
            canvas.create_text(10, 10, text=f"{high:.1f}", anchor="nw", fill=MUTED); canvas.create_text(10, height - 8, text=f"{low:.1f}", anchor="sw", fill=MUTED)


def main() -> None:
    """Launch the local desktop dashboard."""
    AstraGridApp().mainloop()


if __name__ == "__main__": main()
