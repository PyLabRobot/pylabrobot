"""What each pod carries (`head96`, `pipettes`, `gripper`), the instrument-level shortcuts to them,
and finding a pod by the names Biomek gives it."""

from pylabrobot.beckman_coulter.biomek_i7.device import BiomekI7
from pylabrobot.beckman_coulter.biomek_i7.driver.pod_move_tests import PodMoveTest
from pylabrobot.beckman_coulter.biomek_i7.simulation_tests_support import (
  generic_biomek,
  generic_driver,
  generic_simulated_boards,
)


class PodFeaturesTests(PodMoveTest):
  async def test_each_pod_carries_what_its_module_has(self) -> None:
    driver = await self.homed()
    left, right = self.pod(driver, "left"), self.pod(driver, "right")
    self.assertEqual((left.module_type, right.module_type), ("multichannel", "span8"))
    self.assertIsNotNone(left.head96)
    self.assertIsNone(left.pipettes)
    self.assertIsNone(right.head96)
    self.assertIsNotNone(right.pipettes)
    self.assertIsNotNone(left.gripper)
    self.assertIsNotNone(right.gripper)

  async def test_shortcuts_resolve_only_when_one_pod_has_the_feature(self) -> None:
    driver = await self.homed()
    left, right = self.pod(driver, "left"), self.pod(driver, "right")
    self.assertIs(driver.head96, left.head96)
    self.assertIs(driver.pipettes, right.pipettes)
    with self.assertRaisesRegex(ValueError, "left_x_arm.gripper"):
      driver.gripper
    with self.assertRaisesRegex(ValueError, "left_x_arm"):
      driver.x_arm
    self.assertEqual(driver.arms, [left, right])
    driver.right_x_arm = None  # an instrument with one pod
    self.assertIs(driver.gripper, left.gripper)
    self.assertIsNone(driver.pipettes)
    self.assertIs(driver.x_arm, left)

  async def test_each_feature_knows_its_arm(self) -> None:
    driver = await self.homed()
    left, right = self.pod(driver, "left"), self.pod(driver, "right")
    assert left.head96 is not None and right.pipettes is not None
    self.assertIs(left.head96.arm, left)
    self.assertIs(right.pipettes.arm, right)
    for pod in (left, right):
      assert pod.gripper is not None
      self.assertIs(pod.gripper.arm, pod)

  async def test_the_device_reads_through_to_the_driver(self) -> None:
    driver = await self.homed()
    lh = BiomekI7(driver=driver)
    self.assertIs(lh.left_x_arm, driver.left_x_arm)
    self.assertIs(lh.right_x_arm, driver.right_x_arm)
    self.assertIs(lh.head96, driver.head96)
    self.assertIs(lh.pipettes, driver.pipettes)
    self.assertIs(lh.chassis, driver.chassis)
    self.assertEqual(lh.arms, driver.arms)
    with self.assertRaises(ValueError):
      lh.gripper
    with self.assertRaises(ValueError):
      lh.x_arm
    self.assertIsNone(generic_biomek().left_x_arm)  # not yet set up

  async def test_a_pod_by_any_name_biomek_gives_it(self) -> None:
    driver = await self.homed()
    left, right = self.pod(driver, "left"), self.pod(driver, "right")
    for name in ("left", 1, "Pod1", "MC_1"):
      self.assertIs(driver.arm(name), left)
    for name in ("right", 2, "Pod2", "SPAN8_2"):
      self.assertIs(driver.arm(name), right)
    with self.assertRaises(KeyError):
      driver.arm("Pod3")


