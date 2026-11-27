"""LC3 lifecycle and positioning against captured controller replies."""

import asyncio
import json
import struct
import unittest
from pathlib import Path
from typing import Any, Tuple
from unittest import mock

from .carousel import MantisCarousel
from .driver.carousel_configuration import CarouselConfiguration
from .driver.errors import MantisMotionError, MantisNotConnectedError, MantisTimeoutError
from .driver.framing import build_frame, parse_frame
from .driver.master_tests import FakeLink

with Path(__file__).with_name("carousel_test_data.json").open() as file:
  CAPTURE = json.load(file)

CONFIGURATION = CarouselConfiguration(
  device_id="L-000143",
  capacity=24,
  steps_per_revolution=1653.3333333333333,
  slot_one_position=102.627260269657,
  idle_position=98.9052517798639,
  preposition=2.0,
  homing_slow_speed=174.19354838709677,
  homing_fast_speed=217.74193548387098,
  homing_acceleration=653.2258064516129,
  default_speed=174.19354838709677,
  default_acceleration=653.2258064516129,
)
HOME_PAYLOAD = bytes.fromhex("000000000100000000000000000089400000000000408f40000000000070a740")
STOP_PAYLOAD = bytes.fromhex("0000000000000070a740")


class CarouselLink(FakeLink):
  """Play recorded completions and position readbacks for each captured motion request."""

  def __init__(self):
    """Start unhomed; accepted commands can withhold their acknowledgement or completion."""
    answers = {}
    for key, replies in CAPTURE["reads"].items():
      command, payload = key.split(":")
      answers[0, int(command), bytes.fromhex(payload)] = [bytes.fromhex(replies[0])]
    super().__init__(answers=answers)
    self.complete_motion = True
    self.acknowledge_motion = True
    self.finish_homed = True
    self.motion_written = asyncio.Event()

  async def write(self, data: bytes) -> int:
    """Accept only captured motor requests, using their captured reply and completion bytes."""
    packet, board, command, payload = parse_frame(data)
    if command in (21, 22, 24):
      record = CAPTURE["motions"][f"{command}:{payload.hex()}"]
      key = board, command, payload
      if command == 24 or self.acknowledge_motion:
        self.answers[key] = [bytes.fromhex(record["reply"])]
      if command == 24 or self.complete_motion:
        self.events[key] = [
          build_frame(
            packet_number=packet,
            board=0,
            command=event["code"],
            payload=bytes.fromhex(event["payload"]),
          )
          for event in record["events"]
        ]
        self.answers[0, 20, b"\0\0"] = [bytes.fromhex("0400" if self.finish_homed else "0000")]
        if "position" in record:
          self.answers[0, 27, b"\0\0"] = [bytes.fromhex(record["position"])]
      else:
        self.answers[0, 20, b"\0\0"] = [bytes.fromhex("0500")]
      self.motion_written.set()
    return await super().write(data)

  def commands(self):
    """The board, command and payload of every request."""
    return [parse_frame(frame)[1:] for frame in self.written]


class CarouselTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    """Open a captured LC3 link with the carousel's unit configuration."""
    self.link = CarouselLink()
    self.carousel = MantisCarousel(configuration=CONFIGURATION, io=self.link.io)
    self.carousel._driver.default_read_timeout = 0.05
    await self.carousel.setup()
    self.addAsyncCleanup(self.carousel.stop)

  async def test_setup_selects_only_the_carousel_motor_board(self):
    """Discovery does not probe pressure boards or initialize the Mantis arm."""
    self.assertEqual(self.link.commands(), [(0, 1, b""), (0, 10, b"\0\0")])
    self.assertEqual(self.carousel.firmware_version, "4.20.6")
    await self.carousel.setup()
    self.link.io.setup.assert_awaited_once()
    with self.assertRaisesRegex(MantisMotionError, "not homed"):
      await self.carousel.move_to(15)
    self.assertFalse(any(command in (21, 22) for _, command, _ in self.link.commands()))

  async def test_home_requires_completion_and_is_idempotent(self):
    """Home uses the configured rates and sends no second home once referenced."""
    await self.carousel.home()
    await self.carousel.home()
    self.assertEqual(self.link.commands().count((0, 21, HOME_PAYLOAD)), 1)
    self.assertIn((0, 17, b"\0\0"), self.link.commands())
    self.assertEqual(await self.carousel.request_status(), 4)

  async def test_rotation_uses_short_path_and_backlash_approach(self):
    """The 15° to 345° move crosses zero and approaches the target from below."""
    await self.carousel.home()
    await self.carousel.move_to(15, speed=30)
    await self.carousel.move_to(345, speed=30)
    moves = [
      struct.unpack("<hddd", payload)[1]
      for _, command, payload in self.link.commands()
      if command == 22
    ]
    for actual, expected in zip(moves, (68.88888888888889, -78.07407407407408, -68.88888888888891)):
      self.assertAlmostEqual(actual, expected)
    self.assertEqual(len(moves), 3)
    self.assertAlmostEqual(await self.carousel.request_position(), 345, places=4)

  async def test_calibrated_slots_and_repeated_selection(self):
    """Slot numbers include the unit's angular offset; selecting the same slot does not move."""
    await self.carousel.home()
    await self.carousel.move_to_slot(1, speed=30)
    self.assertAlmostEqual(
      await self.carousel.request_position(), CONFIGURATION.slot_one_position, places=4
    )
    await self.carousel.move_to_slot(2, speed=30)
    await self.carousel.move_to_slot(2, speed=30)
    self.assertAlmostEqual(
      await self.carousel.request_position(), CONFIGURATION.slot_one_position + 15, places=4
    )
    self.assertEqual(sum(command == 22 for _, command, _ in self.link.commands()), 2)

  async def test_cancellation_stops_an_accepted_move(self):
    """A move whose completion is pending must be stopped before cancellation propagates."""
    await self.carousel.home()
    self.link.complete_motion = False
    self.link.motion_written.clear()
    task = asyncio.create_task(self.carousel.move_to(15, speed=30))
    await self.link.motion_written.wait()
    task.cancel()
    with self.assertRaises(asyncio.CancelledError):
      await task
    self.assertIn((0, 24, STOP_PAYLOAD), self.link.commands())
    self.assertEqual(await self.carousel.request_status(), 4)
    self.assertEqual(self.carousel._driver._event_waiters, [])

  async def test_missing_acknowledgement_stops_without_retrying_motion(self):
    """An unanswered move can already be in motion."""
    await self.carousel.home()
    self.link.acknowledge_motion = False
    self.link.complete_motion = False
    with self.assertRaises(MantisTimeoutError):
      await self.carousel.move_to(15, speed=30)
    self.assertEqual(sum(command == 22 for _, command, _ in self.link.commands()), 1)
    self.assertIn((0, 24, STOP_PAYLOAD), self.link.commands())

  async def test_missing_completion_stops_motion(self):
    """An accepted move without its completion event has a bounded wait."""
    await self.carousel.home()
    self.link.complete_motion = False
    with self.assertRaises(MantisTimeoutError):
      await self.carousel.move_to(15, speed=30, timeout=0.02)
    self.assertIn((0, 24, STOP_PAYLOAD), self.link.commands())

  async def test_home_without_homed_bit_stops_and_raises(self):
    """A completion event alone cannot establish the home reference."""
    self.link.finish_homed = False
    with self.assertRaisesRegex(MantisMotionError, "not homed"):
      await self.carousel.home()
    self.assertIn((0, 24, STOP_PAYLOAD), self.link.commands())

  async def test_shutdown_during_motion_closes_after_stopping(self):
    """Closing the carousel stops its motor and ends an outstanding operation."""
    await self.carousel.home()
    self.link.complete_motion = False
    self.link.motion_written.clear()
    task = asyncio.create_task(self.carousel.move_to(15, speed=30))
    await self.link.motion_written.wait()
    await self.carousel.stop()
    with self.assertRaises(MantisNotConnectedError):
      await task
    self.assertIn((0, 24, STOP_PAYLOAD), self.link.commands())
    self.link.io.stop.assert_awaited()

  def test_backlash_path_respects_both_travel_limits(self):
    """When the nearest negative approach crosses the limit, use the positive equivalent."""
    self.carousel._limits = (0, 2000)
    self.assertEqual(
      self.carousel._plan_rotation(position=0, current=10), (CONFIGURATION.steps_per_revolution,)
    )
    self.carousel._limits = (0, 1000)
    with self.assertRaises(MantisMotionError):
      self.carousel._plan_rotation(position=0, current=10)

  async def test_invalid_slots_send_nothing(self):
    """Reject invalid indices before contacting the controller."""
    before = len(self.link.written)
    invalid_slots: Tuple[Any, ...] = (0, 25, True, 1.5)
    for slot in invalid_slots:
      with self.subTest(slot=slot), self.assertRaises(ValueError):
        await self.carousel.move_to_slot(slot)
    self.assertEqual(len(self.link.written), before)


class CarouselUsbTests(unittest.TestCase):
  def test_usb_product_and_serial_are_required(self):
    """The shared VID/PID must never select the main Mantis in place of the LC3."""
    with mock.patch("pylabrobot.formulatrix.mantis.carousel.FTDI") as ftdi:
      MantisCarousel(configuration=CONFIGURATION)
    self.assertEqual(ftdi.call_args.kwargs["usb_product"], "LC3")
    self.assertEqual(ftdi.call_args.kwargs["device_id"], "L-000143")
    self.assertEqual(ftdi.call_args.kwargs["interface_select"], 2)
