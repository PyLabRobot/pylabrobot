"""The pods' models in the resource tree: built from the declared geometry, following each pod's X,
and taking up the X the rail guard counts (`driver/pod_geometry.py`)."""

import unittest

from pylabrobot.beckman_coulter.biomek_i7.deck import FRAME_X, biomek_y
from pylabrobot.beckman_coulter.biomek_i7.device import (
  DECK_X,
  DECK_Y,
  DECK_Z,
  BiomekI7,
  BiomekI7Device,
)
from pylabrobot.beckman_coulter.biomek_i7.driver import pod_geometry as g
from pylabrobot.beckman_coulter.biomek_i7.driver.pod_move_tests import PodMoveTest
from pylabrobot.beckman_coulter.biomek_i7.resource_model import MM_PER_CM
from pylabrobot.beckman_coulter.biomek_i7.resource_model.pods import gripper_model
from pylabrobot.resources import Resource
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.rotation import Rotation
from pylabrobot.beckman_coulter.biomek_i7.simulation_tests_support import (
  generic_biomek,
  generic_configuration,
  generic_deck,
)


def biomek_x_range(lh: BiomekI7Device, resource: Resource) -> g.Range:
  """A resource's X extent in Biomek's X, cm."""
  low = (resource.get_location_wrt(lh).x - DECK_X - FRAME_X) / MM_PER_CM
  return low, low + resource.get_size_x() / MM_PER_CM


