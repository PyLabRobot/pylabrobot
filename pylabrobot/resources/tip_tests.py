import unittest

from pylabrobot.resources import Coordinate, HeadTool, Resource
from pylabrobot.resources.hamilton import (
  TIP_DIAMETER,
  HamiltonTip,
  TipPickupMethod,
  TipSize,
  hamilton_96_tiprack_1000uL,
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
      maximal_volume=10.0,
      fitting_depth=1.0,
      diameter=TIP_DIAMETER[TipSize.STANDARD_VOLUME],
      size_z=10.0,
    )
    self.assertEqual(
      serialize(tip),
      {
        "type": "Tip",
        "name": "test_tip",
        "category": "tip",
        "diameter": TIP_DIAMETER[TipSize.STANDARD_VOLUME],
        "size_z": 10.0,
        "pick_up_location": None,
        "has_filter": False,
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
      maximal_volume=10.0,
      fitting_depth=1.0,
      diameter=TIP_DIAMETER[TipSize.STANDARD_VOLUME],
      size_z=10.0,
    )
    self.assertEqual(Tip.deserialize(tip.serialize()), tip)

  def test_serialize_subclass(self):
    tip = HamiltonTip(
      name="test_tip",
      has_filter=False,
      size_z=10.0,
      maximal_volume=10.0,
      tip_size=TipSize.HIGH_VOLUME,
      pickup_method=TipPickupMethod.OUT_OF_RACK,
    )
    self.assertEqual(
      tip.serialize(),
      {
        "type": "HamiltonTip",
        "name": "test_tip",
        "category": "tip",
        "diameter": 8.2,
        "pick_up_location": None,
        "has_filter": False,
        "size_z": 10.0,
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
      size_z=10.0,
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
      maximal_volume=400.0,
      fitting_depth=1.0,
      diameter=TIP_DIAMETER[TipSize.STANDARD_VOLUME],
      size_z=10.0,
    )
    self.assertEqual(tip.nominal_volume, 400.0)

  def test_deserialize_legacy_without_nominal_volume(self):
    """A payload predating nominal_volume deserializes, falling back to maximal_volume."""
    tip = HamiltonTip(
      name="test_tip",
      has_filter=False,
      size_z=10.0,
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

  def test_state_carries_the_volume_and_a_change_to_it_is_announced(self):
    """A tip's tracker was neither published nor wired to the state callbacks, so anything drawing
    what a tip holds read a field that never arrived and drew the tip empty however full it was."""
    tip = Tip(
      name="tip", diameter=5, size_z=50, has_filter=False, maximal_volume=300, fitting_depth=8
    )
    seen: list = []
    tip.register_state_update_callback(seen.append)
    tip.tracker.set_volume(120.0)
    self.assertEqual(tip.serialize_state()["volume"], 120.0)
    self.assertEqual(tip.serialize_state()["max_volume"], 300)
    self.assertEqual(len(seen), 1)
    twin = Tip(
      name="tip", diameter=5, size_z=50, has_filter=False, maximal_volume=300, fitting_depth=8
    )
    twin.load_state(tip.serialize_state())
    self.assertEqual(twin.tracker.get_used_volume(), 120.0)

  def test_a_tip_in_a_rack_still_announces_its_own_state(self):
    """A spot registers on its tip's tracker to publish the spot, and the tracker kept one
    callback, so the tip's own was dropped the moment it entered a rack."""
    rack = hamilton_96_tiprack_1000uL(name="rack", with_tips=True)
    tip = rack.get_item("A1").get_tip()
    seen: list = []
    tip.register_state_update_callback(seen.append)
    tip.tracker.set_volume(50.0)
    self.assertEqual([state["volume"] for state in seen], [50.0])

  def test_equality_includes_tool_and_tip_fields(self):
    """Matching resource geometry alone does not make different tips equal."""
    tip = Tip("tip", TIP_DIAMETER[TipSize.STANDARD_VOLUME], 50, False, 300, 8, collar_height=4)
    data = tip.serialize()
    for changes in (
      {"name": "other"},
      {"diameter": 7},
      {"fitting_depth": 9},
      {"pick_up_location": Coordinate(3, 3, 50).serialize()},
      {"has_filter": True},
      {"size_z": 51},
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
    """Hamilton size and pickup method participate in equality."""
    tip = hamilton_tip_300uL("tip")
    for changes in (
      {"tip_size": TipSize.HIGH_VOLUME.name},
      {"pickup_method": TipPickupMethod.OUT_OF_WASH_LIQUID.name},
    ):
      with self.subTest(changes=changes):
        other = Tip.deserialize({**tip.serialize(), **changes})
        self.assertNotEqual(tip, other)

  @unittest.skip("Tecan tip diameter has not been measured.")
  def test_equality_includes_tecan_tip_type(self):
    """Tecan tip type participates in equality."""
    tecan_tip = DiTi_100ul_Te_MO_tip("tip")
    other = Tip.deserialize({**tecan_tip.serialize(), "tip_type": TipType.STANDARD.name})
    self.assertNotEqual(tecan_tip, other)

  def test_tools_are_unhashable(self):
    """Tools and tips cannot be used as dictionary keys or set members."""
    resources = (
      HeadTool("tool", 6, 6, 50, fitting_depth=8),
      Tip("tip", TIP_DIAMETER[TipSize.STANDARD_VOLUME], 50, False, 300, 8),
      hamilton_tip_300uL("hamilton_tip"),
    )
    for resource in resources:
      with self.subTest(resource=resource.name), self.assertRaises(TypeError):
        hash(resource)

  @unittest.skip("Tecan tip diameter has not been measured.")
  def test_tecan_tip_is_unhashable(self):
    """Tecan tips cannot be used as dictionary keys or set members."""
    tip = DiTi_100ul_Te_MO_tip("tip")
    with self.assertRaises(TypeError):
      hash(tip)

  def test_tool_and_tip_round_trips(self):
    """Both serializers preserve model, geometry, pickup location, and tip-specific fields."""
    tool = HeadTool("tool", 10, 12, 30, fitting_depth=4, model="tool_model")
    tip = Tip(
      "tip",
      TIP_DIAMETER[TipSize.STANDARD_VOLUME],
      50,
      False,
      300,
      8,
      collar_height=4,
      model="tip_model",
    )
    hamilton_tip = hamilton_tip_300uL("hamilton_tip")
    for resource in (tool, tip, hamilton_tip):
      with self.subTest(resource=resource.name):
        resource.pick_up_location = Coordinate(3, 3, 30)
        data = resource.serialize()
        for restored in (HeadTool.deserialize(data), Resource.deserialize(data)):
          self.assertEqual(restored.serialize(), data)

  @unittest.skip("Tecan tip diameter has not been measured.")
  def test_tecan_tip_round_trip(self):
    """The resource serializer preserves Tecan tip properties."""
    tip = DiTi_100ul_Te_MO_tip("tecan_tip")
    tip.pick_up_location = Coordinate(3, 3, 30)
    data = tip.serialize()
    self.assertEqual(Tip.deserialize(data).serialize(), data)

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

  @unittest.skip("Tecan tip diameter has not been measured.")
  def test_tecan_tip_name_is_required(self):
    """A Tecan tip cannot have a None name."""
    with self.assertRaisesRegex(TypeError, "name must be a string"):
      DiTi_100ul_Te_MO_tip(None)  # type: ignore[arg-type]

  def test_unknown_diameter_raises(self):
    """Incomplete tip definitions cannot substitute arbitrary diameters."""
    with self.assertRaisesRegex(NotImplementedError, "Tip diameter is not defined"):
      DiTi_100ul_Te_MO_tip("tip")
    with self.assertRaisesRegex(NotImplementedError, "Tip diameter is not defined"):
      HamiltonTip("tip", False, 30, 10, TipSize.CORE_384_HEAD_TIP, TipPickupMethod.OUT_OF_RACK)
    with self.assertRaisesRegex(NotImplementedError, "Tip diameter is not defined"):
      hamilton_tip_5000uL("tip")

  def test_incomplete_tecan_rack_raises(self):
    """A catalog rack cannot construct tips with an unknown diameter."""
    with self.assertRaisesRegex(NotImplementedError, "Tip diameter is not defined"):
      DiTi_100ul_Te_MO("rack")
