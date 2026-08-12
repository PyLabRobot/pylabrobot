"""Tests for custom Opentrons labware definition building and uploading.

Builder-level tests pin the definition content produced from PLR geometry
(dimensions, well positions, the PLR front-left vs Opentrons back-left y-flip,
grip height). Flex-level tests drive ``OpentronsFlex._ensure_labware_loaded``
with an injected ``ChatterboxTransport`` and assert labware without an
official Opentrons definition is uploaded once and then loaded by the
uploaded definition's namespace/loadName/version, while official-name labware
keeps loading with zero uploads.
"""

import asyncio
import unittest
from typing import Tuple

from pylabrobot.opentrons.flex import OpentronsFlex
from pylabrobot.opentrons.labware_definitions import (
  build_container_definition,
  build_movable_labware_definition,
  build_plate_definition,
  build_tip_rack_definition,
)
from pylabrobot.opentrons.robot import OpentronsError
from pylabrobot.opentrons.transport import ChatterboxTransport
from pylabrobot.resources import Plate, Resource, TipRack, TipSpot, Trough, Well
from pylabrobot.resources.opentrons.flex_deck import FlexDeck
from pylabrobot.resources.tip import Tip
from pylabrobot.resources.utils import create_ordered_items_2d


def _plate(name: str = "Black Plate-1") -> Plate:
  """A 2x2-well plate with hand-picked geometry so expected numbers are exact."""
  return Plate(
    name=name,
    size_x=127.0,
    size_y=80.0,
    size_z=14.0,
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=2,
      num_items_y=2,
      dx=10.0,
      dy=8.0,
      dz=1.0,
      item_dx=9.0,
      item_dy=9.0,
      size_x=6.0,
      size_y=6.0,
      size_z=10.0,
      max_volume=360.0,
    ),
  )


def _tip_rack(name: str = "hamilton tips 300") -> TipRack:
  """A 2x2-spot tip rack with hand-picked geometry and a pinned prototype tip."""

  def make_tip(name: str) -> Tip:
    return Tip(
      has_filter=False,
      total_tip_length=50.0,
      maximal_volume=200.0,
      fitting_depth=8.0,
      name=name,
    )

  return TipRack(
    name=name,
    size_x=120.0,
    size_y=82.0,
    size_z=90.0,
    ordered_items=create_ordered_items_2d(
      TipSpot,
      num_items_x=2,
      num_items_y=2,
      dx=10.0,
      dy=8.0,
      dz=0.0,
      item_dx=9.0,
      item_dy=9.0,
      size_x=5.0,
      size_y=5.0,
      make_tip=make_tip,
    ),
  )


def _trough(name: str = "hamilton trough") -> Trough:
  return Trough(name=name, size_x=120.0, size_y=80.0, size_z=40.0, max_volume=290000.0)


