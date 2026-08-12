"""Tests for custom Opentrons labware definition building and uploading.

Builder-level tests pin the definition content produced from PLR geometry
(dimensions, well positions and shapes, the shared front-left slot anchoring,
grip height). Flex-level tests drive ``OpentronsFlex._ensure_labware_loaded``
with an injected ``ChatterboxTransport`` and assert labware without an
official Opentrons definition is uploaded and then loaded by the uploaded
definition's namespace/loadName/version, that the run-scoped caches reset
with the run, and that official-name labware keeps loading with zero uploads.
"""

import asyncio
import unittest
from typing import Any, Dict, Optional, Tuple

from pylabrobot.opentrons.flex import OpentronsFlex
from pylabrobot.opentrons.flex_head import FlexHead8
from pylabrobot.opentrons.labware_definitions import (
  build_container_definition,
  build_movable_labware_definition,
  build_plate_definition,
  build_tip_rack_definition,
  container_cavity_footprint,
)
from pylabrobot.opentrons.robot import OpentronsError
from pylabrobot.opentrons.transport import ChatterboxTransport
from pylabrobot.resources import (
  CrossSectionType,
  Plate,
  Resource,
  ResourceHolder,
  TipRack,
  TipSpot,
  Trough,
  TubeRack,
  Well,
  WellBottomType,
)
from pylabrobot.resources.opentrons.flex_deck import FlexDeck
from pylabrobot.resources.rotation import Rotation
from pylabrobot.resources.tip import Tip
from pylabrobot.resources.utils import create_ordered_items_2d


def _plate(
  name: str = "Black Plate-1",
  num_items_x: int = 2,
  num_items_y: int = 2,
  cross_section_type: CrossSectionType = CrossSectionType.CIRCLE,
  bottom_type: WellBottomType = WellBottomType.UNKNOWN,
) -> Plate:
  """A plate with hand-picked geometry so expected numbers are exact."""
  return Plate(
    name=name,
    size_x=127.0,
    size_y=80.0,
    size_z=14.0,
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=num_items_x,
      num_items_y=num_items_y,
      dx=10.0,
      dy=8.0,
      dz=1.0,
      item_dx=9.0,
      item_dy=9.0,
      size_x=6.0,
      size_y=6.0,
      size_z=10.0,
      max_volume=360.0,
      cross_section_type=cross_section_type,
      bottom_type=bottom_type,
    ),
  )


def _tip_rack(
  name: str = "hamilton tips 300", num_items_x: int = 2, num_items_y: int = 2
) -> TipRack:
  """A tip rack with hand-picked geometry and a pinned prototype tip."""

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
      num_items_x=num_items_x,
      num_items_y=num_items_y,
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


def _tube_rack(name: str = "tube rack") -> TubeRack:
  """A 12x8 tube rack: deck-assignable and column-shaped, but not pipettable.

  The rack's holders are not wells, so no well-bearing definition can be
  built from it -- the mainstream labware type that reaches the unbuildable
  path through a pipetting op.
  """
  return TubeRack(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=45.0,
    ordered_items=create_ordered_items_2d(
      ResourceHolder,
      num_items_x=12,
      num_items_y=8,
      dx=10.0,
      dy=8.0,
      dz=0.0,
      item_dx=9.0,
      item_dy=9.0,
      size_x=8.0,
      size_y=8.0,
      size_z=40.0,
    ),
  )


class TestDefinitionLoadNames(unittest.TestCase):
  """Load names are the sanitized PLR name plus a digest of the raw name, so
  distinct names that sanitize identically never share a definitionUri."""

  def test_load_name_is_sanitized_name_plus_digest(self):
    definition = build_plate_definition(_plate())
    self.assertEqual(definition["parameters"]["loadName"], "black_plate_1_e2a464")

  def test_colliding_sanitized_names_get_distinct_load_names(self):
    a = build_plate_definition(_plate(name="My Plate"))
    b = build_plate_definition(_plate(name="my plate"))
    self.assertEqual(a["parameters"]["loadName"], "my_plate_3f2f85")
    self.assertEqual(b["parameters"]["loadName"], "my_plate_1aa3c7")
    self.assertNotEqual(a["parameters"]["loadName"], b["parameters"]["loadName"])
    for definition in (a, b):
      self.assertRegex(definition["parameters"]["loadName"], r"^[a-z0-9._]+$")


