"""Pump tests over a fake io, with command frames as an original CLARIOstar (firmware 1.20) takes them."""

import unittest
from typing import Optional, Tuple

from pylabrobot.bmg_labtech.clariostar.driver.errors import PumpAbortedError, describe_quit_codes
from pylabrobot.bmg_labtech.clariostar.driver.features.pumps import Pumps
from pylabrobot.bmg_labtech.clariostar.driver.master_tests import (
  STATUS_IDLE,
  STATUS_QUERY_FRAME,
  FakeFTDI,
  make_driver,
  parse_status,
)
from pylabrobot.bmg_labtech.protocol.framing import ftdi_extract_payload, ftdi_wrap_payload

PUMPS_LOGGER = "pylabrobot.bmg_labtech.clariostar.driver.features.pumps"

# Command frames verified on firmware 1.20, and replies to them.
BACKFLUSH_REFERENCE = bytes.fromhex("02000c0c02110c0100003a0d")  # pump 1, 100 uL/s, 500 uL
BACKFLUSH_PUMP_2 = bytes.fromhex("02000c0c02120c0100003b0d")
BACKFLUSH_1000_UL = bytes.fromhex("02000c0c02110c0200003b0d")
BACKFLUSH_430_UL_S = bytes.fromhex("02000c0c0211010100002f0d")
PRIME_REFERENCE = bytes.fromhex("02000c0c02010c0100002a0d")  # pump 1, 100 uL/s, 500 uL
PRIME_300_UL_S = bytes.fromhex("02000c0c020104010000220d")
PRIME_2000_UL = bytes.fromhex("02000c0c02010c0400002d0d")
# A backflush's reply that reads idle although the reader then runs it.
BACKFLUSH_REFERENCE_REPLY = bytes.fromhex("0200180c010500043008000000000000000000c00001280d")
BACKFLUSH_REPLY = bytes.fromhex("0200180c012500042008020000000000000000c000013a0d")
PRIME_REPLY = bytes.fromhex("0200180c012500440000020000000000000000c00001520d")  # door open
# A prime's reply with the door bit clear, and a warning with the door-abort codes 243/12.
PRIME_DOOR_SHUT_REPLY = bytes.fromhex("0200180c012500040000020000000000000000c00001120d")
PRIME_DOOR_SHUT_WARNING = bytes.fromhex("0200180c018500040000f30c00000000000000c000026f0d")


def status(busy: bool, door_open: bool, warning: Optional[Tuple[int, int]] = None) -> bytes:
  """A STATUS reply from this reader with the busy and door bits, and a warning's first two codes,
  set as given (synthetic), built from a reply to a prime."""
  payload = bytearray(ftdi_extract_payload(PRIME_REPLY))
  payload[1] = (payload[1] & ~0x20) | (0x20 if busy else 0)
  payload[3] = (payload[3] & ~0x40) | (0x40 if door_open else 0)
  if warning is not None:
    payload[1] |= 0x80
    payload[6:8] = bytes(warning)
  return ftdi_wrap_payload(bytes(payload), header=0x0C)


IDLE = status(busy=False, door_open=False)
BUSY = status(busy=True, door_open=False)
IDLE_DOOR_OPEN = status(busy=False, door_open=True)
BUSY_DOOR_OPEN = status(busy=True, door_open=True)
# A warning left from an earlier command (codes made up), present before a pump command.
STALE_IDLE = status(busy=False, door_open=False, warning=(110, 0))
STALE_BUSY = status(busy=True, door_open=False, warning=(110, 0))


