from typing import cast

from pylabrobot.resources.opentrons.load import load_shared_opentrons_resource
from pylabrobot.resources.plate import Plate

def agilent_1_reservoir_290ml(name: str) -> Plate:
  return cast(Plate, load_shared_opentrons_resource(
    definition="agilent_1_reservoir_290ml",
    name=name,
    version=1
  ))

def axygen_1_reservoir_90ml(name: str) -> Plate:
  return cast(Plate, load_shared_opentrons_resource(
    definition="axygen_1_reservoir_90ml",
    name=name,
    version=1
  ))

def nest_12_reservoir_15ml(name: str) -> Plate:
  return cast(Plate, load_shared_opentrons_resource(
    definition="nest_12_reservoir_15ml",
    name=name,
    version=1
  ))

def nest_1_reservoir_195ml(name: str) -> Plate:
  return cast(Plate, load_shared_opentrons_resource(
    definition="nest_1_reservoir_195ml",
    name=name,
    version=1
  ))

def nest_1_reservoir_290ml(name: str) -> Plate:
  return cast(Plate, load_shared_opentrons_resource(
    definition="nest_1_reservoir_290ml",
    name=name,
    version=1
  ))

def usascientific_12_reservoir_22ml(name: str) -> Plate:
  return cast(Plate, load_shared_opentrons_resource(
    definition="usascientific_12_reservoir_22ml",
    name=name,
    version=1
  ))
