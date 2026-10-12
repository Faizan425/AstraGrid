"""Evidence-based diagnostic annotations for AstraGrid Resilience Lab.

Transparent, rule-based diagnostic engine that operates strictly on
recorded observations, estimates, and physical transitions post-run.
It does not use machine learning, never feeds back into the controller,
and does not leak hidden future fault schedules.
"""
from __future__ import annotations

from typing import Any

from astra_grid.config import ScenarioConfig
from astra_grid.models import Observation
from astra_grid.runner import SimulationRun

from .models import DiagnosticAnnotation


class DiagnosticRuleEngine:
    """Evaluates transparent diagnostic rules on a completed SimulationRun."""

    def __init__(self, config: ScenarioConfig | None = None) -> None:
        self.config = config

    def annotate(self, run: SimulationRun) -> list[DiagnosticAnnotation]:
        """Generate structured diagnostic annotations across all timesteps."""
        cfg = run.config
        annotations: list[DiagnosticAnnotation] = []
        observations = run.telemetry_history
        transitions = run.transitions
        estimates = run.estimates
        total_steps = len(transitions)

        for step in range(total_steps):
            obs: Observation | None = observations[step] if step < len(observations) else None
            trans = transitions[step] if step < len(transitions) else None
            est = estimates[step] if step < len(estimates) else None

            # Rule 1: Solar Underperformance Rule
            # Solar output below expected profile during nonzero illumination forecast
            if obs is not None:
                nominal_profile = cfg.power.solar_profile[step % len(cfg.power.solar_profile)]
                expected_kw = cfg.power.solar_rated_kw * nominal_profile
                if expected_kw > 0.05 and obs.solar_kw < 0.8 * expected_kw:
                    annotations.append(DiagnosticAnnotation(
                        step=step,
                        rule_id="SOLAR_UNDERPERFORMANCE",
                        severity="WARNING",
                        explanation=(
                            f"Measured solar generation ({obs.solar_kw:.1f} kW) is significantly below "
                            f"the expected rated illumination forecast ({expected_kw:.1f} kW); "
                            "consistent with solar array degradation, partial shading, or sensor metering deficit."
                        ),
                        category="OBSERVATION",
                        observed_evidence={
                            "measured_solar_kw": obs.solar_kw,
                            "expected_solar_kw": expected_kw,
                            "deficit_kw": expected_kw - obs.solar_kw,
                        },
                        supporting_references=["telemetry.solar_kw", "config.power.solar_profile"],
                    ))

            # Rule 2: SOC Sensor Disagreement Rule
            # Disagreement between independent SOC sensor readings
            if obs is not None and len(obs.soc_sensors) >= 2:
                readings = list(obs.soc_sensors.values())
                soc_range = max(readings) - min(readings)
                if soc_range > 0.05:
                    annotations.append(DiagnosticAnnotation(
                        step=step,
                        rule_id="SOC_SENSOR_DISAGREEMENT",
                        severity="WARNING",
                        explanation=(
                            f"Independent battery SOC sensors exhibit a divergence of {soc_range:.3f} "
                            f"(readings: {obs.soc_sensors}); consistent with sensor drift, bias, or byzantine telemetry anomaly."
                        ),
                        category="OBSERVATION",
                        observed_evidence={
                            "soc_sensors": dict(obs.soc_sensors),
                            "max_difference": soc_range,
                        },
                        supporting_references=["telemetry.soc_sensors"],
                    ))

            # Rule 3: Equipment Reported Unavailable / Degraded
            if obs is not None and obs.equipment_status:
                for eq_key, val in obs.equipment_status.items():
                    if val is False or (isinstance(val, (int, float)) and not isinstance(val, bool) and float(val) < 0.99):
                        annotations.append(DiagnosticAnnotation(
                            step=step,
                            rule_id="EQUIPMENT_STATUS_DEGRADED",
                            severity="WARNING" if val is not False else "CRITICAL",
                            explanation=(
                                f"Equipment status channel '{eq_key}' reported abnormal reading ({val}); "
                                "consistent with subsystem mechanical/electrical failure or capability reduction."
                            ),
                            category="OBSERVATION",
                            observed_evidence={
                                "channel": eq_key,
                                "reported_status": val,
                            },
                            supporting_references=[f"telemetry.equipment_status.{eq_key}"],
                        ))

            # Rule 4: Resource Inventory Approaching Reserve
            if trans is not None:
                after = trans.after
                safe_b_kwh = cfg.battery.safe_reserve_kwh
                if safe_b_kwh < after.battery_kwh <= safe_b_kwh + 0.1 * cfg.battery.capacity_kwh:
                    annotations.append(DiagnosticAnnotation(
                        step=step,
                        rule_id="BATTERY_RESERVE_PROXIMITY",
                        severity="WARNING",
                        explanation=(
                            f"Physical battery storage ({after.battery_kwh:.1f} kWh) is within 10% of "
                            f"the configured safe reserve margin ({safe_b_kwh:.1f} kWh); "
                            "operating margin for discretionary workloads is depleted."
                        ),
                        category="INFERENCE",
                        observed_evidence={
                            "battery_kwh": after.battery_kwh,
                            "safe_reserve_kwh": safe_b_kwh,
                            "capacity_kwh": cfg.battery.capacity_kwh,
                        },
                        supporting_references=["state.battery_kwh", "config.battery.safe_reserve_kwh"],
                    ))

                # Oxygen reserve proximity
                o2_reserve = cfg.life_support.oxygen_reserve_kg
                if o2_reserve < after.oxygen_kg <= o2_reserve + 2.0:
                    annotations.append(DiagnosticAnnotation(
                        step=step,
                        rule_id="OXYGEN_RESERVE_PROXIMITY",
                        severity="WARNING",
                        explanation=(
                            f"Physical oxygen inventory ({after.oxygen_kg:.2f} kg) is approaching "
                            f"configured safety reserve ({o2_reserve:.2f} kg); "
                            "indicates net negative metabolic atmosphere balance."
                        ),
                        category="INFERENCE",
                        observed_evidence={
                            "oxygen_kg": after.oxygen_kg,
                            "oxygen_reserve_kg": o2_reserve,
                        },
                        supporting_references=["state.oxygen_kg", "config.life_support.oxygen_reserve_kg"],
                    ))

            # Rule 5: Critical Load Shortfall Under Deficit
            if trans is not None and trans.critical_shortfall_kw > 1e-9:
                annotations.append(DiagnosticAnnotation(
                    step=step,
                    rule_id="CRITICAL_POWER_DEFICIT",
                    severity="CRITICAL",
                    explanation=(
                        f"Physical electrical shortfall of {trans.critical_shortfall_kw:.2f} kW observed on "
                        "critical habitat life-support and control systems; "
                        "power supply is physically insufficient to meet life-support requirements."
                    ),
                    category="OBSERVATION",
                    observed_evidence={
                        "critical_shortfall_kw": trans.critical_shortfall_kw,
                        "demand_kw": trans.demand_kw,
                        "supplied_kw": trans.supplied_kw,
                    },
                    supporting_references=["transition.critical_shortfall_kw"],
                ))

            # Rule 6: Telemetry Quality Degradation
            if est is not None and est.telemetry_quality in {"degraded", "suspect"}:
                annotations.append(DiagnosticAnnotation(
                    step=step,
                    rule_id="TELEMETRY_QUALITY_DEGRADATION",
                    severity="WARNING",
                    explanation=(
                        f"State estimator reports telemetry quality is '{est.telemetry_quality}' "
                        f"due to suspect sensor flags ({est.suspect_sensors}); "
                        "state estimates have higher uncertainty."
                    ),
                    category="INFERENCE",
                    observed_evidence={
                        "telemetry_quality": est.telemetry_quality,
                        "suspect_sensors": list(est.suspect_sensors),
                    },
                    supporting_references=["estimate.telemetry_quality", "estimate.suspect_sensors"],
                ))

        return annotations
