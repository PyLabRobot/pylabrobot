import unittest

from pylabrobot.resources import Coordinate, HeadTool, Resource
from pylabrobot.resources.hamilton import (
  TIP_DIAMETER,
  HamiltonTip,
  TipPickupMethod,
  TipSize,
  hamilton_tip_300uL,
  hamilton_tip_300uL_filter,
  hamilton_tip_5000uL,
)
from pylabrobot.resources.imcs.tip_racks import imcs_tip_300uL, imcs_tip_1000uL
from pylabrobot.resources.tecan.tip_creators import DiTi_100ul_Te_MO_tip, TipType
from pylabrobot.resources.tecan.tip_racks import DiTi_100ul_Te_MO
from pylabrobot.resources.tip import Tip
from pylabrobot.serializer import serialize


class TipTests(unittest.TestCase):
  """Test for tip classes."""

  def test_serialize(self):
    tip = Tip(
      name="test_tip",
      has_filter=False,
      total_tip_length=10.0,
      maximal_volume=10.0,
      fitting_depth=1.0,
      diameter=6.0,
      size_z=10.0,
    )
    self.assertEqual(
      serialize(tip),
      {
        "type": "Tip",
        "name": "test_tip",
        "diameter": 6.0,
        "size_z": 10.0,
        "pick_up_location": None,
        "has_filter": False,
        "total_tip_length": 10.0,
        "nominal_volume": 10.0,
        "maximal_volume": 10.0,
        "fitting_depth": 1.0,
        "collar_height": None,
      },
    )

  def test_deserialize(self):
    tip = Tip(
      name="test_tip",
      has_filter=False,
      total_tip_length=10.0,
      maximal_volume=10.0,
      fitting_depth=1.0,
      diameter=6.0,
      size_z=10.0,
    )
    self.assertEqual(Tip.deserialize(serialize(tip)), tip)

  def test_serialize_subclass(self):
    tip = HamiltonTip(
      name="test_tip",
      has_filter=False,
      total_tip_length=10.0,
      maximal_volume=10.0,
      tip_size=TipSize.HIGH_VOLUME,
      pickup_method=TipPickupMethod.OUT_OF_RACK,
    )
    self.assertEqual(
      tip.serialize(),
      {
        "type": "HamiltonTip",
        "name": "test_tip",
        "diameter": 8.2,
        "pick_up_location": None,
        "has_filter": False,
        "total_tip_length": 10.0,
        "nominal_volume": 10.0,
        "maximal_volume": 10.0,
        "pickup_method": "OUT_OF_RACK",
        "tip_size": "HIGH_VOLUME",
        "collar_height": None,
      },
    )

  def test_deserialize_subclass(self):
    tip = HamiltonTip(
      name="test_tip",
      has_filter=False,
      total_tip_length=10.0,
      maximal_volume=10.0,
      tip_size=TipSize.HIGH_VOLUME,
      pickup_method=TipPickupMethod.OUT_OF_RACK,
    )
    self.assertEqual(Tip.deserialize(tip.serialize()), tip)

  def test_nominal_volume_defaults_to_maximal_volume(self):
    """An unspecified nominal_volume falls back to maximal_volume (the physical capacity)."""
    tip = Tip(
      name="test_tip",
      has_filter=False,
      total_tip_length=10.0,
      maximal_volume=400.0,
      fitting_depth=1.0,
      diameter=6.0,
      size_z=10.0,
    )
    self.assertEqual(tip.nominal_volume, 400.0)

  def test_deserialize_legacy_without_nominal_volume(self):
    """A payload predating nominal_volume deserializes, falling back to maximal_volume."""
    tip = HamiltonTip(
      name="test_tip",
      has_filter=False,
      total_tip_length=10.0,
      maximal_volume=400.0,
      tip_size=TipSize.HIGH_VOLUME,
      pickup_method=TipPickupMethod.OUT_OF_RACK,
    )
    legacy = tip.serialize()
    del legacy["nominal_volume"]
    self.assertEqual(Tip.deserialize(legacy).nominal_volume, 400.0)

  def test_factory_models_and_diameters(self):
    """Catalog tips retain their model across instances and use their size class's diameter."""
    for factory, size in (
      (hamilton_tip_300uL, TipSize.STANDARD_VOLUME),
      (hamilton_tip_5000uL, TipSize.XL),
      (imcs_tip_300uL, TipSize.STANDARD_VOLUME),
      (imcs_tip_1000uL, TipSize.HIGH_VOLUME),
    ):
      with self.subTest(factory=factory.__name__):
        first, second = factory("first"), factory("second")
        self.assertEqual(first.model, factory.__name__)
        self.assertEqual(first.model, second.model)
        self.assertNotEqual(first, second)
        self.assertEqual(first.get_size_x(), TIP_DIAMETER[size])
        self.assertEqual(first.get_size_y(), TIP_DIAMETER[size])
        self.assertEqual(first.get_size_z(), first.total_tip_length)

  def test_equality_includes_tool_and_tip_fields(self):
    """Matching resource geometry alone does not make different tips equal."""
    tip = Tip("tip", 6, 50, False, 50, 300, 8, collar_height=4)
    data = tip.serialize()
    for changes in (
      {"name": "other"},
      {"diameter": 7},
      {"fitting_depth": 9},
      {"pick_up_location": Coordinate(3, 3, 50).serialize()},
      {"has_filter": True},
      {"total_tip_length": 51},
      {"nominal_volume": 250},
      {"maximal_volume": 350},
      {"collar_height": 5},
    ):
      with self.subTest(changes=changes):
        other = Tip.deserialize({**data, **changes})
        self.assertNotEqual(tip, other)
        self.assertNotEqual(other, tip)
    self.assertNotEqual(hamilton_tip_300uL("tip"), hamilton_tip_300uL_filter("tip"))

  def test_equality_includes_vendor_fields(self):
    """Hamilton size and pickup method, and Tecan type, participate in equality."""
    tip = hamilton_tip_300uL("tip")
    for changes in (
      {"tip_size": TipSize.HIGH_VOLUME.name},
      {"pickup_method": TipPickupMethod.OUT_OF_WASH_LIQUID.name},
    ):
      with self.subTest(changes=changes):
        other = Tip.deserialize({**tip.serialize(), **changes})
        self.assertNotEqual(tip, other)
    tecan_tip = DiTi_100ul_Te_MO_tip("tip", diameter=6)
    other = Tip.deserialize({**tecan_tip.serialize(), "tip_type": TipType.STANDARD.name})
    self.assertNotEqual(tecan_tip, other)

  def test_equal_tools_have_equal_hashes(self):
    """Equal numeric values and a round trip must preserve dictionary and set lookup."""
    resources = (
      HeadTool("tool", 6, 6, 50, fitting_depth=8),
      Tip("tip", 6, 50, False, 50, 300, 8),
      hamilton_tip_300uL("hamilton_tip"),
      DiTi_100ul_Te_MO_tip("tecan_tip", diameter=6),
    )
    for resource in resources:
      with self.subTest(resource=resource.name):
        copy = HeadTool.deserialize(resource.serialize())
        if isinstance(copy, Tip):
          copy.maximal_volume = float(copy.maximal_volume)
          copy.total_tip_length = float(copy.total_tip_length)
        self.assertEqual(resource, copy)
        self.assertEqual(hash(resource), hash(copy))
        self.assertEqual(len({resource, copy}), 1)
        self.assertEqual({resource: "value"}[copy], "value")

  def test_tool_and_tip_round_trips(self):
    """Both serializers preserve model, geometry, pickup location, and tip-specific fields."""
    tool = HeadTool("tool", 10, 12, 30, fitting_depth=4, model="tool_model")
    tip = Tip("tip", 6, 50, False, 50, 300, 8, collar_height=4, model="tip_model")
    hamilton_tip = hamilton_tip_300uL("hamilton_tip")
    tecan_tip = DiTi_100ul_Te_MO_tip("tecan_tip", diameter=6)
    for resource in (tool, tip, hamilton_tip, tecan_tip):
      with self.subTest(resource=resource.name):
        resource.pick_up_location = Coordinate(3, 3, 30)
        data = resource.serialize()
        for restored in (HeadTool.deserialize(data), Resource.deserialize(data)):
          self.assertEqual(restored.serialize(), data)

  def test_tip_resource_state_round_trip(self):
    """The resource deserializer restores tip state and placement through its parent."""
    parent = Resource("parent", 100, 100, 100)
    tip = hamilton_tip_300uL("tip")
    tip.rotate(z=90)
    tip.metadata = {"batch": "example"}
    tip.pick_up_location = Coordinate(4.1, 4.1, 59.9)
    parent.assign_child_resource(tip, location=Coordinate(10, 20, 30))

    restored = Resource.deserialize(parent.serialize())
    self.assertEqual(restored.serialize(), parent.serialize())
    restored_tip = restored.get_resource("tip")
    self.assertIsInstance(restored_tip, HamiltonTip)
    self.assertEqual(restored_tip.location, tip.location)

    standalone = Tip.deserialize(tip.serialize())
    self.assertIsNone(standalone.parent)
    self.assertIsNone(standalone.location)
    self.assertEqual(standalone.rotation.serialize(), tip.rotation.serialize())
    self.assertEqual(standalone.metadata, tip.metadata)

  def test_names_are_required(self):
    """Catalog callers must supply names and cannot pass None."""
    with self.assertRaises(TypeError):
      hamilton_tip_300uL()  # type: ignore[call-arg]
    with self.assertRaisesRegex(TypeError, "name must be a string"):
      hamilton_tip_300uL(None)  # type: ignore[arg-type]
    with self.assertRaisesRegex(TypeError, "name must be a string"):
      DiTi_100ul_Te_MO_tip(None, diameter=6)  # type: ignore[arg-type]

  def test_missing_diameter_must_be_supplied(self):
    """Unmeasured Tecan and Hamilton size classes require an explicit diameter."""
    with self.assertRaises(TypeError):
      DiTi_100ul_Te_MO_tip("tip")  # type: ignore[call-arg]
    with self.assertRaisesRegex(ValueError, "diameter is required"):
      HamiltonTip("tip", False, 30, 10, TipSize.CORE_384_HEAD_TIP, TipPickupMethod.OUT_OF_RACK)
    tip = HamiltonTip(
      "tip", False, 30, 10, TipSize.CORE_384_HEAD_TIP, TipPickupMethod.OUT_OF_RACK, diameter=4
    )
    self.assertEqual(tip.get_size_x(), 4)

  def test_tecan_rack_binds_diameter_and_names_each_tip(self):
    """A rack binds the caller's diameter while TipSpot supplies each name."""
    rack = DiTi_100ul_Te_MO("rack", tip_diameter=6)
    tips = [spot.get_tip() for spot in rack.get_all_items()]
    self.assertEqual(len({tip.name for tip in tips}), 96)
    self.assertTrue(all(tip.get_size_x() == 6 for tip in tips))
    self.assertTrue(all(tip.model == DiTi_100ul_Te_MO_tip.__name__ for tip in tips))
