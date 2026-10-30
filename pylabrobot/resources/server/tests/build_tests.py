"""The typed builders of `build`: each makes the definition `build.resource` makes from its spec,
and the store takes what they make."""

import unittest
from typing import Callable, Dict, List

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.server import build, handles
from pylabrobot.resources.server.client import ResourceServer
from pylabrobot.resources.server.tree import SPEC_KINDS
from pylabrobot.resources.server.v1 import types_pb2 as pb

K = pb.ResourceKind
SIZE = Coordinate(10, 20, 30)


def _tip() -> pb.ResourceDefinition:
  return build.tip(
    "tip",
    Coordinate(7, 7, 60),
    diameter=7,
    maximal_volume=300,
    fitting_depth=8,
    has_filter=True,
    collar_height=5,
    hamilton_tip_size="STANDARD_VOLUME_TIP",
    hamilton_pickup_method=pb.TIP_PICKUP_METHOD_OUT_OF_RACK,
  )


# One of every builder, each with its spec set.
BUILT: Dict[str, Callable[[], pb.ResourceDefinition]] = {
  "deck": lambda: build.deck(
    "deck", SIZE, num_tracks=54, track_origin=Coordinate(100, 63, 100), track_pitch=22.5
  ),
  "carrier": lambda: build.carrier("carrier", SIZE),
  "holder": lambda: build.holder("holder", SIZE, child_location=Coordinate(1, 2, 3), capacity=1),
  "plate_holder": lambda: build.plate_holder("site", SIZE, pedestal_size_z=2),
  "embedded_tip_rack_holder": lambda: build.embedded_tip_rack_holder("holder", SIZE),
  "petri_dish_holder": lambda: build.petri_dish_holder("holder", SIZE),
  "resource_stack": lambda: build.resource_stack("stack", SIZE, direction=pb.STACK_DIRECTION_X),
  "plate_adapter": lambda: build.plate_adapter(
    "adapter", SIZE, 1, 2, 3, 4, 5, 6, 9, 9, plate_z_offset=7
  ),
  "container": lambda: build.container("container", SIZE, max_volume=10),
  "well": lambda: build.well(
    "well",
    SIZE,
    max_volume=360,
    material_z_thickness=1,
    height_volume_data=[(0, 0), (10, 360)],
    no_go_zones=[(Coordinate(0, 0, 0), Coordinate(1, 1, 1))],
    bottom_type=pb.WELL_BOTTOM_TYPE_FLAT,
    cross_section_type=pb.CROSS_SECTION_TYPE_CIRCLE,
  ),
  "trough": lambda: build.trough("trough", SIZE, through_base_to_container_base=2),
  "tube": lambda: build.tube("tube", SIZE, max_volume=1500),
  "petri_dish": lambda: build.petri_dish("dish", SIZE),
  "trash": lambda: build.trash("trash", SIZE),
  "plate": lambda: build.plate("plate", SIZE, num_items_x=1, num_items_y=1, stacking_z_height=3),
  "tip_rack": lambda: build.tip_rack("rack", SIZE, style=pb.TIP_RACK_STYLE_EMBEDDED),
  "tube_rack": lambda: build.tube_rack("rack", SIZE),
  "container_rack": lambda: build.container_rack("rack", SIZE),
  "lid": lambda: build.lid("lid", SIZE, nesting_z_height=2),
  "head_tool": lambda: build.head_tool("paddle", SIZE, fitting_depth=3, grip_line_height=4),
  "tip": _tip,
  "tip_spot": lambda: build.tip_spot("spot", SIZE, prototype_tip=_tip()),
  "pipette_channel": lambda: build.pipette_channel(
    "channel", SIZE, reference_point=Coordinate(4.5, 4.5, 0)
  ),
  "tip_mounting_shaft": lambda: build.tip_mounting_shaft(
    "shaft", SIZE, tip_pickup_mode="core", cross_section_type=pb.CROSS_SECTION_TYPE_CIRCLE
  ),
  "n_channel_pipette": lambda: build.n_channel_pipette(
    "head", SIZE, reference_point=Coordinate(1, 2, 3), independent_channel_actuation=True
  ),
  "link_body": lambda: build.link_body(
    "link", SIZE, proximal_joint=Coordinate(0, 0, 0), distal_joint=Coordinate(0, 10, 0)
  ),
  "mechanical_gripper": lambda: build.mechanical_gripper(
    "gripper",
    SIZE,
    proximal_joint=Coordinate(0, 0, 0),
    tool_center_point=Coordinate(0, 0, -10),
    jaw_width_min=0,
    jaw_width_max=100,
    body="body",
    fingers=["left", "right"],
  ),
}


def _specs(attributes: pb.ResourceAttributes) -> List[str]:
  return [field.name for field, _ in attributes.ListFields() if field.name in SPEC_KINDS]