class TestBuildPlateDefinition(unittest.TestCase):
  """build_plate_definition maps PLR plate geometry into a wellPlate definition."""

  def test_identity_and_dimensions(self):
    definition = build_plate_definition(_plate())
    self.assertEqual(definition["namespace"], "pylabrobot")
    self.assertEqual(definition["version"], 1)
    self.assertEqual(definition["schemaVersion"], 2)
    self.assertEqual(definition["metadata"]["displayCategory"], "wellPlate")
    self.assertEqual(definition["metadata"]["displayName"], "Black Plate-1")
    self.assertEqual(definition["parameters"]["loadName"], "black_plate_1_e2a464")
    self.assertFalse(definition["parameters"]["isTiprack"])
    self.assertEqual(
      definition["dimensions"],
      {"xDimension": 127.0, "yDimension": 80.0, "zDimension": 14.0},
    )

  def test_ordering_is_column_major(self):
    definition = build_plate_definition(_plate())
    self.assertEqual(definition["ordering"], [["A1", "B1"], ["A2", "B2"]])

  def test_corner_offset_is_zero_front_left_anchor(self):
    # PLR and Opentrons schema-2 definitions both anchor labware at the
    # slot's front-left-bottom corner, so no frame conversion applies.
    definition = build_plate_definition(_plate())
    self.assertEqual(definition["cornerOffsetFromSlot"], {"x": 0, "y": 0, "z": 0})

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

  def test_rectangular_wells_carry_x_y_dimensions(self):
    definition = build_plate_definition(_plate(cross_section_type=CrossSectionType.RECTANGLE))
    well = definition["wells"]["A1"]
    self.assertEqual(well["shape"], "rectangular")
    self.assertEqual(well["xDimension"], 6.0)
    self.assertEqual(well["yDimension"], 6.0)
    self.assertNotIn("diameter", well)

  def test_well_bottom_shape_maps_from_plr_bottom_type(self):
    self.assertEqual(
      build_plate_definition(_plate(bottom_type=WellBottomType.V))["groups"][0]["metadata"],
      {"wellBottomShape": "v"},
    )
    self.assertEqual(
      build_plate_definition(_plate(bottom_type=WellBottomType.U))["groups"][0]["metadata"],
      {"wellBottomShape": "u"},
    )
    # UNKNOWN falls back to flat, the schema's safest default.
    self.assertEqual(
      build_plate_definition(_plate())["groups"][0]["metadata"],
      {"wellBottomShape": "flat"},
    )

  def test_format_derives_from_grid(self):
    self.assertEqual(build_plate_definition(_plate())["parameters"]["format"], "irregular")
    plate_96 = _plate(num_items_x=12, num_items_y=8)
    self.assertEqual(build_plate_definition(plate_96)["parameters"]["format"], "96Standard")
    plate_384 = _plate(num_items_x=24, num_items_y=16)
    self.assertEqual(build_plate_definition(plate_384)["parameters"]["format"], "384Standard")

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
    self.assertEqual(definition["parameters"]["format"], "irregular")  # 2x2 is not an SBS grid
    self.assertTrue(definition["parameters"]["isTiprack"])
    self.assertEqual(definition["parameters"]["tipLength"], 50.0)
    self.assertEqual(definition["parameters"]["tipOverlap"], 8.0)
    self.assertEqual(definition["parameters"]["loadName"], "hamilton_tips_300_0558ff")

  def test_full_rack_format_is_96standard(self):
    definition = build_tip_rack_definition(_tip_rack(num_items_x=12, num_items_y=8))
    self.assertEqual(definition["parameters"]["format"], "96Standard")

  def test_spot_geometry_and_zero_corner_offset(self):
    definition = build_tip_rack_definition(_tip_rack())
    self.assertEqual(definition["cornerOffsetFromSlot"], {"x": 0, "y": 0, "z": 0})
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
    self.assertEqual(definition["parameters"]["loadName"], "hamilton_trough_9544de")
    self.assertEqual(definition["ordering"], [["A1"]])
    self.assertEqual(definition["cornerOffsetFromSlot"], {"x": 0, "y": 0, "z": 0})
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

  def test_rotated_cavity_uploads_the_shared_deck_frame_footprint(self):
    # The uploaded rectangle and the ops' fit guard must read the same
    # helper, or a rotated cavity is guarded on the wrong axis.
    trough = _trough()
    trough.rotation = Rotation(z=90)
    definition = build_container_definition(trough)
    cavity_x, cavity_y = container_cavity_footprint(trough)
    self.assertEqual((cavity_x, cavity_y), (80.0, 120.0))
    self.assertEqual(definition["dimensions"]["xDimension"], cavity_x)
    self.assertEqual(definition["dimensions"]["yDimension"], cavity_y)
    self.assertEqual(definition["wells"]["A1"]["xDimension"], cavity_x)
    self.assertEqual(definition["wells"]["A1"]["yDimension"], cavity_y)

  def test_center_multichannel_quirk_matches_shipped_reservoirs(self):
    # Every shipped Opentrons 1-well reservoir carries this quirk; the engine
    # centers a multi-channel nozzle array on the cavity because of it, so
    # container ops send no manual centering offsets.
    definition = build_container_definition(_trough())
    self.assertEqual(definition["parameters"]["quirks"], ["centerMultichannelOnWells"])

  def test_grip_height_from_grip_distance(self):
    self.assertNotIn("gripHeightFromLabwareBottom", build_container_definition(_trough()))
    definition = build_container_definition(_trough(), grip_distance_from_top=10.0)
    self.assertEqual(definition["gripHeightFromLabwareBottom"], 30.0)  # 40 - 10


