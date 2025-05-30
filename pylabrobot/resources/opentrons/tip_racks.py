from typing import cast

from pylabrobot.resources.opentrons.load import (
  load_shared_opentrons_resource,
)
from pylabrobot.resources.tip_rack import TipRack


def eppendorf_96_tiprack_1000ul_eptips(name: str, with_tips=True) -> TipRack:
  tr = cast(
    TipRack,
    load_shared_opentrons_resource(
      definition="eppendorf_96_tiprack_1000ul_eptips",
      name=name,
      version=1,
    ),
  )
  if with_tips:
    tr.fill()
  else:
    tr.empty()
  return tr


def tipone_96_tiprack_200ul(name: str, with_tips=True) -> TipRack:
  tr = cast(
    TipRack,
    load_shared_opentrons_resource(definition="tipone_96_tiprack_200ul", name=name, version=1),
  )
  if with_tips:
    tr.fill()
  else:
    tr.empty()
  return tr


def opentrons_96_tiprack_300ul(name: str, with_tips=True) -> TipRack:
  tr = cast(
    TipRack,
    load_shared_opentrons_resource(definition="opentrons_96_tiprack_300ul", name=name, version=1),
  )
  if with_tips:
    tr.fill()
  else:
    tr.empty()
  return tr


def opentrons_96_tiprack_10ul(name: str, with_tips=True) -> TipRack:
  tr = cast(
    TipRack,
    load_shared_opentrons_resource(definition="opentrons_96_tiprack_10ul", name=name, version=1),
  )
  if with_tips:
    tr.fill()
  else:
    tr.empty()
  return tr


def opentrons_96_filtertiprack_10ul(name: str, with_tips=True) -> TipRack:
  tr = cast(
    TipRack,
    load_shared_opentrons_resource(
      definition="opentrons_96_filtertiprack_10ul",
      name=name,
      version=1,
    ),
  )
  if with_tips:
    tr.fill()
  else:
    tr.empty()
  return tr


def geb_96_tiprack_10ul(name: str, with_tips=True) -> TipRack:
  tr = cast(
    TipRack,
    load_shared_opentrons_resource(definition="geb_96_tiprack_10ul", name=name, version=1),
  )
  if with_tips:
    tr.fill()
  else:
    tr.empty()
  return tr


def opentrons_96_filtertiprack_200ul(name: str, with_tips=True) -> TipRack:
  tr = cast(
    TipRack,
    load_shared_opentrons_resource(
      definition="opentrons_96_filtertiprack_200ul",
      name=name,
      version=1,
    ),
  )
  if with_tips:
    tr.fill()
  else:
    tr.empty()
  return tr


def eppendorf_96_tiprack_10ul_eptips(name: str, with_tips=True) -> TipRack:
  tr = cast(
    TipRack,
    load_shared_opentrons_resource(
      definition="eppendorf_96_tiprack_10ul_eptips",
      name=name,
      version=1,
    ),
  )
  if with_tips:
    tr.fill()
  else:
    tr.empty()
  return tr


def opentrons_96_tiprack_1000ul(name: str, with_tips=True) -> TipRack:
  tr = cast(
    TipRack,
    load_shared_opentrons_resource(definition="opentrons_96_tiprack_1000ul", name=name, version=1),
  )
  if with_tips:
    tr.fill()
  else:
    tr.empty()
  return tr


def opentrons_96_tiprack_20ul(name: str, with_tips=True) -> TipRack:
  tr = cast(
    TipRack,
    load_shared_opentrons_resource(definition="opentrons_96_tiprack_20ul", name=name, version=1),
  )
  if with_tips:
    tr.fill()
  else:
    tr.empty()
  return tr


def opentrons_96_filtertiprack_1000ul(name: str, with_tips=True) -> TipRack:
  tr = cast(
    TipRack,
    load_shared_opentrons_resource(
      definition="opentrons_96_filtertiprack_1000ul",
      name=name,
      version=1,
    ),
  )
  if with_tips:
    tr.fill()
  else:
    tr.empty()
  return tr


def opentrons_96_filtertiprack_20ul(name: str, with_tips=True) -> TipRack:
  tr = cast(
    TipRack,
    load_shared_opentrons_resource(
      definition="opentrons_96_filtertiprack_20ul",
      name=name,
      version=1,
    ),
  )
  if with_tips:
    tr.fill()
  else:
    tr.empty()
  return tr


def geb_96_tiprack_1000ul(name: str, with_tips=True) -> TipRack:
  tr = cast(
    TipRack,
    load_shared_opentrons_resource(definition="geb_96_tiprack_1000ul", name=name, version=1),
  )
  if with_tips:
    tr.fill()
  else:
    tr.empty()
  return tr
