# pylint: disable=invalid-name

import enum

from pylabrobot.resources.tip import Tip


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



def DiTi_100ul_Te_MO_tip() -> TecanTip:
  """ Tip for DiTi_100ul_Te_MO """
  return TecanTip(
    has_filter=False,
    total_tip_length=42.0,
    maximal_volume=110.0,
    tip_type=TipType.DITI
  )


def DiTi_50ul_Te_MO_tip() -> TecanTip:
  """ Tip for DiTi_50ul_Te_MO """
  return TecanTip(
    has_filter=False,
    total_tip_length=28.0,
    maximal_volume=60.0,
    tip_type=TipType.DITI
  )


def DiTi_200ul_Te_MO_tip() -> TecanTip:
  """ Tip for DiTi_200ul_Te_MO """
  return TecanTip(
    has_filter=False,
    total_tip_length=42.0,
    maximal_volume=220.0,
    tip_type=TipType.DITI
  )


def DiTi_100ul_Filter_Te_MO_tip() -> TecanTip:
  """ Tip for DiTi_100ul_Filter_Te_MO """
  return TecanTip(
    has_filter=False,
    total_tip_length=42.0,
    maximal_volume=90.0,
    tip_type=TipType.DITI
  )


def DiTi_200ul_Filter_Te_MO_tip() -> TecanTip:
  """ Tip for DiTi_200ul_Filter_Te_MO """
  return TecanTip(
    has_filter=False,
    total_tip_length=42.0,
    maximal_volume=170.0,
    tip_type=TipType.DITI
  )


def Adapter_96_DiTi_MCA384_tip() -> TecanTip:
  """ Tip for Adapter_96_DiTi_MCA384 """
  return TecanTip(
    has_filter=False,
    total_tip_length=15.9,
    maximal_volume=110.0,
    tip_type=TipType.DITI
  )


def Adapter_DiTi_Combo_MCA384_tip() -> TecanTip:
  """ Tip for Adapter_DiTi_Combo_MCA384 """
  return TecanTip(
    has_filter=False,
    total_tip_length=15.9,
    maximal_volume=110.0,
    tip_type=TipType.DITI
  )


def Adapter_DiTi_MCA384_tip() -> TecanTip:
  """ Tip for Adapter_DiTi_MCA384 """
  return TecanTip(
    has_filter=False,
    total_tip_length=15.9,
    maximal_volume=110.0,
    tip_type=TipType.DITI
  )


def DiTi_100ul_Filter_MCA96_tip() -> TecanTip:
  """ Tip for DiTi_100ul_Filter_MCA96 """
  return TecanTip(
    has_filter=False,
    total_tip_length=43.1,
    maximal_volume=90.0,
    tip_type=TipType.DITI
  )


def DiTi_100ul_MCA96_tip() -> TecanTip:
  """ Tip for DiTi_100ul_MCA96 """
  return TecanTip(
    has_filter=False,
    total_tip_length=43.1,
    maximal_volume=110.0,
    tip_type=TipType.DITI
  )


def DiTi_200ul_Filter_MCA96_tip() -> TecanTip:
  """ Tip for DiTi_200ul_Filter_MCA96 """
  return TecanTip(
    has_filter=False,
    total_tip_length=43.1,
    maximal_volume=170.0,
    tip_type=TipType.DITI
  )


def DiTi_200ul_MCA96_tip() -> TecanTip:
  """ Tip for DiTi_200ul_MCA96 """
  return TecanTip(
    has_filter=False,
    total_tip_length=43.1,
    maximal_volume=220.0,
    tip_type=TipType.DITI
  )


def DiTi_50ul_MCA96_tip() -> TecanTip:
  """ Tip for DiTi_50ul_MCA96 """
  return TecanTip(
    has_filter=False,
    total_tip_length=29.6,
    maximal_volume=60.0,
    tip_type=TipType.DITI
  )


def Base_Nested_DiTi_MCA96_tip() -> TecanTip:
  """ Tip for Base_Nested_DiTi_MCA96 """
  return TecanTip(
    has_filter=False,
    total_tip_length=43.1,
    maximal_volume=220.0,
    tip_type=TipType.DITI
  )


