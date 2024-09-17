import dataclasses
from typing import Optional
from pylabrobot.liquid_handling.parameter_sets.parameter_set import ParameterSet
from pylabrobot.liquid_handling.backends.hamilton.STAR import STAR


@dataclasses.dataclass
class STARParameterSet(ParameterSet):
  """ A collection of parameters for a liquid transfer on a Hamilton STAR.
  
  A rough adoption of HamiltonLiquidClass.
  - This includes blow_out and jet configuration
  - Parameters are lists to allow channel-specific values.
  - `blow_out_air_volumes` is shared between aspiration and dispense.
  """

  blow_out_air_volumes: Optional[list[float]]

  aspiration_blow_out: Optional[list[bool]]
  aspiration_jet: Optional[list[bool]]
  aspiration_flow_rates: Optional[list[float]]
  aspiration_mix_flow_rates: Optional[list[float]]
  aspiration_transport_air_volumes: Optional[list[float]]
  aspiration_swap_speeds: Optional[list[float]]
  aspiration_settling_times: Optional[list[float]]
  aspiration_clot_retract_heights: Optional[list[float]]
  aspiration_lld_modes: Optional[list[STAR.LLDMode]]

  dispense_blow_out: Optional[list[bool]]
  dispense_jet: Optional[list[bool]]
  dispense_flow_rates: Optional[list[float]]
  dispense_mix_speeds: Optional[list[float]]
  dispense_transport_air_volumes: Optional[list[float]]
  dispense_swap_speeds: Optional[list[float]]
  dispense_settling_times: Optional[list[float]]
  dispense_stop_back_volumes: Optional[list[float]]
  dispense_lld_modes: Optional[list[STAR.LLDMode]]

  def make_asp_kwargs(self) -> dict[str, float]:
    return {
      "jet": self.aspiration_jet,
      "blow_out": self.aspiration_blow_out,
      "flow_rates": self.aspiration_flow_rates,
      "mix_flow_rate": self.aspiration_mix_flow_rates,
      "transport_air_volume": self.aspiration_transport_air_volumes,
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
      "flow_rate": self.dispense_flow_rates,
      "mix_speed": self.dispense_mix_speeds,
      "transport_air_volume": self.dispense_transport_air_volumes,
      "swap_speed": self.dispense_swap_speeds,
      "settling_time": self.dispense_settling_times,
      "stop_back_volume": self.dispense_stop_back_volumes,
      "blow_out_air_volume": self.blow_out_air_volumes,
      "lld_mode": self.dispense_lld_modes,
    }
