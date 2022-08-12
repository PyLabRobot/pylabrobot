import unittest

from .liquid_classes import LiquidClass, LiquidDevice, TipType


class TestLiquidClass(unittest.TestCase):
  """ Tests for liquid classes """

  def test_auto_zero(self):
    # empty correction curve should not have 0
    lc = LiquidClass(
      device=[LiquidDevice.CHANNELS_1000uL],
      tip_type=TipType.HIGH_VOLUME_TIP_WITH_FILTER_1000uL,
      dispense_mode=2,
      pressure_lld=0,
      max_height_difference=0,
      flow_rate=(100, 180),
      mix_flow_rate=(75, 75),
      air_transport_volume=(5, 5),
      blowout_volume=(30, 30),
      swap_speed=(2, 2),
      settling_time=(1, 0),
      over_aspirate_volume=0,
      clot_retract_height=0,
      stop_flow_rate=100,
      stop_back_volume=0,
      correction_curve={}
    )
    self.assertNotIn(0, lc.correction_curve)

    # non-empty correction curve auto-has 0
    lc2 = LiquidClass(
      device=[LiquidDevice.CHANNELS_1000uL],
      tip_type=TipType.HIGH_VOLUME_TIP_WITH_FILTER_1000uL,
      dispense_mode=2,
      pressure_lld=0,
      max_height_difference=0,
      flow_rate=(100, 180),
      mix_flow_rate=(75, 75),
      air_transport_volume=(5, 5),
      blowout_volume=(30, 30),
      swap_speed=(2, 2),
      settling_time=(1, 0),
      over_aspirate_volume=0,
      clot_retract_height=0,
      stop_flow_rate=100,
      stop_back_volume=0,
      correction_curve={40: 42}
    )
    self.assertIn(0, lc2.correction_curve)


  def test_compute_corrected_volume(self):
    # test volume correction
    lc = LiquidClass(
      device=[LiquidDevice.CHANNELS_1000uL],
      tip_type=TipType.HIGH_VOLUME_TIP_WITH_FILTER_1000uL,
      dispense_mode=2,
      pressure_lld=0,
      max_height_difference=0,
      flow_rate=(100, 180),
      mix_flow_rate=(75, 75),
      air_transport_volume=(5, 5),
      blowout_volume=(30, 30),
      swap_speed=(2, 2),
      settling_time=(1, 0),
      over_aspirate_volume=0,
      clot_retract_height=0,
      stop_flow_rate=100,
      stop_back_volume=0,
      correction_curve={1: 2, 2: 8, 3: 27}
    )

    self.assertIn(0, lc.correction_curve)

    self.assertEqual(0, lc.correction_curve[0])

    corrected = lc.compute_corrected_volume(0)
    self.assertEqual(0, corrected)

    corrected = lc.compute_corrected_volume(0.5)
    self.assertEqual(1, corrected)

    corrected = lc.compute_corrected_volume(1)
    self.assertEqual(corrected, 2)

    corrected = lc.compute_corrected_volume(1.5)
    self.assertEqual(corrected, 5)

    corrected = lc.compute_corrected_volume(2.5)
    self.assertEqual(corrected, 17.5)

    corrected = lc.compute_corrected_volume(9)
    self.assertEqual(corrected, 81)


if __name__ == "__main__":
  unittest.main()