class TestBuildPlateDefinition(unittest.TestCase):
  """build_plate_definition maps PLR plate geometry into a wellPlate definition."""

  def test_identity_and_dimensions(self):
    definition = build_plate_definition(_plate())
    self.assertEqual(definition["namespace"], "pylabrobot")
    self.assertEqual(definition["version"], 1)
    self.assertEqual(definition["schemaVersion"], 2)
    self.assertEqual(definition["metadata"]["displayCategory"], "wellPlate")
    self.assertEqual(definition["metadata"]["displayName"], "Black Plate-1")
    self.assertEqual(definition["parameters"]["loadName"], "black_plate_1")
    self.assertFalse(definition["parameters"]["isTiprack"])
    self.assertEqual(
      definition["dimensions"],
      {"xDimension": 127.0, "yDimension": 80.0, "zDimension": 14.0},
    )

  def test_ordering_is_column_major(self):
    definition = build_plate_definition(_plate())
    self.assertEqual(definition["ordering"], [["A1", "B1"], ["A2", "B2"]])

  def test_corner_offset_y_flip(self):
    # PLR anchors at the slot's front-left, Opentrons at the back-left: an
    # 80 mm-deep plate in an 86 mm-deep slot sits 6 mm toward the back.
    definition = build_plate_definition(_plate())
    self.assertEqual(definition["cornerOffsetFromSlot"], {"x": 0, "y": 6.0, "z": 0})

  def test_well_geometry_carries_depth_volume_and_centers(self):
    definition = build_plate_definition(_plate())
    # A1 (back-left well): origin (10, 17, 1), 6 mm square, so center (13, 20).
    self.assertEqual(
      definition["wells"]["A1"],
      {
        "depth": 10.0,
        "x": 13.0,
        "y": 20.0,
        "z": 1.0,
        "shape": "circular",
        "diameter": 6.0,
        "totalLiquidVolume": 360.0,
      },
    )
    # B1 is one 9 mm pitch toward the front: center y = 8 + 3 = 11.
    self.assertEqual(definition["wells"]["B1"]["y"], 11.0)
    self.assertEqual(definition["groups"][0]["wells"], ["A1", "B1", "A2", "B2"])

  def test_grip_height_from_grip_distance(self):
    self.assertNotIn("gripHeightFromLabwareBottom", build_plate_definition(_plate()))
    definition = build_plate_definition(_plate(), grip_distance_from_top=4.0)
    self.assertEqual(definition["gripHeightFromLabwareBottom"], 10.0)  # 14 - 4
    clamped = build_plate_definition(_plate(), grip_distance_from_top=20.0)
    self.assertEqual(clamped["gripHeightFromLabwareBottom"], 0.0)


class TestBuildTipRackDefinition(unittest.TestCase):
  """build_tip_rack_definition maps rack geometry and the prototype tip."""

  def test_tip_parameters_come_from_prototype_tip(self):
    definition = build_tip_rack_definition(_tip_rack())
    self.assertEqual(definition["metadata"]["displayCategory"], "tipRack")
    self.assertEqual(definition["parameters"]["format"], "96Standard")
    self.assertTrue(definition["parameters"]["isTiprack"])
    self.assertEqual(definition["parameters"]["tipLength"], 50.0)
    self.assertEqual(definition["parameters"]["tipOverlap"], 8.0)
    self.assertEqual(definition["parameters"]["loadName"], "hamilton_tips_300")

  def test_spot_geometry_and_y_flip(self):
    definition = build_tip_rack_definition(_tip_rack())
    self.assertEqual(definition["cornerOffsetFromSlot"], {"x": 0, "y": 4.0, "z": 0})  # 86 - 82
    self.assertEqual(definition["ordering"], [["A1", "B1"], ["A2", "B2"]])
    # A1 spot origin (10, 17, 0), 5 mm square: center (12.5, 19.5).
    self.assertEqual(
      definition["wells"]["A1"],
      {
        "depth": 0,
        "x": 12.5,
        "y": 19.5,
        "z": 0.0,
        "shape": "circular",
        "diameter": 5.0,
        "totalLiquidVolume": 200.0,
      },
    )

  def test_grip_height_from_grip_distance(self):
    self.assertNotIn("gripHeightFromLabwareBottom", build_tip_rack_definition(_tip_rack()))
    definition = build_tip_rack_definition(_tip_rack(), grip_distance_from_top=10.0)
    self.assertEqual(definition["gripHeightFromLabwareBottom"], 80.0)  # 90 - 10


class TestBuildContainerDefinition(unittest.TestCase):
  """build_container_definition maps a container to a single-cavity reservoir."""

  def test_single_a1_cavity_spans_the_container(self):
    definition = build_container_definition(_trough())
    self.assertEqual(definition["namespace"], "pylabrobot")
    self.assertEqual(definition["metadata"]["displayCategory"], "reservoir")
    self.assertEqual(definition["parameters"]["loadName"], "hamilton_trough")
    self.assertEqual(definition["ordering"], [["A1"]])
    self.assertEqual(definition["cornerOffsetFromSlot"], {"x": 0, "y": 6.0, "z": 0})  # 86 - 80
    self.assertEqual(
      definition["dimensions"],
      {"xDimension": 120.0, "yDimension": 80.0, "zDimension": 40.0},
    )
    self.assertEqual(
      definition["wells"],
      {
        "A1": {
          "depth": 40.0,
          "x": 60.0,
          "y": 40.0,
          "z": 0,
          "shape": "rectangular",
          "xDimension": 120.0,
          "yDimension": 80.0,
          "totalLiquidVolume": 290000.0,
        }
      },
    )
    self.assertEqual(definition["groups"][0]["wells"], ["A1"])


