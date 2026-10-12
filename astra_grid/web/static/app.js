/**
 * AstraGrid — Mission Control & Resilience Simulation Web Frontend
 * High-performance, zero-dependency client application.
 */

(function () {
  'use strict';

  // --- State ---
  const state = {
    currentRunId: null,
    currentScenario: 'combined_fault',
    currentController: 'astragrid',
    autoPlayTimer: null,
    isAutoPlaying: false,
    fullViewData: null,
    replayData: null,
    replayStep: 0,
    activeTab: 'tab-cockpit',
  };

  // --- DOM Elements ---
  const el = {
    // Top HUD
    hudMode: document.getElementById('hud-mode'),
    hudModeContainer: document.getElementById('hud-mode-container'),
    hudTime: document.getElementById('hud-time'),
    hudHorizon: document.getElementById('hud-horizon'),
    hudController: document.getElementById('hud-controller'),

    // Control Deck
    selectScenario: document.getElementById('select-scenario'),
    selectController: document.getElementById('select-controller'),
    btnInit: document.getElementById('btn-init'),
    btnStep: document.getElementById('btn-step'),
    btnStep10: document.getElementById('btn-step-10'),
    btnAutoPlay: document.getElementById('btn-auto-play'),
    labelAutoPlay: document.getElementById('label-auto-play'),
    btnRunAll: document.getElementById('btn-run-all'),
    timelineProgressFill: document.getElementById('timeline-progress-fill'),
    lblProgressStep: document.getElementById('lbl-progress-step'),
    lblProgressPct: document.getElementById('lbl-progress-pct'),

    // Cockpit Card 1: Battery
    badgeBatteryStatus: document.getElementById('badge-battery-status'),
    gaugeSocArc: document.getElementById('gauge-soc-arc'),
    gaugeReserveArc: document.getElementById('gauge-reserve-arc'),
    valSoc: document.getElementById('val-soc'),
    valBatteryKwh: document.getElementById('val-battery-kwh'),
    valBatteryReserve: document.getElementById('val-battery-reserve'),
    valBatteryRate: document.getElementById('val-battery-rate'),

    // Cockpit Card 2: Power
    badgeBusStatus: document.getElementById('badge-bus-status'),
    valSolarKw: document.getElementById('val-solar-kw'),
    barSolar: document.getElementById('bar-solar'),
    valBackupKw: document.getElementById('val-backup-kw'),
    barBackup: document.getElementById('bar-backup'),
    valDemandKw: document.getElementById('val-demand-kw'),
    barDemand: document.getElementById('bar-demand'),
    valCriticalKw: document.getElementById('val-critical-kw'),
    valShortfallKw: document.getElementById('val-shortfall-kw'),

    // Cockpit Card 3: Life Support
    badgeLifeStatus: document.getElementById('badge-life-status'),
    valOxygen: document.getElementById('val-oxygen'),
    barOxygen: document.getElementById('bar-oxygen'),
    valOxygenSub: document.getElementById('val-oxygen-sub'),
    valCo2: document.getElementById('val-co2'),
    barCo2: document.getElementById('bar-co2'),
    valCo2Sub: document.getElementById('val-co2-sub'),
    valWater: document.getElementById('val-water'),
    barWater: document.getElementById('bar-water'),
    valWaterSub: document.getElementById('val-water-sub'),
    valTemp: document.getElementById('val-temp'),
    barTemp: document.getElementById('bar-temp'),
    valTempSub: document.getElementById('val-temp-sub'),

    // Cockpit Card 4: Tasks & Debt
    badgeDebtStatus: document.getElementById('badge-debt-status'),
    valTasksDone: document.getElementById('val-tasks-done'),
    valTasksPending: document.getElementById('val-tasks-pending'),
    valTasksMissed: document.getElementById('val-tasks-missed'),
    valDebtSteps: document.getElementById('val-debt-steps'),
    valFallbacks: document.getElementById('val-fallbacks'),
    valUnvalidated: document.getElementById('val-unvalidated'),

    // Schematic
    schemValSolar: document.getElementById('schem-val-solar'),
    schemValBackup: document.getElementById('schem-val-backup'),
    schemValBattery: document.getElementById('schem-val-battery'),
    schemValPdu: document.getElementById('schem-val-pdu'),
    schemValCritical: document.getElementById('schem-val-critical'),
    schemValThermal: document.getElementById('schem-val-thermal'),
    schemValTasks: document.getElementById('schem-val-tasks'),
    flowSolar: document.getElementById('flow-solar'),
    flowBackup: document.getElementById('flow-backup'),
    flowBattery: document.getElementById('flow-battery'),
    flowCritical: document.getElementById('flow-critical'),
    flowThermal: document.getElementById('flow-thermal'),
    flowTasks: document.getElementById('flow-tasks'),

    // Inspector
    inspProposedCtrl: document.getElementById('insp-proposed-ctrl'),
    inspSolverStatus: document.getElementById('insp-solver-status'),
    inspValidationStatus: document.getElementById('insp-validation-status'),
    inspExecutedCtrl: document.getElementById('insp-executed-ctrl'),
    inspFallbackUsed: document.getElementById('insp-fallback-used'),
    inspReasonsList: document.getElementById('insp-reasons-list'),
    inspActionJson: document.getElementById('insp-action-json'),

    // Task Ledger
    badgeTaskCount: document.getElementById('badge-task-count'),
    tbodyTasks: document.getElementById('tbody-tasks'),

    // Comparison
    btnRunComparison: document.getElementById('btn-run-comparison'),
    deltaShortfall: document.getElementById('delta-shortfall'),
    deltaTasks: document.getElementById('delta-tasks'),
    deltaReserve: document.getElementById('delta-reserve'),
    deltaFallbacks: document.getElementById('delta-fallbacks'),
    tbodyComparison: document.getElementById('tbody-comparison'),

    // Events & Telemetry
    badgeEventCount: document.getElementById('badge-event-count'),
    eventFeedList: document.getElementById('event-feed-list'),
    telemetryJsonViewer: document.getElementById('telemetry-json-viewer'),

    // Replay
    btnReplayPrev: document.getElementById('btn-replay-prev'),
    btnReplayNext: document.getElementById('btn-replay-next'),
    btnReplayEvent: document.getElementById('btn-replay-event'),
    replayScrubber: document.getElementById('replay-scrubber'),
    lblReplayStep: document.getElementById('lbl-replay-step'),
    replayPanel1: document.getElementById('replay-panel-1'),
    replayPanel2: document.getElementById('replay-panel-2'),
    replayPanel3: document.getElementById('replay-panel-3'),
    replayPanel4: document.getElementById('replay-panel-4'),
    replayPanel5: document.getElementById('replay-panel-5'),
    replayPanel6: document.getElementById('replay-panel-6'),
    replayPanel7: document.getElementById('replay-panel-7'),
    replayPanel8: document.getElementById('replay-panel-8'),

    // Canvases
    canvasPower: document.getElementById('canvas-power'),
    canvasBattery: document.getElementById('canvas-battery'),
    canvasResources: document.getElementById('canvas-resources'),
    canvasThermal: document.getElementById('canvas-thermal'),
  };

  // --- API Utilities ---
  async function apiFetch(endpoint, options = {}) {
    try {
      const resp = await fetch(endpoint, {
        headers: { 'Content-Type': 'application/json', ...options.headers },
        ...options,
      });
      if (!resp.ok) {
        const error = await resp.json().catch(() => ({ message: resp.statusText }));
        throw new Error(error.detail?.message || error.detail || error.message || 'API request failed');
      }
      return await resp.json();
    } catch (err) {
      console.error(`API Error on ${endpoint}:`, err);
      throw err;
    }
  }

  // --- Simulation Actions ---
  async function initSimulation() {
    stopAutoPlay();
    const scenario = el.selectScenario.value;
    const controller = el.selectController.value;
    state.currentScenario = scenario;
    state.currentController = controller;

    el.btnInit.disabled = true;
    el.btnInit.innerHTML = `<span class="spinner"></span> INITIALIZING...`;

    try {
      const data = await apiFetch('/api/simulations', {
        method: 'POST',
        body: JSON.stringify({ scenario_id: scenario, controller: controller }),
      });
      state.currentRunId = data.run_id;
      await refreshFullView();
    } catch (err) {
      alert(`Initialization failed: ${err.message}`);
    } finally {
      el.btnInit.disabled = false;
      el.btnInit.innerHTML = `
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M23 4v6h-6M1 20v-6h6M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>
        INITIALIZE`;
    }
  }

  async function stepSimulation() {
    if (!state.currentRunId) await initSimulation();
    if (!state.currentRunId) return;

    el.btnStep.disabled = true;
    try {
      await apiFetch(`/api/simulations/${state.currentRunId}/step`, { method: 'POST' });
      await refreshFullView();
    } catch (err) {
      stopAutoPlay();
      console.error(err);
    } finally {
      el.btnStep.disabled = false;
    }
  }

  async function step10Simulation() {
    if (!state.currentRunId) await initSimulation();
    if (!state.currentRunId) return;

    el.btnStep10.disabled = true;
    try {
      await apiFetch(`/api/simulations/${state.currentRunId}/run`, {
        method: 'POST',
        body: JSON.stringify({ steps: 10 }),
      });
      await refreshFullView();
    } catch (err) {
      console.error(err);
    } finally {
      el.btnStep10.disabled = false;
    }
  }

  async function runAllSimulation() {
    if (!state.currentRunId) await initSimulation();
    if (!state.currentRunId) return;

    el.btnRunAll.disabled = true;
    el.btnRunAll.innerHTML = `<span class="spinner"></span> RUNNING 24h...`;
    try {
      await apiFetch(`/api/simulations/${state.currentRunId}/run`, {
        method: 'POST',
        body: JSON.stringify({ steps: 96 }),
      });
      await refreshFullView();
    } catch (err) {
      console.error(err);
    } finally {
      el.btnRunAll.disabled = false;
      el.btnRunAll.innerHTML = `
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 4 15 12 5 20 5 4"/><line x1="19" y1="5" x2="19" y2="19"/></svg>
        RUN ALL (24h)`;
    }
  }

  function toggleAutoPlay() {
    if (state.isAutoPlaying) {
      stopAutoPlay();
    } else {
      startAutoPlay();
    }
  }

  function startAutoPlay() {
    if (!state.currentRunId) {
      initSimulation().then(startAutoPlay);
      return;
    }
    state.isAutoPlaying = true;
    el.btnAutoPlay.classList.add('btn-active');
    el.labelAutoPlay.textContent = 'PAUSE';

    state.autoPlayTimer = setInterval(async () => {
      if (!state.currentRunId || (state.fullViewData && state.fullViewData.snapshot?.completed)) {
        stopAutoPlay();
        return;
      }
      try {
        await apiFetch(`/api/simulations/${state.currentRunId}/step`, { method: 'POST' });
        await refreshFullView();
      } catch (e) {
        stopAutoPlay();
      }
    }, 450);
  }

  function stopAutoPlay() {
    state.isAutoPlaying = false;
    if (state.autoPlayTimer) {
      clearInterval(state.autoPlayTimer);
      state.autoPlayTimer = null;
    }
    el.btnAutoPlay.classList.remove('btn-active');
    el.labelAutoPlay.textContent = 'AUTO RUN';
  }

  // --- Fetch and Update UI ---
  async function refreshFullView() {
    if (!state.currentRunId) return;
    try {
      const data = await apiFetch(`/api/simulations/${state.currentRunId}/full_view`);
      state.fullViewData = data;
      renderAll(data);
      if (state.activeTab === 'tab-replay') {
        loadReplayData();
      }
    } catch (err) {
      console.error('Error refreshing full view:', err);
    }
  }

  function renderAll(data) {
    renderHUD(data);
    renderProgress(data);
    renderBatteryCard(data);
    renderPowerCard(data);
    renderLifeSupportCard(data);
    renderDebtCard(data);
    renderSchematic(data);
    renderInspector(data);
    renderTaskLedger(data);
    renderEventTimeline(data);
    renderTelemetryStream(data);
    renderCharts(data);
  }

  // --- Render Sections ---
  function renderHUD(data) {
    const snap = data.snapshot || {};
    const step = snap.step || 0;
    const hours = (step * 0.25).toFixed(1);
    el.hudTime.textContent = `STEP ${String(step).padStart(2, '0')} (${hours}h)`;

    const mode = snap.mode || 'normal';
    el.hudMode.textContent = mode.toUpperCase().replace('_', ' ');
    el.hudMode.className = `hud-badge mode-${mode}`;

    const estState = data.state?.estimated || {};
    const horizon = data.state?.survival_horizon?.hours;
    el.hudHorizon.textContent = horizon !== undefined ? `${horizon.toFixed(1)} h` : '-- h';

    el.hudController.textContent = snap.controller === 'astragrid' ? 'AstraGrid MILP' : 'Baseline Heuristic';
  }

  function renderProgress(data) {
    const step = data.snapshot?.step || 0;
    const maxSteps = data.max_steps || 96;
    const pct = Math.min(100, (step / maxSteps) * 100);

    el.timelineProgressFill.style.width = `${pct}%`;
    el.lblProgressStep.textContent = `STEP ${step} / ${maxSteps}`;
    el.lblProgressPct.textContent = `${pct.toFixed(1)}%`;
  }

  function renderBatteryCard(data) {
    const est = data.state?.estimated || {};
    const metrics = data.metrics || {};
    const soc = est.soc !== undefined ? est.soc : (metrics.minimum_true_soc || 1.0);
    const socPct = Math.round(soc * 100);

    el.valSoc.textContent = `${socPct}%`;

    // SVG radial gauge arc: radius = 50, circumference = 2 * PI * 50 = 314.159
    const circ = 2 * Math.PI * 50;
    const offset = circ - (socPct / 100) * circ;
    el.gaugeSocArc.style.strokeDashoffset = offset;

    // Color code gauge based on state
    if (soc <= 0.25) {
      el.gaugeSocArc.style.stroke = '#ef4444';
      el.badgeBatteryStatus.textContent = 'CRITICAL';
      el.badgeBatteryStatus.className = 'card-badge badge-red';
    } else if (soc <= 0.40) {
      el.gaugeSocArc.style.stroke = '#f59e0b';
      el.badgeBatteryStatus.textContent = 'DEGRADED';
      el.badgeBatteryStatus.className = 'card-badge badge-amber';
    } else {
      el.gaugeSocArc.style.stroke = '#00f0ff';
      el.badgeBatteryStatus.textContent = 'HEALTHY';
      el.badgeBatteryStatus.className = 'card-badge badge-green';
    }

    const cap = data.battery_capacity || 60.0;
    const reserve = data.battery_reserve || 12.0;
    const kwh = (soc * cap).toFixed(1);
    el.valBatteryKwh.textContent = `${kwh} / ${cap.toFixed(0)} kWh`;
    el.valBatteryReserve.textContent = `${reserve.toFixed(1)} kWh (${Math.round((reserve / cap) * 100)}%)`;

    // Net rate from recent transitions
    const history = data.history || [];
    if (history.length >= 2) {
      const prev = history[history.length - 2];
      const curr = history[history.length - 1];
      const deltaKwh = curr.battery_kwh - prev.battery_kwh;
      const rateKw = (deltaKwh / 0.25).toFixed(1);
      const sign = rateKw > 0 ? '+' : '';
      el.valBatteryRate.textContent = `${sign}${rateKw} kW`;
      el.valBatteryRate.style.color = rateKw >= 0 ? '#10b981' : '#f59e0b';
    } else {
      el.valBatteryRate.textContent = '0.0 kW';
      el.valBatteryRate.style.color = 'inherit';
    }
  }

  function renderPowerCard(data) {
    const obs = data.last_observation || {};
    const plan = data.plan || {};
    const executed = plan.executed || {};
    const trans = data.history?.length ? data.history[data.history.length - 1] : null;

    const solarKw = obs.solar_kw !== undefined ? obs.solar_kw : (trans?.solar_kw || 0.0);
    const backupKw = executed.backup_kw !== undefined ? executed.backup_kw : (trans?.backup_kw || 0.0);
    const demandKw = trans?.bus_demand_kw || 10.5;
    const critShortfall = trans?.critical_shortfall_kw || 0.0;

    el.valSolarKw.textContent = `${solarKw.toFixed(1)} kW`;
    el.barSolar.style.width = `${Math.min(100, (solarKw / 25.0) * 100)}%`;

    el.valBackupKw.textContent = `${backupKw.toFixed(1)} kW`;
    el.barBackup.style.width = `${Math.min(100, (backupKw / 15.0) * 100)}%`;

    el.valDemandKw.textContent = `${demandKw.toFixed(1)} kW`;
    el.barDemand.style.width = `${Math.min(100, (demandKw / 25.0) * 100)}%`;

    el.valCriticalKw.textContent = `10.5 kW`;
    el.valShortfallKw.textContent = `${critShortfall.toFixed(2)} kW`;

    if (critShortfall > 0) {
      el.badgeBusStatus.textContent = 'SHORTFALL';
      el.badgeBusStatus.className = 'card-badge badge-red';
      el.valShortfallKw.style.color = '#ef4444';
    } else {
      el.badgeBusStatus.textContent = 'BALANCED';
      el.badgeBusStatus.className = 'card-badge badge-green';
      el.valShortfallKw.style.color = '#10b981';
    }
  }

  function renderLifeSupportCard(data) {
    const est = data.state?.estimated || {};
    const trans = data.history?.length ? data.history[data.history.length - 1] : null;

    const oxygen = trans?.oxygen_kg !== undefined ? trans.oxygen_kg : (est.oxygen_kg || 48.0);
    const co2 = trans?.co2_kg !== undefined ? trans.co2_kg : (est.co2_kg || 0.8);
    const water = trans?.water_kg !== undefined ? trans.water_kg : (est.water_kg || 120.0);
    const temp = trans?.temperature_c !== undefined ? trans.temperature_c : (est.temperature_c || 21.0);

    el.valOxygen.textContent = `${oxygen.toFixed(1)} kg`;
    el.barOxygen.style.width = `${Math.min(100, (oxygen / 50.0) * 100)}%`;
    el.valOxygenSub.textContent = `Reserve: ${data.oxygen_reserve || 12.0} kg`;

    el.valCo2.textContent = `${co2.toFixed(2)} kg`;
    el.barCo2.style.width = `${Math.min(100, (co2 / (data.co2_limit || 2.0)) * 100)}%`;
    el.valCo2Sub.textContent = `Limit: ${data.co2_limit || 2.0} kg`;

    el.valWater.textContent = `${water.toFixed(1)} kg`;
    el.barWater.style.width = `${Math.min(100, (water / 150.0) * 100)}%`;
    el.valWaterSub.textContent = `Reserve: ${data.water_reserve || 30.0} kg`;

    el.valTemp.textContent = `${temp.toFixed(1)} °C`;
    const tempPct = Math.max(0, Math.min(100, ((temp - 10) / (30 - 10)) * 100));
    el.barTemp.style.width = `${tempPct}%`;
    el.valTempSub.textContent = `Safe: ${data.temp_min || 18} - ${data.temp_max || 24}°C`;

    // Life support status badge
    if (oxygen < (data.oxygen_reserve || 12.0) || co2 > (data.co2_limit || 2.0) || temp < 18 || temp > 24) {
      el.badgeLifeStatus.textContent = 'ALARM';
      el.badgeLifeStatus.className = 'card-badge badge-red';
    } else {
      el.badgeLifeStatus.textContent = 'NOMINAL';
      el.badgeLifeStatus.className = 'card-badge badge-green';
    }
  }

  function renderDebtCard(data) {
    const metrics = data.metrics || {};
    const tasks = data.tasks || [];

    const done = metrics.tasks_completed || tasks.filter((t) => t.status === 'completed').length;
    const pending = tasks.filter((t) => t.status === 'pending' || t.status === 'running').length;
    const missed = metrics.missed_hard_deadlines || tasks.filter((t) => t.status === 'missed').length;
    const debtSteps = metrics.mission_debt_steps || 0;

    el.valTasksDone.textContent = done;
    el.valTasksPending.textContent = pending;
    el.valTasksMissed.textContent = missed;
    el.valDebtSteps.textContent = debtSteps;

    el.valFallbacks.textContent = metrics.fallback_count || 0;
    el.valUnvalidated.textContent = metrics.unvalidated_actions_executed || 0;
  }

  function renderSchematic(data) {
    const obs = data.last_observation || {};
    const plan = data.plan || {};
    const executed = plan.executed || {};
    const trans = data.history?.length ? data.history[data.history.length - 1] : null;

    const solarKw = obs.solar_kw !== undefined ? obs.solar_kw : (trans?.solar_kw || 0.0);
    const backupKw = executed.backup_kw !== undefined ? executed.backup_kw : (trans?.backup_kw || 0.0);
    const batteryKwh = trans?.battery_kwh || 60.0;
    const demandKw = trans?.bus_demand_kw || 10.5;

    el.schemValSolar.textContent = `${solarKw.toFixed(1)} kW`;
    el.schemValBackup.textContent = `${backupKw.toFixed(1)} kW`;
    el.schemValBattery.textContent = `${batteryKwh.toFixed(1)} kWh`;
    el.schemValPdu.textContent = `${(solarKw + backupKw).toFixed(1)} kW`;

    // Active flow paths
    if (solarKw > 0.1) el.flowSolar.classList.add('flow-active');
    else el.flowSolar.classList.remove('flow-active');

    if (backupKw > 0.1) el.flowBackup.classList.add('flow-active');
    else el.flowBackup.classList.remove('flow-active');

    if (trans && trans.solar_kw + backupKw < demandKw) el.flowBattery.classList.add('flow-active');
    else el.flowBattery.classList.remove('flow-active');

    el.flowCritical.classList.add('flow-active');
    el.schemValCritical.textContent = '10.5 kW';
    el.schemValThermal.textContent = `${(trans?.temperature_c && trans.temperature_c < 20 ? 1.5 : 0.0).toFixed(1)} kW`;
    el.schemValTasks.textContent = `${executed.task_ids?.length ? (executed.task_ids.length * 2.5).toFixed(1) : '0.0'} kW`;
  }

  function renderInspector(data) {
    const plan = data.plan || {};
    const proposed = plan.proposed;
    const validator = plan.validator;
    const executed = plan.executed;

    el.inspProposedCtrl.textContent = proposed?.controller || 'None';

    if (proposed?.status) {
      el.inspSolverStatus.textContent = proposed.status.toUpperCase();
      el.inspSolverStatus.className = `box-badge ${proposed.status === 'optimal' || proposed.status === 'candidate' ? 'badge-green' : 'badge-amber'}`;
    } else {
      el.inspSolverStatus.textContent = 'AWAITING';
      el.inspSolverStatus.className = 'box-badge';
    }

    if (validator) {
      el.inspValidationStatus.textContent = validator.accepted ? 'ACCEPTED' : 'REJECTED';
      el.inspValidationStatus.className = `box-badge ${validator.accepted ? 'badge-green' : 'badge-red'}`;
    } else {
      el.inspValidationStatus.textContent = 'PENDING';
      el.inspValidationStatus.className = 'box-badge';
    }

    el.inspExecutedCtrl.textContent = executed ? (validator?.accepted ? proposed?.controller : 'Baseline Fallback') : 'None';
    el.inspFallbackUsed.textContent = validator && !validator.accepted ? 'Yes (Fallback Engaged)' : 'No';
    el.inspFallbackUsed.style.color = validator && !validator.accepted ? '#f59e0b' : '#10b981';

    // Reasons list
    el.inspReasonsList.innerHTML = '';
    const reasons = validator?.reasons || [];
    if (reasons.length === 0) {
      el.inspReasonsList.innerHTML = `<li class="reason-item text-green">✓ All constraints validated safely.</li>`;
    } else {
      reasons.forEach((r) => {
        const li = document.createElement('li');
        li.className = 'reason-item text-alarm';
        li.textContent = `⚠ ${r}`;
        el.inspReasonsList.appendChild(li);
      });
    }

    // Executed action JSON
    el.inspActionJson.textContent = JSON.stringify(executed || {}, null, 2);
  }

  function renderTaskLedger(data) {
    const tasks = data.tasks || [];
    el.badgeTaskCount.textContent = `${tasks.length} Tasks Configured`;

    if (tasks.length === 0) {
      el.tbodyTasks.innerHTML = `<tr><td colspan="10" class="text-muted text-center">No tasks loaded. Initialize a scenario.</td></tr>`;
      return;
    }

    el.tbodyTasks.innerHTML = '';
    tasks.forEach((t) => {
      const tr = document.createElement('tr');
      const statusClass = `badge-${t.status === 'completed' ? 'green' : t.status === 'running' ? 'cyan' : t.status === 'missed' ? 'red' : 'amber'}`;
      const progressPct = t.duration_steps > 0 ? Math.round(((t.duration_steps - t.remaining_steps) / t.duration_steps) * 100) : 0;

      tr.innerHTML = `
        <td class="mono font-bold">${t.id}</td>
        <td><span class="badge ${t.priority >= 3 ? 'badge-red' : 'badge-info'}">P${t.priority}</span></td>
        <td class="mono">${t.power_kw} kW</td>
        <td class="mono">${t.duration_steps} steps</td>
        <td class="mono">Step ${t.release_step}</td>
        <td class="mono">${t.deadline_step !== null ? 'Step ' + t.deadline_step : 'None'}</td>
        <td><span class="badge ${t.hard_deadline ? 'badge-red' : 'badge-info'}">${t.hard_deadline ? 'HARD' : 'FLEX'}</span></td>
        <td><span class="badge ${statusClass}">${t.status.toUpperCase()}</span></td>
        <td>
          <div class="table-progress-wrap">
            <div class="meter-bar"><div class="meter-fill fill-cyan" style="width: ${progressPct}%;"></div></div>
            <span class="table-progress-lbl mono">${progressPct}%</span>
          </div>
        </td>
        <td class="mono text-center">${t.deferrals}</td>
      `;
      el.tbodyTasks.appendChild(tr);
    });
  }

  function renderEventTimeline(data) {
    const events = data.events || [];
    el.badgeEventCount.textContent = `${events.length} Events`;

    if (events.length === 0) {
      el.eventFeedList.innerHTML = `<div class="empty-feed-placeholder text-muted">Awaiting simulation events...</div>`;
      return;
    }

    el.eventFeedList.innerHTML = '';
    // Show newest first
    const reversed = [...events].reverse();
    reversed.forEach((e) => {
      const card = document.createElement('div');
      card.className = `event-card event-cat-${e.category || 'general'}`;
      const timeHrs = (e.step * 0.25).toFixed(1);

      card.innerHTML = `
        <div class="event-meta">
          <span class="event-step mono">STEP ${e.step} (${timeHrs}h)</span>
          <span class="event-badge badge-info">${(e.category || 'SYSTEM').toUpperCase()}</span>
          <span class="event-action mono">${e.action || ''}</span>
        </div>
        <div class="event-msg">${e.message || ''}</div>
        ${e.reasons?.length ? `<div class="event-reasons mono">${e.reasons.join('; ')}</div>` : ''}
      `;
      el.eventFeedList.appendChild(card);
    });
  }

  function renderTelemetryStream(data) {
    const obs = data.last_observation;
    if (!obs) {
      el.telemetryJsonViewer.textContent = '{"status": "Awaiting initial observation..."}';
      return;
    }
    el.telemetryJsonViewer.textContent = JSON.stringify(obs, null, 2);
  }

  // --- HTML5 Canvas Charts ---
  function renderCharts(data) {
    const history = data.history || [];
    drawPowerChart(el.canvasPower, history);
    drawBatteryChart(el.canvasBattery, history, data.battery_reserve || 12.0);
    drawResourcesChart(el.canvasResources, history, data.oxygen_reserve || 12.0, data.co2_limit || 2.0);
    drawThermalChart(el.canvasThermal, history, data.temp_min || 18.0, data.temp_max || 24.0);
  }

  function setupCanvas(canvas) {
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    const width = rect.width || canvas.width || 700;
    const height = rect.height || canvas.height || 260;

    canvas.width = width * dpr;
    canvas.height = height * dpr;
    const ctx = canvas.getContext('2d');
    ctx.resetTransform?.();
    ctx.scale(dpr, dpr);
    return { ctx, width, height };
  }

  function drawGrid(ctx, width, height, maxSteps, yMin, yMax, yLabelFormatter) {
    ctx.clearRect(0, 0, width, height);

    const padLeft = 45;
    const padRight = 15;
    const padTop = 15;
    const padBottom = 25;
    const plotW = width - padLeft - padRight;
    const plotH = height - padTop - padBottom;

    // Draw horizontal grid lines
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.07)';
    ctx.lineWidth = 1;
    ctx.fillStyle = '#64748b';
    ctx.font = '10px "JetBrains Mono", monospace';
    ctx.textAlign = 'right';

    const yDivs = 4;
    for (let i = 0; i <= yDivs; i++) {
      const y = padTop + (i / yDivs) * plotH;
      const val = yMax - (i / yDivs) * (yMax - yMin);
      ctx.beginPath();
      ctx.moveTo(padLeft, y);
      ctx.lineTo(width - padRight, y);
      ctx.stroke();
      ctx.fillText(yLabelFormatter ? yLabelFormatter(val) : val.toFixed(1), padLeft - 6, y + 3);
    }

    // Draw vertical step lines
    ctx.textAlign = 'center';
    const xDivs = 8;
    for (let i = 0; i <= xDivs; i++) {
      const stepVal = Math.round((i / xDivs) * maxSteps);
      const x = padLeft + (i / xDivs) * plotW;
      ctx.beginPath();
      ctx.moveTo(x, padTop);
      ctx.lineTo(x, height - padBottom);
      ctx.stroke();
      ctx.fillText(`s${stepVal}`, x, height - 8);
    }

    return { padLeft, padRight, padTop, padBottom, plotW, plotH };
  }

  function drawSeries(ctx, bounds, dataPoints, maxSteps, yMin, yMax, color, lineWidth = 2, fill = false) {
    if (!dataPoints || dataPoints.length === 0) return;
    const { padLeft, padTop, plotW, plotH } = bounds;
    const yRange = yMax - yMin || 1;

    ctx.beginPath();
    let firstX = 0;
    let firstY = 0;

    dataPoints.forEach((pt, idx) => {
      const x = padLeft + (pt.step / maxSteps) * plotW;
      const normY = Math.max(0, Math.min(1, (pt.val - yMin) / yRange));
      const y = padTop + plotH - normY * plotH;

      if (idx === 0) {
        ctx.moveTo(x, y);
        firstX = x;
        firstY = y;
      } else {
        ctx.lineTo(x, y);
      }
    });

    ctx.strokeStyle = color;
    ctx.lineWidth = lineWidth;
    ctx.stroke();

    if (fill && dataPoints.length > 1) {
      const lastX = padLeft + (dataPoints[dataPoints.length - 1].step / maxSteps) * plotW;
      ctx.lineTo(lastX, padTop + plotH);
      ctx.lineTo(firstX, padTop + plotH);
      ctx.closePath();
      ctx.fillStyle = color.replace(')', ', 0.12)').replace('rgb', 'rgba');
      ctx.fill();
    }
  }

  function drawThresholdLine(ctx, bounds, val, yMin, yMax, color, label) {
    const { padLeft, padRight, padTop, plotW, plotH } = bounds;
    const yRange = yMax - yMin || 1;
    if (val < yMin || val > yMax) return;

    const normY = (val - yMin) / yRange;
    const y = padTop + plotH - normY * plotH;

    ctx.save();
    ctx.strokeStyle = color;
    ctx.setLineDash([4, 4]);
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(padLeft, y);
    ctx.lineTo(padLeft + plotW, y);
    ctx.stroke();

    if (label) {
      ctx.fillStyle = color;
      ctx.font = '9px "JetBrains Mono", monospace';
      ctx.textAlign = 'right';
      ctx.fillText(label, padLeft + plotW - 4, y - 4);
    }
    ctx.restore();
  }

  function drawPowerChart(canvas, history) {
    const { ctx, width, height } = setupCanvas(canvas);
    const bounds = drawGrid(ctx, width, height, 96, 0, 30, (v) => `${v.toFixed(0)}k`);

    const solarPts = history.map((h) => ({ step: h.step, val: h.solar_kw }));
    const backupPts = history.map((h) => ({ step: h.step, val: h.backup_kw }));
    const demandPts = history.map((h) => ({ step: h.step, val: h.bus_demand_kw }));
    const shortfallPts = history.map((h) => ({ step: h.step, val: h.critical_shortfall_kw }));

    drawSeries(ctx, bounds, solarPts, 96, 0, 30, 'rgb(234, 179, 8)', 2, true);
    drawSeries(ctx, bounds, backupPts, 96, 0, 30, 'rgb(0, 240, 255)', 2);
    drawSeries(ctx, bounds, demandPts, 96, 0, 30, 'rgb(168, 85, 247)', 2);
    drawSeries(ctx, bounds, shortfallPts, 96, 0, 30, 'rgb(239, 68, 68)', 2.5);
  }

  function drawBatteryChart(canvas, history, reserveKwh) {
    const { ctx, width, height } = setupCanvas(canvas);
    const bounds = drawGrid(ctx, width, height, 96, 0, 70, (v) => `${v.toFixed(0)}k`);

    const battPts = history.map((h) => ({ step: h.step, val: h.battery_kwh }));
    drawSeries(ctx, bounds, battPts, 96, 0, 70, 'rgb(0, 240, 255)', 2.5, true);
    drawThresholdLine(ctx, bounds, reserveKwh, 0, 70, '#ef4444', `Safe Reserve (${reserveKwh} kWh)`);
  }

  function drawResourcesChart(canvas, history, o2Reserve, co2Limit) {
    const { ctx, width, height } = setupCanvas(canvas);
    const bounds = drawGrid(ctx, width, height, 96, 0, 60, (v) => `${v.toFixed(0)}kg`);

    const o2Pts = history.map((h) => ({ step: h.step, val: h.oxygen_kg }));
    const co2Pts = history.map((h) => ({ step: h.step, val: h.co2_kg * 10 })); // Scale 10x for visibility

    drawSeries(ctx, bounds, o2Pts, 96, 0, 60, 'rgb(0, 240, 255)', 2);
    drawSeries(ctx, bounds, co2Pts, 96, 0, 60, 'rgb(245, 158, 11)', 2);
    drawThresholdLine(ctx, bounds, o2Reserve, 0, 60, '#ef4444', `O₂ Reserve (${o2Reserve}kg)`);
    drawThresholdLine(ctx, bounds, co2Limit * 10, 0, 60, '#ef4444', `CO₂ Limit (${co2Limit}kg)`);
  }

  function drawThermalChart(canvas, history, minT, maxT) {
    const { ctx, width, height } = setupCanvas(canvas);
    const bounds = drawGrid(ctx, width, height, 96, 10, 30, (v) => `${v.toFixed(0)}°C`);

    const tempPts = history.map((h) => ({ step: h.step, val: h.temperature_c }));
    drawSeries(ctx, bounds, tempPts, 96, 10, 30, 'rgb(16, 185, 129)', 2.5);
    drawThresholdLine(ctx, bounds, minT, 10, 30, '#ef4444', `Min ${minT}°C`);
    drawThresholdLine(ctx, bounds, maxT, 10, 30, '#ef4444', `Max ${maxT}°C`);
  }

  // --- Matched Controller Comparison ---
  async function runMatchedComparison() {
    el.btnRunComparison.disabled = true;
    el.btnRunComparison.innerHTML = `<span class="spinner"></span> RUNNING MATCHED COMPARISON...`;

    try {
      const scenario = el.selectScenario.value;
      const data = await apiFetch('/api/simulations/compare_full', {
        method: 'POST',
        body: JSON.stringify({ scenario_id: scenario }),
      });

      const pair = data.pair || {};
      const astragrid = pair.astragrid || {};
      const baseline = pair.baseline || {};
      const deltas = pair.delta_astragrid_minus_baseline || {};

      // Update Delta Cards
      el.deltaShortfall.textContent = `${(deltas.critical_shortfall_kwh || 0.0).toFixed(2)} kWh`;
      el.deltaShortfall.style.color = (deltas.critical_shortfall_kwh || 0) <= 0 ? '#10b981' : '#ef4444';

      el.deltaTasks.textContent = `${deltas.tasks_completed >= 0 ? '+' : ''}${deltas.tasks_completed || 0}`;
      el.deltaTasks.style.color = (deltas.tasks_completed || 0) >= 0 ? '#10b981' : '#ef4444';

      el.deltaReserve.textContent = `${deltas.battery_reserve_violation_steps || 0}`;
      el.deltaReserve.style.color = (deltas.battery_reserve_violation_steps || 0) <= 0 ? '#10b981' : '#ef4444';

      el.deltaFallbacks.textContent = `${deltas.fallback_activations || 0}`;

      // Populate Comparison Table
      const metricsList = [
        { key: 'critical_shortfall_kwh', name: 'Critical Power Shortfall (kWh)', unit: 'kWh', lowerBetter: true },
        { key: 'minimum_battery_kwh', name: 'Minimum Battery Stored (kWh)', unit: 'kWh', lowerBetter: false },
        { key: 'battery_reserve_violation_steps', name: 'Battery Reserve Violation Steps', unit: '', lowerBetter: true },
        { key: 'tasks_completed', name: 'Tasks Completed', unit: '', lowerBetter: false },
        { key: 'hard_deadline_misses', name: 'Hard Deadline Misses', unit: '', lowerBetter: true },
        { key: 'fallback_activations', name: 'Fallback Activations', unit: '', lowerBetter: true },
        { key: 'safety_violation_steps', name: 'Total Safety Violation Steps', unit: '', lowerBetter: true },
        { key: 'actions_executed', name: 'Actions Executed', unit: '', lowerBetter: false },
        { key: 'actions_rejected', name: 'Actions Rejected by Validator', unit: '', lowerBetter: true },
      ];

      el.tbodyComparison.innerHTML = '';
      metricsList.forEach((m) => {
        const valA = astragrid[m.key] !== undefined ? astragrid[m.key] : '—';
        const valB = baseline[m.key] !== undefined ? baseline[m.key] : '—';
        const d = deltas[m.key] !== undefined ? deltas[m.key] : 0;

        let interp = 'Tied';
        let deltaColor = 'text-muted';
        if (typeof d === 'number' && Math.abs(d) > 1e-6) {
          const astraWon = m.lowerBetter ? d < 0 : d > 0;
          interp = astraWon ? 'AstraGrid Superior' : 'Baseline Ahead';
          deltaColor = astraWon ? 'text-green' : 'text-alarm';
        }

        const tr = document.createElement('tr');
        tr.innerHTML = `
          <td class="font-bold">${m.name}</td>
          <td class="mono">${typeof valA === 'number' ? valA.toFixed(2) : valA}</td>
          <td class="mono">${typeof valB === 'number' ? valB.toFixed(2) : valB}</td>
          <td class="mono font-bold ${deltaColor}">${typeof d === 'number' ? (d > 0 ? '+' : '') + d.toFixed(2) : d}</td>
          <td><span class="badge ${interp.includes('Superior') ? 'badge-green' : interp.includes('Ahead') ? 'badge-red' : 'badge-info'}">${interp}</span></td>
        `;
        el.tbodyComparison.appendChild(tr);
      });
    } catch (err) {
      alert(`Comparison failed: ${err.message}`);
    } finally {
      el.btnRunComparison.disabled = false;
      el.btnRunComparison.innerHTML = `
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
        RUN FULL COMPARISON`;
    }
  }

  // --- Step-by-Step Replay Player ---
  async function loadReplayData() {
    if (!state.currentRunId) return;
    try {
      const data = await apiFetch(`/api/simulations/${state.currentRunId}/replay_data`);
      state.replayData = data;
      el.replayScrubber.max = Math.max(0, (data.total_steps || 1) - 1);
      state.replayStep = Math.min(state.replayStep, parseInt(el.replayScrubber.max, 10));
      el.replayScrubber.value = state.replayStep;
      renderReplayStep(state.replayStep);
    } catch (err) {
      console.error('Error loading replay data:', err);
    }
  }

  function renderReplayStep(stepIdx) {
    if (!state.replayData || !state.replayData.steps || state.replayData.steps.length === 0) {
      el.lblReplayStep.textContent = `STEP 00 / 00`;
      return;
    }

    const total = state.replayData.total_steps;
    const stepData = state.replayData.steps[stepIdx] || {};
    el.lblReplayStep.textContent = `STEP ${String(stepIdx).padStart(2, '0')} / ${String(total - 1).padStart(2, '0')}`;

    el.replayPanel1.textContent = JSON.stringify(stepData.observation || { note: 'No telemetry observation' }, null, 2);
    el.replayPanel2.textContent = JSON.stringify(
      { estimated_state: stepData.estimated_state, capability: stepData.capability },
      null,
      2
    );
    el.replayPanel3.textContent = JSON.stringify(stepData.control?.proposed_controller || { note: 'No proposed plan' }, null, 2);
    el.replayPanel4.textContent = JSON.stringify(
      {
        validation_accepted: stepData.control?.validation_accepted,
        reasons: stepData.control?.validation_reasons,
      },
      null,
      2
    );
    el.replayPanel5.textContent = JSON.stringify(
      {
        executed_controller: stepData.control?.executed_controller,
        fallback_attempted: stepData.control?.fallback_attempted,
        emergency_policy: stepData.control?.emergency_policy,
      },
      null,
      2
    );
    el.replayPanel6.textContent = JSON.stringify(stepData.audit || { note: 'No audit record' }, null, 2);
    el.replayPanel7.textContent = JSON.stringify(stepData.transition || { note: 'No physical transition' }, null, 2);
    el.replayPanel8.textContent = JSON.stringify(
      {
        events_at_step: stepData.events || [],
        transition_error: stepData.transition?.transition_error || null,
        deficits: stepData.transition?.resource_shortfalls || null,
      },
      null,
      2
    );
  }

  function jumpToNextEvent() {
    if (!state.replayData || !state.replayData.steps) return;
    const steps = state.replayData.steps;
    for (let i = state.replayStep + 1; i < steps.length; i++) {
      if ((steps[i].events && steps[i].events.length > 0) || steps[i].transition?.critical_shortfall_kw > 0) {
        state.replayStep = i;
        el.replayScrubber.value = i;
        renderReplayStep(i);
        return;
      }
    }
    // Loop back to start if not found ahead
    for (let i = 0; i < state.replayStep; i++) {
      if ((steps[i].events && steps[i].events.length > 0) || steps[i].transition?.critical_shortfall_kw > 0) {
        state.replayStep = i;
        el.replayScrubber.value = i;
        renderReplayStep(i);
        return;
      }
    }
  }

  // --- Tab Navigation Setup ---
  function setupTabs() {
    const tabButtons = document.querySelectorAll('.nav-tab');
    tabButtons.forEach((btn) => {
      btn.addEventListener('click', () => {
        const targetTab = btn.getAttribute('data-tab');
        tabButtons.forEach((b) => b.classList.remove('active'));
        btn.classList.add('active');

        document.querySelectorAll('.tab-pane').forEach((pane) => pane.classList.remove('active'));
        const activePane = document.getElementById(targetTab);
        if (activePane) activePane.classList.add('active');

        state.activeTab = targetTab;

        if (targetTab === 'tab-charts' && state.fullViewData) {
          renderCharts(state.fullViewData);
        } else if (targetTab === 'tab-replay') {
          loadReplayData();
        }
      });
    });
  }

  // --- Wire Event Listeners ---
  function wireEvents() {
    el.btnInit.addEventListener('click', initSimulation);
    el.btnStep.addEventListener('click', stepSimulation);
    el.btnStep10.addEventListener('click', step10Simulation);
    el.btnAutoPlay.addEventListener('click', toggleAutoPlay);
    el.btnRunAll.addEventListener('click', runAllSimulation);
    el.btnRunComparison.addEventListener('click', runMatchedComparison);

    // Replay controls
    el.replayScrubber.addEventListener('input', (e) => {
      state.replayStep = parseInt(e.target.value, 10);
      renderReplayStep(state.replayStep);
    });
    el.btnReplayPrev.addEventListener('click', () => {
      if (state.replayStep > 0) {
        state.replayStep--;
        el.replayScrubber.value = state.replayStep;
        renderReplayStep(state.replayStep);
      }
    });
    el.btnReplayNext.addEventListener('click', () => {
      if (state.replayData && state.replayStep < state.replayData.total_steps - 1) {
        state.replayStep++;
        el.replayScrubber.value = state.replayStep;
        renderReplayStep(state.replayStep);
      }
    });
    el.btnReplayEvent.addEventListener('click', jumpToNextEvent);

    // Window resize re-renders charts
    window.addEventListener('resize', () => {
      if (state.activeTab === 'tab-charts' && state.fullViewData) {
        renderCharts(state.fullViewData);
      }
    });
  }

  // --- Init on DOM Load ---
  document.addEventListener('DOMContentLoaded', () => {
    setupTabs();
    wireEvents();
    initSimulation();
  });
})();
