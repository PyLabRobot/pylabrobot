import json
import unittest

from pylabrobot.resources import McKesson_tube_5mL_Rb
from pylabrobot.resources.tube import Tube, TubeBottomType


class McKessonTubeTests(unittest.TestCase):
  def test_geometry_and_calibration_survive_json_round_trip(self):
    tube = McKesson_tube_5mL_Rb(name="mckesson_tube")
    restored = Tube.deserialize(json.loads(json.dumps(tube.serialize())))

    self.assertEqual(restored.bottom_type, TubeBottomType.U)
    self.assertEqual(restored.get_anchor(z="cavity_bottom").z, 1.0)
    self.assertAlmostEqual(restored.compute_height_from_volume(2_500), 32.998568533)
    self.assertAlmostEqual(restored.compute_volume_from_height(32.998568533), 2_500)

  def test_height_outside_nominal_fit_raises(self):
    tube = McKesson_tube_5mL_Rb(name="mckesson_tube")
    with self.assertRaises(ValueError):
      tube.compute_volume_from_height(0)