def DiTi_100ul_Nested_MCA96_tip() -> TecanTip:
  """ Tip for DiTi_100ul_Nested_MCA96 """
  return TecanTip(
    has_filter=False,
    total_tip_length=43.1,
    maximal_volume=110.0,
    tip_type=TipType.DITI
  )


def DiTi_100ul_SBS_MCA96_tip() -> TecanTip:
  """ Tip for DiTi_100ul_SBS_MCA96 """
  return TecanTip(
    has_filter=False,
    total_tip_length=43.1,
    maximal_volume=110.0,
    tip_type=TipType.DITI
  )


def DiTi_200ul_SBS_MCA96_tip() -> TecanTip:
  """ Tip for DiTi_200ul_SBS_MCA96 """
  return TecanTip(
    has_filter=False,
    total_tip_length=43.1,
    maximal_volume=220.0,
    tip_type=TipType.DITI
  )


def DiTi_50ul_SBS_MCA96_tip() -> TecanTip:
  """ Tip for DiTi_50ul_SBS_MCA96 """
  return TecanTip(
    has_filter=False,
    total_tip_length=29.6,
    maximal_volume=60.0,
    tip_type=TipType.DITI
  )


def DiTi_50ul_Nested_MCA96_tip() -> TecanTip:
  """ Tip for DiTi_50ul_Nested_MCA96 """
  return TecanTip(
    has_filter=False,
    total_tip_length=29.6,
    maximal_volume=60.0,
    tip_type=TipType.DITI
  )


def Adapter_96_DiTi_1to1_MCA384_tip() -> TecanTip:
  """ Tip for Adapter_96_DiTi_1to1_MCA384 """
  return TecanTip(
    has_filter=False,
    total_tip_length=25.2,
    maximal_volume=110.0,
    tip_type=TipType.DITI
  )


def DiTi_200ul_Nested_MCA96_tip() -> TecanTip:
  """ Tip for DiTi_200ul_Nested_MCA96 """
  return TecanTip(
    has_filter=False,
    total_tip_length=43.1,
    maximal_volume=220.0,
    tip_type=TipType.DITI
  )


def DiTi_200ul_w_b_filter_MCA96_tip() -> TecanTip:
  """ Tip for DiTi_200ul_w_b_filter_MCA96 """
  return TecanTip(
    has_filter=False,
    total_tip_length=43.1,
    maximal_volume=175.0,
    tip_type=TipType.DITI
  )


def DiTi_200ul_wide_bore_MCA96_tip() -> TecanTip:
  """ Tip for DiTi_200ul_wide_bore_MCA96 """
  return TecanTip(
    has_filter=False,
    total_tip_length=43.1,
    maximal_volume=220.0,
    tip_type=TipType.DITI
  )


def Adapter_96_DiTi_4to1_MCA384_tip() -> TecanTip:
  """ Tip for Adapter_96_DiTi_4to1_MCA384 """
  return TecanTip(
    has_filter=False,
    total_tip_length=25.2,
    maximal_volume=110.0,
    tip_type=TipType.DITI
  )


def DiTi_500ul_Filter_SBS_MCA96_tip() -> TecanTip:
  """ Tip for DiTi_500ul_Filter_SBS_MCA96 """
  return TecanTip(
    has_filter=False,
    total_tip_length=54.0,
    maximal_volume=405.0,
    tip_type=TipType.DITI
  )


def DiTi_500ul_SBS_MCA96_tip() -> TecanTip:
  """ Tip for DiTi_500ul_SBS_MCA96 """
  return TecanTip(
    has_filter=False,
    total_tip_length=54.0,
    maximal_volume=502.0,
    tip_type=TipType.DITI
  )


def DiTi_Nested_Waste_MCA384_tip() -> TecanTip:
  """ Tip for DiTi_Nested_Waste_MCA384 """
  print("WARNING: total_tip_length <= 0.")
  print("Please get in touch at https://forums.pylabrobot.org/c/pylabrobot/23")
  return TecanTip(
    has_filter=False,
    total_tip_length=0.0,
    maximal_volume=0.0,
    tip_type=TipType.DITI
  )


