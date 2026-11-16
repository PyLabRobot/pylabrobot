"""Opentrons Flex tip racks loaded from Opentrons labware definitions."""

from pylabrobot.resources.opentrons.load import load_ot_tip_rack
from pylabrobot.resources.tip_rack import StandingTipRack


def flex_96_tiprack_50ul(
  name: str = "flex_96_tiprack_50ul", with_tips: bool = True
) -> StandingTipRack:
  """Opentrons Flex 96 Tip Rack 50 µL."""
  return load_ot_tip_rack(
    ot_name="opentrons_flex_96_tiprack_50ul", plr_resource_name=name, with_tips=with_tips
  )


def flex_96_filtertiprack_50ul(
  name: str = "flex_96_filtertiprack_50ul", with_tips: bool = True
) -> StandingTipRack:
  """Opentrons Flex 96 Filter Tip Rack 50 µL."""
  return load_ot_tip_rack(
    ot_name="opentrons_flex_96_filtertiprack_50ul", plr_resource_name=name, with_tips=with_tips
  )


def flex_96_tiprack_200ul(
  name: str = "flex_96_tiprack_200ul", with_tips: bool = True
) -> StandingTipRack:
  """Opentrons Flex 96 Tip Rack 200 µL."""
  return load_ot_tip_rack(
    ot_name="opentrons_flex_96_tiprack_200ul", plr_resource_name=name, with_tips=with_tips
  )


def flex_96_tiprack_1000ul(
  name: str = "flex_96_tiprack_1000ul", with_tips: bool = True
) -> StandingTipRack:
  """Opentrons Flex 96 Tip Rack 1000 µL."""
  return load_ot_tip_rack(
    ot_name="opentrons_flex_96_tiprack_1000ul", plr_resource_name=name, with_tips=with_tips
  )
