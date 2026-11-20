"""The declared configuration: how it converts, how it is kept, and how discovery checks it against
the boards. On the package's generic configuration, whose physical values are one instrument's."""

import asyncio
import dataclasses
import json
import os
import tempfile
import unittest

from pylabrobot.beckman_coulter.biomek_i7.defaults import GENERIC_CONFIGURATION
from pylabrobot.beckman_coulter.biomek_i7.driver.configuration import DeviceConfiguration
from pylabrobot.beckman_coulter.biomek_i7.driver.master import BiomekI7Driver
from pylabrobot.beckman_coulter.biomek_i7.simulation_tests_support import (
  generic_configuration,
  generic_driver,
  sealed,
)

MASTER_LOGGER = "pylabrobot.beckman_coulter.biomek_i7.driver.master"


class DeclaredConfigurationTests(unittest.TestCase):
  def setUp(self) -> None:
    self.config = generic_configuration()
    self.left, self.right = self.config.pod("left"), self.config.pod("right")

  def test_pods_as_the_instrument_file_has_them(self) -> None:
    self.assertEqual((self.left.component, self.left.module_type), ("MC_1", "multichannel"))
    self.assertEqual((self.right.component, self.right.module_type), ("SPAN8_2", "span8"))
    self.assertEqual(self.left.geometry.bridge_x_span, 39.0154)
    self.assertEqual(self.right.geometry.bridge_x_span, 25.7204)
    self.assertAlmostEqual(self.left.conversion["X"].offset, -10.519)
    self.assertAlmostEqual(self.right.conversion["X"].offset, -146.40669375)
    self.assertEqual(self.left.settings["TipLoadForceLimitSlope"], 2150)

  def test_conversions_give_the_counts_biomek_logged(self) -> None:
    # Targets Biomek's instrument file holds, and the counts its logs show it sent for them.
    self.assertEqual(self.left.to_counts("GR", self.left.park["GR"]), -175443)  # the park's GR
    self.assertEqual(self.left.to_counts("D", 0.0), 43176)  # the plunger's home
    self.assertEqual(self.left.to_counts("Z", 39.763), -256)  # the rest Z
    self.assertEqual(self.right.to_counts("Z1", 39.107921235034354), 9029)  # the Span-8 Z park
    self.assertEqual(self.right.to_counts("D1", 1000.0), 24000)  # a pump's full stroke
    self.assertAlmostEqual(self.left.to_value("X", self.left.to_counts("X", 42.0)), 42.0, 3)

  def test_the_bridges_meet_where_biomek_stopped(self) -> None:
    """The left bridge's right edge meets the right bridge's left edge at left X + |right X| =
    250,807 counts. Biomek never commanded more than 250,678 in 20 days of logs."""
    lg, rg = self.left.geometry, self.right.geometry
    left_reach = -lg.bridge_to_head_x_offset - lg.mandrel1_x_offset + lg.bridge_x_span
    right_reach = -rg.bridge_to_head_x_offset - rg.mandrel1_x_offset
    lx, rx = self.left.conversion["X"], self.right.conversion["X"]
    # Deck X of a pod at `c` counts is c / const - offset; the edges meet when
    # (L - R) / 2560 = (-rx.offset + right_reach) - (-lx.offset + left_reach).
    combined = ((-rx.offset + right_reach) - (-lx.offset + left_reach)) * lx.const
    self.assertEqual(round(combined), 250807)
    self.assertLess(250678, combined)

  def test_each_pod_works_from_its_own_corner_of_a_position(self) -> None:
    """Heights Biomek logged (Details, 2026-10-20), less the moving pod's corner: the same at every
    position, as the labware there is the same. The Multichannel (Pod1) approaching four plates to
    dispense, and the Span-8 (Pod2) pressing onto tips in four boxes. Against the other pod's
    corner they spread by up to 0.01 cm."""
    positions = self.config.deck_positions
    mc = {"P6": 27.757, "P9": 27.750, "P12": 27.756, "P16": 27.760}
    span8 = {"P14": 20.741, "P15": 20.744, "P21": 20.737, "P23": 20.727}
    for pod, logged, height in ((1, mc, 11.788), (2, span8, 4.770)):
      for name, z in logged.items():
        with self.subTest(pod=pod, position=name):
          self.assertAlmostEqual(z - positions[name].corner(pod)[2], height, delta=0.0011)
    self.assertEqual(positions["P6"].framed, {1: "Position", 2: "Position"})
    with self.assertRaises(ValueError):
      positions["P6"].corner(3)

  def test_unknown_axis_and_side(self) -> None:
    with self.assertRaises(ValueError):
      self.left.to_counts("Z1", 1.0)
    config = generic_configuration()
    del config.pods["right"]
    with self.assertRaisesRegex(ValueError, "no pod declared on the right"):
      config.pod("right")


