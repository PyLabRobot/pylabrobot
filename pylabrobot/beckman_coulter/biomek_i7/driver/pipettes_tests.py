"""A Span-8 pod's channels (`Pod.pipettes`) on the simulator: valves, cutoff velocity, pump moves
through them, and the tip check."""

import struct

from pylabrobot.beckman_coulter.biomek_i7.driver.errors import TipCheckError
from pylabrobot.beckman_coulter.biomek_i7.driver.pod_move_tests import PodMoveTest


class PipettesTests(PodMoveTest):
  async def test_valves_then_pump_moves_through_them(self) -> None:
    driver = await self.homed()
    pod = self.pod(driver, "right")
    pipettes = pod.pipettes
    assert pipettes is not None
    usb = self.usb(driver, "span8")
    await pipettes.set_valves({"D1": "input", "D2": "input"})
    pump_params = [
      params for name, params in self.sent(driver, "span8") if name == "Set Move Parameters"
    ]
    self.assertEqual(len(pump_params), 2)
    self.assertEqual(struct.unpack("<IIIIiB", pump_params[0]), (200, 23200, 200, 50000, 0, 0))
    self.assertEqual(self.sent(driver, "span8")[-1][0], "Move All")
    self.assertEqual(pipettes.valves["D1"], "input")
    pipettes._set_cutoff_velocity(1001)
    self.assertEqual(pipettes._pod._cutoff_velocity, 1000)  # counts/s, quantized to 4
    self.mark(driver)
    result = await pod.move_axes({"D1": 12000})
    (m,) = result.motions
    (params,) = [
      params for name, params in self.sent(driver, "span8") if name == "Set Move Parameters"
    ]
    self.assertEqual(
      struct.unpack("<IIIIiB", params), (200, m.velocity, 1000, m.acceleration, 12000, 0)
    )
    self.assertEqual(usb.axis_model("D1").position, 12000)
    await pipettes.set_valves({"D1": "output"})
    self.assertEqual(usb.axis_model("D1").valve, 1)
    with self.assertRaises(ValueError):
      pipettes._set_cutoff_velocity(100)
    with self.assertRaises(ValueError):
      await pipettes.set_valves({"Z1": "input"})

  async def test_verify_tips(self) -> None:
    driver = await self.homed()
    pod = self.pod(driver, "right")
    pipettes = pod.pipettes
    assert pipettes is not None
    self.usb(driver, "span8").tip_states = bytes([0, 0, 2, 2, 1, 2, 2, 2])
    await pipettes.verify_tips({0: True, 1: True, 2: False})  # channels, 0-based
    with self.assertRaises(TipCheckError) as caught:
      await pipettes.verify_tips({0: False, 2: True, 4: True})
    message = str(caught.exception)
    for text in ("Probe 1 should not", "Probe 3 should have", "probe 5 has"):
      self.assertIn(text, message)
