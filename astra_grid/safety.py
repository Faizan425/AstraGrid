"""Operating modes and conservative survival-horizon estimation."""
from __future__ import annotations

from dataclasses import asdict, dataclass

from .config import ScenarioConfig
from .faults import Capability
from .models import EstimatedState, Event, Mode


@dataclass
class SurvivalHorizon:
    hours: float
    limiting_factor: str
    factors_hours: dict[str, float]
    assumptions: list[str]
    def to_dict(self) -> dict[str, object]: return asdict(self)


class SurvivalEstimator:
    """A conservative linear projection, explicitly not a flight survival guarantee."""
    def __init__(self, config: ScenarioConfig) -> None: self.config = config

    def estimate(self, state: EstimatedState, capability: Capability, current_step: int) -> SurvivalHorizon:
        cfg, life, b, p, t = self.config, self.config.life_support, self.config.battery, self.config.power, self.config.thermal
        if state.oxygen_kg is None or state.co2_kg is None or state.water_kg is None or state.temperature_c is None:
            return SurvivalHorizon(0.0, "telemetry_unavailable", {"telemetry": 0.0}, ["Critical resource sensor is unavailable."])
        if state.oxygen_kg <= life.oxygen_reserve_kg or state.co2_kg >= life.co2_limit_kg or state.water_kg <= life.water_reserve_kg or state.temperature_c < t.min_temperature_c or state.temperature_c > t.max_temperature_c:
            return SurvivalHorizon(0.0, "boundary_breached", {"boundary": 0.0}, ["At least one configured critical boundary is already breached."])
        # Critical-only demand includes life support and control; forecast solar uses the next profile cycle.
        critical_kw = p.fixed_essential_kw + p.control_computer_kw + life.oxygen_generator_kw + life.co2_removal_kw + life.air_circulation_kw + life.water_recovery_kw
        samples = [p.solar_rated_kw * p.solar_profile[(current_step + i) % len(p.solar_profile)] * capability.solar_factor for i in range(min(cfg.controller.horizon_steps, len(p.solar_profile)))]
        avg_generation = sum(samples) / len(samples) + (p.backup_max_kw if p.backup_available and capability.backup_available else 0.0)
        net_kw = max(0.01, critical_kw - avg_generation * p.pdu_efficiency * capability.pdu_factor)
        energy_h = max(0.0, state.battery_kwh - b.safe_reserve_kwh) / net_kw
        oxygen_net = max(0.001, life.oxygen_demand_kgph - life.oxygen_generation_kgph * capability.oxygen_factor)
        oxygen_h = (state.oxygen_kg - life.oxygen_reserve_kg) / oxygen_net
        co2_net = max(0.001, life.co2_generation_kgph - life.co2_removal_kgph * capability.co2_factor)
        co2_h = (life.co2_limit_kg - state.co2_kg) / co2_net
        recovered = min(life.water_recovery_capacity_kgph * capability.water_factor, life.water_demand_kgph * life.wastewater_return_fraction) * life.water_recovery_efficiency
        water_net = max(0.001, life.water_demand_kgph + life.oxygen_generation_kgph * capability.oxygen_factor * life.oxygen_water_kg_per_kg - recovered)
        water_h = (state.water_kg - life.water_reserve_kg) / water_net
        equilibrium = t.external_temperature_c + ((critical_kw * t.recoverable_heat_fraction + t.heater_kw * t.heater_efficiency) / max(0.001, t.envelope_ua_kw_per_c))
        thermal_h = 1_000_000.0 if t.min_temperature_c <= equilibrium <= t.max_temperature_c else max(0.0, (state.temperature_c - t.min_temperature_c) * t.thermal_capacity_kwh_per_c / max(0.01, t.envelope_ua_kw_per_c * (state.temperature_c - t.external_temperature_c)))
        factors = {"energy": energy_h, "oxygen": oxygen_h, "co2": co2_h, "water": water_h, "thermal": thermal_h}
        limiting = min(factors, key=factors.get)
        return SurvivalHorizon(max(0.0, factors[limiting]), limiting, factors, ["Linear projection assumes critical services are requested continuously.", "Uses forecast-average solar plus available dispatchable backup.", "Thermal estimate uses the configured single-zone model."])


class FaultManager:
    """Deterministic operating modes with two healthy steps required to recover."""
    def __init__(self) -> None:
        self.mode = Mode.NORMAL
        self._healthy_steps = 0

    def update(self, state: EstimatedState, horizon: SurvivalHorizon, air_available: bool, step: int) -> Event | None:
        prior = self.mode
        severe = horizon.hours <= 2 or not air_available or state.telemetry_quality == "low"
        warning = horizon.hours <= 8 or state.telemetry_quality != "high"
        if self.mode == Mode.CONTROLLED_SHUTDOWN:
            return None
        if severe:
            self.mode = Mode.EMERGENCY; self._healthy_steps = 0
        elif warning:
            self.mode = Mode.DEGRADED; self._healthy_steps = 0
        elif self.mode != Mode.NORMAL:
            self._healthy_steps += 1
            if self._healthy_steps >= 2: self.mode = Mode.NORMAL
        if self.mode != prior:
            cause = "TELEMETRY_DEGRADED" if state.telemetry_quality != "high" else ("SURVIVAL_MARGIN" if horizon.hours <= 8 else "HEALTHY_HYSTERESIS")
            return Event(f"mode-{step}", step, "mode", f"enter {self.mode}", [cause], f"Operating mode changed from `{prior}` to `{self.mode}`.", {"horizon_hours": horizon.hours, "quality": state.telemetry_quality}, self.mode)
        return None

    def shutdown(self, step: int, reason: str) -> Event:
        self.mode = Mode.CONTROLLED_SHUTDOWN
        return Event(f"shutdown-{step}", step, "mode", "controlled shutdown", [reason], "No feasible policy can preserve configured critical services; controlled shutdown declared.", mode=self.mode)
