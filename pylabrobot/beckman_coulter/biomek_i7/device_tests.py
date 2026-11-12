import asyncio
import glob
import json
import os
import struct
import unittest
from unittest import mock
from typing import Dict

from pylabrobot.beckman_coulter.biomek_i7.device import DECK_Z, RAIL_TOP_Z, BiomekI7
from pylabrobot.beckman_coulter.biomek_i7.driver.simulator import RECORDING_ENV
from pylabrobot.beckman_coulter.biomek_i7.resource_model import alps
from pylabrobot.beckman_coulter.biomek_i7.simulation_tests_support import (
  as_instrument,
  configuration_file,
  generic_biomek,
  generic_configuration,
  generic_driver,
  generic_simulated_boards,
)
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.resource import Resource

MODELS = os.path.join(os.path.dirname(__file__), "resource_model")


def glb_bounds(path: str):
  """A .glb's extent in mm, from its POSITION accessors (files are written in metres)."""
  with open(path, "rb") as f:
    data = f.read()
  length = struct.unpack_from("<I", data, 12)[0]
  doc = json.loads(data[20 : 20 + length])
  lo, hi = [float("inf")] * 3, [float("-inf")] * 3
  for accessor in doc["accessors"]:
    if "min" in accessor:
      lo = [min(a, b * 1000) for a, b in zip(lo, accessor["min"])]
      hi = [max(a, b * 1000) for a, b in zip(hi, accessor["max"])]
  return lo, hi


class BiomekI7Tests(unittest.TestCase):
  def setUp(self):
    self.biomek = generic_biomek()

  def deck_frame(self, resource: Resource) -> Coordinate:
    """Where `resource` is in the deck's frame, measured through the device that holds both."""
    deck = self.biomek.deck.location
    assert deck is not None
    return resource.get_location_wrt(self.biomek) - deck

  def test_every_mesh_fits_the_resource_it_draws(self):
    resources: Dict[str, Resource] = {
      r.model: r for r in [self.biomek, *self.biomek.get_all_children()] if r.model is not None
    }
    for name in dir(alps):
      if name.startswith("biomek_"):
        alp = getattr(alps, name)("x")
        assert alp.model is not None
        resources[alp.model] = alp
    files = glob.glob(os.path.join(MODELS, "beckman_coulter_*.glb"))
    self.assertGreater(len(files), 0)
    for path in files:
      model = os.path.basename(path)[: -len(".glb")]
      with self.subTest(model=model):
        drawn = resources[model]
        lo, hi = glb_bounds(path)
        size = (drawn.get_size_x(), drawn.get_size_y(), drawn.get_size_z())
        for axis in range(3):
          self.assertGreaterEqual(lo[axis], -1.0)
          self.assertLessEqual(hi[axis], size[axis] + 1.0)

  def test_the_deck_is_inside_the_frame(self):
    deck = self.biomek.deck
    for alp in deck.alps.values():
      if alp.alp_type == "ELx405DW":
        continue  # the washer stands behind the frame, as it does on the instrument
      loc = alp.get_location_wrt(self.biomek)
      with self.subTest(alp=alp.name):
        self.assertGreaterEqual(loc.x, 0)
        self.assertLessEqual(loc.x + alp.get_size_x(), self.biomek.get_size_x())
        self.assertGreaterEqual(loc.y, 0)
        self.assertLessEqual(loc.y + alp.get_size_y(), self.biomek.get_size_y())
        self.assertAlmostEqual(loc.z, DECK_Z)

  def test_moving_a_bridge_carries_its_pod(self):
    before = self.biomek.pod_models["left"].get_location_wrt(self.biomek)
    self.biomek.update_bridge_location("left", 600.0)
    after = self.biomek.pod_models["left"].get_location_wrt(self.biomek)
    self.assertAlmostEqual(after.y, before.y)
    self.assertAlmostEqual(self.deck_frame(self.biomek.bridges["left"]).x, 600.0)

  def test_moving_a_pod_along_its_bridge(self):
    carriage = self.biomek.pod_models["left"].get_location_wrt(self.biomek).z
    self.biomek.update_pod_location("left", 250.0, z=150.0)
    self.assertAlmostEqual(self.deck_frame(self.biomek.pod_models["left"]).y, 250.0)
    self.assertAlmostEqual(self.deck_frame(self.biomek.get_resource("left_pod_head")).z, 150.0)
    # The head moves in Z; the carriage keeps its height under the rail.
    self.assertAlmostEqual(self.biomek.pod_models["left"].get_location_wrt(self.biomek).z, carriage)
    self.assertLess(carriage, RAIL_TOP_Z)
    with self.assertRaises(ValueError):  # a Span-8's probes are placed one by one
      self.biomek.update_pod_location("right", 250.0, z=150.0)

  def test_moving_a_span8_probe(self):
    self.biomek.update_probe_location("right", 3, 300.0, 120.0)
    channel = self.biomek.get_resource("right_pod_channel_3")
    end = self.deck_frame(channel) + channel.reference_point  # type: ignore[attr-defined]
    self.assertAlmostEqual(end.y, 300.0)
    self.assertAlmostEqual(end.z, 120.0)


class DeviceSessionTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self) -> None:
    # Debug mode makes a simulated Home All ~100x slower; see driver/homing_tests.py.
    asyncio.get_running_loop().set_debug(False)

  async def test_without_simulation_it_drives_the_instrument_and_opens_nothing_yet(self):
    instrument = configuration_file(as_instrument(generic_configuration()))
    device = BiomekI7(declared_configuration_json=instrument)
    self.assertEqual(device.driver.boards, [])  # not set up: no board is opened
    with self.assertRaisesRegex(ValueError, "no declared configuration"):
      BiomekI7()  # never the generic one

  async def test_simulation_without_files_is_the_generic_instrument(self):
    with mock.patch.dict(os.environ, {RECORDING_ENV: ""}):
      device = BiomekI7(simulation=True)
    self.assertTrue(device.driver.declared.generic)
    self.assertIn("TL1", device.deck.position_names)  # its default deck, from the layout
    self.assertEqual(set(device.bridges), {"left", "right"})  # drawn before setup
    await device.setup()
    self.addAsyncCleanup(device.stop)
    self.assertEqual([a.module_type for a in device.arms], ["multichannel", "span8"])

  async def test_simulation_drives_a_simulated_instrument(self):
    device = generic_biomek()
    await device.setup()
    self.addAsyncCleanup(device.stop)
    assert device.driver is not None and device.driver.right_x_arm is not None
    self.assertEqual(await device.driver.right_x_arm.request_homed_state("X"), "homed")

  async def test_setup_and_home_all_go_to_the_driver(self):
    driver = generic_driver(generic_simulated_boards())
    device = BiomekI7(driver=driver)
    await device.setup()
    self.addAsyncCleanup(device.stop)
    assert driver.left_x_arm
    self.assertEqual(await driver.left_x_arm.request_homed_state("GR"), "homed")
    await device.home_all()
    self.assertEqual(await driver.left_x_arm.request_homed_state("GR"), "homed")


if __name__ == "__main__":
  unittest.main()