class TestBuildMovableLabwareDefinition(unittest.TestCase):
  """build_movable_labware_definition builds the minimal gripper-move stub."""

  def test_stub_has_fake_well_and_grip_geometry(self):
    resource = Resource(name="lid stack", size_x=100.0, size_y=90.0, size_z=20.0)
    definition = build_movable_labware_definition(resource, grip_distance_from_top=5.0)
    self.assertEqual(definition["namespace"], "pylabrobot")
    self.assertEqual(definition["parameters"]["loadName"], "lid_stack_2196eb")
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

  def test_grip_height_omitted_without_grip_distance(self):
    resource = Resource(name="lid stack", size_x=100.0, size_y=90.0, size_z=20.0)
    definition = build_movable_labware_definition(resource)
    self.assertNotIn("gripHeightFromLabwareBottom", definition)

  def test_grip_height_clamped_at_labware_bottom(self):
    resource = Resource(name="shim", size_x=10.0, size_y=10.0, size_z=3.0)
    definition = build_movable_labware_definition(resource, grip_distance_from_top=7.0)
    self.assertEqual(definition["gripHeightFromLabwareBottom"], 0.0)


def _flex_with_transport(
  transport: Optional[ChatterboxTransport] = None,
) -> Tuple[OpentronsFlex, ChatterboxTransport]:
  transport = transport or ChatterboxTransport(
    pipette=("p1000_single_flex", 1, 1.0, 1000.0), mount="right"
  )
  flex = OpentronsFlex(deck=FlexDeck(), host="localhost", transport=transport)
  return flex, transport


def _flex_head8_with_gripper() -> Tuple[OpentronsFlex, ChatterboxTransport, FlexHead8]:
  """A set-up Flex with an 8-channel head AND a gripper, so one bench can
  drive both the gripper-intent and pipetting-intent load paths."""
  transport = ChatterboxTransport(pipettes=[("p50_multi_flex", 8, 1.0, 50.0, "left")], gripper=True)
  flex = OpentronsFlex(deck=FlexDeck(), host="localhost", transport=transport)
  asyncio.run(flex.setup())
  head = flex.left
  assert isinstance(head, FlexHead8)
  return flex, transport, head


def _load_labware_commands(transport: ChatterboxTransport) -> list:
  return [c for c in transport.commands if c["commandType"] == "loadLabware"]


