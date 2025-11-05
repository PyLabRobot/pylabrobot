from typing import Any, Dict

from pylabrobot.utils.interpolation import interpolate_1d


class HamiltonLiquidClass:
  """A liquid class like used in VENUS / Venus on Vantage."""

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
    """Compute the piston displacement volume needed to achieve a desired liquid volume.

    This method determines how far the pipette piston must move (i.e., the commanded
    internal volume) to aspirate or dispense a specified *target liquid volume*.
    Because factors such as air compressibility, liquid viscosity, and tip geometry
    affect the relationship between piston displacement and actual transferred volume,
    Hamilton liquid classes use an empirically derived **correction curve**.

    The correction curve maps *nominal liquid volumes* (target) to the corresponding
    *piston displacement volumes* required to achieve them. If the requested
    `target_volume` exactly matches a calibration point, its mapped value is used.
    Otherwise, the function performs **piecewise linear interpolation** between the
    two nearest calibration points. For values outside the calibration range,
    the nearest segment is linearly extrapolated.

    This interpolation is performed using
    :func:`pylabrobot.utils.interpolation.interpolate_1d`.

    Args:
      target_volume: The liquid volume to be aspirated or dispensed (in µL).

    Returns:
      The corrected piston displacement volume (in µL) that the pipette mechanism
      must execute to achieve the desired liquid transfer.

    Raises:
      ValueError: If the correction curve data is invalid or non-numeric.
    """
    if self.curve is None:
      return target_volume

    return interpolate_1d(target_volume, self.curve, bounds_handling="extrapolate")

  def serialize(self) -> Dict[str, Any]:
    """Serialize the liquid class to a dictionary."""
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