class SpecTests(unittest.TestCase):
  def test_every_spec_has_a_builder(self):
    built = {spec for make in BUILT.values() for spec in _specs(make().attributes)}
    self.assertEqual(built, set(SPEC_KINDS))

  def test_every_builder_gives_only_specs_its_kind_takes(self):
    for name, make in BUILT.items():
      with self.subTest(name):
        attributes = make().attributes
        for spec in _specs(attributes):
          self.assertIn(attributes.kind, SPEC_KINDS[spec], spec)

  def test_the_common_fields(self):
    child = build.lid("lid", SIZE)
    made = build.holder(
      "site",
      SIZE,
      location=Coordinate(1, 2, 3),
      category="plate_holder",
      model="a site",
      identifier="A1",
      metadata={"note": "kept"},
      children=[child],
    )
    self.assertEqual(
      made,
      build.resource(
        "site",
        SIZE,
        kind=K.RESOURCE_KIND_RESOURCE_HOLDER,
        location=Coordinate(1, 2, 3),
        category="plate_holder",
        model="a site",
        identifier="A1",
        metadata={"note": "kept"},
        children=[child],
        holder=pb.HolderSpec(child_location=build.vec3(Coordinate(0, 0, 0))),
      ),
    )

  def test_a_well_is_the_container_spec_by_field(self):
    self.assertEqual(
      BUILT["well"](),
      build.resource(
        "well",
        SIZE,
        kind=K.RESOURCE_KIND_WELL,
        container=pb.ContainerSpec(
          max_volume=360,
          material_z_thickness=1,
          height_volume_data=[
            pb.HeightVolumePoint(height=0, volume=0),
            pb.HeightVolumePoint(height=10, volume=360),
          ],
          no_go_zones=[
            pb.Box(min=build.vec3(Coordinate(0, 0, 0)), max=build.vec3(Coordinate(1, 1, 1)))
          ],
          bottom_type=pb.WELL_BOTTOM_TYPE_FLAT,
          cross_section_type=pb.CROSS_SECTION_TYPE_CIRCLE,
        ),
      ),
    )

  def test_unset_options_stay_unset(self):
    attributes = build.well("well", SIZE).attributes
    self.assertFalse(attributes.container.HasField("max_volume"))
    self.assertFalse(attributes.container.HasField("material_z_thickness"))
    self.assertFalse(attributes.HasField("location"))
    self.assertFalse(build.deck("deck", SIZE).attributes.deck.HasField("hamilton"))

  def test_a_plate_grid(self):
    well = build.well("well", Coordinate(8, 8, 10))
    made = build.plate(
      "plate",
      SIZE,
      num_items_x=12,
      num_items_y=8,
      item=well,
      item_offset=Coordinate(10, 7, 1),
      item_dx=9,
      item_dy=9,
    )
    self.assertEqual(
      made,
      build.resource(
        "plate",
        SIZE,
        kind=K.RESOURCE_KIND_PLATE,
        itemized=pb.ItemizedSpec(
          num_items_x=12,
          num_items_y=8,
          grid=pb.ItemGrid(
            item=well, offset=build.vec3(Coordinate(10, 7, 1)), item_dx=9, item_dy=9
          ),
        ),
        plate=pb.PlateSpec(plate_type="skirted"),
      ),
    )

  def test_a_tip(self):
    tip = _tip().attributes
    self.assertEqual(tip.tip.nominal_volume, 300, "the maximal volume, when none is given")
    self.assertEqual(tip.tip.hamilton.tip_size, "STANDARD_VOLUME_TIP")
    self.assertEqual(tip.head_tool.fitting_depth, 8)


class ServerTests(unittest.IsolatedAsyncioTestCase):
  async def test_the_store_takes_what_each_builder_makes(self):
    server = ResourceServer.local()
    for index, (name, make) in enumerate(BUILT.items()):
      with self.subTest(name):
        made = await server.create(make(), name=f"{name}_{index}")
        self.assertEqual(made.kind, make().attributes.kind)

  async def test_a_plate_made_from_a_grid(self):
    server = ResourceServer.local()
    plate = await server.create(
      build.plate(
        "plate",
        Coordinate(127.76, 85.48, 14.2),
        num_items_x=12,
        num_items_y=8,
        item=build.well("well_{identifier}", Coordinate(6.9, 6.9, 10.7), max_volume=360),
        item_offset=Coordinate(10.9, 7.96, 1.5),
        item_dx=9,
        item_dy=9,
      ),
      name="plate",
      kind=handles.Plate,
    )
    self.assertEqual(plate.num_items, 96)
    self.assertEqual(
      await plate.get_well("H12").get_location_wrt(plate), Coordinate(10.9 + 11 * 9, 7.96, 1.5)
    )


if __name__ == "__main__":
  unittest.main()
