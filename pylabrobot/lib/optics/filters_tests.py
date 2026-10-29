import unittest

from pylabrobot.lib.optics.filters import DichroicFilter, OpticalFilter


class OpticalFilterTests(unittest.TestCase):
  def test_a_single_band_has_a_centre_and_bandwidth(self):
    em_615_18 = OpticalFilter(1, "615-18", passbands=((604.9, 623.2),))  # a CLARIOstar's report
    center, bandwidth = em_615_18.center_wavelength, em_615_18.bandwidth
    assert center is not None and bandwidth is not None
    self.assertAlmostEqual(center, 614.05)
    self.assertAlmostEqual(bandwidth, 18.3)

  def test_several_or_open_bands_have_none(self):
    quad = OpticalFilter(1, passbands=((370, 410), (473, 491), (559, 568.2), (632.8, 647.1)))
    self.assertIsNone(quad.center_wavelength)
    self.assertIsNone(OpticalFilter(1, passbands=((500, None),)).bandwidth)
    self.assertIsNone(OpticalFilter(1).center_wavelength)

  def test_bands_that_are_not_bands_are_refused(self):
    for passbands in (
      ((500, 480),),
      ((-1, 10),),
      ((400, 450), (440, 500)),
      ((400, None), (500, 600)),
    ):
      with self.subTest(passbands), self.assertRaises(ValueError):
        OpticalFilter(1, passbands=passbands)


class DichroicFilterTests(unittest.TestCase):
  def test_a_quad_band_dichroic_leaves_its_gaps_unspecified(self):
    # Semrock Di03-R405/488/561/635: reflection and transmission bands with gaps between them.
    DichroicFilter(
      1,
      reflection_bands=((370, 410), (473, 491), (559, 568.2), (632.8, 647.1)),
      transmission_bands=((429.5, 462), (502.5, 544.5), (582, 617.5), (663, 800)),
    )

  def test_reflection_and_transmission_must_not_overlap(self):
    with self.assertRaises(ValueError):
      DichroicFilter(1, reflection_bands=((400, 500),), transmission_bands=((490, None),))