class DeviceModelTests(PodMoveTest):
  async def test_the_models_take_up_the_x_the_rail_counts(self) -> None:
    driver = await self.homed()
    lh = BiomekI7(driver=driver, deck=generic_deck())
    left, right = self.pod(driver, "left"), self.pod(driver, "right")
    await left.move_axes({"X": 100000})
    await right.move_axes({"X": right.axis("X").home_position})
    for pod in (left, right):
      assert pod.declared is not None
      geometry = pod.declared.geometry
      x = pod.declared.to_value("X", pod.cpositions["X"][0] or 0)
      bridge = g.bridge_x_range(geometry, x)
      pipettor = g.pipettor_x_range(geometry, x)
      margin = geometry.avoidance_margin
      with self.subTest(side=pod.side):
        for got, want in zip(biomek_x_range(lh, lh.bridges[pod.side]), bridge):
          self.assertAlmostEqual(got, want, delta=1e-4)  # 1 um: PyLabRobot rounds coordinates
        for got, want in zip(
          biomek_x_range(lh, lh.pod_models[pod.side]), (pipettor[0] + margin, pipettor[1] - margin)
        ):
          self.assertAlmostEqual(got, want, delta=1e-4)  # 1 um: PyLabRobot rounds coordinates

  async def test_a_bridge_follows_its_pod(self) -> None:
    driver = await self.homed()
    lh = BiomekI7(driver=driver, deck=generic_deck())
    left = self.pod(driver, "left")
    assert left.declared is not None
    for counts in (60000, 140000):
      await left.move_axes({"X": counts})
      x = left.declared.to_value("X", counts)
      self.assertAlmostEqual(
        biomek_x_range(lh, lh.bridges["left"])[0], g.bridge_x_range(left.declared.geometry, x)[0]
      )
    await lh.stop()  # stops following
    before = lh.bridges["left"].location
    left._positions_changed()
    self.assertEqual(lh.bridges["left"].location, before)

  async def test_the_multichannel_follows_y_and_z_at_mandrel_a1(self) -> None:
    driver = await self.homed()
    lh = BiomekI7(driver=driver, deck=generic_deck())
    left = self.pod(driver, "left")
    d = left.declared
    assert d is not None
    # Homed, GR is at -16 degrees: the fingers under the head. Turn them out first, as the park does.
    await left.move_axes({"GR": d.to_counts("GR", d.park["GR"])})
    await left.move_axes({"Y": 50000, "Z": -60000})
    y, z = d.to_value("Y", 50000), d.to_value("Z", -60000)
    pod = lh.pod_models["left"]
    back = pod.get_location_wrt(lh).y - DECK_Y + pod.get_size_y()  # deck y grows to the back
    self.assertAlmostEqual(biomek_y(back) / MM_PER_CM, y - d.geometry.mandrel1_y_offset, delta=1e-4)
    head = lh.get_resource("left_pod_head")
    self.assertAlmostEqual((head.get_location_wrt(lh).z - DECK_Z) / MM_PER_CM, z, delta=1e-4)
    # Mandrel A1's shaft is over A1's X and Y, its end at Z; the carriage did not move in Z.
    a1 = head.get_item("A1")  # type: ignore[attr-defined]
    centre = a1.get_location_wrt(lh) + Coordinate(a1.get_size_x() / 2, a1.get_size_y() / 2, 0)
    self.assertAlmostEqual(biomek_y(centre.y - DECK_Y) / MM_PER_CM, y, delta=1e-4)
    self.assertAlmostEqual(
      (centre.x - DECK_X - FRAME_X) / MM_PER_CM,
      d.to_value("X", left.cpositions["X"][1]),
      delta=1e-4,
    )
    self.assertAlmostEqual((centre.z - DECK_Z) / MM_PER_CM, z, delta=1e-4)
    carriage = pod.get_location_wrt(lh).z
    await left.move_axes({"Z": -20000})
    self.assertAlmostEqual(pod.get_location_wrt(lh).z, carriage)

  async def test_each_arms_line_marks_the_x_its_pod_names(self) -> None:
    driver = await self.homed()
    lh = BiomekI7(driver=driver, deck=generic_deck())
    left, right = self.pod(driver, "left"), self.pod(driver, "right")
    await right.move_axes({"X": right.axis("X").home_position})
    for pod, counts in ((left, 60000), (left, 140000), (right, right.axis("X").home_position)):
      if pod is left:
        await left.move_axes({"X": counts})
      d = pod.declared
      assert d is not None and pod.resource is lh.bridges[pod.side]
      bridge = pod.resource
      line = bridge.get_location_wrt(lh).x + bridge.reference_point.x  # type: ignore[attr-defined]
      x = d.to_value("X", counts)  # cm, Biomek's X: what mandrel A1 / probe 1 is at
      self.assertAlmostEqual((line - DECK_X - FRAME_X) / MM_PER_CM, x, delta=1e-4)
      # The pod's own mark is at the same X.
      model = lh.pod_models[pod.side]
      ref = model.reference_point  # type: ignore[attr-defined]
      ref_x = ref["x"] if isinstance(ref, dict) else ref.x
      self.assertAlmostEqual(model.get_location_wrt(lh).x + ref_x, line, delta=1e-3)

  async def test_the_multichannel_marks_mandrel_a1(self) -> None:
    driver = await self.homed()
    lh = BiomekI7(driver=driver, deck=generic_deck())
    left = self.pod(driver, "left")
    d = left.declared
    assert d is not None
    await left.move_axes({"GR": d.to_counts("GR", d.park["GR"])})
    await left.move_axes({"X": 100000, "Y": 50000})
    pod = lh.pod_models["left"]
    ref = pod.reference_point  # type: ignore[attr-defined]
    at = pod.get_location_wrt(lh)
    self.assertAlmostEqual(
      (at.x + ref["x"] - DECK_X - FRAME_X) / MM_PER_CM, d.to_value("X", 100000), delta=1e-4
    )
    self.assertAlmostEqual(
      biomek_y(at.y + ref["y"] - DECK_Y) / MM_PER_CM, d.to_value("Y", 50000), delta=1e-4
    )
    lo, hi = ref["y_range"]
    self.assertAlmostEqual((lo + hi) / 2, ref["y"])

  def test_the_models_are_the_declared_pods_before_setup(self) -> None:
    lh = generic_biomek()
    for side, declared in lh.driver.declared.pods.items():
      self.assertAlmostEqual(lh.bridges[side].get_size_x(), declared.geometry.bridge_x_span * 10)
    self.assertEqual(lh.pod_models["right"].name, "right_pod")
    self.assertEqual(set(lh.gripper_models), {"left", "right"})

  def test_the_96_head_stands_its_mandrels_out_of_its_body(self) -> None:
    lh = generic_biomek()
    settings = lh.driver.declared.pods["left"].settings
    head = lh.get_resource("left_pod_head")
    # Flange to the mandrels' end: Head/PodToMandrel1 Z.
    self.assertAlmostEqual(head.get_size_z(), 105.245, delta=1e-6)
    for name in ("A1", "H12"):
      shaft = head.get_item(name)  # type: ignore[attr-defined]
      with self.subTest(shaft=name):
        assert shaft.location is not None
        self.assertAlmostEqual(shaft.location.z, 0.0)  # its end is the head's bottom
        # It stands out of the body by the head's seating depth: a seated tip meets the body.
        self.assertAlmostEqual(
          shaft.get_size_z(), settings["TipSeatingDepth"] * MM_PER_CM, delta=1e-6
        )

  def test_the_span8_channels_stand_apart_at_their_narrowest(self) -> None:
    lh = generic_biomek()
    settings = lh.driver.declared.pods["right"].settings
    pod = lh.pod_models["right"]
    channels = [lh.get_resource(f"right_pod_channel_{i}") for i in range(1, 9)]
    for front, back in zip(channels[1:], channels):  # probe 1 is at the back (deck y grows back)
      gap = back.get_location_wrt(lh).y - (front.get_location_wrt(lh).y + front.get_size_y())
      with self.subTest(probe=channels.index(front) + 1):
        self.assertGreater(gap, 0.5)
    for channel in channels:
      shaft = channel.children[0]
      with self.subTest(channel=channel.name):
        # The probe is as wide as its tip hub, and each channel spans the pod in X.
        self.assertAlmostEqual(
          shaft.get_size_x(), 2 * settings["DefaultTipHubInfo"]["MaxRadius"] * MM_PER_CM
        )
        self.assertAlmostEqual(
          channel.get_location_wrt(lh).x, pod.get_location_wrt(lh).x, delta=1e-3
        )
        self.assertAlmostEqual(channel.get_size_x(), pod.get_size_x())

  async def test_the_span8_probes_follow_y_span_and_z(self) -> None:
    driver = await self.homed()
    lh = BiomekI7(driver=driver, deck=generic_deck())
    right = self.pod(driver, "right")
    d = right.declared
    assert d is not None
    park = d.park
    await right.move_axes({a: d.to_counts(a, park[a]) for a in ("GR", "GY")})  # gripper clear
    y1, span = 30.0, 14.0  # cm: probe 1's Y, and probe 1 to 8
    await right.move_axes({"Y": d.to_counts("Y", y1), "S": d.to_counts("S", span)})
    await right.move_axes({"Z3": d.to_counts("Z3", 25.0)})
    x = d.to_value("X", right.cpositions["X"][1])
    for probe in range(1, 9):
      channel = lh.get_resource(f"right_pod_channel_{probe}")
      end = channel.get_location_wrt(lh) + channel.reference_point  # type: ignore[attr-defined]
      with self.subTest(probe=probe):
        # Within a count's rounding: each probe's Y is a whole number of counts.
        self.assertAlmostEqual(
          biomek_y(end.y - DECK_Y) / MM_PER_CM, y1 + (probe - 1) * span / 7, delta=1e-3
        )
        self.assertAlmostEqual((end.x - DECK_X - FRAME_X) / MM_PER_CM, x, delta=1e-3)
    channel = lh.get_resource("right_pod_channel_3")
    end = channel.get_location_wrt(lh) + channel.reference_point  # type: ignore[attr-defined]
    self.assertAlmostEqual((end.z - DECK_Z) / MM_PER_CM, 25.0, delta=1e-3)
    # The carriage follows probe 1.
    pod = lh.pod_models["right"]
    back = pod.get_location_wrt(lh).y - DECK_Y + pod.get_size_y()
    self.assertAlmostEqual(
      biomek_y(back) / MM_PER_CM, y1 - d.geometry.mandrel1_y_offset, delta=1e-3
    )

  async def test_after_home_the_bridge_and_the_gripper_agree_on_x(self) -> None:
    driver = await self.homed()
    lh = BiomekI7(driver=driver, deck=generic_deck())
    for side in ("left", "right"):
      pod = self.pod(driver, side)
      d = pod.declared
      assert d is not None
      x = d.to_value("X", pod.cpositions["X"][1])  # adjusted: what the model follows
      with self.subTest(side=side):
        self.assertAlmostEqual(
          biomek_x_range(lh, lh.bridges[side])[0], g.bridge_x_range(d.geometry, x)[0], delta=1e-4
        )
        gripper = lh.gripper_models[side].gripper
        centre = gripper.get_location_wrt(lh) + gripper.proximal_joint.rotated(
          gripper.get_absolute_rotation()
        )
        finger = d.gripper_model["Finger"]
        want = x + d.geometry.gripper_x_offset - finger["PadsCenterToRotationCenter"]
        self.assertAlmostEqual((centre.x - DECK_X - FRAME_X) / MM_PER_CM, want, delta=1e-3)