class TestBuildMovableLabwareDefinition(unittest.TestCase):
  """build_movable_labware_definition builds the minimal gripper-move stub."""

  def test_stub_has_fake_well_and_grip_geometry(self):
    resource = Resource(name="lid stack", size_x=100.0, size_y=90.0, size_z=20.0)
    definition = build_movable_labware_definition(resource, grip_distance_from_top=5.0)
    self.assertEqual(definition["namespace"], "pylabrobot")
    self.assertEqual(definition["parameters"]["loadName"], "lid_stack")
    self.assertEqual(definition["ordering"], [["A1"]])
    self.assertEqual(definition["cornerOffsetFromSlot"], {"x": 0, "y": 0, "z": 0})
    self.assertEqual(
      definition["wells"]["A1"],
      {
        "depth": 0,
        "x": 50.0,
        "y": 45.0,
        "z": 0,
        "shape": "circular",
        "diameter": 5,
        "totalLiquidVolume": 0,
      },
    )
    self.assertEqual(definition["gripHeightFromLabwareBottom"], 15.0)  # 20 - 5
    self.assertEqual(
      definition["gripperOffsets"],
      {
        "default": {
          "pickUpOffset": {"x": 0, "y": 0, "z": 0},
          "dropOffset": {"x": 0, "y": 0, "z": 0},
        }
      },
    )

  def test_grip_height_clamped_at_labware_bottom(self):
    resource = Resource(name="shim", size_x=10.0, size_y=10.0, size_z=3.0)
    definition = build_movable_labware_definition(resource, grip_distance_from_top=7.0)
    self.assertEqual(definition["gripHeightFromLabwareBottom"], 0.0)


def _flex_with_transport() -> Tuple[OpentronsFlex, ChatterboxTransport]:
  transport = ChatterboxTransport(pipette=("p1000_single_flex", 1, 1.0, 1000.0), mount="right")
  flex = OpentronsFlex(deck=FlexDeck(), host="localhost", transport=transport)
  return flex, transport


def _load_labware_commands(transport: ChatterboxTransport) -> list:
  return [c for c in transport.commands if c["commandType"] == "loadLabware"]


