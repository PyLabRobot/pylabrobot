from typing import cast

from pylabrobot.resources.opentrons.load import load_shared_opentrons_resource
from pylabrobot.resources.tip_rack import TipRack


def eppendorf_96_tiprack_1000ul_eptips(name: str) -> TipRack:
  return cast(TipRack, load_shared_opentrons_resource(
    definition="eppendorf_96_tiprack_1000ul_eptips",
    name=name,
    version=1
  ))


def tipone_96_tiprack_200ul(name: str) -> TipRack:
  return cast(TipRack, load_shared_opentrons_resource(
    definition="tipone_96_tiprack_200ul",
    name=name,
    version=1
  ))


def opentrons_96_tiprack_300ul(name: str) -> TipRack:
  return cast(TipRack, load_shared_opentrons_resource(
    definition="opentrons_96_tiprack_300ul",
    name=name,
    version=1
  ))


def opentrons_96_tiprack_10ul(name: str) -> TipRack:
  return cast(TipRack, load_shared_opentrons_resource(
    definition="opentrons_96_tiprack_10ul",
    name=name,
    version=1
  ))


def opentrons_96_filtertiprack_10ul(name: str) -> TipRack:
  return cast(TipRack, load_shared_opentrons_resource(
    definition="opentrons_96_filtertiprack_10ul",
    name=name,
    version=1
  ))


def geb_96_tiprack_10ul(name: str) -> TipRack:
  return cast(TipRack, load_shared_opentrons_resource(
    definition="geb_96_tiprack_10ul",
    name=name,
    version=1
  ))


def opentrons_96_filtertiprack_200ul(name: str) -> TipRack:
  return cast(TipRack, load_shared_opentrons_resource(
    definition="opentrons_96_filtertiprack_200ul",
    name=name,
    version=1
  ))


def eppendorf_96_tiprack_10ul_eptips(name: str) -> TipRack:
  return cast(TipRack, load_shared_opentrons_resource(
    definition="eppendorf_96_tiprack_10ul_eptips",
    name=name,
    version=1
  ))


def opentrons_96_tiprack_1000ul(name: str) -> TipRack:
  return cast(TipRack, load_shared_opentrons_resource(
    definition="opentrons_96_tiprack_1000ul",
    name=name,
    version=1
  ))


def opentrons_96_tiprack_20ul(name: str) -> TipRack:
  return cast(TipRack, load_shared_opentrons_resource(
    definition="opentrons_96_tiprack_20ul",
    name=name,
    version=1
  ))


def opentrons_96_filtertiprack_1000ul(name: str) -> TipRack:
  return cast(TipRack, load_shared_opentrons_resource(
    definition="opentrons_96_filtertiprack_1000ul",
    name=name,
    version=1
  ))


def opentrons_96_filtertiprack_20ul(name: str) -> TipRack:
  return cast(TipRack, load_shared_opentrons_resource(
    definition="opentrons_96_filtertiprack_20ul",
    name=name,
    version=1
  ))


def geb_96_tiprack_1000ul(name: str) -> TipRack:
  return cast(TipRack, load_shared_opentrons_resource(
    definition="geb_96_tiprack_1000ul",
    name=name,
    version=1
  ))