class GripperModelTests(unittest.TestCase):
  """The gripper's model posed by hand: it turns about the rotation centre to any GR, its fingers
  span the instrument file's finger outline, and its pads stand GG apart."""

  def setUp(self) -> None:
    self.declared = generic_configuration().pod("left")
    self.model = gripper_model("g", self.declared.gripper_model, self.declared.limits["GG"])
    self.finger = self.declared.gripper_model["Finger"]

  def test_the_tool_centre_point_turns_with_gr_about_the_centre(self) -> None:
    centre = Coordinate(100, 200, 300)
    gripper = self.model.gripper
    for gr in (-16.0, 0.0, 37.5, 90.0, 180.0):
      self.model.pose(centre, gr, 8.0)
      with self.subTest(gr=gr):
        turn = Rotation(z=-gr)
        joint = gripper.location + gripper.proximal_joint.rotated(turn)  # type: ignore[operator]
        for got, want in zip(joint.vector(), centre.vector()):
          self.assertAlmostEqual(got, want, places=6)
        tcp = gripper.location + self.model.pads().rotated(turn)  # type: ignore[operator]
        along = Coordinate(
          self.finger["PadsCenterToRotationCenter"] * MM_PER_CM,
          -self.finger["FingerCenterOffset"] * MM_PER_CM,
          -self.model.depth * MM_PER_CM,
        ).rotated(turn)
        for got, want in zip(tcp.vector(), (centre + along).vector()):
          self.assertAlmostEqual(got, want, places=6)

  def test_the_fingers_span_the_outline_from_the_centre(self) -> None:
    self.model.pose(Coordinate.zero(), 90.0, 8.0)
    gripper = self.model.gripper
    xs = [p["X"] for p in self.finger["Base"]]
    for finger in gripper.fingers:
      assert finger.location is not None
      start = (finger.location.x - gripper.proximal_joint.x) / MM_PER_CM
      self.assertAlmostEqual(start, min(xs), places=6)
      self.assertAlmostEqual(start + finger.get_size_x() / MM_PER_CM, max(xs), places=6)
      self.assertAlmostEqual(
        (finger.location.z - gripper.proximal_joint.z) / MM_PER_CM, -self.model.depth, places=6
      )

  def test_the_pads_stand_gg_apart(self) -> None:
    gripper = self.model.gripper
    for gg in (6.5, 8.0, 12.0):
      self.model.pose(Coordinate.zero(), 0.0, gg)
      faces = []
      for finger, pad in zip(gripper.fingers, gripper.pads):
        assert finger.location is not None and pad.location is not None
        low = finger.location.y + pad.location.y
        faces.append((low, low + pad.get_size_y()))
      gap = faces[0][0] - faces[1][1]  # the first pad's face is its low side
      self.assertAlmostEqual(gap / MM_PER_CM, gg, places=6)


class HeldStackTests(unittest.TestCase):
  def test_a_layer_wider_than_the_one_below_widens_the_footprint(self) -> None:
    pod = generic_configuration().pod("left")
    model, geometry = g.GripperModel.from_bif(pod.gripper_model), pod.geometry
    base = g.HeldLabware(length=12.776, width=8.548)
    # A layer hanging 1 cm past the plate's far end along the fingers.
    stacked = g.HeldLabware(12.776, 8.548, layers=(g.StackedLayer(1.0, 0.0, 12.776, 8.548),))
    pose = g.GripperPose(0.0, 7.889)
    plain = g.gripper_x_range(geometry, model, 0.0, g.GripperPose(pose.gr, pose.gg, base))
    wider = g.gripper_x_range(geometry, model, 0.0, g.GripperPose(pose.gr, pose.gg, stacked))
    self.assertAlmostEqual(wider[1] - plain[1], 1.0)
    self.assertAlmostEqual(wider[0], plain[0])
