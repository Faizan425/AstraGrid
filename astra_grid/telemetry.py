"""Observation generation and robust estimation with no truth-state API to controls."""
from __future__ import annotations

from dataclasses import dataclass
from statistics import median

from .config import ScenarioConfig
from .faults import FaultEngine
from .models import EstimatedState, Observation, PhysicalState


class TelemetryGenerator:
    """Converts private simulator state into potentially faulty sensor readings."""
    def __init__(self, config: ScenarioConfig, faults: FaultEngine) -> None:
        self.config, self.faults = config, faults

    def observe(self, state: PhysicalState, solar_kw: float, load_kw: float) -> Observation:
        soc = state.battery_kwh / self.config.battery.capacity_kwh
        sensors = {name: self._apply(name, soc, state.step) for name in ("soc_sensor_1", "soc_sensor_2", "soc_sensor_3")}
        return Observation(
            step=state.step, solar_kw=solar_kw, load_kw=load_kw,
            temperature_c=self._apply("temperature", state.temperature_c, state.step),
            oxygen_kg=self._apply("oxygen", state.oxygen_kg, state.step),
            co2_kg=self._apply("co2", state.co2_kg, state.step),
            water_kg=self._apply("water", state.water_kg, state.step), soc_sensors=sensors,
        )

    def _apply(self, channel: str, value: float, step: int) -> float | None:
        fault = self.faults.telemetry_fault(step, channel)
        if fault is None: return value
        kind = str(fault.parameters.get("kind", "value"))
        if kind == "missing": return None
        if kind == "bias": return value + float(fault.parameters.get("bias", 0))
        return float(fault.parameters.get("value", value))


@dataclass
class EstimateResult:
    state: EstimatedState
    evidence: list[str]


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
