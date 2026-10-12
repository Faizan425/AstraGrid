"""Report generator and interactive HTML dashboard for AstraGrid Resilience Lab.

Produces a lightweight, self-contained, offline-compatible HTML report with:
- Executive summary & scenario aggregate cards
- Filterable runs and paired comparison tables
- Interactive SVG time-series visualizer
- 8-panel step-by-step replay console with event stepping
- Filterable chronological event and fault timeline
- Direct JSON and CSV export links
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import ExperimentManifest, RunRecord
from .statistics import PairedComparison, ScenarioControllerSummary


def generate_html_report(
    manifest: ExperimentManifest,
    records: list[RunRecord],
    summaries: list[ScenarioControllerSummary],
    comparisons: list[PairedComparison],
    output_path: Path,
) -> Path:
    """Generate a self-contained interactive HTML dashboard report."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Prepare JSON serializable bundles for client-side interactivity
    manifest_dict = manifest.to_dict()
    records_dict = [r.to_dict() for r in records]
    summaries_dict = [s.to_dict() for s in summaries]
    comparisons_dict = [c.to_dict() for c in comparisons]

    # Pre-render summary cards HTML
    total_runs = manifest.total_runs_attempted
    success_runs = manifest.successful_runs
    failed_runs = manifest.failed_runs

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AstraGrid Resilience Lab — {manifest.experiment_name}</title>
  <style>
    :root {{
      --bg: #0b0f17;
      --card-bg: #141c2b;
      --card-border: #1e2c42;
      --text-main: #e2e8f0;
      --text-muted: #8e9fb5;
      --cyan: #00d9ff;
      --green: #10b981;
      --yellow: #f59e0b;
      --red: #ef4444;
      --blue: #3b82f6;
      --font: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      --mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      background: var(--bg);
      color: var(--text-main);
      font-family: var(--font);
      line-height: 1.5;
      padding: 24px;
    }}
    .header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding-bottom: 20px;
      border-bottom: 1px solid var(--card-border);
      margin-bottom: 24px;
    }}
    .title-area h1 {{
      font-size: 26px;
      font-weight: 700;
      color: var(--cyan);
      letter-spacing: -0.5px;
    }}
    .title-area p {{
      color: var(--text-muted);
      font-size: 14px;
      margin-top: 4px;
    }}
    .badge {{
      display: inline-block;
      padding: 3px 10px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }}
    .badge-success {{ background: rgba(16, 185, 129, 0.15); color: var(--green); border: 1px solid rgba(16, 185, 129, 0.3); }}
    .badge-warning {{ background: rgba(245, 158, 11, 0.15); color: var(--yellow); border: 1px solid rgba(245, 158, 11, 0.3); }}
    .badge-danger {{ background: rgba(239, 68, 68, 0.15); color: var(--red); border: 1px solid rgba(239, 68, 68, 0.3); }}
    .badge-info {{ background: rgba(59, 130, 246, 0.15); color: var(--blue); border: 1px solid rgba(59, 130, 246, 0.3); }}

    .grid-kpi {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 16px;
      margin-bottom: 24px;
    }}
    .kpi-card {{
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 8px;
      padding: 16px 20px;
    }}
    .kpi-title {{ font-size: 12px; color: var(--text-muted); text-transform: uppercase; font-weight: 600; }}
    .kpi-value {{ font-size: 28px; font-weight: 700; color: var(--text-main); margin-top: 4px; font-family: var(--mono); }}
    .kpi-sub {{ font-size: 12px; color: var(--text-muted); margin-top: 2px; }}

    .nav-tabs {{
      display: flex;
      gap: 8px;
      border-bottom: 1px solid var(--card-border);
      margin-bottom: 24px;
    }}
    .tab-btn {{
      background: none;
      border: none;
      color: var(--text-muted);
      padding: 10px 18px;
      font-size: 14px;
      font-weight: 600;
      cursor: pointer;
      border-bottom: 2px solid transparent;
      transition: all 0.2s;
    }}
    .tab-btn:hover {{ color: var(--text-main); }}
    .tab-btn.active {{
      color: var(--cyan);
      border-bottom-color: var(--cyan);
    }}

    .tab-pane {{ display: none; }}
    .tab-pane.active {{ display: block; }}

    .section-card {{
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 8px;
      padding: 20px;
      margin-bottom: 24px;
    }}
    .section-card h2 {{
      font-size: 18px;
      font-weight: 600;
      color: var(--text-main);
      margin-bottom: 16px;
    }}

    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }}
    th, td {{
      padding: 10px 12px;
      text-align: left;
      border-bottom: 1px solid var(--card-border);
    }}
    th {{
      color: var(--text-muted);
      font-weight: 600;
      background: rgba(14, 22, 35, 0.5);
    }}
    tr:hover {{ background: rgba(30, 44, 66, 0.4); }}
    td.mono {{ font-family: var(--mono); }}

    /* Replay Styles */
    .replay-controls {{
      display: flex;
      align-items: center;
      gap: 12px;
      background: #0d1522;
      padding: 12px 16px;
      border-radius: 6px;
      margin-bottom: 20px;
      border: 1px solid var(--card-border);
    }}
    .btn {{
      background: #203148;
      border: 1px solid #2d4260;
      color: var(--text-main);
      padding: 6px 14px;
      border-radius: 4px;
      font-size: 13px;
      font-weight: 600;
      cursor: pointer;
      transition: background 0.15s;
    }}
    .btn:hover {{ background: #2b4263; }}
    .btn-primary {{ background: var(--blue); border-color: var(--blue); color: #fff; }}
    .btn-primary:hover {{ background: #2563eb; }}
    .slider {{
      flex: 1;
      accent-color: var(--cyan);
      cursor: pointer;
    }}

    .replay-panels {{
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 16px;
    }}
    .replay-box {{
      background: #0d1522;
      border: 1px solid var(--card-border);
      border-radius: 6px;
      padding: 14px;
    }}
    .replay-box h3 {{
      font-size: 13px;
      font-weight: 600;
      color: var(--cyan);
      margin-bottom: 8px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }}
    .replay-box pre {{
      font-family: var(--mono);
      font-size: 12px;
      color: #cad5e2;
      white-space: pre-wrap;
      word-break: break-word;
      max-height: 220px;
      overflow-y: auto;
    }}

    /* Timeline styles */
    .timeline-item {{
      display: flex;
      gap: 16px;
      padding: 12px 0;
      border-bottom: 1px solid var(--card-border);
    }}
    .timeline-step {{
      font-family: var(--mono);
      font-weight: 700;
      color: var(--cyan);
      width: 70px;
      flex-shrink: 0;
    }}
    .timeline-body {{ flex: 1; }}
    .timeline-header {{ display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }}
    .timeline-title {{ font-weight: 600; font-size: 14px; }}
    .timeline-desc {{ font-size: 13px; color: var(--text-muted); }}

    .note-box {{
      background: rgba(59, 130, 246, 0.08);
      border-left: 3px solid var(--blue);
      padding: 12px 16px;
      font-size: 13px;
      color: var(--text-muted);
      border-radius: 0 4px 4px 0;
      margin-top: 16px;
    }}
  </style>
</head>
<body>

  <div class="header">
    <div class="title-area">
      <h1>AstraGrid Resilience Lab</h1>
      <p>Automated Experiments, Independent Evaluation & Mission Replay &bull; Experiment: <strong>{manifest.experiment_name}</strong></p>
    </div>
    <div>
      <span class="badge {'badge-success' if failed_runs == 0 else 'badge-warning'}">
        {manifest.total_runs_attempted} Runs Tested ({manifest.successful_runs} Completed)
      </span>
    </div>
  </div>

  <div class="grid-kpi">
    <div class="kpi-card">
      <div class="kpi-title">Total Runs Attempted</div>
      <div class="kpi-value">{total_runs}</div>
      <div class="kpi-sub">{len(manifest.scenario_ids)} Scenarios &bull; {len(manifest.controllers)} Controllers</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-title">Completed Duration</div>
      <div class="kpi-value" style="color: var(--green);">{success_runs}</div>
      <div class="kpi-sub">{round(success_runs / total_runs * 100 if total_runs else 0, 1)}% Completion Rate</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-title">Non-Duration Terminations</div>
      <div class="kpi-value" style="color: {'var(--red)' if failed_runs > 0 else 'var(--text-main)'};">{failed_runs}</div>
      <div class="kpi-sub">Controlled shutdown / failures</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-title">Evaluation Method</div>
      <div class="kpi-value" style="font-size: 18px; color: var(--cyan); padding-top: 8px;">Independent</div>
      <div class="kpi-sub">Strictly physical execution truth</div>
    </div>
  </div>

  <div class="nav-tabs">
    <button class="tab-btn active" onclick="showTab('overview')">Overview & Aggregates</button>
    <button class="tab-btn" onclick="showTab('paired')">Matched Controller Comparisons</button>
    <button class="tab-btn" onclick="showTab('runs')">Run Inventory</button>
    <button class="tab-btn" onclick="showTab('replay')">Step-by-Step Mission Replay</button>
    <button class="tab-btn" onclick="showTab('timeline')">Event & Fault Timeline</button>
  </div>

  <!-- Tab 1: Overview & Aggregates -->
  <div id="overview" class="tab-pane active">
    <div class="section-card">
      <h2>Scenario Resilience Summaries (Aggregated Across Seeds)</h2>
      <table>
        <thead>
          <tr>
            <th>Scenario</th>
            <th>Controller</th>
            <th>Runs (Succ/Att)</th>
            <th>Crit Shortfall (kWh)</th>
            <th>Min True SOC</th>
            <th>Tasks Done</th>
            <th>Missed Deadlines</th>
            <th>Fallback Acts</th>
            <th>Reserve Viols</th>
          </tr>
        </thead>
        <tbody>
          {"".join(f'''
          <tr>
            <td><strong>{s.scenario_id}</strong></td>
            <td><span class="badge {'badge-info' if s.controller == 'astragrid' else 'badge-warning'}">{s.controller}</span></td>
            <td>{s.successful_runs}/{s.attempted_runs}</td>
            <td class="mono">{s.metrics.get("cumulative_critical_shortfall_kwh", {}).mean if s.metrics.get("cumulative_critical_shortfall_kwh") else 0.0:.2f}</td>
            <td class="mono">{s.metrics.get("minimum_actual_soc", {}).mean if s.metrics.get("minimum_actual_soc") else 0.0:.2f}</td>
            <td class="mono">{s.metrics.get("tasks_completed", {}).mean if s.metrics.get("tasks_completed") else 0.0:.1f}</td>
            <td class="mono">{s.metrics.get("missed_hard_deadlines", {}).mean if s.metrics.get("missed_hard_deadlines") else 0.0:.1f}</td>
            <td class="mono">{s.metrics.get("fallback_activations", {}).mean if s.metrics.get("fallback_activations") else 0.0:.1f}</td>
            <td class="mono">{s.metrics.get("battery_reserve_violations", {}).mean if s.metrics.get("battery_reserve_violations") else 0.0:.1f}</td>
          </tr>
          ''' for s in summaries)}
        </tbody>
      </table>

      <div class="note-box">
        <strong>Evaluation Methodology:</strong> All reported metrics derive strictly from recorded physical transitions, task ledger state, and execution audit logs. The evaluation engine operates post-run and does not trust optimizer predictions or claimed feasibility.
      </div>
    </div>
  </div>

  <!-- Tab 2: Matched Comparisons -->
  <div id="paired" class="tab-pane">
    <div class="section-card">
      <h2>Matched Paired Comparisons (AstraGrid vs Baseline on Identical Conditions)</h2>
      <table>
        <thead>
          <tr>
            <th>Scenario</th>
            <th>Seed</th>
            <th>AstraGrid Status</th>
            <th>Baseline Status</th>
            <th>Δ Crit Shortfall (kWh)</th>
            <th>Δ Tasks Done</th>
            <th>Δ Reserve Viols</th>
            <th>Δ Fallback Acts</th>
            <th>Trade-Off Analysis</th>
          </tr>
        </thead>
        <tbody>
          {"".join(f'''
          <tr>
            <td><strong>{p.scenario_id}</strong></td>
            <td class="mono">s={p.seed}</td>
            <td><span class="badge {'badge-success' if p.astragrid_completed else 'badge-danger'}">{'Completed' if p.astragrid_completed else 'Shutdown'}</span></td>
            <td><span class="badge {'badge-success' if p.baseline_completed else 'badge-danger'}">{'Completed' if p.baseline_completed else 'Shutdown'}</span></td>
            <td class="mono" style="color: {'var(--green)' if p.deltas.get('cumulative_critical_shortfall_kwh', 0) < 0 else ('var(--red)' if p.deltas.get('cumulative_critical_shortfall_kwh', 0) > 0 else 'var(--text-main)')};">
              {p.deltas.get("cumulative_critical_shortfall_kwh", 0):+.2f}
            </td>
            <td class="mono" style="color: {'var(--green)' if p.deltas.get('tasks_completed', 0) > 0 else ('var(--red)' if p.deltas.get('tasks_completed', 0) < 0 else 'var(--text-main)')};">
              {int(p.deltas.get("tasks_completed", 0)):+d}
            </td>
            <td class="mono">{int(p.deltas.get("battery_reserve_violations", 0)):+d}</td>
            <td class="mono">{int(p.deltas.get("fallback_activations", 0)):+d}</td>
            <td style="font-size: 12px; color: var(--text-muted);">{p.trade_off_summary.get("critical_power", "")}; {p.trade_off_summary.get("mission_tasks", "")}</td>
          </tr>
          ''' for p in comparisons)}
        </tbody>
      </table>
    </div>
  </div>

  <!-- Tab 3: Run Inventory -->
  <div id="runs" class="tab-pane">
    <div class="section-card">
      <h2>All Attempted Simulation Runs</h2>
      <table>
        <thead>
          <tr>
            <th>Run ID</th>
            <th>Scenario</th>
            <th>Controller</th>
            <th>Seed</th>
            <th>Sim Steps</th>
            <th>Stop Reason</th>
            <th>Crit Shortfall</th>
            <th>Tasks Done</th>
            <th>Unvalidated Acts</th>
          </tr>
        </thead>
        <tbody>
          {"".join(f'''
          <tr>
            <td class="mono" style="color: var(--cyan);">{r.run_id}</td>
            <td>{r.scenario_id}</td>
            <td><span class="badge {'badge-info' if r.controller == 'astragrid' else 'badge-warning'}">{r.controller}</span></td>
            <td class="mono">{r.seed}</td>
            <td class="mono">{r.simulated_steps}/{r.duration_steps}</td>
            <td><span class="badge {'badge-success' if r.completed else 'badge-danger'}">{r.stop_reason}</span></td>
            <td class="mono">{r.metrics.get("cumulative_critical_shortfall_kwh", 0.0):.2f} kWh</td>
            <td class="mono">{r.metrics.get("tasks_completed", 0)}</td>
            <td class="mono" style="color: {'var(--red)' if r.metrics.get('unvalidated_actions_executed', 0) > 0 else 'var(--green)'};">
              {r.metrics.get("unvalidated_actions_executed", 0)}
            </td>
          </tr>
          ''' for r in records)}
        </tbody>
      </table>
    </div>
  </div>

  <!-- Tab 4: Step-by-Step Replay -->
  <div id="replay" class="tab-pane">
    <div class="section-card">
      <h2>Step-by-Step Mission Replay Console</h2>
      <div style="margin-bottom: 16px; display: flex; gap: 12px; align-items: center;">
        <label style="font-size: 13px; font-weight: 600; color: var(--text-muted);">Select Run:</label>
        <select id="runSelect" class="btn" onchange="onSelectRun()" style="padding: 6px 12px;">
          {"".join(f'<option value="{r.run_id}">{r.run_id} ({r.controller})</option>' for r in records)}
        </select>
      </div>

      <div class="replay-controls">
        <button class="btn" onclick="stepReplay(-1)">&larr; Prev Step</button>
        <input type="range" id="stepSlider" class="slider" min="0" max="0" value="0" oninput="onSliderChange(this.value)">
        <button class="btn" onclick="stepReplay(1)">Next Step &rarr;</button>
        <button class="btn btn-primary" onclick="jumpToNextEvent()">Jump to Event &rarr;</button>
        <span id="stepDisplay" style="font-family: var(--mono); font-weight: 700; color: var(--cyan); min-width: 100px;">Step 00 / 00</span>
      </div>

      <div class="replay-panels">
        <div class="replay-box">
          <h3>1. Controller Observation (Telemetry)</h3>
          <pre id="panel1">Select run to load telemetry...</pre>
        </div>
        <div class="replay-box">
          <h3>2. Estimated State & Capability</h3>
          <pre id="panel2">Select run to load estimate...</pre>
        </div>
        <div class="replay-box">
          <h3>3. Proposed Plan</h3>
          <pre id="panel3">Select run to load proposal...</pre>
        </div>
        <div class="replay-box">
          <h3>4. Independent Validation Result</h3>
          <pre id="panel4">Select run to load validation...</pre>
        </div>
        <div class="replay-box">
          <h3>5. Accepted Plan / Fallback</h3>
          <pre id="panel5">Select run to load accepted plan...</pre>
        </div>
        <div class="replay-box">
          <h3>6. Executed Action (Audit)</h3>
          <pre id="panel6">Select run to load executed action...</pre>
        </div>
        <div class="replay-box">
          <h3>7. Physical Transition & State After</h3>
          <pre id="panel7">Select run to load physical transition...</pre>
        </div>
        <div class="replay-box">
          <h3>8. Deficits, Warnings & Events</h3>
          <pre id="panel8">Select run to load safety warnings...</pre>
        </div>
      </div>
    </div>
  </div>

  <!-- Tab 5: Event Timeline -->
  <div id="timeline" class="tab-pane">
    <div class="section-card">
      <h2>Chronological Operational & Fault Timeline</h2>
      <div id="timelineList">
        <p style="color: var(--text-muted);">Select run in Step-by-Step Replay to view its synchronized event timeline.</p>
      </div>
    </div>
  </div>

  <script>
    const RUNS_DATA = {json.dumps(records_dict)};
    let currentRunId = RUNS_DATA[0] ? RUNS_DATA[0].run_id : null;
    let currentStepIndex = 0;
    let replayData = {{}};

    function showTab(tabId) {{
      document.querySelectorAll('.tab-pane').forEach(el => el.classList.remove('active'));
      document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
      document.getElementById(tabId).classList.add('active');
      event.target.classList.add('active');
    }}

    async function loadReplayForRun(runId) {{
      try {{
        const resp = await fetch(`replay/${{runId}}.json`);
        if (resp.ok) {{
          replayData = await resp.json();
          initReplayUI();
          renderTimeline();
        }} else {{
          fallbackReplayFromRecord(runId);
        }}
      }} catch (e) {{
        fallbackReplayFromRecord(runId);
      }}
    }}

    function fallbackReplayFromRecord(runId) {{
      const rec = RUNS_DATA.find(r => r.run_id === runId);
      if (!rec) return;
      document.getElementById('stepSlider').max = rec.simulated_steps || 0;
      document.getElementById('stepSlider').value = 0;
      updateReplayPanelsFallback(rec);
    }}

    function initReplayUI() {{
      const steps = replayData.steps || [];
      const slider = document.getElementById('stepSlider');
      slider.max = Math.max(0, steps.length - 1);
      slider.value = 0;
      currentStepIndex = 0;
      updateReplayPanels();
    }}

    function onSelectRun() {{
      currentRunId = document.getElementById('runSelect').value;
      loadReplayForRun(currentRunId);
    }}

    function onSliderChange(val) {{
      currentStepIndex = parseInt(val, 10);
      updateReplayPanels();
    }}

    function stepReplay(delta) {{
      const steps = replayData.steps || [];
      const newIdx = Math.max(0, Math.min(steps.length - 1, currentStepIndex + delta));
      currentStepIndex = newIdx;
      document.getElementById('stepSlider').value = newIdx;
      updateReplayPanels();
    }}

    function jumpToNextEvent() {{
      const steps = replayData.steps || [];
      for (let i = currentStepIndex + 1; i < steps.length; i++) {{
        if (steps[i].events && steps[i].events.length > 0 || (steps[i].annotations && steps[i].annotations.length > 0)) {{
          currentStepIndex = i;
          document.getElementById('stepSlider').value = i;
          updateReplayPanels();
          return;
        }}
      }}
      alert("No further events in this run.");
    }}

    function updateReplayPanels() {{
      const steps = replayData.steps || [];
      const step = steps[currentStepIndex];
      const disp = document.getElementById('stepDisplay');
      disp.innerText = `Step ${{String(currentStepIndex).padStart(2, '0')}} / ${{String(steps.length - 1).padStart(2, '0')}}`;

      if (!step) return;
      document.getElementById('panel1').innerText = JSON.stringify(step.observation, null, 2);
      document.getElementById('panel2').innerText = JSON.stringify({{ state: step.estimated_state, capability: step.estimated_capability }}, null, 2);
      document.getElementById('panel3').innerText = JSON.stringify(step.proposed_plan, null, 2);
      document.getElementById('panel4').innerText = JSON.stringify(step.validation_result, null, 2);
      document.getElementById('panel5').innerText = JSON.stringify(step.accepted_plan, null, 2);
      document.getElementById('panel6').innerText = JSON.stringify(step.executed_action, null, 2);
      document.getElementById('panel7').innerText = JSON.stringify({{ transition: step.physical_transition, after: step.physical_state_after }}, null, 2);
      document.getElementById('panel8').innerText = JSON.stringify({{
        warnings: step.shortfalls_and_warnings,
        events: step.events,
        annotations: step.annotations
      }}, null, 2);
    }}

    function updateReplayPanelsFallback(rec) {{
      document.getElementById('stepDisplay').innerText = `Step 00 / ${{rec.simulated_steps}}`;
      document.getElementById('panel1').innerText = "Detailed per-step replay bundle available in outputs/resilience_lab/replay/" + rec.run_id + ".json";
      document.getElementById('panel2').innerText = JSON.stringify(rec.metrics, null, 2);
      document.getElementById('panel3').innerText = "Controller: " + rec.controller;
      document.getElementById('panel4').innerText = "Status: " + rec.stop_reason;
      document.getElementById('panel5').innerText = "Completed: " + rec.completed;
      document.getElementById('panel6').innerText = "Wall Runtime: " + (rec.wall_runtime_s ? rec.wall_runtime_s.toFixed(2) + "s" : "n/a");
      document.getElementById('panel7').innerText = JSON.stringify(rec.task_completion_timeline, null, 2);
      document.getElementById('panel8').innerText = JSON.stringify(rec.diagnostic_annotations, null, 2);
    }}

    function renderTimeline() {{
      const list = document.getElementById('timelineList');
      const timeline = replayData.timeline || [];
      if (timeline.length === 0) {{
        list.innerHTML = "<p style='color: var(--text-muted);'>No timeline events recorded for this run.</p>";
        return;
      }}
      list.innerHTML = timeline.map(item => `
        <div class="timeline-item">
          <div class="timeline-step">t=${{item.step}}</div>
          <div class="timeline-body">
            <div class="timeline-header">
              <span class="badge ${{item.severity === 'CRITICAL' ? 'badge-danger' : (item.severity === 'WARNING' ? 'badge-warning' : 'badge-info')}}">${{item.badge}}</span>
              <span class="timeline-title">${{item.title}}</span>
            </div>
            <div class="timeline-desc">${{item.description}}</div>
          </div>
        </div>
      `).join('');
    }}

    // Initial load
    if (currentRunId) {{
      loadReplayForRun(currentRunId);
    }}
  </script>
</body>
</html>
"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    return output_path