class TestCustomLabwareLoadFlow(unittest.TestCase):
  """_ensure_labware_loaded uploads a definition for labware with no official name."""

  def test_plate_without_official_name_uploads_then_loads(self):
    flex, transport = _flex_with_transport()
    asyncio.run(flex.setup())
    try:
      plate = _plate()
      flex.deck.assign_child_at_slot(plate, "C1")
      asyncio.run(flex._ensure_labware_loaded(plate))

      self.assertEqual(len(transport.labware_definitions), 1)
      definition = transport.labware_definitions[0]
      load_cmds = _load_labware_commands(transport)
      self.assertEqual(len(load_cmds), 1)
      params = load_cmds[0]["params"]
      # loadLabware must reference exactly the uploaded definition's identity.
      self.assertEqual(params["namespace"], definition["namespace"])
      self.assertEqual(params["loadName"], definition["parameters"]["loadName"])
      self.assertEqual(params["version"], definition["version"])
      self.assertEqual(params["namespace"], "pylabrobot")
      self.assertEqual(params["loadName"], "black_plate_1")
      self.assertEqual(params["version"], 1)
      self.assertEqual(params["location"], {"slotName": "C1"})
    finally:
      asyncio.run(flex.stop())

  def test_second_use_hits_cache_no_second_upload_or_load(self):
    flex, transport = _flex_with_transport()
    asyncio.run(flex.setup())
    try:
      plate = _plate()
      flex.deck.assign_child_at_slot(plate, "C1")
      first = asyncio.run(flex._ensure_labware_loaded(plate))
      second = asyncio.run(flex._ensure_labware_loaded(plate))

      self.assertEqual(first, second)
      self.assertEqual(len(transport.labware_definitions), 1)
      self.assertEqual(len(_load_labware_commands(transport)), 1)
    finally:
      asyncio.run(flex.stop())

  def test_reload_after_off_deck_reuses_uploaded_definition(self):
    flex, transport = _flex_with_transport()
    asyncio.run(flex.setup())
    try:
      plate = _plate()
      flex.deck.assign_child_at_slot(plate, "C1")
      asyncio.run(flex._ensure_labware_loaded(plate))
      asyncio.run(flex.labware_moved_off_deck(plate))
      flex.deck.assign_child_at_slot(plate, "D2")
      asyncio.run(flex._ensure_labware_loaded(plate))

      # A fresh loadLabware at the new slot, but the definition uploads once per run.
      self.assertEqual(len(transport.labware_definitions), 1)
      load_cmds = _load_labware_commands(transport)
      self.assertEqual(len(load_cmds), 2)
      self.assertEqual(load_cmds[1]["params"]["location"], {"slotName": "D2"})
    finally:
      asyncio.run(flex.stop())

  def test_official_name_labware_loads_with_zero_uploads(self):
    flex, transport = _flex_with_transport()
    asyncio.run(flex.setup())
    try:
      plate = _plate()
      plate.ot_load_name = "corning_96_wellplate_360ul_flat"  # type: ignore[attr-defined]
      flex.deck.assign_child_at_slot(plate, "C1")
      asyncio.run(flex._ensure_labware_loaded(plate))

      self.assertEqual(len(transport.labware_definitions), 0)
      load_cmds = _load_labware_commands(transport)
      self.assertEqual(len(load_cmds), 1)
      params = load_cmds[0]["params"]
      self.assertEqual(params["namespace"], "opentrons")
      self.assertEqual(params["loadName"], "corning_96_wellplate_360ul_flat")
      self.assertEqual(params["version"], 1)
    finally:
      asyncio.run(flex.stop())

  def test_container_uploads_single_cavity_definition(self):
    flex, transport = _flex_with_transport()
    asyncio.run(flex.setup())
    try:
      trough = _trough()
      flex.deck.assign_child_at_slot(trough, "B1")
      asyncio.run(flex._ensure_labware_loaded(trough))

      self.assertEqual(len(transport.labware_definitions), 1)
      definition = transport.labware_definitions[0]
      self.assertEqual(list(definition["wells"]), ["A1"])
      params = _load_labware_commands(transport)[0]["params"]
      self.assertEqual(params["namespace"], "pylabrobot")
      self.assertEqual(params["loadName"], "hamilton_trough")
    finally:
      asyncio.run(flex.stop())

  def test_tip_rack_without_official_name_uploads_tiprack_definition(self):
    flex, transport = _flex_with_transport()
    asyncio.run(flex.setup())
    try:
      rack = _tip_rack()
      flex.deck.assign_child_at_slot(rack, "C1")
      asyncio.run(flex._ensure_labware_loaded(rack))

      self.assertEqual(len(transport.labware_definitions), 1)
      self.assertTrue(transport.labware_definitions[0]["parameters"]["isTiprack"])
      params = _load_labware_commands(transport)[0]["params"]
      self.assertEqual(params["loadName"], "hamilton_tips_300")
    finally:
      asyncio.run(flex.stop())

  def test_unbuildable_resource_still_raises_loudly(self):
    flex, transport = _flex_with_transport()
    asyncio.run(flex.setup())
    try:
      widget = Resource(name="widget", size_x=100.0, size_y=90.0, size_z=20.0)
      flex.deck.assign_child_at_slot(widget, "C1")
      with self.assertRaises(OpentronsError):
        asyncio.run(flex._ensure_labware_loaded(widget))
      self.assertEqual(len(transport.labware_definitions), 0)
      self.assertEqual(len(_load_labware_commands(transport)), 0)
    finally:
      asyncio.run(flex.stop())
