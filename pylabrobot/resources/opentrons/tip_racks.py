from pylabrobot.resources.opentrons.load import load_ot_tip_rack
from pylabrobot.resources.tip_rack import StandingTipRack


def eppendorf_96_tiprack_1000ul_eptips(name: str, with_tips=True) -> StandingTipRack:
  return load_ot_tip_rack(
    ot_name="eppendorf_96_tiprack_1000ul_eptips", plr_resource_name=name, with_tips=with_tips
  )


def tipone_96_tiprack_200ul(name: str, with_tips=True) -> StandingTipRack:
  return load_ot_tip_rack(
    ot_name="tipone_96_tiprack_200ul", plr_resource_name=name, with_tips=with_tips
  )


def opentrons_96_tiprack_300ul(name: str, with_tips=True) -> StandingTipRack:
  return load_ot_tip_rack(
    ot_name="opentrons_96_tiprack_300ul", plr_resource_name=name, with_tips=with_tips
  )


def opentrons_96_tiprack_10ul(name: str, with_tips=True) -> StandingTipRack:
  return load_ot_tip_rack(
    ot_name="opentrons_96_tiprack_10ul", plr_resource_name=name, with_tips=with_tips
  )


def opentrons_96_filtertiprack_10ul(name: str, with_tips=True) -> StandingTipRack:
  return load_ot_tip_rack(
    ot_name="opentrons_96_filtertiprack_10ul", plr_resource_name=name, with_tips=with_tips
  )


def geb_96_tiprack_10ul(name: str, with_tips=True) -> StandingTipRack:
  return load_ot_tip_rack(
    ot_name="geb_96_tiprack_10ul", plr_resource_name=name, with_tips=with_tips
  )


def opentrons_96_filtertiprack_200ul(name: str, with_tips=True) -> StandingTipRack:
  return load_ot_tip_rack(
    ot_name="opentrons_96_filtertiprack_200ul", plr_resource_name=name, with_tips=with_tips
  )


def eppendorf_96_tiprack_10ul_eptips(name: str, with_tips=True) -> StandingTipRack:
  return load_ot_tip_rack(
    ot_name="eppendorf_96_tiprack_10ul_eptips", plr_resource_name=name, with_tips=with_tips
  )


def opentrons_96_tiprack_1000ul(name: str, with_tips=True) -> StandingTipRack:
  return load_ot_tip_rack(
    ot_name="opentrons_96_tiprack_1000ul", plr_resource_name=name, with_tips=with_tips
  )


def opentrons_96_tiprack_20ul(name: str, with_tips=True) -> StandingTipRack:
  return load_ot_tip_rack(
    ot_name="opentrons_96_tiprack_20ul", plr_resource_name=name, with_tips=with_tips
  )


def opentrons_96_filtertiprack_1000ul(name: str, with_tips=True) -> StandingTipRack:
  return load_ot_tip_rack(
    ot_name="opentrons_96_filtertiprack_1000ul", plr_resource_name=name, with_tips=with_tips
  )


def opentrons_96_filtertiprack_20ul(name: str, with_tips=True) -> StandingTipRack:
  return load_ot_tip_rack(
    ot_name="opentrons_96_filtertiprack_20ul", plr_resource_name=name, with_tips=with_tips
  )


def geb_96_tiprack_1000ul(name: str, with_tips=True) -> StandingTipRack:
  return load_ot_tip_rack(
    ot_name="geb_96_tiprack_1000ul", plr_resource_name=name, with_tips=with_tips
  )
