import enum
import warnings
from typing import Optional, Union

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.tip import Tip


class TipType(enum.Enum):
  """Tip type"""

  STANDARD = "Standard"
  DITI = "DiTi"
  STDLOWVOL = "StandardLowVolume"
  DITILOWVOL = "DiTiLowVolume"
  MCADITI = "MCADiTi"
  AIRDITI = "ZaapDiTi"


class TecanTip(Tip):
  """Represents a single tip for Tecan instruments."""

  def __init__(
    self,
    name: str,
    diameter: Optional[float],
    has_filter: bool,
    size_z: float,
    maximal_volume: float,
    tip_type: Union[TipType, str],
    fitting_depth: float = 0,
    nominal_volume: Optional[float] = None,
    collar_height: Optional[float] = None,
    category: str = "tip",
    model: Optional[str] = None,
    pick_up_location: Optional[Coordinate] = None,
  ):
    """Initialize a Tecan tip with its name and firmware tip characteristics."""
    if diameter is None:
      raise NotImplementedError(f"Tip diameter is not defined for {model or name}.")
    if isinstance(tip_type, str):
      tip_type = TipType[tip_type]
    if size_z <= 0:
      warnings.warn(
        "WARNING: size_z <= 0. Please get in touch at https://discuss.pylabrobot.org",
        UserWarning,
        stacklevel=3,
      )

    super().__init__(
      diameter=diameter,
      size_z=size_z,
      has_filter=has_filter,
      maximal_volume=maximal_volume,
      fitting_depth=fitting_depth,
      name=name,
      nominal_volume=nominal_volume,
      collar_height=collar_height,
      category=category,
      model=model,
      pick_up_location=pick_up_location,
    )
    self.tip_type = tip_type

  def __eq__(self, other: object) -> bool:
    """Compare tip fields and Tecan's tip type."""
    return isinstance(other, TecanTip) and super().__eq__(other) and self.tip_type == other.tip_type

  def serialize(self) -> dict:
    """Serialize a Tecan tip with its firmware tip type."""
    data = super().serialize()
    return {**data, "tip_type": self.tip_type.name}


def standard_fixed_tip(name: str) -> TecanTip:
  """Default standard fixed tip"""
  return TecanTip(
    diameter=None,
    model=standard_fixed_tip.__name__,
    name=name,
    has_filter=False,
    size_z=39.0,
    maximal_volume=1000,
    tip_type=TipType.STANDARD,
  )


def DiTi_100ul_Te_MO_tip(name: str) -> TecanTip:
  """Tip for DiTi_100ul_Te_MO"""
  return TecanTip(
    diameter=None,
    model=DiTi_100ul_Te_MO_tip.__name__,
    name=name,
    has_filter=False,
    size_z=42.0,
    maximal_volume=110.0,
    tip_type=TipType.DITI,
  )


def DiTi_50ul_Te_MO_tip(name: str) -> TecanTip:
  """Tip for DiTi_50ul_Te_MO"""
  return TecanTip(
    diameter=None,
    model=DiTi_50ul_Te_MO_tip.__name__,
    name=name,
    has_filter=False,
    size_z=28.0,
    maximal_volume=60.0,
    tip_type=TipType.DITI,
  )


def DiTi_200ul_Te_MO_tip(name: str) -> TecanTip:
  """Tip for DiTi_200ul_Te_MO"""
  return TecanTip(
    diameter=None,
    model=DiTi_200ul_Te_MO_tip.__name__,
    name=name,
    has_filter=False,
    size_z=42.0,
    maximal_volume=220.0,
    tip_type=TipType.DITI,
  )


def DiTi_100ul_Filter_Te_MO_tip(name: str) -> TecanTip:
  """Tip for DiTi_100ul_Filter_Te_MO"""
  return TecanTip(
    diameter=None,
    model=DiTi_100ul_Filter_Te_MO_tip.__name__,
    name=name,
    has_filter=False,
    size_z=42.0,
    maximal_volume=90.0,
    tip_type=TipType.DITI,
  )


def DiTi_200ul_Filter_Te_MO_tip(name: str) -> TecanTip:
  """Tip for DiTi_200ul_Filter_Te_MO"""
  return TecanTip(
    diameter=None,
    model=DiTi_200ul_Filter_Te_MO_tip.__name__,
    name=name,
    has_filter=False,
    size_z=42.0,
    maximal_volume=170.0,
    tip_type=TipType.DITI,
  )


