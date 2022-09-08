""" Liquid classes for liquid handling robots. These define aspects of liquid handling operations
such as correction curves for volumes, preferred tip types, air transport volumes, etc. """

import enum
import typing

__all__ = [
 "HighVolumeFilter_Water_DispenseJet_Empty_with_transport_vol",
 "HighVolumeFilter_Water_DispenseSurface_Empty",
 "HighVolumeFilter_Water_DispenseJet_Empty_no_transport_vol",
 "StandardVolume_Water_DispenseSurface_Part_no_transport_vol",
 "StandardVolumeFilter_Water_DispenseSurface_Part_no_transport_vol",
]


class LiquidDevice(enum.Enum):
  """ Liquid device as defined by Hamilton CO-RE Liquid Editor

  Seems to be the liquid handler head.
  """
  # pylint: disable=invalid-name

  CHANNELS_1000uL = enum.auto()
  CORE_384 = enum.auto()
  CORE_384_WASH_STATION = enum.auto()
  CHANNELS_5mL = enum.auto()
  CORE_96 = enum.auto()
  CORE_96_WASH_STATION = enum.auto()
  CR_NEEDLE_WAS_STATION = enum.auto()
  DC_WASH_STATION = enum.auto()
  NEEDLE_WASH_STATION_THIRD_GENERATION = enum.auto()


class TipType(enum.Enum):
  """ Tip type as defined by Hamilton CO-RE Liquid Editor

  TODO: there are a few tip types missing here, maybe add those later.
  """
  # pylint: disable=invalid-name

  HIGH_VOLUME_TIP_1000uL = 4
  HIGH_VOLUME_TIP_WITH_FILTER_1000uL = 5
  NEEDLE_1000uL = 13
  LOW_VOLUME_TIP_10uL = 2
  LOW_VOLUME_TIP_WITH_FILTER_10uL = 3
  NEEDLE_10uL = 11
  STANDARD_VOLUME_TIP_300uL = 0
  STANDARD_VOLUME_TIP_WITH_FILTER_300uL = 1
  TIP_5mL = 25


class CorrectionCurvePoint:
  """ TypedDict for points on a correction curve.

  See `LiquidClass` and `compute_corrected_volume`. """

  target: float
  correct: float