class PumpTestCase(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.use_chunk(25)

  def use_chunk(self, chunk: int) -> None:
    """A fresh driver over a fake io that answers in reads of `chunk` bytes."""
    self.io = FakeFTDI(chunk=chunk)
    self.driver = make_driver(self.io)
    self.driver.configuration.pumps = ("XCalibur", "XCalibur", None)
    self.driver.configuration.firmware_version = "1.20"
    self.driver._discover_features()
    assert self.driver.pumps is not None
    self.pumps: Pumps = self.driver.pumps
    self.pumps.default_poll_interval = 0


class StatusTests(unittest.TestCase):
  def test_the_door_bit_reads_from_recorded_replies(self):
    self.assertTrue(parse_status(PRIME_REPLY).reagent_door_open)
    self.assertFalse(parse_status(BACKFLUSH_REPLY).reagent_door_open)
    self.assertFalse(parse_status(PRIME_DOOR_SHUT_REPLY).reagent_door_open)
    self.assertFalse(parse_status(STATUS_IDLE).reagent_door_open)  # a Plus without pumps

  def test_the_door_abort_is_described_by_its_two_codes(self):
    self.assertIn("reagent door", describe_quit_codes((243, 12, 0, 0, 0)))
    self.assertIn("wellplate", describe_quit_codes((243, 0, 0, 0, 0)))


class FeatureTests(unittest.TestCase):
  def test_built_only_when_a_pump_is_reported(self):
    driver = make_driver(FakeFTDI())
    driver._discover_features()
    self.assertIsNone(driver.pumps)
    driver.configuration.pumps = (None, None, None)
    driver._discover_features()
    self.assertIsNone(driver.pumps)
    driver.configuration.pumps = ("XCalibur", None, None)
    driver._discover_features()
    self.assertIsInstance(driver.pumps, Pumps)


class EncodingTests(PumpTestCase):
  async def test_backflushes_send_the_verified_frames(self):
    for (pump, volume, flow_rate), frame in [
      ((1, 500, 100), BACKFLUSH_REFERENCE),
      ((2, 500, 100), BACKFLUSH_PUMP_2),
      ((1, 1000, 100), BACKFLUSH_1000_UL),
      ((1, 500, 430), BACKFLUSH_430_UL_S),
    ]:
      self.io.written.clear()
      self.io.replies = [IDLE, IDLE, BACKFLUSH_REPLY, IDLE]
      await self.pumps.backflush(pump, volume=volume, flow_rate=flow_rate)
      self.assertEqual(self.io.written[2], frame)

  async def test_primes_send_the_verified_frames(self):
    for (pump, volume, flow_rate), frame in [
      ((1, 500, 100), PRIME_REFERENCE),
      ((1, 500, 300), PRIME_300_UL_S),
      ((1, 2000, 100), PRIME_2000_UL),
    ]:
      self.io.written.clear()
      self.io.replies = [IDLE_DOOR_OPEN, IDLE_DOOR_OPEN, PRIME_REPLY, IDLE_DOOR_OPEN]
      await self.pumps.prime(pump, volume=volume, flow_rate=flow_rate)
      self.assertEqual(self.io.written[2], frame)

  async def test_an_inferred_setting_is_sent_with_a_warning(self):
    self.io.replies = [IDLE, IDLE, BACKFLUSH_REPLY, BUSY, IDLE]
    with self.assertLogs(PUMPS_LOGGER, level="WARNING") as logs:
      await self.pumps.backflush(1, volume=1500, flow_rate=300)
    self.assertIn("inferred", "\n".join(logs.output))
    self.assertEqual(self.io.written[2][4:8], bytes.fromhex("02110403"))

  async def test_a_pump_number_that_is_not_an_int_is_refused(self):
    with self.assertRaisesRegex(ValueError, "pump must be one of"):
      await self.pumps.prime(1.0, volume=500)  # type: ignore[arg-type]
    self.assertEqual(self.io.written, [])

  def test_the_largest_volume_has_byte_9(self):
    # Pump 1, 4500 uL at 100 uL/s is 02 01 0c 09.
    self.assertEqual(self.pumps._encode(1, 4500, 100, backflush=False), bytes.fromhex("010c09"))

  async def test_every_setting_error_is_listed_and_nothing_is_sent(self):
    with self.assertRaises(ValueError) as caught:
      await self.pumps.prime(3, volume=750, flow_rate=50)
    message = str(caught.exception)
    self.assertIn("pump must be one of", message)
    self.assertIn("flow_rate must be one of", message)
    self.assertIn("volume must be a multiple", message)
    for volume in (0, 5000):
      with self.assertRaises(ValueError):
        await self.pumps.backflush(1, volume=volume)
    self.assertEqual(self.io.written, [])

  async def test_a_pump_not_fitted_is_refused(self):
    self.driver.configuration.pumps = ("XCalibur", None, None)
    with self.assertRaisesRegex(ValueError, "pump 2 is not fitted"):
      await self.pumps.backflush(2, volume=500)
    self.assertEqual(self.io.written, [])


class RunTests(PumpTestCase):
  async def test_on_firmware_whose_door_bit_is_not_checked_a_prime_is_sent_with_a_warning(self):
    self.driver.configuration.firmware_version = "1.35"
    self.io.replies = [IDLE, IDLE, PRIME_DOOR_SHUT_REPLY, BUSY, IDLE]
    with self.assertLogs(PUMPS_LOGGER, level="WARNING") as logs:
      await self.pumps.prime(1, volume=500)
    self.assertIn("not checked on firmware 1.35", "\n".join(logs.output))
    self.assertEqual(self.io.written[2], PRIME_REFERENCE)

  async def test_the_readers_refusal_of_a_shut_door_raises_where_the_door_is_not_checked(self):
    self.driver.configuration.firmware_version = "1.35"
    refused = status(busy=False, door_open=False, warning=(243, 12))
    self.io.replies = [IDLE, IDLE, PRIME_DOOR_SHUT_REPLY, refused, IDLE]
    with self.assertLogs(PUMPS_LOGGER, level="WARNING"):
      with self.assertRaises(PumpAbortedError) as caught:
        await self.pumps.prime(1, volume=500)
    self.assertEqual(caught.exception.quit_codes, (243, 12, 0, 0, 0))

  async def test_a_warning_while_still_busy_aborts_at_once(self):
    # The reader itself reports the command aborted, so no wait for idle.
    aborted = status(busy=True, door_open=True, warning=(243, 12))
    self.io.replies = [IDLE_DOOR_OPEN, IDLE_DOOR_OPEN, PRIME_REPLY, aborted]
    with self.assertRaises(PumpAbortedError) as caught:
      await self.pumps.prime(1, volume=500)
    self.assertEqual(caught.exception.quit_codes, (243, 12, 0, 0, 0))

  async def test_a_door_that_closes_and_reopens_still_ends_the_prime_early(self):
    self.io.replies = [
      IDLE_DOOR_OPEN, IDLE_DOOR_OPEN, PRIME_REPLY, BUSY, BUSY_DOOR_OPEN, IDLE_DOOR_OPEN,
    ]  # fmt: skip
    with self.assertLogs(PUMPS_LOGGER, level="WARNING"):
      with self.assertRaisesRegex(PumpAbortedError, "reagent door closed during the prime"):
        await self.pumps.prime(1, volume=500)

  async def test_refused_while_another_operation_runs(self):
    self.driver._measuring = True
    with self.assertRaisesRegex(RuntimeError, "running on this reader"):
      await self.pumps.backflush(1, volume=500)
    self.assertEqual(self.io.written, [])

  async def test_the_guard_is_released_after_a_pump_command(self):
    self.io.replies = [IDLE, IDLE, BACKFLUSH_REPLY, BUSY, IDLE]
    await self.pumps.backflush(1, volume=500)
    self.assertFalse(self.driver._measuring)

  async def test_a_prime_with_the_door_shut_sends_nothing(self):
    self.io.replies = [IDLE]
    with self.assertRaisesRegex(RuntimeError, "reagent door"):
      await self.pumps.prime(1, volume=500)
    self.assertEqual(self.io.written, [STATUS_QUERY_FRAME])

  async def test_the_door_closing_with_the_reader_warning_aborts_with_its_codes(self):
    # Door bit clear, busy clear and the 243/12 warning in one poll's reply.
    closed = status(busy=False, door_open=False, warning=(243, 12))
    self.io.replies = [IDLE_DOOR_OPEN, IDLE_DOOR_OPEN, PRIME_REPLY, BUSY_DOOR_OPEN, closed]
    with self.assertRaises(PumpAbortedError) as caught:
      await self.pumps.prime(1, volume=500)
    self.assertEqual(caught.exception.quit_codes, (243, 12, 0, 0, 0))

  async def test_the_door_closing_without_a_warning_waits_for_idle_then_aborts(self):
    # The reader may still be pumping: no stop is reported until it reads idle.
    self.io.replies = [
      IDLE_DOOR_OPEN, IDLE_DOOR_OPEN, PRIME_REPLY, BUSY_DOOR_OPEN, BUSY, BUSY, IDLE,
    ]  # fmt: skip
    with self.assertLogs(PUMPS_LOGGER, level="WARNING") as logs:
      with self.assertRaisesRegex(PumpAbortedError, "reagent door closed during the prime"):
        await self.pumps.prime(1, volume=500)
    self.assertIn("waiting until the reader is idle", "\n".join(logs.output))
    self.assertEqual(len(self.io.written), 7)

  async def test_waits_for_busy_even_when_the_reply_reads_idle(self):
    self.io.replies = [IDLE, IDLE, BACKFLUSH_REFERENCE_REPLY, IDLE, BUSY, BUSY, IDLE]
    await self.pumps.backflush(1, volume=500)
    self.assertEqual(
      self.io.written, [STATUS_QUERY_FRAME] * 2 + [BACKFLUSH_REFERENCE] + [STATUS_QUERY_FRAME] * 4
    )

  async def test_a_busy_reply_counts_as_started(self):
    self.io.replies = [IDLE_DOOR_OPEN, IDLE_DOOR_OPEN, PRIME_REPLY, BUSY_DOOR_OPEN, IDLE_DOOR_OPEN]
    await self.pumps.prime(1, volume=500)
    self.assertEqual(len(self.io.written), 5)

  async def test_a_pump_that_never_starts_times_out(self):
    self.io.replies = [IDLE, IDLE, BACKFLUSH_REFERENCE_REPLY, IDLE]
    with self.assertRaisesRegex(TimeoutError, "did not start"):
      await self.pumps.backflush(1, volume=500, start_timeout=0)

  async def test_a_pump_that_never_ends_times_out(self):
    self.io.replies = [IDLE, IDLE, BACKFLUSH_REPLY, BUSY]
    with self.assertRaisesRegex(TimeoutError, "still runs"):
      await self.pumps.backflush(1, volume=500, timeout=0)

  async def test_the_door_abort_raises_and_the_link_stays_in_step(self):
    # The door shuts after the check: the reader's unasked warning is read as the first poll's
    # reply, and the poll's own reply must not be taken as the answer to the next command.
    self.use_chunk(24)  # reads end at the command reply's last byte: the warning is read whole
    self.io.replies = [
      IDLE_DOOR_OPEN,  # door check
      IDLE_DOOR_OPEN,  # idle before the command
      PRIME_DOOR_SHUT_REPLY + PRIME_DOOR_SHUT_WARNING,
      IDLE,  # the first poll's own reply, read late and discarded
      IDLE_DOOR_OPEN,  # the next status, asked after the abort
    ]
    with self.assertRaises(PumpAbortedError) as caught:
      await self.pumps.prime(1, volume=500)
    self.assertEqual(caught.exception.quit_codes, (243, 12, 0, 0, 0))
    self.assertIn("reagent door", str(caught.exception))
    self.assertTrue((await self.driver.request_status()).reagent_door_open)

  async def test_a_lost_door_warning_is_caught_by_the_door_bit(self):
    # Constructed: a warning sent between exchanges. Reads of 25 bytes take its first byte with
    # the command reply; the first poll then
    # reads a broken frame and is retried after a purge, which drops the warning.
    self.use_chunk(25)
    self.io.replies = [
      IDLE_DOOR_OPEN,  # door check
      IDLE_DOOR_OPEN,  # idle before the command
      PRIME_DOOR_SHUT_REPLY + PRIME_DOOR_SHUT_WARNING,
      IDLE,  # the first poll's reply, purged with the warning's rest
      IDLE,  # the retried poll: idle, door shut
      IDLE_DOOR_OPEN,  # the next status, asked after the abort
    ]
    with self.assertRaises(PumpAbortedError) as caught:
      await self.pumps.prime(1, volume=500)
    self.assertIsNone(caught.exception.quit_codes)
    self.assertIn("reagent door closed during the prime", str(caught.exception))
    self.assertTrue((await self.driver.request_status()).reagent_door_open)

  async def test_a_backflush_does_not_watch_the_door(self):
    self.io.replies = [IDLE, IDLE, BACKFLUSH_REPLY, BUSY, IDLE]
    await self.pumps.backflush(1, volume=500)
    self.assertEqual(len(self.io.written), 5)

  async def test_a_malformed_command_reply_is_not_resent(self):
    # As for INIT: the reader may already be pumping, so the driver polls status instead of
    # sending the command again.
    self.io.replies = [IDLE, IDLE, BACKFLUSH_REPLY[:10], BUSY, IDLE]
    with self.assertLogs(PUMPS_LOGGER, level="WARNING"):
      await self.pumps.backflush(1, volume=500)
    self.assertEqual(self.io.written.count(BACKFLUSH_REFERENCE), 1)

  async def test_a_warning_in_the_command_reply_aborts(self):
    self.io.replies = [IDLE_DOOR_OPEN, IDLE_DOOR_OPEN, PRIME_DOOR_SHUT_WARNING]
    with self.assertRaises(PumpAbortedError) as caught:
      await self.pumps.prime(1, volume=500)
    self.assertEqual(caught.exception.quit_codes, (243, 12, 0, 0, 0))

  async def test_a_warning_present_before_the_command_is_not_taken_for_new(self):
    self.io.replies = [STALE_IDLE, STALE_IDLE, BACKFLUSH_REPLY, STALE_BUSY, STALE_IDLE]
    await self.pumps.backflush(1, volume=500)
    self.assertEqual(len(self.io.written), 5)


if __name__ == "__main__":
  unittest.main()