def Adapter_96_DiTi_MCA384_tip(name: str) -> TecanTip:
  """Tip for Adapter_96_DiTi_MCA384"""
  return TecanTip(
    diameter=None,
    model=Adapter_96_DiTi_MCA384_tip.__name__,
    name=name,
    has_filter=False,
    size_z=15.9,
    maximal_volume=110.0,
    tip_type=TipType.DITI,
  )


def Adapter_DiTi_Combo_MCA384_tip(name: str) -> TecanTip:
  """Tip for Adapter_DiTi_Combo_MCA384"""
  return TecanTip(
    diameter=None,
    model=Adapter_DiTi_Combo_MCA384_tip.__name__,
    name=name,
    has_filter=False,
    size_z=15.9,
    maximal_volume=110.0,
    tip_type=TipType.DITI,
  )


def Adapter_DiTi_MCA384_tip(name: str) -> TecanTip:
  """Tip for Adapter_DiTi_MCA384"""
  return TecanTip(
    diameter=None,
    model=Adapter_DiTi_MCA384_tip.__name__,
    name=name,
    has_filter=False,
    size_z=15.9,
    maximal_volume=110.0,
    tip_type=TipType.DITI,
  )


def DiTi_100ul_Filter_MCA96_tip(name: str) -> TecanTip:
  """Tip for DiTi_100ul_Filter_MCA96"""
  return TecanTip(
    diameter=None,
    model=DiTi_100ul_Filter_MCA96_tip.__name__,
    name=name,
    has_filter=False,
    size_z=43.1,
    maximal_volume=90.0,
    tip_type=TipType.DITI,
  )


def DiTi_100ul_MCA96_tip(name: str) -> TecanTip:
  """Tip for DiTi_100ul_MCA96"""
  return TecanTip(
    diameter=None,
    model=DiTi_100ul_MCA96_tip.__name__,
    name=name,
    has_filter=False,
    size_z=43.1,
    maximal_volume=110.0,
    tip_type=TipType.DITI,
  )


def DiTi_200ul_Filter_MCA96_tip(name: str) -> TecanTip:
  """Tip for DiTi_200ul_Filter_MCA96"""
  return TecanTip(
    diameter=None,
    model=DiTi_200ul_Filter_MCA96_tip.__name__,
    name=name,
    has_filter=False,
    size_z=43.1,
    maximal_volume=170.0,
    tip_type=TipType.DITI,
  )


def DiTi_200ul_MCA96_tip(name: str) -> TecanTip:
  """Tip for DiTi_200ul_MCA96"""
  return TecanTip(
    diameter=None,
    model=DiTi_200ul_MCA96_tip.__name__,
    name=name,
    has_filter=False,
    size_z=43.1,
    maximal_volume=220.0,
    tip_type=TipType.DITI,
  )


def DiTi_50ul_MCA96_tip(name: str) -> TecanTip:
  """Tip for DiTi_50ul_MCA96"""
  return TecanTip(
    diameter=None,
    model=DiTi_50ul_MCA96_tip.__name__,
    name=name,
    has_filter=False,
    size_z=29.6,
    maximal_volume=60.0,
    tip_type=TipType.DITI,
  )


def Base_Nested_DiTi_MCA96_tip(name: str) -> TecanTip:
  """Tip for Base_Nested_DiTi_MCA96"""
  return TecanTip(
    diameter=None,
    model=Base_Nested_DiTi_MCA96_tip.__name__,
    name=name,
    has_filter=False,
    size_z=43.1,
    maximal_volume=220.0,
    tip_type=TipType.DITI,
  )


def DiTi_100ul_Nested_MCA96_tip(name: str) -> TecanTip:
  """Tip for DiTi_100ul_Nested_MCA96"""
  return TecanTip(
    diameter=None,
    model=DiTi_100ul_Nested_MCA96_tip.__name__,
    name=name,
    has_filter=False,
    size_z=43.1,
    maximal_volume=110.0,
    tip_type=TipType.DITI,
  )


def DiTi_100ul_SBS_MCA96_tip(name: str) -> TecanTip:
  """Tip for DiTi_100ul_SBS_MCA96"""
  return TecanTip(
    diameter=None,
    model=DiTi_100ul_SBS_MCA96_tip.__name__,
    name=name,
    has_filter=False,
    size_z=43.1,
    maximal_volume=110.0,
    tip_type=TipType.DITI,
  )


