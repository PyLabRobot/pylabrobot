import dataclasses
from typing import Optional
import warnings

from pylabrobot.liquid_handling.parameter_sets.parameter_set import ParameterSet
from pylabrobot.liquid_handling.backends.hamilton.STAR import STAR
from pylabrobot.liquid_handling.liquid_classes.hamilton.base import HamiltonLiquidClass


@dataclasses.dataclass
class STARParameterSet(ParameterSet):
  """ A collection of parameters for a liquid transfer on a Hamilton STAR.

  A rough adoption of HamiltonLiquidClass.
  - This includes blow_out and jet configuration
  - Parameters are lists to allow channel-specific values.
  - `blow_out_air_volumes` is shared between aspiration and dispense.
  """

  blow_out_air_volumes: Optional[list[float]]
  transport_air_volumes: Optional[list[float]]

  aspiration_blow_out: Optional[list[bool]]
  aspiration_jet: Optional[list[bool]]
  aspiration_flow_rates: Optional[list[float]]
  aspiration_mix_flow_rates: Optional[list[float]]
  aspiration_swap_speeds: Optional[list[float]]
  aspiration_settling_times: Optional[list[float]]
  aspiration_clot_retract_heights: Optional[list[float]]
  aspiration_lld_modes: Optional[list[STAR.LLDMode]]

  dispense_blow_out: Optional[list[bool]]
  dispense_jet: Optional[list[bool]]
  dispense_flow_rates: Optional[list[float]]
  dispense_mix_speeds: Optional[list[float]]
  dispense_swap_speeds: Optional[list[float]]
  dispense_settling_times: Optional[list[float]]
  dispense_stop_back_volumes: Optional[list[float]]
  dispense_lld_modes: Optional[list[STAR.LLDMode]]

  def make_asp_kwargs(self) -> dict[str, float]:
    return {
      "jet": self.aspiration_jet,
      "blow_out": self.aspiration_blow_out,
      "flow_rates": self.aspiration_flow_rates,
      "mix_speed": self.aspiration_mix_flow_rates,
      "transport_air_volume": self.transport_air_volumes,
      "swap_speed": self.aspiration_swap_speeds,
      "settling_time": self.aspiration_settling_times,
      "clot_detection_height": self.aspiration_clot_retract_heights,
      "blow_out_air_volume": self.blow_out_air_volumes,
      "lld_mode": self.aspiration_lld_modes,
    }

  def make_disp_kwargs(self) -> dict[str, float]:
    return {
      "jet": self.dispense_jet,
      "blow_out": self.dispense_blow_out,
      "flow_rates": self.dispense_flow_rates,
      "mix_speed": self.dispense_mix_speeds,
      "transport_air_volume": self.transport_air_volumes,
      "swap_speed": self.dispense_swap_speeds,
      "settling_time": self.dispense_settling_times,
      "stop_back_volume": self.dispense_stop_back_volumes,
      "blow_out_air_volume": self.blow_out_air_volumes,
      "lld_mode": self.dispense_lld_modes,
    }

  @classmethod
  def from_hamilton_liquid_class(
    cls,
    hlc: HamiltonLiquidClass,
    jet: Optional[list[bool]] = None,
    blow_out: Optional[list[bool]] = None,
    aspiration_lld_modes: Optional[list[STAR.LLDMode]] = None,
    dispense_lld_modes: Optional[list[STAR.LLDMode]] = None,
    num_channels: int = 8,
  ):
    warnings.warn("This method is deprecated. Hamilton liquid classes will be removed soon.",
                  DeprecationWarning)

    if jet is not None and len(jet) != num_channels:
      raise ValueError(f"jet must have length {num_channels}")
    if blow_out is not None and len(blow_out) != num_channels:
      raise ValueError(f"blow_out must have length {num_channels}")
    if aspiration_lld_modes is not None and len(aspiration_lld_modes) != num_channels:
      raise ValueError(f"aspiration_lld_modes must have length {num_channels}")
    if dispense_lld_modes is not None and len(dispense_lld_modes) != num_channels:
      raise ValueError(f"dispense_lld_modes must have length {num_channels}")
    if not hlc.aspiration_air_transport_volume == hlc.dispense_air_transport_volume:
      raise ValueError("Different transport air volumes not supported.")
    if not hlc.aspiration_blow_out_volume == hlc.dispense_blow_out_volume:
      raise ValueError("Different blow out volumes not supported.")

    return cls(
      blow_out_air_volumes=[hlc.aspiration_blow_out_volume] * num_channels,
      transport_air_volumes=[hlc.aspiration_air_transport_volume] * num_channels,
      aspiration_blow_out=[blow_out] * num_channels,
      aspiration_jet=[jet] * num_channels,
      aspiration_flow_rates=[hlc.aspiration_flow_rate] * num_channels,
      aspiration_mix_flow_rates=[hlc.aspiration_mix_flow_rate] * num_channels,
      aspiration_swap_speeds=[hlc.aspiration_swap_speed] * num_channels,
      aspiration_settling_times=[hlc.aspiration_settling_time] * num_channels,
      aspiration_clot_retract_heights=[hlc.aspiration_clot_retract_height] * num_channels,
      aspiration_lld_modes=aspiration_lld_modes,
      dispense_blow_out=[blow_out] * num_channels,
      dispense_jet=[jet] * num_channels,
      dispense_flow_rates=[hlc.dispense_flow_rate] * num_channels,
      dispense_mix_speeds=[hlc.dispense_mode] * num_channels,
      dispense_swap_speeds=[hlc.dispense_swap_speed] * num_channels,
      dispense_settling_times=[hlc.dispense_settling_time] * num_channels,
      dispense_stop_back_volumes=[hlc.dispense_stop_back_volume] * num_channels,
      dispense_lld_modes=dispense_lld_modes,
    )
