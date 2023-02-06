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
    total_tip_length=390,
    maximal_volume=1000,
    tip_type=TipType.STANDARD
  )