def DiTi_200ul_SBS_MCA96_tip(name: str) -> TecanTip:
  """Tip for DiTi_200ul_SBS_MCA96"""
  return TecanTip(
    diameter=None,
    model=DiTi_200ul_SBS_MCA96_tip.__name__,
    name=name,
    has_filter=False,
    size_z=43.1,
    maximal_volume=220.0,
    tip_type=TipType.DITI,
  )


def DiTi_50ul_SBS_MCA96_tip(name: str) -> TecanTip:
  """Tip for DiTi_50ul_SBS_MCA96"""
  return TecanTip(
    diameter=None,
    model=DiTi_50ul_SBS_MCA96_tip.__name__,
    name=name,
    has_filter=False,
    size_z=29.6,
    maximal_volume=60.0,
    tip_type=TipType.DITI,
  )


def DiTi_50ul_Nested_MCA96_tip(name: str) -> TecanTip:
  """Tip for DiTi_50ul_Nested_MCA96"""
  return TecanTip(
    diameter=None,
    model=DiTi_50ul_Nested_MCA96_tip.__name__,
    name=name,
    has_filter=False,
    size_z=29.6,
    maximal_volume=60.0,
    tip_type=TipType.DITI,
  )


def Adapter_96_DiTi_1to1_MCA384_tip(name: str) -> TecanTip:
  """Tip for Adapter_96_DiTi_1to1_MCA384"""
  return TecanTip(
    diameter=None,
    model=Adapter_96_DiTi_1to1_MCA384_tip.__name__,
    name=name,
    has_filter=False,
    size_z=25.2,
    maximal_volume=110.0,
    tip_type=TipType.DITI,
  )


def DiTi_200ul_Nested_MCA96_tip(name: str) -> TecanTip:
  """Tip for DiTi_200ul_Nested_MCA96"""
  return TecanTip(
    diameter=None,
    model=DiTi_200ul_Nested_MCA96_tip.__name__,
    name=name,
    has_filter=False,
    size_z=43.1,
    maximal_volume=220.0,
    tip_type=TipType.DITI,
  )


def DiTi_200ul_w_b_filter_MCA96_tip(name: str) -> TecanTip:
  """Tip for DiTi_200ul_w_b_filter_MCA96"""
  return TecanTip(
    diameter=None,
    model=DiTi_200ul_w_b_filter_MCA96_tip.__name__,
    name=name,
    has_filter=False,
    size_z=43.1,
    maximal_volume=175.0,
    tip_type=TipType.DITI,
  )


def DiTi_200ul_wide_bore_MCA96_tip(name: str) -> TecanTip:
  """Tip for DiTi_200ul_wide_bore_MCA96"""
  return TecanTip(
    diameter=None,
    model=DiTi_200ul_wide_bore_MCA96_tip.__name__,
    name=name,
    has_filter=False,
    size_z=43.1,
    maximal_volume=220.0,
    tip_type=TipType.DITI,
  )


def Adapter_96_DiTi_4to1_MCA384_tip(name: str) -> TecanTip:
  """Tip for Adapter_96_DiTi_4to1_MCA384"""
  return TecanTip(
    diameter=None,
    model=Adapter_96_DiTi_4to1_MCA384_tip.__name__,
    name=name,
    has_filter=False,
    size_z=25.2,
    maximal_volume=110.0,
    tip_type=TipType.DITI,
  )


def DiTi_500ul_Filter_SBS_MCA96_tip(name: str) -> TecanTip:
  """Tip for DiTi_500ul_Filter_SBS_MCA96"""
  return TecanTip(
    diameter=None,
    model=DiTi_500ul_Filter_SBS_MCA96_tip.__name__,
    name=name,
    has_filter=False,
    size_z=54.0,
    maximal_volume=405.0,
    tip_type=TipType.DITI,
  )


def DiTi_500ul_SBS_MCA96_tip(name: str) -> TecanTip:
  """Tip for DiTi_500ul_SBS_MCA96"""
  return TecanTip(
    diameter=None,
    model=DiTi_500ul_SBS_MCA96_tip.__name__,
    name=name,
    has_filter=False,
    size_z=54.0,
    maximal_volume=502.0,
    tip_type=TipType.DITI,
  )


def DiTi_Nested_Waste_MCA384_tip(name: str) -> TecanTip:
  """Tip for DiTi_Nested_Waste_MCA384"""
  return TecanTip(
    diameter=None,
    model=DiTi_Nested_Waste_MCA384_tip.__name__,
    name=name,
    has_filter=False,
    size_z=0.0,
    maximal_volume=0.0,
    tip_type=TipType.DITI,
  )


