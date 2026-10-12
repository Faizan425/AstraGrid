"""Observation generation and robust estimation with no truth-state API to controls."""
from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from math import floor, isfinite

from .config import ScenarioConfig
from .faults import Capability, FaultEngine
from .models import EstimatedState, Observation, PhysicalState


_CAPABILITY_QUANTIZATION_STEP = 0.25


def _quantize_factor(value: float, step: float = _CAPABILITY_QUANTIZATION_STEP) -> float:
    """Floor-quantise a capability factor to prevent exact internal state leakage.

    Equipment health monitors report coarsened categorical levels (1.0, 0.75,
    0.5, 0.25, 0.0) rather than exact internal degradation coefficients.
    Floor-rounding is deliberately conservative: the controller never
    overestimates equipment health from telemetry alone.
    """
    clamped = max(0.0, min(1.0, value))
    return round(floor(clamped / step + 1e-9) * step, 10)


class TelemetryGenerator:
    """Converts private simulator state into potentially faulty sensor readings."""
    def __init__(self, config: ScenarioConfig, faults: FaultEngine) -> None:
        self.config, self.faults = config, faults

    def observe(self, state: PhysicalState, solar_kw: float, load_kw: float, true_capability: Capability | None = None) -> Observation:
        """Generate readings from private state and telemetry-visible equipment monitors.

        ``true_capability`` is consumed only to generate status telemetry.  It
        is not returned to the controller and can be made missing or false by a
        normal telemetry fault on an ``equipment_*`` channel.  Float capability
        factors are conservatively floor-quantised before entering the
        observation so that exact internal degradation coefficients never leak
        into controller-visible telemetry.
        """
        soc = state.battery_kwh / self.config.battery.capacity_kwh
        sensors = {name: self._apply(name, soc, state.step) for name in ("soc_sensor_1", "soc_sensor_2", "soc_sensor_3")}
        capability = true_capability or Capability()
        # Equipment health monitors report coarsened categorical indicators,
        # not the exact internal degradation factors.  Boolean status passes
        # through unchanged; float factors are conservatively floor-quantised.
        status: dict[str, float | bool | None] = {}
        for name, value in capability.__dict__.items():
            key = f"equipment_{name}"
            reported: float | bool = value if isinstance(value, bool) else _quantize_factor(float(value))
            status[key] = self._apply_status(key, reported, state.step)
        return Observation(
            step=state.step, solar_kw=solar_kw, load_kw=load_kw,
            temperature_c=self._apply("temperature", state.temperature_c, state.step),
            oxygen_kg=self._apply("oxygen", state.oxygen_kg, state.step),
            co2_kg=self._apply("co2", state.co2_kg, state.step),
            water_kg=self._apply("water", state.water_kg, state.step), soc_sensors=sensors,
            equipment_status=status,
        )

    def _apply(self, channel: str, value: float, step: int) -> float | None:
        fault = self.faults.telemetry_fault(step, channel)
        if fault is None: return value
        kind = str(fault.parameters.get("kind", "value"))
        if kind == "missing": return None
        if kind == "bias": return value + float(fault.parameters.get("bias", 0))
        return float(fault.parameters.get("value", value))

    def _apply_status(self, channel: str, value: float | bool, step: int) -> float | bool | None:
        fault = self.faults.telemetry_fault(step, channel)
        if fault is None:
            return value
        kind = str(fault.parameters.get("kind", "value"))
        if kind == "missing":
            return None
        if kind == "bias" and isinstance(value, float):
            return value + float(fault.parameters.get("bias", 0))
        return fault.parameters.get("value", value)


@dataclass
class EstimateResult:
    state: EstimatedState
    evidence: list[str]


@dataclass
class CapabilityEstimate:
    """Controller-visible capability estimate derived only from status telemetry."""
    capability: Capability
    evidence: list[str]


class CapabilityEstimator:
    """Conservative translator from equipment telemetry to controller inputs.

    Missing boolean health is treated as unavailable; missing or malformed
    factors are treated as zero available capability. This prevents the
    controller from assuming a hidden component is healthy.
    """
    _BOOL_FIELDS = {"backup_available", "pdu_available", "air_available", "heater_available", "computer_available"}
    _FACTOR_FIELDS = {"solar_factor", "battery_capacity_factor", "battery_discharge_factor", "pdu_factor", "oxygen_factor", "co2_factor", "water_factor"}

    def update(self, observation: Observation) -> CapabilityEstimate:
        values: dict[str, object] = {}
        evidence: list[str] = []
        for field, default in Capability().__dict__.items():
            raw = observation.equipment_status.get(f"equipment_{field}")
            if field in self._BOOL_FIELDS:
                if isinstance(raw, bool):
                    values[field] = raw
                else:
                    values[field] = False
                    evidence.append(f"CAPABILITY_STATUS_MISSING_{field.upper()}")
            elif field in self._FACTOR_FIELDS:
                if isinstance(raw, (int, float)) and not isinstance(raw, bool) and isfinite(float(raw)) and 0 <= float(raw) <= 1:
                    values[field] = float(raw)
                else:
                    values[field] = 0.0
                    evidence.append(f"CAPABILITY_STATUS_MISSING_{field.upper()}")
            else:  # defensive compatibility if Capability gains a field later
                values[field] = default
        return CapabilityEstimate(Capability(**values), evidence)  # type: ignore[arg-type]


class StateEstimator:
    """Uses only observations and earlier estimates; no simulator state is accepted."""
    def __init__(self, config: ScenarioConfig) -> None:
        self.config = config
        self.previous: EstimatedState | None = None

    def update(self, observation: Observation) -> EstimateResult:
        values = {name: value for name, value in observation.soc_sensors.items() if value is not None and 0 <= value <= 1}
        suspects: list[str] = []
        evidence: list[str] = []
        if values:
            centre = float(median(values.values()))
            # Three equal-quality sources use a robust median. 0.12 SOC is intentionally
            # conservative for this prototype and catches a plausible-looking 75% lie.
            suspects = [name for name, value in values.items() if abs(value - centre) > 0.12]
            if suspects: evidence.append("SOC_SENSOR_DISAGREEMENT")
            quality = "low" if len(values) < 2 or len(suspects) > 1 else ("medium" if suspects else "high")
        else:
            centre = self.previous.soc if self.previous else 0.0
            quality = "low"
            evidence.append("SOC_MISSING")
        if self.previous and abs(centre - self.previous.soc) > 0.35:
            quality = "low"; evidence.append("SOC_RATE_IMPLAUSIBLE")
        estimated = EstimatedState(
            step=observation.step, battery_kwh=centre * self.config.battery.capacity_kwh, soc=centre,
            oxygen_kg=observation.oxygen_kg, co2_kg=observation.co2_kg, water_kg=observation.water_kg,
            temperature_c=observation.temperature_c, telemetry_quality=quality, suspect_sensors=suspects,
        )
        self.previous = estimated
        return EstimateResult(estimated, evidence)
