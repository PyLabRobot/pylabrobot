from typing import cast

from pylabrobot.resources.opentrons.load import load_shared_opentrons_resource
from pylabrobot.resources.tube_rack import TubeRack


def opentrons_24_tuberack_eppendorf_2ml_safelock_snapcap(name: str) -> TubeRack:
  return cast(TubeRack, load_shared_opentrons_resource(
    definition="opentrons_24_tuberack_eppendorf_2ml_safelock_snapcap",
    name=name,
    version=1
  ))


def opentrons_24_tuberack_eppendorf_2ml_safelock_snapcap_acrylic(name: str) -> TubeRack:
  return cast(TubeRack, load_shared_opentrons_resource(
    definition="opentrons_24_tuberack_eppendorf_2ml_safelock_snapcap_acrylic",
    name=name,
    version=1
  ))


def opentrons_6_tuberack_falcon_50ml_conical(name: str) -> TubeRack:
  return cast(TubeRack, load_shared_opentrons_resource(
    definition="opentrons_6_tuberack_falcon_50ml_conical",
    name=name,
    version=1
  ))


def opentrons_15_tuberack_nest_15ml_conical(name: str) -> TubeRack:
  return cast(TubeRack, load_shared_opentrons_resource(
    definition="opentrons_15_tuberack_nest_15ml_conical",
    name=name,
    version=1
  ))


def opentrons_24_tuberack_nest_2ml_screwcap(name: str) -> TubeRack:
  return cast(TubeRack, load_shared_opentrons_resource(
    definition="opentrons_24_tuberack_nest_2ml_screwcap",
    name=name,
    version=1
  ))


def opentrons_24_tuberack_generic_0point75ml_snapcap_acrylic(name: str) -> TubeRack:
  return cast(TubeRack, load_shared_opentrons_resource(
    definition="opentrons_24_tuberack_generic_0.75ml_snapcap_acrylic",
    name=name,
    version=1
  ))


def opentrons_10_tuberack_nest_4x50ml_6x15ml_conical(name: str) -> TubeRack:
  return cast(TubeRack, load_shared_opentrons_resource(
    definition="opentrons_10_tuberack_nest_4x50ml_6x15ml_conical",
    name=name,
    version=1
  ))


def opentrons_10_tuberack_falcon_4x50ml_6x15ml_conical_acrylic(name: str) -> TubeRack:
  return cast(TubeRack, load_shared_opentrons_resource(
    definition="opentrons_10_tuberack_falcon_4x50ml_6x15ml_conical_acrylic",
    name=name,
    version=1
  ))


def opentrons_24_tuberack_nest_1point5ml_screwcap(name: str) -> TubeRack:
  return cast(TubeRack, load_shared_opentrons_resource(
    definition="opentrons_24_tuberack_nest_1.5ml_screwcap",
    name=name,
    version=1
  ))


def opentrons_24_tuberack_nest_1point5ml_snapcap(name: str) -> TubeRack:
  return cast(TubeRack, load_shared_opentrons_resource(
    definition="opentrons_24_tuberack_nest_1.5ml_snapcap",
    name=name,
    version=1
  ))


def opentrons_10_tuberack_falcon_4x50ml_6x15ml_conical(name: str) -> TubeRack:
  return cast(TubeRack, load_shared_opentrons_resource(
    definition="opentrons_10_tuberack_falcon_4x50ml_6x15ml_conical",
    name=name,
    version=1
  ))


def opentrons_24_tuberack_nest_2ml_snapcap(name: str) -> TubeRack:
  return cast(TubeRack, load_shared_opentrons_resource(
    definition="opentrons_24_tuberack_nest_2ml_snapcap",
    name=name,
    version=1
  ))


def opentrons_24_tuberack_nest_0point5ml_screwcap(name: str) -> TubeRack:
  return cast(TubeRack, load_shared_opentrons_resource(
    definition="opentrons_24_tuberack_nest_0.5ml_screwcap",
    name=name,
    version=1
  ))


def opentrons_24_tuberack_eppendorf_1point5ml_safelock_snapcap(name: str) -> TubeRack:
  return cast(TubeRack, load_shared_opentrons_resource(
    definition="opentrons_24_tuberack_eppendorf_1.5ml_safelock_snapcap",
    name=name,
    version=1
  ))


def opentrons_6_tuberack_nest_50ml_conical(name: str) -> TubeRack:
  return cast(TubeRack, load_shared_opentrons_resource(
    definition="opentrons_6_tuberack_nest_50ml_conical",
    name=name,
    version=1
  ))


def opentrons_15_tuberack_falcon_15ml_conical(name: str) -> TubeRack:
  return cast(TubeRack, load_shared_opentrons_resource(
    definition="opentrons_15_tuberack_falcon_15ml_conical",
    name=name,
    version=1
  ))


def opentrons_24_tuberack_generic_2ml_screwcap(name: str) -> TubeRack:
  return cast(TubeRack, load_shared_opentrons_resource(
    definition="opentrons_24_tuberack_generic_2ml_screwcap",
    name=name,
    version=1
  ))

