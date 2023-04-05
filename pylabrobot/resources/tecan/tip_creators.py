import enum

from pylabrobot.resources import Tip


class TipType(enum.Enum):
  """ Tip type """
  STANDARD = "Standard"
  DITI = "DiTi"
  STDLOWVOL = "StandardLowVolume"
  DITILOWVOL = "DiTiLowVolume"
  MCADITI = "MCADiTi"
  AIRDITI = "ZaapDiTi"


class TecanTip(Tip):
  """ Represents a single tip for Tecan instruments. """

  def __init__(
    self,
    has_filter: bool,
    total_tip_length: float,
    maximal_volume: float,
    tip_type: TipType,
    fitting_depth: float = 0
  ):
    super().__init__(has_filter, total_tip_length, maximal_volume, fitting_depth)
    self.tip_type = tip_type


def standard_fixed_tip() -> TecanTip:
  """ Default standard fixed tip """
  return TecanTip(
    has_filter=False,
    total_tip_length=39.0,
    maximal_volume=1000,
    tip_type=TipType.STANDARD
  )


def diti_1000ul_liha() -> TecanTip:
  """ 1000ul tip """
  return TecanTip(
    has_filter=False,
    total_tip_length=32.6,
    maximal_volume=1100,
    tip_type=TipType.DITI
  )


def diti_10ul_liha() -> TecanTip:
  """ 10ul tip """
  return TecanTip(
    has_filter=False,
    total_tip_length=31.3,
    maximal_volume=23,
    tip_type=TipType.DITI
  )
