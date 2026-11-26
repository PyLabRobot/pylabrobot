"""A pod's coordinated move (`Pod.move_axes`), against a simulated instrument: what each board is
sent, and the position record kept as the Master Controller keeps it."""

import struct
from typing import List, Tuple

from pylabrobot.beckman_coulter.biomek_i7.driver import motion
from pylabrobot.beckman_coulter.biomek_i7.driver import protocol as p
from pylabrobot.beckman_coulter.biomek_i7.driver.errors import NotHomedError
from pylabrobot.beckman_coulter.biomek_i7.driver.features.pod import Pod
from pylabrobot.beckman_coulter.biomek_i7.driver.homing_tests import SimulatedRunTest
from pylabrobot.beckman_coulter.biomek_i7.driver.master import BiomekI7Driver
from pylabrobot.beckman_coulter.biomek_i7.simulation_tests_support import (
  generic_configuration,
  sealed,
)

SPAN8_MOTORS = 21  # X, Y1-Y8, Z1-Z8, GY, GG, GZ, GR: every Span-8 axis but the pumps


class PodMoveTest(SimulatedRunTest):
  """Helpers for tests on homed simulated pods."""

  async def homed(self, homed=True, configuration=None) -> BiomekI7Driver:
    driver = await self.start(homed, configuration=configuration)
    await driver.home()
    self.mark(driver)
    return driver

  def sent(self, driver: BiomekI7Driver, module: str) -> List[Tuple[str, bytes]]:
    """(command name, parameters) of each request a board was sent since the mark."""
    requests = self.usb(driver, module).requests[self.marks[module] :]
    return [(p.COMMANDS[(m[0], m[4])], m[5:]) for m in requests]

  def pod(self, driver: BiomekI7Driver, side: str) -> Pod:
    pod = driver.left_x_arm if side == "left" else driver.right_x_arm
    assert pod is not None
    return pod