def DiTi_1000ul_SBS_LiHa_tip() -> TecanTip:
  """ Tip for DiTi_1000ul_SBS_LiHa """
  return TecanTip(
    has_filter=False,
    total_tip_length=32.6,
    maximal_volume=1100.0,
    tip_type=TipType.DITI
  )


def DiTi_200ul_SBS_LiHa_tip() -> TecanTip:
  """ Tip for DiTi_200ul_SBS_LiHa """
  print("WARNING: total_tip_length <= 0.")
  print("Please get in touch at https://forums.pylabrobot.org/c/pylabrobot/23")
  return TecanTip(
    has_filter=False,
    total_tip_length=-5.0,
    maximal_volume=220.0,
    tip_type=TipType.DITI
  )


def DiTi_50ul_SBS_LiHa_tip() -> TecanTip:
  """ Tip for DiTi_50ul_SBS_LiHa """
  print("WARNING: total_tip_length <= 0.")
  print("Please get in touch at https://forums.pylabrobot.org/c/pylabrobot/23")
  return TecanTip(
    has_filter=False,
    total_tip_length=-5.3,
    maximal_volume=55.0,
    tip_type=TipType.DITI
  )


def DiTi_5000ul_LiHa_tip() -> TecanTip:
  """ Tip for DiTi_5000ul_LiHa """
  return TecanTip(
    has_filter=False,
    total_tip_length=46.6,
    maximal_volume=5130.0,
    tip_type=TipType.DITI
  )


def DiTi_5000ul_Filter_LiHa_tip() -> TecanTip:
  """ Tip for DiTi_5000ul_Filter_LiHa """
  return TecanTip(
    has_filter=False,
    total_tip_length=46.6,
    maximal_volume=5130.0,
    tip_type=TipType.DITI
  )


def DiTi_125ul_Filter_MCA384_tip() -> TecanTip:
  """ Tip for DiTi_125ul_Filter_MCA384 """
  return TecanTip(
    has_filter=False,
    total_tip_length=47.3,
    maximal_volume=115.0,
    tip_type=TipType.DITI
  )


def DiTi_125ul_MCA384_tip() -> TecanTip:
  """ Tip for DiTi_125ul_MCA384 """
  return TecanTip(
    has_filter=False,
    total_tip_length=47.3,
    maximal_volume=129.0,
    tip_type=TipType.DITI
  )


def DiTi_15ul_Filter_MCA384_tip() -> TecanTip:
  """ Tip for DiTi_15ul_Filter_MCA384 """
  return TecanTip(
    has_filter=False,
    total_tip_length=28.6,
    maximal_volume=18.0,
    tip_type=TipType.DITI
  )


def DiTi_15ul_MCA384_tip() -> TecanTip:
  """ Tip for DiTi_15ul_MCA384 """
  return TecanTip(
    has_filter=False,
    total_tip_length=28.6,
    maximal_volume=19.0,
    tip_type=TipType.DITI
  )


def DiTi_50ul_Filter_MCA384_tip() -> TecanTip:
  """ Tip for DiTi_50ul_Filter_MCA384 """
  return TecanTip(
    has_filter=False,
    total_tip_length=41.4,
    maximal_volume=44.0,
    tip_type=TipType.DITI
  )


def DiTi_50ul_MCA384_tip() -> TecanTip:
  """ Tip for DiTi_50ul_MCA384 """
  return TecanTip(
    has_filter=False,
    total_tip_length=41.4,
    maximal_volume=53.0,
    tip_type=TipType.DITI
  )


def DiTi_1000ul_Filter_LiHa_tip() -> TecanTip:
  """ Tip for DiTi_1000ul_Filter_LiHa """
  return TecanTip(
    has_filter=False,
    total_tip_length=32.6,
    maximal_volume=1050.0,
    tip_type=TipType.DITI
  )


def DiTi_1000ul_LiHa_tip() -> TecanTip:
  """ Tip for DiTi_1000ul_LiHa """
  return TecanTip(
    has_filter=False,
    total_tip_length=32.6,
    maximal_volume=1100.0,
    tip_type=TipType.DITI
  )


