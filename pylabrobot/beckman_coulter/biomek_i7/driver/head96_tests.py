"""The Multichannel pod's 96-channel head (`Pod.head96`) on the simulator: loading tips (`LT`), against
an `LT` Biomek logged, request for request."""

import dataclasses
import struct

from pylabrobot.beckman_coulter.biomek_i7.driver.recipes.homing import (
  PLUNGER_RECIPES,
  AtValue,
  Home,
)
from pylabrobot.beckman_coulter.biomek_i7.driver.recipes.tip_load import TipLoad
from pylabrobot.beckman_coulter.biomek_i7.driver.pod_move_tests import PodMoveTest
from pylabrobot.beckman_coulter.biomek_i7.simulation_tests_support import generic_configuration

# A logged `LT`: 20 µL tips on this instrument (2026-07-31), with no settling wait.
LOGGED = "LT 43176 -50081 518.750 100 2.000 -48673 50 31216 -47926 50 43176"


class Head96Tests(PodMoveTest):
  async def test_press_stops_on_the_tips_and_reports_where(self) -> None:
    driver = await self.homed()
    pod = self.pod(driver, "left")
    head96 = pod.head96
    assert head96 is not None
    usb = self.usb(driver, "multichannel")
    await pod.move_axes({"Z": -46881, "D": 43176})
    usb.axis_model("Z").obstacle = -48983  # the tips' collars
    self.mark(driver)
    load = dataclasses.replace(TipLoad.parse(LOGGED), settling_time=0.0)
    self.assertEqual(await head96.load_tips(load), -48983)
    sent = self.sent(driver, "multichannel")
    self.assertEqual(
      [n for n, _ in sent],
      [
        # D is already at load_d. Press Z, force-limited.
        "Set Move Parameters",
        "Move All",
        # Read Z: the loaded Z.
        "Stop Motion Service",
        "Set State",
        "Get Current Position",
        # Relieve, still force-limited.
        "Set Move Parameters",
        "Move All",
        # Read Z again; the simulated drive is where it was sent, so the unforced relieve sends
        # nothing (Biomek's drive had eased off a few counts, and it moved).
        "Stop Motion Service",
        "Set State",
        "Get Current Position",
        # Leave: D, then Z, as the Master Controller sends them.
        "Set Move Parameters",
        "Set Move Parameters",
        "Move All",
        # End: D back.
        "Set Move Parameters",
        "Move All",
      ],
    )
    smp = [params for name, params in sent if name == "Set Move Parameters"]
    force_and_position = [struct.unpack_from("<Ii", params, 12) for params in smp]
    self.assertEqual(
      force_and_position,
      [(518750, -50081), (518750, -48673), (0, 31216), (0, -47926), (0, 43176)],
    )
    self.assertEqual(pod.cpositions["Z"], (-47926, -47926))
    self.assertEqual(pod.cpositions["D"], (43176, 43176))

  async def test_bad_arguments_are_refused_before_anything_is_sent(self) -> None:
    driver = await self.homed()
    head96 = self.pod(driver, "left").head96
    assert head96 is not None
    load = TipLoad.parse(LOGGED)
    for bad in (
      dataclasses.replace(load, load_force=-1.0),
      dataclasses.replace(load, load_speed=0),
      dataclasses.replace(load, leave_speed=1001),
      dataclasses.replace(load, settling_time=-0.5),
      dataclasses.replace(load, load_z=10**9),
    ):
      with self.assertRaises(ValueError):
        await head96.load_tips(bad)
    self.assertEqual(self.sent(driver, "multichannel"), [])
    self.assertEqual(self.sent(driver, "span8"), [])

  def test_parse(self) -> None:
    load = TipLoad.parse(LOGGED)
    self.assertEqual((load.load_force, load.settling_time, load.end_d), (518.75, 2.0, 43176))
    with self.assertRaises(ValueError):
      TipLoad.parse("AM 1 2 3")

  def test_the_plunger_homes_to_its_zero(self) -> None:
    # The plunger's approach is its zero in the pod's conversion: the 43176 counts Biomek's HD sent.
    self.assertEqual(PLUNGER_RECIPES["multichannel"], (Home("D", AtValue(0.0)),))
    self.assertEqual(generic_configuration().pods["left"].to_counts("D", 0.0), 43176)

  def test_arguments_from_the_configuration_are_biomeks(self) -> None:
    # Two logged LTs: 12 tips seated at 21.55 cm, and a full head of 96 at 27.3557 cm.
    pod = generic_configuration().pod("left")
    self.assertEqual(TipLoad.from_configuration(pod, 21.55, 12), TipLoad.parse(LOGGED))
    self.assertEqual(
      TipLoad.from_configuration(pod, 27.3557, 96),
      TipLoad.parse("LT 43176 -35219 2400.000 100 2.000 -33811 50 31216 -33063 50 43176"),
    )
    with self.assertRaises(ValueError):
      TipLoad.from_configuration(pod, 21.55, 97)
    with self.assertRaises(ValueError):
      TipLoad.from_configuration(generic_configuration().pod("right"), 21.55, 8)
