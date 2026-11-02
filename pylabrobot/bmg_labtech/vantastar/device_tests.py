"""Tests for the VANTAstar device: stopping with a plate left in, the plate holder following the
drawer, and the estimate of a read's run time; and the devices on the driver's fake USB link the
feature tests use."""

import unittest
from collections import deque
from dataclasses import replace
from typing import Callable, Deque, List
from unittest import mock

from pylabrobot.bmg_labtech.vantastar.device import (
  VANTAstarDevice,
)
from pylabrobot.bmg_labtech.vantastar.driver import master_tests
from pylabrobot.bmg_labtech.vantastar.driver.features.measurement import (
  MeasurementSettings,
  ShakeSettings,
)
from pylabrobot.bmg_labtech.vantastar.driver.master import VANTAstarDriver, VANTAstarStatus
from pylabrobot.bmg_labtech.vantastar.driver.responses_tests import (
  FLUORESCENCE_LAST_PAGE,
  load_flows,
)
from pylabrobot.lib.fluorescence.channels import FluorescenceChannel
from pylabrobot.resources import cor_96_wellplate_360uL_Fb

PHOTOCATALYST_WELLS = ["A1", "A6", "A12", "H1", "H6", "H12"]


def make_device_answering(respond: Callable[[bytes], bytes]) -> VANTAstarDevice:
  """A device on the fake link answering with ``respond``, a 96-well plate on its plate holder."""
  device = VANTAstarDevice(io=master_tests.FakeUSB(respond))
  device.driver.default_poll_interval = 0
  device.driver.measurement_poll_interval = 0
  device.drawer.drawer_open = True
  device.plate_holder.assign_child_resource(cor_96_wellplate_360uL_Fb(name="plate"))
  device.drawer.drawer_open = False
  return device


def make_device(pages: List[bytes]) -> VANTAstarDevice:
  """A device on the fake link: idle status, the RUN reply, the 96-well well list, then a page
  ready for each of ``pages`` but the last, and the block finished; ``pages`` one per data
  request."""
  page_queue: Deque[bytes] = deque(pages)
  block = deque(
    [master_tests.MW_BLOCK_PAGE_READY] * (len(pages) - 1) + [master_tests.MW_BLOCK_LAST_PAGE]
  )

  def respond(payload: bytes) -> bytes:
    """Answer STATUS with an idle status, block status polls and data requests in turn, the well
    list request with the 96-well list, and anything else with the RUN reply."""
    if payload[0] == 0x80:
      return master_tests.STATUS_IDLE
    if payload == master_tests.MW_BLOCK_STATUS:
      return block.popleft()
    if payload == master_tests.VALUES_WELL_LIST:
      return master_tests.WELL_LIST_96
    if payload in (master_tests.VALUES_CURRENT_BLOCK, master_tests.VALUES_NEXT_NEW_BLOCK):
      return page_queue.popleft()
    return master_tests.RUN_REPLY

  return make_device_answering(respond)


def make_recorded_device(name: str) -> VANTAstarDevice:
  """A device answering as the reader did in the recorded read ``name`` (``load_flows``)."""
  return make_device_answering(master_tests.RecordedReader(load_flows()[name]))


class StopTests(unittest.IsolatedAsyncioTestCase):
  """Stopping the device while a plate is in the reader."""

  async def test_drawer_open_when_a_plate_is_left_in(self):
    """When stop finds a plate left in, it opens the drawer, so the plate can be taken off."""
    device = make_device_answering(lambda p: master_tests.STATUS_IDLE)

    async def request_status(retries: int = 5) -> VANTAstarStatus:
      """The recorded status for where the drawer is, with a plate: none is recorded with one."""
      moved = any(c[0] == 0x03 for c in master_tests.commands(device.driver))
      reply = master_tests.STATUS_DRAWER_OPEN if moved else master_tests.STATUS_IDLE
      return replace(VANTAstarDriver._parse_status(reply), plate_detected=True)

    device.driver.request_status = request_status  # type: ignore[method-assign]
    with self.assertRaises(RuntimeError):
      await device.stop()
    plate = device.plate_holder.resource
    assert plate is not None
    device.plate_holder.unassign_child_resource(plate)  # allowed: the drawer is open