class KeepingTests(unittest.TestCase):
  def setUp(self) -> None:
    self.folder = tempfile.TemporaryDirectory()
    self.addCleanup(self.folder.cleanup)
    self.path = os.path.join(self.folder.name, "configuration.json")

  def test_save_and_load_round_trip(self) -> None:
    config = generic_configuration()
    config.save(self.path)
    self.assertEqual(DeviceConfiguration.load(self.path).to_dict(), config.to_dict())
    with open(GENERIC_CONFIGURATION, encoding="utf-8") as fh:
      self.assertEqual(json.load(fh), config.to_dict())

  def test_a_file_changed_after_import_is_refused_at_load(self) -> None:
    for change in (
      lambda data: data["pods"]["left"]["conversion"]["X"].update(const=1.0),  # instrument file
      lambda data: data["modules"]["span8"][0].update(hysteresis=7),  # Master Controller record
    ):
      config = generic_configuration()
      data = config.to_dict()
      change(data)
      with open(self.path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
      with self.assertRaisesRegex(ValueError, "not the ones imported"):
        DeviceConfiguration.load(self.path)

  def test_anonymized_keeps_no_serial_numbers_and_no_measured_hysteresis(self) -> None:
    config = generic_configuration()
    config.generic = False
    config.chassis_serial_number = "A" * 32
    config.instrument_serial_number = "B" * 11
    for pod in config.pods.values():
      pod.module_serial_number = "C" * 32
    config.modules["span8"][0]["hysteresis"] = 12
    config.sources = {
      name: {"path": "C:/somewhere", "kind": "file", "read_at": "2026-01-01", "fingerprint": f}
      for name, f in config.fingerprints().items()
    }
    generic = config.anonymized()
    text = json.dumps(generic.to_dict())
    for serial in ("A" * 32, "B" * 11, "C" * 32):
      self.assertNotIn(serial, text)
    self.assertTrue(generic.generic)
    self.assertEqual({a["hysteresis"] for axes in generic.modules.values() for a in axes}, {0})
    self.assertEqual({k for s in generic.sources.values() for k in s}, {"fingerprint"})
    self.assertEqual(generic.unrecorded_changes(), [])
    self.assertEqual(generic.to_dict(), generic_configuration().to_dict())


class DeclaredAgainstFoundTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self) -> None:
    asyncio.get_running_loop().set_debug(False)

  async def discover_with(self, configuration: DeviceConfiguration) -> BiomekI7Driver:
    driver = generic_driver(configuration=sealed(configuration))
    self.addAsyncCleanup(driver.stop)
    await driver.discover()
    return driver

  async def test_the_generic_instrument_matches(self) -> None:
    driver = generic_driver()
    self.addAsyncCleanup(driver.stop)
    with self.assertNoLogs(MASTER_LOGGER, level="WARNING"):
      await driver.discover()
    assert driver.left_x_arm and driver.left_x_arm.declared
    self.assertEqual(driver.left_x_arm.declared.component, "MC_1")

  async def test_pods_swapped_are_refused(self) -> None:
    config = generic_configuration()
    left, right = config.pods["left"], config.pods["right"]
    config.pods = {
      "left": dataclasses.replace(right, side="left"),
      "right": dataclasses.replace(left, side="right"),
    }
    with self.assertRaisesRegex(ValueError, "span8 pod on the left"):
      await self.discover_with(config)

  async def test_a_pod_declared_but_not_found_is_only_logged(self) -> None:
    config = generic_configuration()
    del config.pods["right"]
    with self.assertLogs(MASTER_LOGGER, level="WARNING") as logs:
      driver = await self.discover_with(config)
    self.assertIn("right pod: declared nothing, found span8", "\n".join(logs.output))
    assert driver.right_x_arm is not None
    self.assertIsNone(driver.right_x_arm.declared)

  async def test_another_instruments_configuration_is_not_checked_on_given_boards(self) -> None:
    # Refused on the instrument's own boards (`serial_numbers_tests.py`); simulated ones are not
    # tied to an instrument.
    config = generic_configuration()
    config.chassis_serial_number = "0" * 32
    driver = await self.discover_with(config)
    self.assertEqual(driver.declared.chassis_serial_number, "0" * 32)

  def test_a_declared_configuration_is_required(self) -> None:
    with self.assertRaisesRegex(ValueError, "no declared configuration"):
      BiomekI7Driver(boards=[])


if __name__ == "__main__":
  unittest.main()