def DiTi_1000ul_SBS_LiHa_tip(name: str) -> TecanTip:
  """Tip for DiTi_1000ul_SBS_LiHa"""
  return TecanTip(
    diameter=None,
    model=DiTi_1000ul_SBS_LiHa_tip.__name__,
    name=name,
    has_filter=False,
    size_z=32.6,
    maximal_volume=1100.0,
    tip_type=TipType.DITI,
  )


def DiTi_200ul_SBS_LiHa_tip(name: str) -> TecanTip:
  """Tip for DiTi_200ul_SBS_LiHa"""
  return TecanTip(
    diameter=None,
    model=DiTi_200ul_SBS_LiHa_tip.__name__,
    name=name,
    has_filter=False,
    size_z=21.0,
    maximal_volume=220.0,
    tip_type=TipType.DITI,
  )


def DiTi_50ul_SBS_LiHa_tip(name: str) -> TecanTip:
  """Tip for DiTi_50ul_SBS_LiHa"""
  return TecanTip(
    diameter=None,
    model=DiTi_50ul_SBS_LiHa_tip.__name__,
    name=name,
    has_filter=False,
    size_z=-5.3,
    maximal_volume=55.0,
    tip_type=TipType.DITI,
  )


def DiTi_5000ul_LiHa_tip(name: str) -> TecanTip:
  """Tip for DiTi_5000ul_LiHa"""
  return TecanTip(
    diameter=None,
    model=DiTi_5000ul_LiHa_tip.__name__,
    name=name,
    has_filter=False,
    size_z=46.6,
    maximal_volume=5130.0,
    tip_type=TipType.DITI,
  )


def DiTi_5000ul_Filter_LiHa_tip(name: str) -> TecanTip:
  """Tip for DiTi_5000ul_Filter_LiHa"""
  return TecanTip(
    diameter=None,
    model=DiTi_5000ul_Filter_LiHa_tip.__name__,
    name=name,
    has_filter=False,
    size_z=46.6,
    maximal_volume=5130.0,
    tip_type=TipType.DITI,
  )


def DiTi_125ul_Filter_MCA384_tip(name: str) -> TecanTip:
  """Tip for DiTi_125ul_Filter_MCA384"""
  return TecanTip(
    diameter=None,
    model=DiTi_125ul_Filter_MCA384_tip.__name__,
    name=name,
    has_filter=False,
    size_z=47.3,
    maximal_volume=115.0,
    tip_type=TipType.DITI,
  )


def DiTi_125ul_MCA384_tip(name: str) -> TecanTip:
  """Tip for DiTi_125ul_MCA384"""
  return TecanTip(
    diameter=None,
    model=DiTi_125ul_MCA384_tip.__name__,
    name=name,
    has_filter=False,
    size_z=47.3,
    maximal_volume=129.0,
    tip_type=TipType.DITI,
  )


def DiTi_15ul_Filter_MCA384_tip(name: str) -> TecanTip:
  """Tip for DiTi_15ul_Filter_MCA384"""
  return TecanTip(
    diameter=None,
    model=DiTi_15ul_Filter_MCA384_tip.__name__,
    name=name,
    has_filter=False,
    size_z=28.6,
    maximal_volume=18.0,
    tip_type=TipType.DITI,
  )


def DiTi_15ul_MCA384_tip(name: str) -> TecanTip:
  """Tip for DiTi_15ul_MCA384"""
  return TecanTip(
    diameter=None,
    model=DiTi_15ul_MCA384_tip.__name__,
    name=name,
    has_filter=False,
    size_z=28.6,
    maximal_volume=19.0,
    tip_type=TipType.DITI,
  )


def DiTi_50ul_Filter_MCA384_tip(name: str) -> TecanTip:
  """Tip for DiTi_50ul_Filter_MCA384"""
  return TecanTip(
    diameter=None,
    model=DiTi_50ul_Filter_MCA384_tip.__name__,
    name=name,
    has_filter=False,
    size_z=41.4,
    maximal_volume=44.0,
    tip_type=TipType.DITI,
  )


def DiTi_50ul_MCA384_tip(name: str) -> TecanTip:
  """Tip for DiTi_50ul_MCA384"""
  return TecanTip(
    diameter=None,
    model=DiTi_50ul_MCA384_tip.__name__,
    name=name,
    has_filter=False,
    size_z=41.4,
    maximal_volume=53.0,
    tip_type=TipType.DITI,
  )


