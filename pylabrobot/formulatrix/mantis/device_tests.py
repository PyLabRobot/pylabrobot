"""Mantis device tests: the parts tree, and the chips placed checked against the tags read."""

import os
import unittest
from typing import Dict, List
from unittest import mock

from pylabrobot.formulatrix.mantis import (
  Mantis,
  MantisDevice,
  mantis_high_volume_hi_fi_chip,
  mantis_low_volume_hi_fi_chip,
)
from pylabrobot.formulatrix.mantis.device import RECORDING_MANTIS_V4
from pylabrobot.formulatrix.mantis.driver.errors import MantisConfigurationError
from pylabrobot.formulatrix.mantis.driver.features.pump import Pump
from pylabrobot.formulatrix.mantis.driver.features.rfid import RFID_READ_TAG_COMMAND
from pylabrobot.formulatrix.mantis.driver.features.rfid_tests import HIGH_VOLUME_HI_FI_TAG_REPLY
from pylabrobot.formulatrix.mantis.driver.master_tests import (
  FakeLink,
  FakeReader,
  make_answers,
  pass_at_once,
)
from pylabrobot.io.ftdi import FTDI

RESOURCE_MODEL = os.path.join(os.path.dirname(__file__), "resource_model")


class MantisTests(unittest.TestCase):
  def make_device(self) -> MantisDevice:
    return Mantis(io=mock.create_autospec(FTDI, instance=True))

  def test_every_part_has_its_model_file(self):
    device = self.make_device()
    for resource in (device, device.left_chip_rack, device.right_chip_rack):
      with self.subTest(resource=resource.name):
        self.assertTrue(os.path.isfile(os.path.join(RESOURCE_MODEL, f"{resource.model}.glb")))

  def test_simulation_refuses_a_link(self):
    with self.assertRaises(ValueError):
      Mantis(simulation=True, io=mock.create_autospec(FTDI, instance=True))


# Interface A: the tag of a low volume Hi-Fi chip in slot 6, antenna 5, as a Mantis v4's reader sent
# it, chunked as it arrived.
LOW_VOLUME_HI_FI_SLOT_6_TAG_REPLY = [
  b"Request mode.\r\n",
  b"[00000000003532534C32303232202020202020202008000000353230323730313139342E30202034323031"
  b"2E30202020330D000000353731322020372E2020202020202020783C0000312C0000574900006B49000000"
  b"0000000000000000000000]\r\n",
]
SLOT_6_ANTENNA = 5


class ChipCheckTests(unittest.IsolatedAsyncioTestCase):
  async def set_up(self, *, chips: Dict[int, str], tags: Dict[int, List[bytes]]) -> MantisDevice:
    self.reader = FakeReader(tags=tags)
    link = FakeLink(answers=make_answers(), reader=self.reader)
    mantis = Mantis(
      io=link.io, rfid_io=self.reader.io, declared_configuration_json=RECORDING_MANTIS_V4
    )
    factories = {
      "low_volume_hi_fi": mantis_low_volume_hi_fi_chip,
      "high_volume_hi_fi": mantis_high_volume_hi_fi_chip,
    }
    for slot, chip_type in chips.items():
      mantis.chip_spots[slot].assign_child_resource(factories[chip_type](name=f"chip_{slot}"))
    mantis.driver.default_read_timeout = 0.25
    holds = mock.patch.object(Pump, "_sleep", pass_at_once)  # the pump's holds pass at once
    holds.start()
    self.addCleanup(holds.stop)
    self.addAsyncCleanup(mantis.stop)
    return mantis

  def tag_reads(self) -> int:
    return self.reader.written.count(RFID_READ_TAG_COMMAND)

  async def test_a_chip_of_the_type_read_sets_up(self):
    mantis = await self.set_up(
      chips={6: "low_volume_hi_fi"}, tags={SLOT_6_ANTENNA: LOW_VOLUME_HI_FI_SLOT_6_TAG_REPLY}
    )
    await mantis.setup(skip_arm=True, skip_self_test=True)
    self.assertEqual(self.tag_reads(), 6)

  async def test_no_chip_placed_reads_no_tag(self):
    mantis = await self.set_up(chips={}, tags={})
    await mantis.setup(skip_arm=True, skip_self_test=True)
    self.assertEqual(self.tag_reads(), 0)

  async def test_another_type_or_no_chip_refuses_and_stops(self):
    cases = [
      (
        {6: "high_volume_hi_fi"},
        {SLOT_6_ANTENNA: LOW_VOLUME_HI_FI_SLOT_6_TAG_REPLY},
        "of type 'low_volume_hi_fi'",
      ),
      (
        {1: "high_volume_hi_fi"},
        {SLOT_6_ANTENNA: LOW_VOLUME_HI_FI_SLOT_6_TAG_REPLY},
        r"holds no chip \(RFID\); chips were read in slots \[6\]",
      ),
    ]
    for chips, tags, message in cases:
      with self.subTest(message=message):
        mantis = await self.set_up(chips=chips, tags=tags)
        with self.assertRaisesRegex(MantisConfigurationError, message):
          await mantis.setup(skip_arm=True, skip_self_test=True)
        self.assertIsNone(mantis.driver.rfid)  # stopped: the links are closed

  async def test_a_chip_of_the_type_read_in_slot_2(self):
    mantis = await self.set_up(
      chips={2: "high_volume_hi_fi"}, tags={1: HIGH_VOLUME_HI_FI_TAG_REPLY}
    )
    await mantis.setup(skip_arm=True, skip_self_test=True)
    self.assertEqual(self.tag_reads(), 6)


class SimulatedChipCheckTests(unittest.IsolatedAsyncioTestCase):
  async def test_the_simulated_reader_reads_the_chips_placed(self):
    mantis = Mantis(simulation=True)
    mantis.chip_spots[6].assign_child_resource(mantis_low_volume_hi_fi_chip(name="chip"))
    await mantis.setup()
    self.addAsyncCleanup(mantis.stop)
    assert mantis.rfid is not None
    tags = await mantis.rfid.request_tags()
    self.assertEqual(tags[6]["chip_type"], "low_volume_hi_fi")  # type: ignore[index]
    self.assertEqual(tags[6]["chip_type_code"], 0x08)  # type: ignore[index]
    self.assertEqual([tags[slot] for slot in range(1, 6)], [None] * 5)


if __name__ == "__main__":
  unittest.main()