class LiquidClass:
  """ Wrapper class for liquid classes. """

  def __init__(
    self,
    device: typing.List[LiquidDevice],
    tip_type: TipType,
    dispense_mode: int,
    pressure_lld: int,
    max_height_difference: int,
    flow_rate: typing.Tuple[int, int],
    mix_flow_rate: typing.Tuple[int, int],
    air_transport_volume: typing.Tuple[int, int],
    blowout_volume: typing.Tuple[int, int],
    swap_speed: typing.Tuple[int, int],
    settling_time: typing.Tuple[int, int],
    over_aspirate_volume: int,
    clot_retract_height: int,
    stop_flow_rate: int,
    stop_back_volume: int,
    correction_curve: CorrectionCurvePoint
  ):
    """ Initialize a new liquid class.

    All tuple arguments are (aspirate, dispense) pairs.

    Args:
      device: list of supported devices
      tip_type: supported tip type
      dispense_mode: 0 = jet partial volume, 1 = jet empty tip (blow out), 2 = surface partial
        volume 3 = surface empty tip (blow out), 4 = Empty tip at fix position
      pressure_lld: 0 = low, 1 = medium, 2 = high, 3 = very high
      max_height_difference: unknown
      flow_rate: ul/s
      mix_flow_rate: ul/s
      air_transport_volume: ul
      blowout_volume: ul
      swap_speed: mm/s
      settling_time: s
      over_aspirate_volume: ul, aspirate only
      clot_retract_height: mm, aspirate only
      stop_flow_rate: ul/s, dispense only
      stop_back_volume: ul, dispense only
      correction_curve: series of data points matching target values (keys) to
        corrected values (values), which are actually used for dispensation/aspiration commands. If
        length > 0, (0, 0) will automatically be added (possibly overriding).
    """

    self.device = device
    self.tip_type = tip_type
    self.dispense_mode = dispense_mode
    self.pressure_lld = pressure_lld
    self.max_height_difference = max_height_difference
    self.flow_rate = flow_rate
    self.mix_flow_rate = mix_flow_rate
    self.air_transport_volume = air_transport_volume
    self.blowout_volume = blowout_volume
    self.swap_speed = swap_speed
    self.settling_time = settling_time
    self.over_aspirate_volume = over_aspirate_volume
    self.clot_retract_height = clot_retract_height
    self.stop_flow_rate = stop_flow_rate
    self.stop_back_volume = stop_back_volume
    self.correction_curve = correction_curve
    if len(correction_curve):
      self.correction_curve.update({0: 0})

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

    targets = sorted(self.correction_curve.keys())

    if len(targets) == 0:
      return target_volume

    if target_volume in self.correction_curve:
      return self.correction_curve[target_volume]

    # use min non-zero value, so second index (if len(targets)>0,
    # then 0 was automatically added at initialization).
    if target_volume < targets[1]: # smaller than min
      return self.correction_curve[targets[1]]/targets[1] * target_volume
    if target_volume > targets[-1]: # larger than max
      return self.correction_curve[targets[-1]]/targets[-1] * target_volume

    # interpolate between two nearest points.
    for pt, t in zip(targets[:-1], targets[1:]):
      if pt < target_volume < t:
        return (self.correction_curve[t]-self.correction_curve[pt])/(t-pt) * \
               (target_volume - t) + self.correction_curve[t] # (y = slope * (x-x1) + y1)

  @property
  def aspirate_kwargs(self):
    """ Return kwargs for aspiration. """
    return {
      "transport_air_volume": [self.air_transport_volume[0] * 10],
      "blow_out_air_volume": [self.blowout_volume[0] * 10],
      "settling_time": [self.settling_time[0] * 10],
      "clot_detection_height": [self.clot_retract_height]
    }

  @property
  def dispense_kwargs(self):
    """ Return kwargs for dispensing. """
    return {
      "transport_air_volume": [self.air_transport_volume[1] * 10],
      "blow_out_air_volume": [self.blowout_volume[1] * 10],
      "settling_time": [self.settling_time[1] * 10],
      "dispensing_mode": [self.dispense_mode],
    }

  def serialize(self) -> dict:
    """ Serialize the liquid class to a dict. """
    return {
      "device": [d.name for d in self.device],
      "tip_type": self.tip_type.name,
      "dispense_mode": self.dispense_mode,
      "pressure_lld": self.pressure_lld,
      "max_height_difference": self.max_height_difference,
      "flow_rate": list(self.flow_rate),
      "mix_flow_rate": list(self.mix_flow_rate),
      "air_transport_volume": list(self.air_transport_volume),
      "blowout_volume": list(self.blowout_volume),
      "swap_speed": list(self.swap_speed),
      "settling_time": list(self.settling_time),
      "over_aspirate_volume": self.over_aspirate_volume,
      "clot_retract_height": self.clot_retract_height,
      "stop_flow_rate": self.stop_flow_rate,
      "stop_back_volume": self.stop_back_volume,
      "correction_curve": {str(k): v for k, v in self.correction_curve.items()},
    }


#: HighVolumeFilter_Water_DispenseJet_Empty_with_transport_vol
HighVolumeFilter_Water_DispenseJet_Empty_with_transport_vol = LiquidClass(
  device=[LiquidDevice.CHANNELS_1000uL],
  tip_type=TipType.HIGH_VOLUME_TIP_WITH_FILTER_1000uL,
  dispense_mode=1, # jet empty tip
  pressure_lld=0,
  max_height_difference=0,
  flow_rate=(250, 400),
  mix_flow_rate=(250, 1),
  air_transport_volume=(0, 0),
  blowout_volume=(40, 40),
  swap_speed=(2, 1),
  settling_time=(1, 0),
  over_aspirate_volume=0,
  clot_retract_height=0,
  stop_flow_rate=250,
  stop_back_volume=0,
  correction_curve={
    10: 13.3,
    20: 24.6,
    50: 57.2,
    100: 109.6,
    200: 212.9,
    500: 521.7,
    1000: 1034.0
  }
)


