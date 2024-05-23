from typing import Any, Dict


class HamiltonLiquidClass:
  """ A liquid class like used in VENUS / Venus on Vantage. """

  def __init__(
    self,
    curve: Dict[float, float],

    aspiration_flow_rate: float,
    aspiration_mix_flow_rate: float,
    aspiration_air_transport_volume: float,
    aspiration_blow_out_volume: float,
    aspiration_swap_speed: float,
    aspiration_settling_time: float,
    aspiration_over_aspirate_volume: float,
    aspiration_clot_retract_height: float,

    dispense_flow_rate: float,
    dispense_mode: float,
    dispense_mix_flow_rate: float,
    dispense_air_transport_volume: float,
    dispense_blow_out_volume: float,
    dispense_swap_speed: float,
    dispense_settling_time: float,
    dispense_stop_flow_rate: float,
    dispense_stop_back_volume: float,
  ):
    self.curve = curve

    self.aspiration_flow_rate = aspiration_flow_rate
    self.aspiration_mix_flow_rate = aspiration_mix_flow_rate
    self.aspiration_air_transport_volume = aspiration_air_transport_volume
    self.aspiration_blow_out_volume = aspiration_blow_out_volume
    self.aspiration_swap_speed = aspiration_swap_speed
    self.aspiration_settling_time = aspiration_settling_time
    self.aspiration_over_aspirate_volume = aspiration_over_aspirate_volume
    self.aspiration_clot_retract_height = aspiration_clot_retract_height

    self.dispense_mode = dispense_mode
    self.dispense_flow_rate = dispense_flow_rate
    self.dispense_mix_flow_rate = dispense_mix_flow_rate
    self.dispense_air_transport_volume = dispense_air_transport_volume
    self.dispense_blow_out_volume = dispense_blow_out_volume
    self.dispense_swap_speed = dispense_swap_speed
    self.dispense_settling_time = dispense_settling_time
    self.dispense_stop_flow_rate = dispense_stop_flow_rate
    self.dispense_stop_back_volume = dispense_stop_back_volume

  def compute_corrected_volume(self, target_volume: float) -> float:
    """ Compute corrected volume using the correction curve.

    Uses the correction curve data point if an exact match is
    available. If the volume is bigger or smaller than the
    min/max key, the min/max key will be used. Otherwise, linear
    interpolation between the two nearest data points is used. If
    no correction curve available, the initial volume will be returned.

    Args:
      Target volume that needs to be pipetted.

    Returns:
      Volume that should actually be pipetted to reach target volume.
    """

    targets = sorted(self.curve.keys())

    if len(targets) == 0:
      return target_volume

    if target_volume in self.curve:
      return self.curve[target_volume]

    # use min non-zero value, so second index (if len(targets)>0,
    # then 0 was automatically added at initialization).
    if target_volume < targets[1]: # smaller than min
      return self.curve[targets[1]]/targets[1] * target_volume
    if target_volume > targets[-1]: # larger than max
      return self.curve[targets[-1]]/targets[-1] * target_volume

    # interpolate between two nearest points.
    for pt, t in zip(targets[:-1], targets[1:]):
      if pt < target_volume < t:
        return (self.curve[t]-self.curve[pt])/(t-pt) * \
               (target_volume - t) + self.curve[t] # (y = slope * (x-x1) + y1)

    assert False, "Should never reach this point. Please file an issue."

  def serialize(self) -> Dict[str, Any]:
    """ Serialize the liquid class to a dictionary. """
    return {
     "curve": self.curve,

      "aspiration_flow_rate": self.aspiration_flow_rate,
      "aspiration_mix_flow_rate": self.aspiration_mix_flow_rate,
      "aspiration_air_transport_volume": self.aspiration_air_transport_volume,
      "aspiration_blow_out_volume": self.aspiration_blow_out_volume,
      "aspiration_swap_speed": self.aspiration_swap_speed,
      "aspiration_settling_time": self.aspiration_settling_time,
      "aspiration_over_aspirate_volume": self.aspiration_over_aspirate_volume,
      "aspiration_clot_retract_height": self.aspiration_clot_retract_height,

      "dispense_mode": self.dispense_mode,
      "dispense_flow_rate": self.dispense_flow_rate,
      "dispense_mix_flow_rate": self.dispense_mix_flow_rate,
      "dispense_air_transport_volume": self.dispense_air_transport_volume,
      "dispense_blow_out_volume": self.dispense_blow_out_volume,
      "dispense_swap_speed": self.dispense_swap_speed,
      "dispense_settling_time": self.dispense_settling_time,
      "dispense_stop_flow_rate": self.dispense_stop_flow_rate,
      "dispense_stop_back_volume": self.dispense_stop_back_volume,
    }