def DiTi_10ul_Filter_LiHa_tip() -> TecanTip:
  """ Tip for DiTi_10ul_Filter_LiHa """
  print("WARNING: total_tip_length <= 0.")
  print("Please get in touch at https://forums.pylabrobot.org/c/pylabrobot/23")
  return TecanTip(
    has_filter=False,
    total_tip_length=-31.3,
    maximal_volume=12.0,
    tip_type=TipType.DITI
  )


def DiTi_10ul_LiHa_tip() -> TecanTip:
  """ Tip for DiTi_10ul_LiHa """
  print("WARNING: total_tip_length <= 0.")
  print("Please get in touch at https://forums.pylabrobot.org/c/pylabrobot/23")
  return TecanTip(
    has_filter=False,
    total_tip_length=-31.3,
    maximal_volume=23.0,
    tip_type=TipType.DITI
  )


def DiTi_200ul_Filter_LiHa_tip() -> TecanTip:
  """ Tip for DiTi_200ul_Filter_LiHa """
  print("WARNING: total_tip_length <= 0.")
  print("Please get in touch at https://forums.pylabrobot.org/c/pylabrobot/23")
  return TecanTip(
    has_filter=False,
    total_tip_length=-5.0,
    maximal_volume=210.0,
    tip_type=TipType.DITI
  )


def DiTi_200ul_LiHa_tip() -> TecanTip:
  """ Tip for DiTi_200ul_LiHa """
  print("WARNING: total_tip_length <= 0.")
  print("Please get in touch at https://forums.pylabrobot.org/c/pylabrobot/23")
  return TecanTip(
    has_filter=False,
    total_tip_length=-5.0,
    maximal_volume=220.0,
    tip_type=TipType.DITI
  )


def DiTi_50ul_Filter_LiHa_tip() -> TecanTip:
  """ Tip for DiTi_50ul_Filter_LiHa """
  print("WARNING: total_tip_length <= 0.")
  print("Please get in touch at https://forums.pylabrobot.org/c/pylabrobot/23")
  return TecanTip(
    has_filter=False,
    total_tip_length=-5.3,
    maximal_volume=55.0,
    tip_type=TipType.DITI
  )


def DiTi_50ul_LiHa_tip() -> TecanTip:
  """ Tip for DiTi_50ul_LiHa """
  print("WARNING: total_tip_length <= 0.")
  print("Please get in touch at https://forums.pylabrobot.org/c/pylabrobot/23")
  return TecanTip(
    has_filter=False,
    total_tip_length=-5.3,
    maximal_volume=55.0,
    tip_type=TipType.DITI
  )


def DiTi_350ul_Nested_LiHa_tip() -> TecanTip:
  """ Tip for DiTi_350ul_Nested_LiHa """
  print("WARNING: total_tip_length <= 0.")
  print("Please get in touch at https://forums.pylabrobot.org/c/pylabrobot/23")
  return TecanTip(
    has_filter=False,
    total_tip_length=-5.0,
    maximal_volume=390.0,
    tip_type=TipType.DITI
  )


def DiTi_10ul_Filter_LiHa_L_tip() -> TecanTip:
  """ Tip for DiTi_10ul_Filter_LiHa_L """
  print("WARNING: total_tip_length <= 0.")
  print("Please get in touch at https://forums.pylabrobot.org/c/pylabrobot/23")
  return TecanTip(
    has_filter=False,
    total_tip_length=-28.1,
    maximal_volume=12.0,
    tip_type=TipType.DITI
  )


def DiTi_10ul_Filter_Nested_LiHa_tip() -> TecanTip:
  """ Tip for DiTi_10ul_Filter_Nested_LiHa """
  print("WARNING: total_tip_length <= 0.")
  print("Please get in touch at https://forums.pylabrobot.org/c/pylabrobot/23")
  return TecanTip(
    has_filter=False,
    total_tip_length=-28.1,
    maximal_volume=12.0,
    tip_type=TipType.DITI
  )


def DiTi_10ul_LiHa_L_tip() -> TecanTip:
  """ Tip for DiTi_10ul_LiHa_L """
  print("WARNING: total_tip_length <= 0.")
  print("Please get in touch at https://forums.pylabrobot.org/c/pylabrobot/23")
  return TecanTip(
    has_filter=False,
    total_tip_length=-28.1,
    maximal_volume=23.0,
    tip_type=TipType.DITI
  )