#: HighVolumeFilter_Water_DispenseSurface_Empty
HighVolumeFilter_Water_DispenseSurface_Empty = LiquidClass(
  device=[LiquidDevice.CHANNELS_1000uL],
  tip_type=TipType.HIGH_VOLUME_TIP_WITH_FILTER_1000uL,
  dispense_mode=3, # surface empty tip
  pressure_lld=0,
  max_height_difference=0,
  flow_rate=(250, 120),
  mix_flow_rate=(120, 120),
  air_transport_volume=(5, 5),
  blowout_volume=(0, 0),
  swap_speed=(2, 2),
  settling_time=(1, 0),
  over_aspirate_volume=5,
  clot_retract_height=0,
  stop_flow_rate=5,
  stop_back_volume=0,
  correction_curve={
    10: 12.5,
    20: 23.9,
    50: 56.3,
    100: 108.3,
    200: 211.0,
    500: 518.3,
    1000: 1028.5
  }
)


#: HighVolumeFilter_Water_DispenseJet_Empty_no_transport_vol
HighVolumeFilter_Water_DispenseJet_Empty_no_transport_vol = LiquidClass(
  device=[LiquidDevice.CHANNELS_1000uL],
  tip_type=TipType.HIGH_VOLUME_TIP_WITH_FILTER_1000uL,
  dispense_mode=1, # jet empty tip
  pressure_lld=0,
  max_height_difference=0,
  flow_rate=(150, 150),
  mix_flow_rate=(150, 1),
  air_transport_volume=(0, 0),
  blowout_volume=(15, 15),
  swap_speed=(2, 1),
  settling_time=(1, 0),
  over_aspirate_volume=0,
  clot_retract_height=0,
  stop_flow_rate=250,
  stop_back_volume=0,
  correction_curve={
    10: 13.3,
    20: 24.6,
    50: 57.2,
    100: 109.6,
    200: 212.9,
    500: 521.7,
    1000: 1034.0
  }
)

#: StandardVolume_Water_DispenseSurface_Part_no_transport_vol
StandardVolume_Water_DispenseSurface_Part_no_transport_vol = LiquidClass(
  device=[LiquidDevice.CHANNELS_1000uL],
  tip_type=TipType.STANDARD_VOLUME_TIP_300uL,
  dispense_mode=2, # surface partial
  pressure_lld=0,
  max_height_difference=0,
  flow_rate=(100, 120),
  mix_flow_rate=(100, 1),
  air_transport_volume=(0, 0),
  blowout_volume=(0, 0),
  swap_speed=(2, 2),
  settling_time=(1, 0),
  over_aspirate_volume=0,
  clot_retract_height=0,
  stop_flow_rate=5,
  stop_back_volume=0,
  correction_curve={
    5: 6.5,
    10: 11.9,
    20: 23.2,
    50: 55.1,
    100: 107.2,
    200: 211.0,
    300: 313.5
  }
)

#: StandardVolumeFilter_Water_DispenseSurface_Part_no_transport_vol
StandardVolumeFilter_Water_DispenseSurface_Part_no_transport_vol = LiquidClass(
  device=[LiquidDevice.CHANNELS_1000uL],
  tip_type=TipType.STANDARD_VOLUME_TIP_300uL,
  dispense_mode=2, # surface partial
  pressure_lld=0,
  max_height_difference=0,
  flow_rate=(100, 120),
  mix_flow_rate=(100, 1),
  air_transport_volume=(0, 0),
  blowout_volume=(0, 0),
  swap_speed=(2, 2),
  settling_time=(1, 0),
  over_aspirate_volume=0,
  clot_retract_height=0,
  stop_flow_rate=5,
  stop_back_volume=0,
  correction_curve={
    5: 6.5,
    10: 11.9,
    20: 23.2,
    50: 55.1,
    100: 107.2,
    200: 211.0,
    300: 313.5
  }
)