def DiTi_1000ul_Filter_LiHa_tip(name: str) -> TecanTip:
  """Tip for DiTi_1000ul_Filter_LiHa"""
  return TecanTip(
    diameter=None,
    model=DiTi_1000ul_Filter_LiHa_tip.__name__,
    name=name,
    has_filter=False,
    size_z=32.6,
    maximal_volume=1050.0,
    tip_type=TipType.DITI,
  )


def DiTi_1000ul_LiHa_tip(name: str) -> TecanTip:
  """Tip for DiTi_1000ul_LiHa"""
  return TecanTip(
    diameter=None,
    model=DiTi_1000ul_LiHa_tip.__name__,
    name=name,
    has_filter=False,
    size_z=32.6,
    maximal_volume=1100.0,
    tip_type=TipType.DITI,
  )


def DiTi_10ul_Filter_LiHa_tip(name: str) -> TecanTip:
  """Tip for DiTi_10ul_Filter_LiHa"""
  return TecanTip(
    diameter=None,
    model=DiTi_10ul_Filter_LiHa_tip.__name__,
    name=name,
    has_filter=False,
    size_z=-31.3,
    maximal_volume=12.0,
    tip_type=TipType.DITI,
  )


def DiTi_10ul_LiHa_tip(name: str) -> TecanTip:
  """Tip for DiTi_10ul_LiHa"""
  return TecanTip(
    diameter=None,
    model=DiTi_10ul_LiHa_tip.__name__,
    name=name,
    has_filter=False,
    size_z=-31.3,
    maximal_volume=23.0,
    tip_type=TipType.DITI,
  )


def DiTi_200ul_Filter_LiHa_tip(name: str) -> TecanTip:
  """Tip for DiTi_200ul_Filter_LiHa"""
  return TecanTip(
    diameter=None,
    model=DiTi_200ul_Filter_LiHa_tip.__name__,
    name=name,
    has_filter=False,
    size_z=-5.0,
    maximal_volume=210.0,
    tip_type=TipType.DITI,
  )


def DiTi_200ul_LiHa_tip(name: str) -> TecanTip:
  """Tip for DiTi_200ul_LiHa"""
  return TecanTip(
    diameter=None,
    model=DiTi_200ul_LiHa_tip.__name__,
    name=name,
    has_filter=False,
    size_z=-5.0,
    maximal_volume=220.0,
    tip_type=TipType.DITI,
  )


def DiTi_50ul_Filter_LiHa_tip(name: str) -> TecanTip:
  """Tip for DiTi_50ul_Filter_LiHa"""
  return TecanTip(
    diameter=None,
    model=DiTi_50ul_Filter_LiHa_tip.__name__,
    name=name,
    has_filter=False,
    size_z=-5.3,
    maximal_volume=55.0,
    tip_type=TipType.DITI,
  )


def DiTi_50ul_LiHa_tip(name: str) -> TecanTip:
  """Tip for DiTi_50ul_LiHa"""
  return TecanTip(
    diameter=None,
    model=DiTi_50ul_LiHa_tip.__name__,
    name=name,
    has_filter=False,
    size_z=-5.3,
    maximal_volume=55.0,
    tip_type=TipType.DITI,
  )


def DiTi_350ul_Nested_LiHa_tip(name: str) -> TecanTip:
  """Tip for DiTi_350ul_Nested_LiHa"""
  return TecanTip(
    diameter=None,
    model=DiTi_350ul_Nested_LiHa_tip.__name__,
    name=name,
    has_filter=False,
    size_z=-5.0,
    maximal_volume=390.0,
    tip_type=TipType.DITI,
  )


def DiTi_10ul_Filter_LiHa_L_tip(name: str) -> TecanTip:
  """Tip for DiTi_10ul_Filter_LiHa_L"""
  return TecanTip(
    diameter=None,
    model=DiTi_10ul_Filter_LiHa_L_tip.__name__,
    name=name,
    has_filter=False,
    size_z=-28.1,
    maximal_volume=12.0,
    tip_type=TipType.DITI,
  )


def DiTi_10ul_Filter_Nested_LiHa_tip(name: str) -> TecanTip:
  """Tip for DiTi_10ul_Filter_Nested_LiHa"""
  return TecanTip(
    diameter=None,
    model=DiTi_10ul_Filter_Nested_LiHa_tip.__name__,
    name=name,
    has_filter=False,
    size_z=-28.1,
    maximal_volume=12.0,
    tip_type=TipType.DITI,
  )


