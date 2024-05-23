from typing import cast

from pylabrobot.resources.opentrons.load import load_shared_opentrons_resource
from pylabrobot.resources.plate import Plate


def corning_384_wellplate_112ul_flat(name: str) -> Plate:
  return cast(Plate, load_shared_opentrons_resource(
    definition="corning_384_wellplate_112ul_flat",
    name=name,
    version=1
  ))


def corning_96_wellplate_360ul_flat(name: str) -> Plate:
  return cast(Plate, load_shared_opentrons_resource(
    definition="corning_96_wellplate_360ul_flat",
    name=name,
    version=1
  ))


def nest_96_wellplate_2ml_deep(name: str) -> Plate:
  return cast(Plate, load_shared_opentrons_resource(
    definition="nest_96_wellplate_2ml_deep",
    name=name,
    version=1
  ))


def nest_96_wellplate_100ul_pcr_full_skirt(name: str) -> Plate:
  return cast(Plate, load_shared_opentrons_resource(
    definition="nest_96_wellplate_100ul_pcr_full_skirt",
    name=name,
    version=1
  ))


def appliedbiosystemsmicroamp_384_wellplate_40ul(name: str) -> Plate:
  return cast(Plate, load_shared_opentrons_resource(
    definition="appliedbiosystemsmicroamp_384_wellplate_40ul",
    name=name,
    version=1
  ))


def thermoscientificnunc_96_wellplate_2000ul(name: str) -> Plate:
  return cast(Plate, load_shared_opentrons_resource(
    definition="thermoscientificnunc_96_wellplate_2000ul",
    name=name,
    version=1
  ))


def usascientific_96_wellplate_2point4ml_deep(name: str) -> Plate:
  return cast(Plate, load_shared_opentrons_resource(
    definition="usascientific_96_wellplate_2.4ml_deep",
    name=name,
    version=1
  ))


def thermoscientificnunc_96_wellplate_1300ul(name: str) -> Plate:
  return cast(Plate, load_shared_opentrons_resource(
    definition="thermoscientificnunc_96_wellplate_1300ul",
    name=name,
    version=1
  ))


def nest_96_wellplate_200ul_flat(name: str) -> Plate:
  return cast(Plate, load_shared_opentrons_resource(
    definition="nest_96_wellplate_200ul_flat",
    name=name,
    version=1
  ))


def corning_6_wellplate_16point8ml_flat(name: str) -> Plate:
  return cast(Plate, load_shared_opentrons_resource(
    definition="corning_6_wellplate_16.8ml_flat",
    name=name,
    version=1
  ))


def corning_24_wellplate_3point4ml_flat(name: str) -> Plate:
  return cast(Plate, load_shared_opentrons_resource(
    definition="corning_24_wellplate_3.4ml_flat",
    name=name,
    version=1
  ))


def corning_12_wellplate_6point9ml_flat(name: str) -> Plate:
  return cast(Plate, load_shared_opentrons_resource(
    definition="corning_12_wellplate_6.9ml_flat",
    name=name,
    version=1
  ))


def biorad_96_wellplate_200ul_pcr(name: str) -> Plate:
  return cast(Plate, load_shared_opentrons_resource(
    definition="biorad_96_wellplate_200ul_pcr",
    name=name,
    version=1
  ))


def corning_48_wellplate_1point6ml_flat(name: str) -> Plate:
  return cast(Plate, load_shared_opentrons_resource(
    definition="corning_48_wellplate_1.6ml_flat",
    name=name,
    version=1
  ))


def biorad_384_wellplate_50ul(name: str) -> Plate:
  return cast(Plate, load_shared_opentrons_resource(
    definition="biorad_384_wellplate_50ul",
    name=name,
    version=1
  ))
