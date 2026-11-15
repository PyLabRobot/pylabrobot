"""The pod's own collision check: the gripper against the pipettor."""

import dataclasses
import unittest

from pylabrobot.beckman_coulter.biomek_i7.driver import pod_geometry as g
from pylabrobot.beckman_coulter.biomek_i7.simulation_tests_support import generic_configuration
from pylabrobot.beckman_coulter.biomek_i7.driver.planning import check, pod_model


class PodCollisionTests(unittest.TestCase):
  def setUp(self) -> None:
    self.right = generic_configuration().pod("right")

  def probes(self, y: float, z: float, span: float = 6.3, tip: float = 0.0) -> pod_model.PipettorState:
    return pod_model.PipettorState(tuple(y + i * span / 7 for i in range(8)), (z,) * 8, (tip,) * 8)

  def test_the_parked_gripper_is_clear_of_the_probes(self) -> None:
    # The park: GR 90, GY 48, GZ at its top, GG 7.889; the probes over the wash station.
    gripper = pod_model.GripperState(48.0, 37.8, 90.0, 7.889)
    self.assertEqual(check.own_collisions_at(self.right, pod_model.PodState(120.0, gripper, self.probes(58.275, 39.1))), [])

  def test_fingers_turned_under_the_probes_collide(self) -> None:
    # GR 0 points the fingers along +X, under the probe axes; with the probes over them in Y
    # and the fingers' top above the probes' tips, they meet.
    gripper = pod_model.GripperState(30.0, 30.0, 0.0, 9.0)
    found = check.own_collisions_at(self.right, pod_model.PodState(120.0, gripper, self.probes(28.0, 20.0)))
    self.assertIn(("fingers", "probe 1"), found)

  def test_probes_at_the_far_end_of_y_clear_the_turned_fingers(self) -> None:
    # Logged before a turn to 0 degrees with GY 61.26: the probes sent to Y 15.274, span 6.3.
    gripper = pod_model.GripperState(61.263, 30.0, 0.0, 9.706)
    self.assertEqual(check.own_collisions_at(self.right, pod_model.PodState(120.0, gripper, self.probes(15.274, 20.0))), [])

  def test_held_labware_counts(self) -> None:
    held = g.HeldLabware(12.78, 8.56, height=1.41, grip_offset=-0.23)
    gripper = pod_model.GripperState(30.0, 30.0, 0.0, 8.5, held)
    self.assertIn("held labware", [p.name for p in pod_model.gripper_parts(self.right, 120.0, gripper)])

  def test_a_turn_through_the_probes_is_caught_on_its_way(self) -> None:
    # From 180 degrees (fingers away from the probes) to 90: never under the probes. To 0 and
    # back is caught at 0, the end; a sweep from 180 to -90 passes 0 only between its ends.
    a = pod_model.GripperState(30.0, 30.0, 180.0, 9.0)
    b = pod_model.GripperState(30.0, 30.0, -180.0, 9.0)
    probes = self.probes(28.0, 20.0)
    self.assertEqual(check.own_collisions_at(self.right, pod_model.PodState(120.0, a, probes)), [])
    self.assertEqual(check.own_collisions_at(self.right, pod_model.PodState(120.0, b, probes)), [])
    found = check.own_collisions(
      self.right, pod_model.PodState(120.0, a, probes), pod_model.PodState(120.0, b, probes)
    )
    self.assertTrue(found)

  def bundle(self, z: float = 30.0) -> pod_model.BundleBox:
    box = pod_model.span8_bundle_box(self.right, 120.0, self.probes(28.0, z))
    assert box is not None
    return box

  def test_the_tubing_bundle_hangs_beside_the_probes_towards_its_anchor(self) -> None:
    wire = self.right.settings["LLSWireModel"]
    box = self.bundle()
    self.assertAlmostEqual(box.x0, 120.0 + wire["WireStartXOffset"])
    self.assertAlmostEqual(box.x1, 120.0 + wire["AnchorXOffset"])
    # The anchor is in front of these probes: the box reaches halfway to its far end.
    self.assertAlmostEqual(box.y0, 28.0)
    far = wire["AnchorCenterY"] + wire["AnchorHalfLength"]
    self.assertAlmostEqual(box.y1, (34.3 + far) / 2)

  def test_the_bundle_loop_drops_half_as_far_as_the_lowest_probe(self) -> None:
    # Low enough that the loop's bottom, not where the bundle leaves the probes, is lowest.
    self.assertAlmostEqual(self.bundle(26.0).z_low - self.bundle(30.0).z_low, -2.0)
    # At the top the bundle leaves the probes below the loop's bottom.
    wire, s = self.right.settings["LLSWireModel"], self.right.settings
    top = self.right.limits["Z1"][1]
    top_box = pod_model.span8_bundle_box(
      self.right, 120.0, pod_model.PipettorState((28.0,) * 8, (top,) * 8, (0.0,) * 8)
    )
    assert top_box is not None
    self.assertLessEqual(
      top_box.z_low, top + s["MandrelExtender"] + wire["FixedTipZOriginToWireOffset"]
    )

  def test_the_bundle_does_not_guard_the_gripper(self) -> None:
    # Biomek's prime approach sweeps the probes from Y 15.1 to 58.3 with the gripper as homing
    # left it (GR -2.2 degrees, GY 39.9, at its top); around Y 47 the bundle's box covers the
    # fingers, and Biomek sends the move all the same.
    gripper = pod_model.GripperState(39.9, 37.931, -2.18, 7.937)
    probes = self.probes(47.86, 39.2)
    box = pod_model.span8_bundle_box(self.right, 145.4, probes)
    assert box is not None
    fingers = next(p for p in pod_model.gripper_parts(self.right, 145.4, gripper) if p.name == "fingers")
    self.assertTrue(
      check.overlap(
        fingers, pod_model.Part("bundle", pod_model._box(box.x0, box.x1, box.y0, box.y1), box.z_low, pod_model.OPEN)
      )
    )
    self.assertEqual(check.own_collisions_at(self.right, pod_model.PodState(145.4, gripper, probes)), [])

  def test_a_span8_without_a_bundle_model_is_refused(self) -> None:
    # No quiet substitution: a Span-8's entry gives its bundle model.
    with self.assertRaisesRegex(ValueError, "LLSWireModel"):
      dataclasses.replace(
        self.right, settings={k: v for k, v in self.right.settings.items() if k != "LLSWireModel"}
      )

  def test_a_pod_without_a_setting_it_uses_is_refused(self) -> None:
    for key in ("AStarSearchLimit", "GripperSqueezeForce", "ProbeSize"):
      settings = {k: v for k, v in self.right.settings.items() if k != key}
      with self.assertRaisesRegex(ValueError, key):
        dataclasses.replace(self.right, settings=settings)


if __name__ == "__main__":
  unittest.main()
