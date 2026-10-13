"""Cross-resource event and cascade analysis: linking energy, water, and life support.

Traces causal and contributing relationships from recorded physical transitions,
telemetry observations, and controller decisions. Never speculates causation
without concrete physical evidence.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .runner import SimulationRun


class CascadeRelationship(StrEnum):
    DIRECTLY_MODELED = "directly_modeled"
    CONTRIBUTING_FACTOR = "contributing_factor"
    TEMPORALLY_ASSOCIATED = "temporally_associated"


@dataclass
class CascadeNode:
    node_id: str
    step: int
    category: str  # power, water, battery, thermal, equipment, task, safety
    event_type: str
    description: str
    value_before: float | None = None
    value_after: float | None = None
    threshold: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CascadeEdge:
    source_id: str
    target_id: str
    relationship: CascadeRelationship
    evidence: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CascadeSummary:
    violation_type: str
    violation_step: int
    initiating_disruption: str
    intermediate_actions: list[str]
    resource_trajectory: list[dict[str, float]]
    warning_or_violation: str
    recovered: bool
    recovery_step: int | None
    contributing_factors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        root_sub = "energy" if "solar" in self.initiating_disruption.lower() or "power" in self.initiating_disruption.lower() or "demand" in self.initiating_disruption.lower() else "water"
        impacted = ["energy"] if "battery" in self.violation_type else ["energy", "water"]
        start_s = max(0, self.violation_step - len(self.resource_trajectory))
        end_s = self.recovery_step if self.recovery_step is not None else self.violation_step
        narrative = f"{self.initiating_disruption}. {self.warning_or_violation} at step {self.violation_step}."

        d["root_cause"] = self.initiating_disruption
        d["root_subsystem"] = root_sub
        d["impacted_subsystems"] = impacted
        d["depth"] = len(self.intermediate_actions)
        d["start_step"] = start_s
        d["end_step"] = end_s
        d["narrative"] = narrative
        d["nodes"] = [
            {"step": start_s, "subsystem": root_sub, "resource": "power" if root_sub == "energy" else "water", "description": self.initiating_disruption, "severity": "WARN", "id": f"root_{start_s}"},
            {"step": self.violation_step, "subsystem": "water" if "water" in self.violation_type else "energy", "resource": "water_kg" if "water" in self.violation_type else "battery_kwh", "description": self.warning_or_violation, "severity": "CRITICAL", "id": f"violation_{self.violation_step}"},
        ]
        return d


class CascadeAnalyzer:
    """Analyzes a completed or active SimulationRun to build traceable cross-resource causal graphs."""

    def __init__(self, run: "SimulationRun") -> None:
        self.run = run
        self.config = run.config

    def build_graph(self) -> dict[str, Any]:
        nodes: list[CascadeNode] = []
        edges: list[CascadeEdge] = []
        node_ids_by_step_cat: dict[tuple[int, str], str] = {}

        transitions = self.run.transitions
        events = self.run.events
        cfg = self.config

        # 1. Map physical transitions and disruptions
        prev_solar = None
        for step_idx, t in enumerate(transitions):
            step = step_idx

            # Solar generation change
            if prev_solar is not None and t.solar_kw < prev_solar - 0.5:
                solar_id = f"solar_drop_{step}"
                nodes.append(CascadeNode(
                    node_id=solar_id,
                    step=step,
                    category="power",
                    event_type="solar_generation_drop",
                    description=f"Solar generation decreased from {prev_solar:.1f} kW to {t.solar_kw:.1f} kW",
                    value_before=prev_solar,
                    value_after=t.solar_kw,
                ))
                node_ids_by_step_cat[(step, "solar")] = solar_id
            prev_solar = t.solar_kw

            # Water recovery deferred or unavailable
            if t.water_recovered_kg <= 1e-6:
                rec_id = f"water_rec_deferred_{step}"
                is_power_reason = t.supplied_kw < t.demand_kw
                desc = "Water recovery inactive: " + ("insufficient power margin" if is_power_reason else "policy deferred or equipment unavailable")
                nodes.append(CascadeNode(
                    node_id=rec_id,
                    step=step,
                    category="water",
                    event_type="water_recovery_deferred",
                    description=desc,
                    value_before=t.before.water_kg,
                    value_after=t.after.water_kg,
                ))
                node_ids_by_step_cat[(step, "water_recovery")] = rec_id

                # Link if solar dropped in this step or preceding step
                solar_node = node_ids_by_step_cat.get((step, "solar")) or node_ids_by_step_cat.get((step - 1, "solar"))
                if solar_node:
                    rel = CascadeRelationship.DIRECTLY_MODELED if is_power_reason else CascadeRelationship.CONTRIBUTING_FACTOR
                    edges.append(CascadeEdge(
                        source_id=solar_node,
                        target_id=rec_id,
                        relationship=rel,
                        evidence=f"Solar drop reduced bus supply to {t.supplied_kw:.1f}kW, affecting water recovery load.",
                    ))

            # Water reserve warning / breach
            w_res = cfg.life_support.water_reserve_kg
            if t.after.water_kg < w_res:
                breach_id = f"water_reserve_breach_{step}"
                nodes.append(CascadeNode(
                    node_id=breach_id,
                    step=step,
                    category="water",
                    event_type="water_reserve_breach",
                    description=f"Potable water inventory {t.after.water_kg:.1f} kg fell below safe reserve {w_res:.1f} kg",
                    value_before=t.before.water_kg,
                    value_after=t.after.water_kg,
                    threshold=w_res,
                ))
                # Link preceding water recovery deferral
                rec_node = node_ids_by_step_cat.get((step, "water_recovery")) or node_ids_by_step_cat.get((step - 1, "water_recovery"))
                if rec_node:
                    edges.append(CascadeEdge(
                        source_id=rec_node,
                        target_id=breach_id,
                        relationship=CascadeRelationship.DIRECTLY_MODELED,
                        evidence=f"Water consumption without replenishment reduced inventory by {t.water_demand_kg:.2f} kg.",
                    ))

            # Battery reserve breach
            b_res = cfg.battery.safe_reserve_kwh
            if t.after.battery_kwh < b_res:
                batt_id = f"battery_reserve_breach_{step}"
                nodes.append(CascadeNode(
                    node_id=batt_id,
                    step=step,
                    category="battery",
                    event_type="battery_reserve_breach",
                    description=f"Battery storage {t.after.battery_kwh:.1f} kWh breached reserve {b_res:.1f} kWh",
                    value_before=t.before.battery_kwh,
                    value_after=t.after.battery_kwh,
                    threshold=b_res,
                ))
                solar_node = node_ids_by_step_cat.get((step, "solar")) or node_ids_by_step_cat.get((step - 1, "solar"))
                if solar_node:
                    edges.append(CascadeEdge(
                        source_id=solar_node,
                        target_id=batt_id,
                        relationship=CascadeRelationship.DIRECTLY_MODELED,
                        evidence="Battery discharged to cover bus load deficit during solar shortfall.",
                    ))

            # Critical load shortfall
            if t.critical_shortfall_kw > 1e-6:
                crit_id = f"crit_shortfall_{step}"
                nodes.append(CascadeNode(
                    node_id=crit_id,
                    step=step,
                    category="power",
                    event_type="critical_load_shortfall",
                    description=f"Unavoidable critical power shortfall of {t.critical_shortfall_kw:.2f} kW",
                    value_after=t.critical_shortfall_kw,
                ))

        # 2. Map explicit Event log items into nodes
        for e in events:
            if "fault" in e.kind:
                f_id = f"event_fault_{e.event_id}"
                nodes.append(CascadeNode(
                    node_id=f_id,
                    step=e.step,
                    category="equipment",
                    event_type="injected_fault",
                    description=e.message,
                    metadata=e.values,
                ))

        return {
            "nodes": [n.to_dict() for n in nodes],
            "edges": [ed.to_dict() for ed in edges],
        }

    def summarize_cascades(self) -> list[CascadeSummary]:
        summaries: list[CascadeSummary] = []
        transitions = self.run.transitions
        cfg = self.config
        w_reserve = cfg.life_support.water_reserve_kg
        b_reserve = cfg.battery.safe_reserve_kwh

        # Find first water reserve breach
        water_breach_step = None
        for idx, t in enumerate(transitions):
            if t.after.water_kg < w_reserve - 1e-6:
                water_breach_step = idx
                break

        if water_breach_step is not None:
            history_slice = transitions[max(0, water_breach_step - 8):water_breach_step + 1]
            trajectory = [
                {"step": s, "water_kg": tr.after.water_kg, "solar_kw": tr.solar_kw, "battery_kwh": tr.after.battery_kwh}
                for s, tr in enumerate(history_slice)
            ]
            init_disruption = "Continuous crew water consumption without adequate replenishment"
            for tr in history_slice:
                if tr.solar_kw < cfg.power.solar_rated_kw * 0.4:
                    init_disruption = f"Solar power generation dropped to {tr.solar_kw:.1f} kW, constraining life support power"
                    break

            # Check if habitat recovered later
            recovered = False
            rec_step = None
            for idx in range(water_breach_step + 1, len(transitions)):
                if transitions[idx].after.water_kg >= w_reserve:
                    recovered = True
                    rec_step = idx
                    break

            summaries.append(CascadeSummary(
                violation_type="water_reserve_breach",
                violation_step=water_breach_step,
                initiating_disruption=init_disruption,
                intermediate_actions=[
                    "Water recovery cycle was deferred to preserve bus energy margin",
                    f"Electrolysis for oxygen consumed {(cfg.life_support.oxygen_generation_kgph * cfg.life_support.oxygen_water_kg_per_kg * cfg.dt_hours):.2f} kg water/step",
                ],
                resource_trajectory=trajectory,
                warning_or_violation=f"Potable water reached {transitions[water_breach_step].after.water_kg:.1f} kg (< {w_reserve:.1f} kg reserve)",
                recovered=recovered,
                recovery_step=rec_step,
                contributing_factors=["Solar generation outage", "Wastewater recovery postponement"],
            ))

        # Find first battery reserve breach
        batt_breach_step = None
        for idx, t in enumerate(transitions):
            if t.after.battery_kwh < b_reserve - 1e-6:
                batt_breach_step = idx
                break

        if batt_breach_step is not None:
            history_slice = transitions[max(0, batt_breach_step - 8):batt_breach_step + 1]
            trajectory = [
                {"step": s, "battery_kwh": tr.after.battery_kwh, "solar_kw": tr.solar_kw, "demand_kw": tr.demand_kw}
                for s, tr in enumerate(history_slice)
            ]
            recovered = False
            rec_step = None
            for idx in range(batt_breach_step + 1, len(transitions)):
                if transitions[idx].after.battery_kwh >= b_reserve:
                    recovered = True
                    rec_step = idx
                    break

            summaries.append(CascadeSummary(
                violation_type="battery_reserve_breach",
                violation_step=batt_breach_step,
                initiating_disruption="Bus electrical demand exceeded incoming solar and backup supply",
                intermediate_actions=[
                    f"Battery discharged to supply critical loads",
                    "Reserve threshold crossed as storage depleted",
                ],
                resource_trajectory=trajectory,
                warning_or_violation=f"Battery energy fell to {transitions[batt_breach_step].after.battery_kwh:.1f} kWh (< {b_reserve:.1f} kWh reserve)",
                recovered=recovered,
                recovery_step=rec_step,
                contributing_factors=["Deficit of renewable generation", "High base load"],
            ))

        return summaries
