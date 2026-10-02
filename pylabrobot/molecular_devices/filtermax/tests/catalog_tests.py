import unittest
from pathlib import Path

from pylabrobot.molecular_devices.filtermax import FilterSlideCatalog
from pylabrobot.molecular_devices.filtermax.errors import (
  FilterMaxAmbiguousFilterError,
  FilterMaxFilterNotFoundError,
)


class TestFilterSlideCatalog(unittest.TestCase):
  def setUp(self) -> None:
    self.catalog = FilterSlideCatalog.from_softmax_xml(
      Path(__file__).with_name("filtermax_slides.xml")
    )

  def test_parse_and_resolve(self) -> None:
    selection = self.catalog.resolve(kind="excitation", technique="absorbance", wavelength=450)
    self.assertEqual(selection.slide_id, 2)
    self.assertEqual(selection.position, 4)
    self.assertEqual(selection.bandwidth, 8)

    lum = self.catalog.resolve(kind="emission", technique="luminescence", wavelength=0)
    self.assertEqual((lum.slide_id, lum.position), (1, 6))

  def test_incompatible_filter_is_rejected(self) -> None:
    with self.assertRaises(FilterMaxFilterNotFoundError):
      self.catalog.resolve(kind="excitation", technique="fluorescence", wavelength=450)

  def test_ambiguous_wavelength_requires_slot(self) -> None:
    with self.assertRaises(FilterMaxAmbiguousFilterError):
      self.catalog.resolve(
        kind="emission",
        technique="fluorescence_polarization",
        wavelength=535,
      )
    selected = self.catalog.resolve(
      kind="emission",
      technique="fluorescence_polarization",
      wavelength=535,
      slide_id=1,
      position=3,
    )
    self.assertEqual(selected.position, 3)

  def test_unknown_slide_id_is_rejected(self) -> None:
    with self.assertRaises(FilterMaxFilterNotFoundError):
      self.catalog.resolve(
        kind="excitation",
        technique="absorbance",
        wavelength=450,
        slide_id=99,
      )