# 365/8 nm excitation, 400/10 nm emission.
CHANNEL_365_400 = FluorescenceChannel(
  excitation_wavelength=365,
  excitation_bandwidth=8,
  emission_wavelength=400,
  emission_bandwidth=10,
)


def sent(device: VANTAstarDevice) -> List[bytes]:
  """The commands ``device`` sent, but STATUS."""
  return [c for c in master_tests.commands(device.driver) if c[0] != 0x80]


class EstimateDurationTests(unittest.IsolatedAsyncioTestCase):
  """The reader's estimate of a read's run time, without reading."""

  async def test_the_read_with_calc_time_only(self):
    """An estimate sends only the RUN the read sends, with CalcTimeOnly set, and returns the
    reader's estimate; the read after it reads."""
    device = make_device([FLUORESCENCE_LAST_PAGE])
    seconds = await device.estimate_duration(
      lambda: device.fluorescence.measure(CHANNEL_365_400, focal_height=5.0, gain=500)
    )
    self.assertEqual(seconds, 95)
    (estimate,) = sent(device)
    self.assertFalse(device.driver.estimating)
    await device.fluorescence.measure(CHANNEL_365_400, focal_height=5.0, gain=500)
    run = sent(device)[1]
    self.assertEqual(run[0], 0x04)
    self.assertEqual((estimate[67], run[67]), (0x03, 0x02))  # CalcTimeOnly, then a read
    self.assertEqual(estimate[:67] + estimate[68:], run[:67] + run[68:])

  async def test_shaking_and_pause_are_skipped(self):
    """Shaking before the read and the pause before reading are left out of an estimate."""
    device = make_device([])
    with mock.patch("asyncio.sleep", new_callable=mock.AsyncMock) as sleep:
      seconds = await device.estimate_duration(
        lambda: device.absorbance.measure(
          [450],
          measurement_settings=MeasurementSettings(pause_before_reading=5),
          shake_settings=ShakeSettings(pattern="orbital", speed=300, duration=5),
        )
      )
    self.assertEqual(seconds, 95)
    self.assertEqual([c[0] for c in sent(device)], [0x04])  # no IdleMove
    self.assertNotIn(mock.call(5), sleep.await_args_list)

  async def test_other_commands_are_refused(self):
    """An estimate of a drawer move or a gain and focus adjustment is refused before anything is
    sent."""
    device = make_device([])
    plate = device.plate_holder.resource
    assert plate is not None
    with self.assertRaises(RuntimeError):
      await device.estimate_duration(lambda: device.drawer.open())
    with self.assertRaises(RuntimeError):
      await device.estimate_duration(
        lambda: device.fluorescence.adjust_gain_and_focus(
          CHANNEL_365_400, wells=plate.get_items(["A1"])
        )
      )
    self.assertEqual(sent(device), [])
    self.assertFalse(device.driver.estimating)

  async def test_without_a_read(self):
    """A call that ends without a read has nothing to estimate."""
    device = make_device([])
    with self.assertRaises(ValueError):
      await device.estimate_duration(lambda: device.drawer.sense_drawer_open())


class PlateHolderTests(unittest.TestCase):
  """The plate holder follows the drawer."""

  def test_plate_holder_follows_the_drawer(self):
    """A plate goes on only while the drawer is open; closed or never sensed, it is refused."""
    device = VANTAstarDevice(io=master_tests.FakeUSB(lambda p: master_tests.STATUS_IDLE))
    plate = cor_96_wellplate_360uL_Fb(name="plate")
    for drawer_open in (None, False):
      device.drawer.drawer_open = drawer_open
      with self.assertRaises(RuntimeError):
        device.plate_holder.assign_child_resource(plate)
    device.drawer.drawer_open = True
    device.plate_holder.assign_child_resource(plate)
    self.assertIs(device.plate_holder.resource, plate)


if __name__ == "__main__":
  unittest.main()
