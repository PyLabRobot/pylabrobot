import unittest

from pylabrobot.resources import (
  PLT_CAR_L5PCR,
  Coordinate,
  Eppendorf_96_wellplate_250ul_Vb,
  Hamilton_96_adapter_188182,
  PlateAdapter,
  PlateCarrier,
  Resource,
  STARLetDeck,
)


class PCRPlateCarrierTests(unittest.TestCase):
  """The 182070 carrier seats removable 188182 adapters at its measured support surface."""

  def test_default_carrier_has_five_empty_sites(self):
    """Constructing the bare carrier provides usable sites without a missing pedestal error."""
    carrier = PLT_CAR_L5PCR("pcr")

    self.assertEqual(carrier.model, "PLT_CAR_L5PCR")
    self.assertEqual(
      (carrier.get_size_x(), carrier.get_size_y(), carrier.get_size_z()),
      (135.0, 497.0, 105.59),
    )
    self.assertEqual(list(carrier.sites), list(range(5)))
    self.assertEqual(len(carrier.get_free_sites()), 5)
    for site in carrier.sites.values():
      self.assertIsNone(site.resource)
      self.assertEqual((site.get_size_x(), site.get_size_y(), site.get_size_z()), (110, 75, 0))
      self.assertEqual(site.pedestal_size_z, 0)

  def test_adapter_mounting_coordinates(self):
    """Manual and preloaded adapters use the same origins without an extra offset or sinking."""
    for with_adapters in (False, True):
      with self.subTest(with_adapters=with_adapters):
        carrier = PLT_CAR_L5PCR("pcr", with_adapters=with_adapters)
        for i, y in enumerate((14.60, 109.90, 205.20, 300.50, 395.80)):
          if not with_adapters:
            carrier[i] = Hamilton_96_adapter_188182(f"pcr_adapter_{i}")
          site = carrier[i]
          adapter = site.resource
          self.assertIsInstance(adapter, PlateAdapter)
          assert isinstance(adapter, PlateAdapter)
          self.assertEqual(adapter.name, f"pcr_adapter_{i}")
          self.assertEqual(adapter.model, "Hamilton_96_adapter_188182")
          self.assertIs(adapter.parent, site)
          self.assertEqual(site.location, Coordinate(12.52, y, 105.59))
          self.assertEqual(adapter.location, Coordinate.zero())
          self.assertEqual(adapter.get_location_wrt(carrier), Coordinate(12.52, y, 105.59))

  def test_adapter_positions_on_starlet(self):
    """The deck's track datum adds its height exactly once to the measured support height."""
    deck = STARLetDeck()
    carrier = PLT_CAR_L5PCR("pcr", with_adapters=True)
    deck.assign_child_resource(carrier, track=1)

    for i, y in ((0, 77.60), (4, 458.80)):
      with self.subTest(site=i):
        adapter = carrier[i].resource
        assert isinstance(adapter, PlateAdapter)
        self.assertEqual(adapter.get_absolute_location(), Coordinate(112.52, y, 205.59))
        self.assertEqual(adapter.get_absolute_location(z="top"), Coordinate(112.52, y, 220.59))

  def test_bare_adapter_corner_centers(self):
    """The four bare-adapter inspection targets use measured hole centers, not edge offsets."""
    deck = STARLetDeck()
    carrier = PLT_CAR_L5PCR("pcr", with_adapters=True)
    deck.assign_child_resource(carrier, track=13)

    for i, column, row_from_front, expected in (
      (0, 0, 0, Coordinate(387.22, 83.50, 220.59)),  # H1
      (0, 11, 0, Coordinate(486.22, 83.50, 220.59)),  # H12
      (4, 0, 7, Coordinate(387.22, 527.70, 220.59)),  # A1
      (4, 11, 7, Coordinate(486.22, 527.70, 220.59)),  # A12
    ):
      with self.subTest(site=i, column=column, row_from_front=row_from_front):
        adapter = carrier[i].resource
        assert isinstance(adapter, PlateAdapter)
        center = adapter.get_absolute_location(z="top") + Coordinate(
          x=adapter.dx + adapter.adapter_hole_size_x / 2 + column * adapter.adapter_hole_dx,
          y=adapter.dy + adapter.adapter_hole_size_y / 2 + row_from_front * adapter.adapter_hole_dy,
        )
        self.assertEqual(center, expected)

  def test_pcr_plate_assignment_preserves_other_adapters(self):
    """A non-skirted PCR plate aligns with one adapter's holes without affecting other sites."""
    carrier = PLT_CAR_L5PCR("pcr", with_adapters=True)
    other_carrier = PLT_CAR_L5PCR("other_pcr", with_adapters=True)
    adapter = carrier[0].resource
    assert isinstance(adapter, PlateAdapter)
    plate = Eppendorf_96_wellplate_250ul_Vb("plate")
    adapter.assign_child_resource(plate)

    self.assertIs(plate.parent, adapter)
    self.assertEqual(adapter.children, [plate])
    for well, x, y in (("H1", 17.22, 20.50), ("A12", 116.22, 83.50)):
      center = plate.get_well(well).get_location_wrt(carrier, x="c", y="c", z="b")
      self.assertAlmostEqual(center.x, x)
      self.assertAlmostEqual(center.y, y)
      self.assertAlmostEqual(center.z, 108.39)
    for other in carrier.get_resources()[1:] + other_carrier.get_resources():
      self.assertIsNot(other, adapter)
      self.assertEqual(other.children, [])

  def test_serialization_round_trip(self):
    """Bare and preloaded carriers retain their sites, adapter identities, and coordinates."""
    for with_adapters in (False, True):
      with self.subTest(with_adapters=with_adapters):
        carrier = PLT_CAR_L5PCR("pcr", with_adapters=with_adapters)
        restored = Resource.deserialize(carrier.serialize())

        assert isinstance(restored, PlateCarrier)
        self.assertEqual(restored.serialize(), carrier.serialize())
        self.assertEqual(len(restored.sites), 5)
        self.assertEqual(len(restored.get_resources()), 5 if with_adapters else 0)