class PodMoveTests(PodMoveTest):
  async def test_first_move_reads_positions_then_moves_together(self) -> None:
    driver = await self.homed()
    pod = self.pod(driver, "left")
    usb = self.usb(driver, "multichannel")
    before = {a.name: (usb.axis_model(a.name).position,) * 2 for a in pod.axes}
    expected = motion.plan_move(pod.axes, before, {"X": 100000, "Y": 50000})
    unknown = sum(1 for c in pod.cpositions.values() if c[0] is None)  # every axis homed
    self.assertGreaterEqual(unknown, 7)
    result = await pod.move_axes({"X": 100000, "Y": 50000})
    motions = result.motions
    self.assertEqual(motions, expected[0])
    self.assertEqual(result.completions, {})
    names = [n for n, _ in self.sent(driver, "multichannel")]
    # enable-all: the positions are unknown after homing.
    n = 1 + 2 * unknown
    self.assertEqual(names[0], "Stop Motion Service")
    self.assertEqual(names[1 : 1 + unknown], ["Set State"] * unknown)
    self.assertEqual(names[1 + unknown : n], ["Get Current Position"] * unknown)
    self.assertEqual(names[n:], ["Set Move Parameters"] * 2 + ["Move All"])
    sent = self.sent(driver, "multichannel")
    for m, (_, params) in zip(motion.spawn_order(motions), sent[n : n + 2]):
      self.assertEqual(
        struct.unpack("<IIIIi", params[:20]), (m.velocity, m.acceleration, m.jerk, 0, m.position)
      )
    self.assertEqual(struct.unpack("<I", sent[n + 2][1])[0], expected[1])
    self.assertEqual((usb.axis_model("X").position, usb.axis_model("Y").position), (100000, 50000))
    self.assertEqual(pod.cpositions["X"], (100000, 100000))

  async def test_later_moves_plan_from_the_record_without_reading(self) -> None:
    driver = await self.homed()
    pod = self.pod(driver, "left")
    await pod.move_axes({"X": 100000})
    self.mark(driver)
    (x,) = (await pod.move_axes({"X": 50000, "Y": 0})).motions  # Y is already at 0
    self.assertEqual(
      [n for n, _ in self.sent(driver, "multichannel")], ["Set Move Parameters", "Move All"]
    )
    self.assertEqual((x.axis, x.velocity), ("X", pod.axis("X").limits.max_velocity))

  async def test_nothing_is_sent_when_every_axis_is_there(self) -> None:
    driver = await self.homed()
    pod = self.pod(driver, "left")
    await pod.move_axes({"X": 100000})
    self.mark(driver)
    self.assertEqual((await pod.move_axes({"X": 100000})).motions, [])
    self.assertEqual(self.sent(driver, "multichannel"), [])

  async def test_bad_requests_send_nothing(self) -> None:
    driver = await self.homed()
    pod = self.pod(driver, "right")
    for targets, speed, forces in (
      ({"X": 10**7}, 1000, {}),
      ({"X": -100}, 0, {}),
      ({"Q": 1}, 1000, {}),
      ({"Z1": -1000}, 1000, {"Z2": 1.0}),  # a force on an axis without a target
      ({"Z1": -1000}, 1000, {"Z1": -1.0}),
    ):
      with self.subTest(targets=targets, speed=speed), self.assertRaises(ValueError):
        await pod.move_axes(targets, speed, forces=forces)
    with self.assertRaises(RuntimeError):  # its valve has not been set
      await pod.move_axes({"D1": 100})
    self.assertEqual(self.sent(driver, "span8"), [])

  async def test_span8_z_moves_down_by_its_hysteresis_more(self) -> None:
    # Hysteresis is measured per instrument; the generic configuration has none, so this test
    # gives two channels some.
    config = generic_configuration()
    for axis in config.modules["span8"]:
      if axis["name"] in ("Z1", "Z2"):
        axis["hysteresis"] = -57
    driver = await self.homed(configuration=sealed(config))
    pod = self.pod(driver, "right")
    await pod.move_axes({"Z1": -20000, "Z2": -20000})
    names = [n for n, _ in self.sent(driver, "span8")]
    self.assertEqual(names[1 : 1 + 2 * SPAN8_MOTORS].count("Get Current Position"), SPAN8_MOTORS)
    usb = self.usb(driver, "span8")
    for name in ("Z1", "Z2"):
      h = pod.axis(name).hysteresis
      self.assertLess(h, 0)
      self.assertEqual(usb.axis_model(name).position, -20000 + h)
      self.assertEqual(pod.cpositions[name], (-20000 + h, -20000))
    # Back up: the move takes up the hysteresis, and the channel ends at its target.
    await pod.move_axes({"Z1": -10000})
    self.assertEqual(usb.axis_model("Z1").position, -10000)
    self.assertEqual(pod.cpositions["Z1"], (-10000, -10000))

  async def test_not_homed_axis_drops_the_move_and_forgets_positions(self) -> None:
    driver = await self.homed()
    pod = self.pod(driver, "left")
    self.usb(driver, "multichannel").axis_model("Y").homed = False  # as after a power cycle
    with self.assertRaises(NotHomedError):
      await pod.move_axes({"X": 1000, "Y": 1000})
    self.assertEqual(self.sent(driver, "multichannel")[-1][0], "Stop Motion Service")
    self.assertTrue(all(c[0] is None for c in pod.cpositions.values()))

  async def test_homing_forgets_positions(self) -> None:
    driver = await self.homed()
    pod = self.pod(driver, "left")
    await pod.move_axes({"X": 100000})
    await pod.home()
    self.assertEqual(pod.cpositions["X"], (None, pod.axis("X").home_position))

  async def test_sensor_stop_is_reported_with_its_position(self) -> None:
    driver = await self.homed()
    pod = self.pod(driver, "right")
    z1 = self.usb(driver, "span8").axis_model("Z1")
    await pod.move_axes({"Z1": -10000})
    z1.sensed_at = -150000
    result = await pod.move_axes({"Z1": -200000, "Z2": -200000}, sensors={"Z1": (1, 1)})
    self.assertEqual(result.completions["Z1"].outcome, "liquid-sensed")
    self.assertEqual(result.stopped_at("Z1"), -150000)
    self.assertIsNone(result.stopped_at("Z2"))
    # The reported position is taken as Z1's, as the Master Controller takes it, with its hysteresis as for any move.
    self.assertEqual(pod.cpositions["Z1"][0], -150000)
    self.assertIsNotNone(pod.cpositions["Z2"][0])

  async def test_both_pods_move_together(self) -> None:
    driver = await self.homed()
    results = await driver.move_axes({"left": {"Y": 20000}, "right": {"Z1": -10000}})
    self.assertEqual(sorted(results), ["left", "right"])
    self.assertEqual([m.axis for m in results["left"].motions], ["Y"])
    self.assertEqual(self.pod(driver, "right").cpositions["Z1"][1], -10000)
    with self.assertRaises(ValueError):
      await driver.move_axes({"middle": {"X": 0}})

  async def test_span8_takes_y_and_span_as_biomek_sends_them(self) -> None:
    driver = await self.homed()
    pod = self.pod(driver, "right")
    # As logged: `AM 7 1000 0 -230264 154304 0` puts every channel at Y 154304 (S 6.3 cm, 0 counts).
    await pod.move_axes({"Y": 154304, "S": 0})
    self.assertEqual({pod.cpositions[f"Y{i}"][1] for i in range(1, 9)}, {154304})
    await pod.move_axes({"S": 7000})  # Y kept: channel i 1000 counts further each
    self.assertEqual(
      [pod.cpositions[f"Y{i}"][1] for i in range(1, 9)], [154304 + 1000 * i for i in range(8)]
    )
    with self.assertRaises(ValueError):
      await pod.move_axes({"Y": 0, "Y3": 0})
    with self.assertRaises(ValueError):
      await pod.move_axes({"S": -1})