def DiTi_10ul_Nested_LiHa_tip() -> TecanTip:
  """ Tip for DiTi_10ul_Nested_LiHa """
  print("WARNING: total_tip_length <= 0.")
  print("Please get in touch at https://forums.pylabrobot.org/c/pylabrobot/23")
  return TecanTip(
    has_filter=False,
    total_tip_length=-28.1,
    maximal_volume=23.0,
    tip_type=TipType.DITI
  )


def DiTi_10ul_SBS_Filter_LiHa_tip() -> TecanTip:
  """ Tip for DiTi_10ul_SBS_Filter_LiHa """
  print("WARNING: total_tip_length <= 0.")
  print("Please get in touch at https://forums.pylabrobot.org/c/pylabrobot/23")
  return TecanTip(
    has_filter=False,
    total_tip_length=-28.1,
    maximal_volume=12.0,
    tip_type=TipType.DITI
  )


def DiTi_10ul_SBS_LiHa_tip() -> TecanTip:
  """ Tip for DiTi_10ul_SBS_LiHa """
  print("WARNING: total_tip_length <= 0.")
  print("Please get in touch at https://forums.pylabrobot.org/c/pylabrobot/23")
  return TecanTip(
    has_filter=False,
    total_tip_length=-28.1,
    maximal_volume=23.0,
    tip_type=TipType.DITI
  )


def DiTi_1000ul_W_B_Filter_LiHa_tip() -> TecanTip:
  """ Tip for DiTi_1000ul_W_B_Filter_LiHa """
  return TecanTip(
    has_filter=False,
    total_tip_length=32.0,
    maximal_volume=995.0,
    tip_type=TipType.DITI
  )


def DiTi_1000ul_CL_Filter_LiHa_tip() -> TecanTip:
  """ Tip for DiTi_1000ul_CL_Filter_LiHa """
  return TecanTip(
    has_filter=False,
    total_tip_length=32.6,
    maximal_volume=1050.0,
    tip_type=TipType.DITI
  )


def DiTi_1000ul_CL_LiHa_tip() -> TecanTip:
  """ Tip for DiTi_1000ul_CL_LiHa """
  return TecanTip(
    has_filter=False,
    total_tip_length=32.6,
    maximal_volume=1100.0,
    tip_type=TipType.DITI
  )


def DiTi_200ul_CL_Filter_LiHa_tip() -> TecanTip:
  """ Tip for DiTi_200ul_CL_Filter_LiHa """
  print("WARNING: total_tip_length <= 0.")
  print("Please get in touch at https://forums.pylabrobot.org/c/pylabrobot/23")
  return TecanTip(
    has_filter=False,
    total_tip_length=-5.0,
    maximal_volume=210.0,
    tip_type=TipType.DITI
  )


def DiTi_200ul_CL_LiHa_tip() -> TecanTip:
  """ Tip for DiTi_200ul_CL_LiHa """
  print("WARNING: total_tip_length <= 0.")
  print("Please get in touch at https://forums.pylabrobot.org/c/pylabrobot/23")
  return TecanTip(
    has_filter=False,
    total_tip_length=-5.0,
    maximal_volume=220.0,
    tip_type=TipType.DITI
  )


def DiTi_50ul_CL_Filter_LiHa_tip() -> TecanTip:
  """ Tip for DiTi_50ul_CL_Filter_LiHa """
  print("WARNING: total_tip_length <= 0.")
  print("Please get in touch at https://forums.pylabrobot.org/c/pylabrobot/23")
  return TecanTip(
    has_filter=False,
    total_tip_length=-5.3,
    maximal_volume=55.0,
    tip_type=TipType.DITI
  )


def DiTi_50ul_CL_LiHa_tip() -> TecanTip:
  """ Tip for DiTi_50ul_CL_LiHa """
  print("WARNING: total_tip_length <= 0.")
  print("Please get in touch at https://forums.pylabrobot.org/c/pylabrobot/23")
  return TecanTip(
    has_filter=False,
    total_tip_length=-5.3,
    maximal_volume=60.0,
    tip_type=TipType.DITI
  )