def DiTi_10ul_LiHa_L_tip(name: str) -> TecanTip:
  """Tip for DiTi_10ul_LiHa_L"""
  return TecanTip(
    diameter=None,
    model=DiTi_10ul_LiHa_L_tip.__name__,
    name=name,
    has_filter=False,
    size_z=-28.1,
    maximal_volume=23.0,
    tip_type=TipType.DITI,
  )


def DiTi_10ul_Nested_LiHa_tip(name: str) -> TecanTip:
  """Tip for DiTi_10ul_Nested_LiHa"""
  return TecanTip(
    diameter=None,
    model=DiTi_10ul_Nested_LiHa_tip.__name__,
    name=name,
    has_filter=False,
    size_z=-28.1,
    maximal_volume=23.0,
    tip_type=TipType.DITI,
  )


def DiTi_10ul_SBS_Filter_LiHa_tip(name: str) -> TecanTip:
  """Tip for DiTi_10ul_SBS_Filter_LiHa"""
  return TecanTip(
    diameter=None,
    model=DiTi_10ul_SBS_Filter_LiHa_tip.__name__,
    name=name,
    has_filter=False,
    size_z=-28.1,
    maximal_volume=12.0,
    tip_type=TipType.DITI,
  )


def DiTi_10ul_SBS_LiHa_tip(name: str) -> TecanTip:
  """Tip for DiTi_10ul_SBS_LiHa"""
  return TecanTip(
    diameter=None,
    model=DiTi_10ul_SBS_LiHa_tip.__name__,
    name=name,
    has_filter=False,
    size_z=-28.1,
    maximal_volume=23.0,
    tip_type=TipType.DITI,
  )


def DiTi_1000ul_W_B_Filter_LiHa_tip(name: str) -> TecanTip:
  """Tip for DiTi_1000ul_W_B_Filter_LiHa"""
  return TecanTip(
    diameter=None,
    model=DiTi_1000ul_W_B_Filter_LiHa_tip.__name__,
    name=name,
    has_filter=False,
    size_z=32.0,
    maximal_volume=995.0,
    tip_type=TipType.DITI,
  )


def DiTi_1000ul_CL_Filter_LiHa_tip(name: str) -> TecanTip:
  """Tip for DiTi_1000ul_CL_Filter_LiHa"""
  return TecanTip(
    diameter=None,
    model=DiTi_1000ul_CL_Filter_LiHa_tip.__name__,
    name=name,
    has_filter=False,
    size_z=32.6,
    maximal_volume=1050.0,
    tip_type=TipType.DITI,
  )


def DiTi_1000ul_CL_LiHa_tip(name: str) -> TecanTip:
  """Tip for DiTi_1000ul_CL_LiHa"""
  return TecanTip(
    diameter=None,
    model=DiTi_1000ul_CL_LiHa_tip.__name__,
    name=name,
    has_filter=False,
    size_z=32.6,
    maximal_volume=1100.0,
    tip_type=TipType.DITI,
  )


def DiTi_200ul_CL_Filter_LiHa_tip(name: str) -> TecanTip:
  """Tip for DiTi_200ul_CL_Filter_LiHa"""
  return TecanTip(
    diameter=None,
    model=DiTi_200ul_CL_Filter_LiHa_tip.__name__,
    name=name,
    has_filter=False,
    size_z=-5.0,
    maximal_volume=210.0,
    tip_type=TipType.DITI,
  )


def DiTi_200ul_CL_LiHa_tip(name: str) -> TecanTip:
  """Tip for DiTi_200ul_CL_LiHa"""
  return TecanTip(
    diameter=None,
    model=DiTi_200ul_CL_LiHa_tip.__name__,
    name=name,
    has_filter=False,
    size_z=-5.0,
    maximal_volume=220.0,
    tip_type=TipType.DITI,
  )


def DiTi_50ul_CL_Filter_LiHa_tip(name: str) -> TecanTip:
  """Tip for DiTi_50ul_CL_Filter_LiHa"""
  return TecanTip(
    diameter=None,
    model=DiTi_50ul_CL_Filter_LiHa_tip.__name__,
    name=name,
    has_filter=False,
    size_z=-5.3,
    maximal_volume=55.0,
    tip_type=TipType.DITI,
  )


def DiTi_50ul_CL_LiHa_tip(name: str) -> TecanTip:
  """Tip for DiTi_50ul_CL_LiHa"""
  return TecanTip(
    diameter=None,
    model=DiTi_50ul_CL_LiHa_tip.__name__,
    name=name,
    has_filter=False,
    size_z=-5.3,
    maximal_volume=60.0,
    tip_type=TipType.DITI,
  )