class StarAlignmentTests(PodMoveTest):
  """The second pass's alignments with STAR (PLR_PARITY_AUDIT.md)."""

  async def test_x_arm_refuses_as_the_stars_does(self) -> None:

    with self.assertRaises(RuntimeError):  # not read yet
      generic_driver(generic_simulated_boards()).x_arm
    driver = await self.homed()
    with self.assertRaises(ValueError):  # two pods
      driver.x_arm
    driver.left_x_arm = driver.right_x_arm = None
    with self.assertRaisesRegex(ValueError, "no pod"):
      driver.x_arm

  async def test_the_span8s_settings_and_trash_are_checked_and_named(self) -> None:
    pipettes = self.pod(await self.homed(), "right").pipettes
    assert pipettes is not None
    with self.assertRaises(ValueError):
      await pipettes._set_lls_sensitivity(5000)
    with self.assertRaises(TypeError):  # the trash is keyword-only: no offsets taken for it
      await pipettes.discard_tips([0], [object()])  # type: ignore[arg-type, misc]

  async def test_star_shaped_calls_cannot_land_on_the_wrong_argument(self) -> None:
    driver = await self.homed()
    head96, pipettes = self.pod(driver, "left").head96, self.pod(driver, "right").pipettes
    assert head96 is not None and pipettes is not None
    # STAR's positional arguments past the targets and offsets (a pickup method, the heights in
    # its own order) are refused rather than taken as heights.
    with self.assertRaises(TypeError):
      await pipettes.pick_up_tips([], [0], None, 100.0)  # type: ignore[misc]
    with self.assertRaises(TypeError):
      await head96.pick_up_tips(None, None, 100.0)  # type: ignore[arg-type, misc]
    with self.assertRaises(TypeError):
      await head96.drop_tips(None, None, 100.0)  # type: ignore[arg-type, misc]
    with self.assertRaises(TypeError):
      await head96.discard_tips(None)  # type: ignore[misc]
    # STAR's per-channel lists; the template applies one value to every probe.
    with self.assertRaisesRegex(ValueError, "settling_times: one value"):
      await pipettes.aspirate([], [], settling_times=[0.1, 0.2])
    with self.assertRaisesRegex(ValueError, "blow_out_air_volumes: one value"):
      await pipettes.dispense([], [], blow_out_air_volumes=[5.0, 10.0])

  async def test_setup_skips_name_features_as_the_star_does(self) -> None:
    driver = await self.homed()
    self.assertEqual(driver.skipped_sides(skip_head96=True), {"left"})
    self.assertEqual(driver.skipped_sides(skip_pipettes=True), {"right"})
    self.assertEqual(
      driver.skipped_sides(skip_pipettes=True, skip_left_x_arm=True), {"left", "right"}
    )
    self.assertEqual(driver.skipped_sides(), set())

  def test_return_resource_takes_place_resources_arguments(self) -> None:
    import inspect

    from pylabrobot.beckman_coulter.biomek_i7.driver.features.gripper import Gripper

    place = inspect.signature(Gripper.place_resource).parameters
    back = inspect.signature(Gripper.return_resource).parameters
    self.assertEqual([n for n in place if n != "to"], list(back))  # as STAR's return_resource

  async def test_axis_states_are_names(self) -> None:
    states = await self.pod(await self.homed(), "left").request_axis_states()
    self.assertEqual(set(states.values()), {"enabled"})

  async def test_the_grip_direction_default_is_an_attribute(self) -> None:
    driver = await self.homed()
    left, right = self.pod(driver, "left").gripper, self.pod(driver, "right").gripper
    assert left is not None and right is not None
    self.assertIsNone(left.default_direction)
    # Each pod's own: the fingers point away from its side, the hand on the labware's near side.
    self.assertEqual(left._direction(None), "right")
    self.assertEqual(right._direction(None), "left")
    left.default_direction = "front"
    self.assertEqual(left._direction(None), "front")
    self.assertEqual(left._direction("back"), "back")
    self.assertEqual(left._direction(-270.0), "back")  # degrees on the deck, as STAR's iSWAP
    with self.assertRaises(ValueError):
      left._direction(45.0)
    with self.assertRaises(ValueError):
      left._direction("up")  # type: ignore[arg-type]

  def test_grip_directions_are_half_a_turn_from_the_legacy_hand_sides(self) -> None:
    from pylabrobot.beckman_coulter.biomek_i7.driver.features.gripper import GRIP_ANGLES

    # Biomek's GR for each, as its logged grips show them.
    # 0 points the fingers along +x (the hand on the labware's left), 90 to the front.
    self.assertEqual(GRIP_ANGLES, {"right": 0.0, "front": 90.0, "left": 180.0, "back": -90.0})