class _FailFirstUploadTransport(ChatterboxTransport):
  """Chatterbox whose FIRST labware-definition upload raises; retries succeed."""

  def __init__(self, **kwargs) -> None:
    super().__init__(**kwargs)
    self._upload_failed_once = False

  async def post(self, path: str, json: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if path.endswith("/labware_definitions") and not self._upload_failed_once:
      self._upload_failed_once = True
      raise RuntimeError("simulated definition upload failure")
    return await super().post(path, json)


class _FailFirstLoadTransport(ChatterboxTransport):
  """Chatterbox whose FIRST loadLabware command fails at the robot; retries succeed."""

  def __init__(self, **kwargs) -> None:
    super().__init__(**kwargs)
    self._load_failed_once = False

  async def post(self, path: str, json: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    result = await super().post(path, json)
    data = (json or {}).get("data", {})
    if (
      path.endswith("/commands")
      and data.get("commandType") == "loadLabware"
      and not self._load_failed_once
    ):
      self._load_failed_once = True
      cmd_data = result["data"]
      cmd_data["status"] = "failed"
      cmd_data["error"] = {"detail": "simulated loadLabware failure"}
    return result


class TestUnbuildableLabwareGuard(unittest.TestCase):
  """Labware no well-bearing definition can be built from is gripper-movable
  but never pipettable: the movable stub's single fake well is zero-depth and
  sits at the labware's own bottom, so pipetting it would drive a tip at the
  deck. Pipetting callers are refused before any wire command."""

  def test_pipetting_a_tube_rack_raises_before_any_wire_command(self):
    flex, transport, head = _flex_head8_with_gripper()
    try:
      rack = _tube_rack()
      flex.deck.assign_child_at_slot(rack, "C1")

      commands_before = len(transport.commands)
      with self.assertRaises(OpentronsError):
        # An untyped script can hand a rack to a Plate parameter; that is
        # exactly the caller this guard exists for.
        asyncio.run(head.aspirate(rack, column=0, volume=20))  # type: ignore[arg-type]

      self.assertEqual(len(transport.labware_definitions), 0)
      self.assertEqual(len(transport.commands), commands_before)
    finally:
      asyncio.run(flex.stop())

  def test_gripper_move_of_the_same_rack_still_works(self):
    flex, transport, head = _flex_head8_with_gripper()
    try:
      rack = _tube_rack()
      flex.deck.assign_child_at_slot(rack, "C1")
      gripper = flex.gripper
      assert gripper is not None

      asyncio.run(gripper.move_labware(rack, "C2"))

      self.assertEqual(len(transport.labware_definitions), 1)
      self.assertEqual(transport.labware_definitions[0]["wells"]["A1"]["depth"], 0)
      self.assertEqual(flex.deck.get_slot(rack), "C2")
    finally:
      asyncio.run(flex.stop())

  def test_pipetting_after_a_gripper_move_still_raises_on_the_load_cache_hit(self):
    # The load cache returns before any type dispatch, so the stub-loaded
    # names are tracked separately for the pipetting refusal to see them.
    flex, transport, head = _flex_head8_with_gripper()
    try:
      rack = _tube_rack()
      flex.deck.assign_child_at_slot(rack, "C1")
      gripper = flex.gripper
      assert gripper is not None
      asyncio.run(gripper.move_labware(rack, "C2"))

      commands_before = len(transport.commands)
      with self.assertRaises(OpentronsError):
        asyncio.run(head.aspirate(rack, column=0, volume=20))  # type: ignore[arg-type]

      self.assertEqual(len(transport.commands), commands_before)
    finally:
      asyncio.run(flex.stop())

  def test_off_deck_clears_the_stub_record(self):
    flex, _transport, head = _flex_head8_with_gripper()
    try:
      rack = _tube_rack()
      flex.deck.assign_child_at_slot(rack, "C1")
      gripper = flex.gripper
      assert gripper is not None
      asyncio.run(gripper.move_labware(rack, "C2"))
      self.assertIn(rack.name, flex._stub_labware)

      asyncio.run(flex.labware_moved_off_deck(rack))
      self.assertNotIn(rack.name, flex._stub_labware)
    finally:
      asyncio.run(flex.stop())


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
      self.assertEqual(params["loadName"], "black_plate_1_e2a464")
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

  def test_reload_after_off_deck_reuploads_definition(self):
    # The definition-identity cache is evicted with the departed labware: a
    # different same-named resource re-added later must not inherit the old
    # geometry, so the re-add re-uploads.
    flex, transport = _flex_with_transport()
    asyncio.run(flex.setup())
    try:
      plate = _plate()
      flex.deck.assign_child_at_slot(plate, "C1")
      asyncio.run(flex._ensure_labware_loaded(plate))
      asyncio.run(flex.labware_moved_off_deck(plate))
      self.assertNotIn(plate.name, flex._defined_labware)

      flex.deck.assign_child_at_slot(plate, "D2")
      asyncio.run(flex._ensure_labware_loaded(plate))

      self.assertEqual(len(transport.labware_definitions), 2)
      load_cmds = _load_labware_commands(transport)
      self.assertEqual(len(load_cmds), 2)
      self.assertEqual(load_cmds[1]["params"]["location"], {"slotName": "D2"})
    finally:
      asyncio.run(flex.stop())

  def test_second_setup_clears_run_scoped_caches(self):
    # labwareIds and uploaded definitions are both run-scoped server-side, so
    # a new run (new setup) must re-upload and re-load.
    flex, transport = _flex_with_transport()
    asyncio.run(flex.setup())
    try:
      plate = _plate()
      flex.deck.assign_child_at_slot(plate, "C1")
      asyncio.run(flex._ensure_labware_loaded(plate))
      self.assertEqual(len(transport.labware_definitions), 1)

      asyncio.run(flex.setup())  # new run
      self.assertEqual(flex._loaded_labware, {})
      self.assertEqual(flex._defined_labware, {})

      asyncio.run(flex._ensure_labware_loaded(plate))
      self.assertEqual(len(transport.labware_definitions), 2)
      self.assertEqual(len(_load_labware_commands(transport)), 2)
    finally:
      asyncio.run(flex.stop())

  def test_failed_upload_leaves_caches_clean_and_retry_works(self):
    flex, transport = _flex_with_transport(
      _FailFirstUploadTransport(pipette=("p1000_single_flex", 1, 1.0, 1000.0), mount="right")
    )
    asyncio.run(flex.setup())
    try:
      plate = _plate()
      flex.deck.assign_child_at_slot(plate, "C1")

      with self.assertRaises(RuntimeError):
        asyncio.run(flex._ensure_labware_loaded(plate))
      self.assertNotIn(plate.name, flex._defined_labware)
      self.assertNotIn(plate.name, flex._loaded_labware)

      asyncio.run(flex._ensure_labware_loaded(plate))
      self.assertEqual(len(transport.labware_definitions), 1)
      self.assertEqual(len(_load_labware_commands(transport)), 1)
      self.assertIn(plate.name, flex._loaded_labware)
    finally:
      asyncio.run(flex.stop())

  def test_failed_load_leaves_no_labware_id_and_retry_reuses_definition(self):
    flex, transport = _flex_with_transport(
      _FailFirstLoadTransport(pipette=("p1000_single_flex", 1, 1.0, 1000.0), mount="right")
    )
    asyncio.run(flex.setup())
    try:
      plate = _plate()
      flex.deck.assign_child_at_slot(plate, "C1")

      with self.assertRaises(RuntimeError):
        asyncio.run(flex._ensure_labware_loaded(plate))
      self.assertNotIn(plate.name, flex._loaded_labware)

      asyncio.run(flex._ensure_labware_loaded(plate))
      # The upload succeeded the first time, so the retry re-loads without a
      # duplicate upload.
      self.assertEqual(len(transport.labware_definitions), 1)
      self.assertEqual(len(_load_labware_commands(transport)), 2)
      self.assertIn(plate.name, flex._loaded_labware)
    finally:
      asyncio.run(flex.stop())

  def test_definition_cache_hit_logs_the_ignored_grip_distance(self):
    # The upload survives a failed load, so the retry reuses the stored
    # definition -- and the grip height it already carries.
    flex, transport = _flex_with_transport(
      _FailFirstLoadTransport(pipette=("p1000_single_flex", 1, 1.0, 1000.0), mount="right")
    )
    asyncio.run(flex.setup())
    try:
      plate = _plate()
      flex.deck.assign_child_at_slot(plate, "C1")
      with self.assertRaises(RuntimeError):
        asyncio.run(flex._ensure_labware_loaded(plate, grip_distance_from_top=4.0))

      with self.assertLogs("pylabrobot.opentrons.flex", level="WARNING") as logs:
        asyncio.run(flex._ensure_labware_loaded(plate, grip_distance_from_top=8.0))

      self.assertEqual(len(transport.labware_definitions), 1)
      self.assertEqual(transport.labware_definitions[0]["gripHeightFromLabwareBottom"], 10.0)
      self.assertTrue(any("grip_distance_from_top=8.0" in line for line in logs.output))
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
      self.assertEqual(params["loadName"], "hamilton_trough_9544de")
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
      self.assertEqual(params["loadName"], "hamilton_tips_300_0558ff")
    finally:
      asyncio.run(flex.stop())

  def test_bare_resource_uploads_movable_stub(self):
    # A resource that is not a Plate/TipRack/Container routes to the
    # non-pipettable movable stub, which only allow_stub callers may ask for.
    flex, transport = _flex_with_transport()
    asyncio.run(flex.setup())
    try:
      widget = Resource(name="widget", size_x=100.0, size_y=90.0, size_z=20.0)
      flex.deck.assign_child_at_slot(widget, "C1")
      asyncio.run(flex._ensure_labware_loaded(widget, allow_stub=True, grip_distance_from_top=5.0))

      self.assertEqual(len(transport.labware_definitions), 1)
      definition = transport.labware_definitions[0]
      self.assertEqual(definition["ordering"], [["A1"]])
      self.assertEqual(definition["wells"]["A1"]["depth"], 0)
      self.assertEqual(definition["gripHeightFromLabwareBottom"], 15.0)  # 20 - 5
      params = _load_labware_commands(transport)[0]["params"]
      self.assertEqual(params["namespace"], "pylabrobot")
      self.assertEqual(params["loadName"], "widget_ff700e")
    finally:
      asyncio.run(flex.stop())
