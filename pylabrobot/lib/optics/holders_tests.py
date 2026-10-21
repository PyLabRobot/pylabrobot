import unittest

from pylabrobot.lib.optics.filters import DichroicFilter, OpticalFilter
from pylabrobot.lib.optics.holders import DichroicFilterSlide, EmissionFilterSlide


class FilterSlideTests(unittest.TestCase):
  def test_a_slot_outside_the_slide_is_refused(self):
    slide = DichroicFilterSlide(maximum_slots=3)
    with self.assertRaises(ValueError):
      slide.register(DichroicFilter(4, "LP TR"))

  def test_an_empty_slot_gives_an_anonymous_filter(self):
    slide = EmissionFilterSlide(maximum_slots=4)
    slide.register(OpticalFilter(1, "615-18", passbands=((604.9, 623.2),)))
    self.assertEqual(slide[1].name, "615-18")
    self.assertEqual(slide[2], OpticalFilter(2))

  def test_a_replaced_filter_is_no_longer_found_by_name(self):
    slide = EmissionFilterSlide(maximum_slots=4)
    slide.register(OpticalFilter(1, "615-18"))
    slide.register(OpticalFilter(1, "520-20"))
    self.assertEqual(slide.by_name("520-20").slot, 1)
    with self.assertRaises(KeyError):
      slide.by_name("615-18")
