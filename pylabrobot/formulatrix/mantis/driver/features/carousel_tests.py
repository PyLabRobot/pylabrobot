"""LC3 lifecycle, positioning and tag reads against captured controller and reader replies."""

import asyncio
import dataclasses
import json
import os
import struct
import tempfile
import unittest
from pathlib import Path
from typing import Any, List, Optional, Tuple
from unittest import mock

from pylabrobot.formulatrix.mantis.device import RECORDING_MANTIS_V4

from .. import master as master_module
from ..carousel_configuration import CarouselConfiguration
from ..errors import (
  FrameError,
  MantisConfigurationError,
  MantisMotionError,
  MantisNotConnectedError,
  MantisTimeoutError,
)
from ..framing import build_frame, parse_frame
from ..master_tests import (
  RECORDED_REPLIES,
  DriverTestCase,
  FakeLink,
  FakeReader,
  make_answers,
)
from . import rfid
from .carousel import MantisCarousel
from .rfid_tests import LOW_VOLUME_HI_FI_TAG_REPLY, compute_tag

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
# An LC3's RFID reader angle as its data.db holds it (RfidCarousel24Offset); no turn to it is
# captured, so no test sends one.
RFID_CONFIGURATION = dataclasses.replace(CONFIGURATION, rfid_slot_one_position=225.029)
# The captured turns at 30 degrees/s: to 15 degrees, its backlash approach from above, and to
# slot 1's calibrated angle.
TURN_TO_15_DEGREES = bytes.fromhex("00008ee3388ee33851408ee3388ee3386140000000000070a740")
TURN_BELOW_15_DEGREES = bytes.fromhex("0000a1bd84f612da4d408ee3388ee3386140000000000070a740")
TURN_TO_SLOT_1 = bytes.fromhex("0000ebffffff33757d408ee3388ee3386140000000000070a740")
# A v3's path into its carousel's slot at the head, measured on the unit, from the chip changer
# route's end: down onto the chip at line 6, the vendor's pull-back, up, and out the same way.
CAROUSEL_PICK_UP_PATH: List[List[Optional[float]]] = [
  [25.0, 41.177, -1.5],
  [90.0, 18.0, -1.5],
  [121.692, 13.813, -1.5],
  [126.766, 18.139, -1.5],
  [126.766, 20.139, -1.5],
  [126.766, 20.139, 3.5],
  [126.766, 20.139, 8.5],
  [124.092, 20.139, 8.5],
  [124.092, 20.686, 8.5],
  [124.092, 20.686, 3.5],
  [124.092, 20.686, -1.5],
  [126.766, 18.139, -1.5],
  [121.692, 13.813, -1.5],
  [90.0, 18.0, -1.5],
  [25.0, 41.177, -1.5],
]
HOME_PAYLOAD = bytes.fromhex("000000000100000000000000000089400000000000408f40000000000070a740")
STOP_PAYLOAD = bytes.fromhex("0000000000000070a740")


class CarouselLink(FakeLink):
  """Play captured completions and position readbacks for each captured motion request."""

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
      motion = CAPTURE["motions"][f"{command}:{payload.hex()}"]
      key = board, command, payload
      if command == 24 or self.acknowledge_motion:
        self.answers[key] = [bytes.fromhex(motion["reply"])]
      if command == 24 or self.complete_motion:
        self.events[key] = [
          build_frame(
            packet_number=packet,
            board=0,
            command=event["code"],
            payload=bytes.fromhex(event["payload"]),
          )
          for event in motion["events"]
        ]
        self.answers[0, 20, b"\0\0"] = [bytes.fromhex("0400" if self.finish_homed else "0000")]
        if "position" in motion:
          self.answers[0, 27, b"\0\0"] = [bytes.fromhex(motion["position"])]
      else:
        self.answers[0, 20, b"\0\0"] = [bytes.fromhex("0500")]
      self.motion_written.set()
    return await super().write(data)

  def commands(self):
    """The board, command and payload of every request."""
    return [parse_frame(frame)[1:] for frame in self.written]


def make_mantis_driver(*, parked: bool = True) -> mock.Mock:
  """The Mantis's driver as a carousel sees it: an arm brought up, its head at idle or not."""
  driver = mock.Mock()
  driver.arm.is_initialized = True
  driver.arm.request_is_parked = mock.AsyncMock(return_value=parked)
  return driver


class CarouselTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    """Open a captured LC3 link with the carousel's unit configuration."""
    self.link = CarouselLink()
    self.mantis_driver = make_mantis_driver()
    self.carousel = MantisCarousel(
      configuration=CONFIGURATION, io=self.link.io, rfid_io=None, driver=self.mantis_driver
    )
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
      await self.carousel.move_to(position=15)
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
    await self.carousel.move_to(position=15, speed=30)
    await self.carousel.move_to(position=345, speed=30)
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
    await self.carousel.move_to_slot(slot=1, speed=30)
    self.assertAlmostEqual(
      await self.carousel.request_position(), CONFIGURATION.slot_one_position, places=4
    )
    await self.carousel.move_to_slot(slot=2, speed=30)
    await self.carousel.move_to_slot(slot=2, speed=30)
    self.assertAlmostEqual(
      await self.carousel.request_position(), CONFIGURATION.slot_one_position + 15, places=4
    )
    self.assertEqual(sum(command == 22 for _, command, _ in self.link.commands()), 2)

  async def test_cancellation_stops_an_accepted_move(self):
    """A move whose completion is pending must be stopped before cancellation propagates."""
    await self.carousel.home()
    self.link.complete_motion = False
    self.link.motion_written.clear()
    task = asyncio.create_task(self.carousel.move_to(position=15, speed=30))
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
      await self.carousel.move_to(position=15, speed=30)
    self.assertEqual(sum(command == 22 for _, command, _ in self.link.commands()), 1)
    self.assertIn((0, 24, STOP_PAYLOAD), self.link.commands())

  async def test_missing_completion_stops_motion(self):
    """An accepted move without its completion event has a bounded wait."""
    await self.carousel.home()
    self.link.complete_motion = False
    with self.assertRaises(MantisTimeoutError):
      await self.carousel.move_to(position=15, speed=30, timeout=0.02)
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
    task = asyncio.create_task(self.carousel.move_to(position=15, speed=30))
    await self.link.motion_written.wait()
    await self.carousel.stop()
    with self.assertRaises(MantisNotConnectedError):
      await task
    self.assertIn((0, 24, STOP_PAYLOAD), self.link.commands())
    self.link.io.stop.assert_awaited()

  async def test_nothing_turns_unless_the_head_reads_at_idle(self):
    """Homing and turning check the head first; with the arm not brought up neither is sent."""
    self.mantis_driver.arm.request_is_parked.return_value = False
    with self.assertRaisesRegex(MantisMotionError, "does not read at idle"):
      await self.carousel.home()
    self.mantis_driver.arm.request_is_parked.return_value = True
    await self.carousel.home()
    self.mantis_driver.arm.is_initialized = False
    with self.assertRaisesRegex(MantisMotionError, "not brought up"):
      await self.carousel.move_to_slot(slot=1, speed=30)
    self.assertEqual(self.link.commands().count((0, 21, HOME_PAYLOAD)), 1)
    self.assertFalse(any(command == 22 for _, command, _ in self.link.commands()))

  async def test_the_angle_read_back_must_be_the_one_asked_for(self):
    """Homed, still and without faults at the angle, as read; a whole turn on counts too."""
    await self.carousel.home()
    await self.carousel.move_to(position=15, speed=30)
    await self.carousel._check_at_position(position=15)
    await self.carousel._check_at_position(position=375)
    with self.assertRaisesRegex(MantisMotionError, "not at 30.00 degrees"):
      await self.carousel._check_at_position(position=30)
    # A homed carousel still moving, as the LC3 read; a Mantis motor's following error while idle.
    for status, message in (("0500", "is moving"), (RECORDED_REPLIES[0, 20, "0100"], "following")):
      with self.subTest(status=status):
        self.link.answers[0, 20, b"\0\0"] = [bytes.fromhex(status)]
        with self.assertRaisesRegex(MantisMotionError, message):
          await self.carousel._check_at_position(position=15)

  async def test_a_hold_at_the_slot_runs_its_block_only_there_and_turns_nothing_meanwhile(self):
    """At slot 1, by its captured turn, the block runs and a turn asked for waits for its end;
    turns follow with their captured frames."""
    await self.carousel.home()
    await self.carousel.move_to_slot(slot=1, speed=30)
    async with self.carousel._hold_at_slot(slot=1):
      turn = asyncio.create_task(self.carousel.move_to(position=15, speed=30))
      for _ in range(20):
        await asyncio.sleep(0)
      self.assertFalse(turn.done())
      turns = [payload for _, command, payload in self.link.commands() if command == 22]
      self.assertEqual(turns, [TURN_TO_SLOT_1])
    await turn
    turns = [payload for _, command, payload in self.link.commands() if command == 22]
    self.assertEqual(turns, [TURN_TO_SLOT_1, TURN_BELOW_15_DEGREES, TURN_TO_15_DEGREES])

  async def test_a_hold_refuses_a_carousel_not_read_at_the_slot(self):
    """Off the slot (at 15 degrees, by its captured turn), still moving, or with a fault (a Mantis
    motor's following error): the block does not run."""
    await self.carousel.home()
    await self.carousel.move_to(position=15, speed=30)
    ran = []
    with mock.patch.object(self.carousel, "move_to", new=mock.AsyncMock()):
      for status, message in (
        ("0400", "not at 102.63 degrees"),
        ("0500", "is moving"),
        (RECORDED_REPLIES[0, 20, "0100"], "following"),
      ):
        with self.subTest(status=status):
          self.link.answers[0, 20, b"\0\0"] = [bytes.fromhex(status)]
          with self.assertRaisesRegex(MantisMotionError, message):
            async with self.carousel._hold_at_slot(slot=1):
              ran.append(status)
    self.assertEqual(ran, [])

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
        await self.carousel.move_to_slot(slot=slot)
    self.assertEqual(len(self.link.written), before)


class CarouselRfidTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    """A carousel with its reader's angle and reader, homed, the head at idle."""
    self.link = CarouselLink()
    self.reader = FakeReader(tags={0: LOW_VOLUME_HI_FI_TAG_REPLY})
    self.reader.antenna = 0  # the LC3's one reader has no antenna switch
    self.carousel = MantisCarousel(
      configuration=RFID_CONFIGURATION,
      io=self.link.io,
      rfid_io=self.reader.io,
      driver=make_mantis_driver(),
    )
    self.carousel._driver.default_read_timeout = 0.05
    await self.carousel.setup()
    self.addAsyncCleanup(self.carousel.stop)
    await self.carousel.home()

  async def test_setup_sets_up_the_reader_as_the_mantis_does(self):
    self.assertEqual(
      self.reader.written,
      [
        rfid.RFID_PING_DEVICE_COMMAND,
        rfid.RFID_SET_ANTENNA_MODE_COMMAND,
        rfid.RFID_SET_UP_ISO15693_PROTOCOL_COMMAND,
      ],
    )
    self.reader.io.set_baudrate.assert_awaited_once_with(baudrate=rfid.RFID_BAUD_RATE)

  async def test_a_tag_is_read_held_at_the_readers_angle(self):
    """The slot's reader angle is asked for, then, held there, the carousel's angle is read back
    before the tag; the turn itself is not captured, so it is not sent here."""
    held: List[bool] = []
    write = self.reader.write

    async def write_held(data: bytes) -> int:
      held.append(self.carousel._operation_lock.locked())
      return await write(data)

    self.reader.io.write.side_effect = write_held
    with (
      mock.patch.object(self.carousel, "move_to", new=mock.AsyncMock()) as move_to,
      mock.patch.object(self.carousel, "_check_at_position", new=mock.AsyncMock()) as check,
    ):
      tag = await self.carousel.request_tag(slot=2)
    move_to.assert_awaited_once_with(position=225.029 + 15)
    check.assert_awaited_once_with(position=225.029 + 15)
    self.assertEqual(held, [True])
    assert tag is not None
    self.assertEqual(
      (tag["serial"], tag["chip_type"], tag["valid"]), ("LS252202", "low_volume_hi_fi", True)
    )

  async def test_only_a_completed_read_counts_a_slot_empty(self):
    """An empty field reads None; a short tag, or no reply in time, raises and is never None."""
    short = [
      b"Request mode.\r\n",
      b"[" + compute_tag(reply=LOW_VOLUME_HI_FI_TAG_REPLY)[:-4].hex().encode() + b"]\r\n",
    ]
    with (
      mock.patch.object(self.carousel, "move_to", new=mock.AsyncMock()),
      mock.patch.object(self.carousel, "_check_at_position", new=mock.AsyncMock()),
      mock.patch.object(rfid, "RFID_REPLY_TIMEOUT", 0.05),
    ):
      self.reader.tags = {}
      self.assertIsNone(await self.carousel.request_tag(slot=1))
      for reply, error in ((short, FrameError), ([b"Request mode.\r\n"], MantisTimeoutError)):
        with self.subTest(error=error.__name__):
          self.reader.tags = {0: reply}
          with self.assertRaises(error):
            await self.carousel.request_tag(slot=1)

  async def test_a_slot_not_on_the_carousel_or_no_angle_sends_nothing(self):
    before = len(self.link.written), len(self.reader.written)
    with self.assertRaises(ValueError):
      await self.carousel.request_tag(slot=25)
    self.carousel.configuration = CONFIGURATION  # no reader's angle
    with self.assertRaisesRegex(MantisConfigurationError, "no RFID reader angle"):
      await self.carousel.request_tag(slot=1)
    self.assertEqual((len(self.link.written), len(self.reader.written)), before)

  async def test_stop_closes_the_readers_link_too(self):
    await self.carousel.stop()
    self.reader.io.stop.assert_awaited_once()


class CarouselDiscoveryTests(DriverTestCase):
  def setUp(self):
    """The LC3's links, captured, in place of the USB devices each declared block names."""
    super().setUp()
    self.lc3_link, self.lc3_reader = CarouselLink(), FakeReader()
    for name, io in (
      ("_build_carousel_io", self.lc3_link.io),
      ("_build_carousel_rfid_io", self.lc3_reader.io),
    ):
      patcher = mock.patch.object(master_module.MantisDriver, name, return_value=io)
      patcher.start()
      self.addCleanup(patcher.stop)
    self.directory = tempfile.TemporaryDirectory()
    self.addCleanup(self.directory.cleanup)

  def declare(self, **blocks) -> str:
    """The v4 recording's device with the given unit blocks."""
    with open(RECORDING_MANTIS_V4, encoding="utf-8") as file:
      declared = {"device": json.load(file)["device"], **blocks}
    path = os.path.join(self.directory.name, "unit.json")
    with open(path, "w", encoding="utf-8") as file:
      json.dump(declared, file)
    return path

  async def test_a_declared_carousel_is_set_up_with_its_reader(self):
    block = {
      **dataclasses.asdict(RFID_CONFIGURATION),
      "pick_up_path": CAROUSEL_PICK_UP_PATH,
    }
    driver = await self.set_up_driver(
      link=FakeLink(answers=make_answers()),
      declared_configuration_json=self.declare(left_carousel=block),
    )
    assert driver.left_carousel is not None
    self.assertIsNone(driver.right_carousel)
    self.assertEqual(driver.left_carousel.firmware_version, "4.20.6")
    configuration = driver.left_carousel.configuration
    assert configuration.pick_up_path is not None
    self.assertEqual(
      [[line.x, line.y, line.z] for line in configuration.pick_up_path], CAROUSEL_PICK_UP_PATH
    )
    self.assertEqual(dataclasses.replace(configuration, pick_up_path=None), RFID_CONFIGURATION)
    self.assertEqual(self.lc3_link.commands(), [(0, 1, b""), (0, 10, b"\0\0")])
    self.assertEqual(self.lc3_reader.written[0], rfid.RFID_PING_DEVICE_COMMAND)
    saved = os.path.join(self.directory.name, "saved.json")
    driver.save_configuration(path=saved)
    with open(saved, encoding="utf-8") as file:
      self.assertEqual(json.load(file)["left_carousel"], block)
    await driver.stop()
    self.assertIsNone(driver.left_carousel)
    self.lc3_link.io.stop.assert_awaited()
    self.lc3_reader.io.stop.assert_awaited()

  async def test_an_lc3_that_does_not_answer_is_left_out(self):
    self.lc3_link.io.setup.side_effect = OSError("no LC3")
    driver = await self.set_up_driver(
      link=FakeLink(answers=make_answers()),
      declared_configuration_json=self.declare(left_carousel=dataclasses.asdict(CONFIGURATION)),
    )
    self.assertIsNone(driver.left_carousel)
    self.assertIsNotNone(driver.arm)

  async def test_a_malformed_block_refuses_setup(self):
    block = dataclasses.asdict(CONFIGURATION)
    for changed, message in (
      ({"capacity": "24"}, "malformed carousel block"),
      ({"pick_up_path": [[1.0, 2.0]]}, "malformed carousel block"),
      ({"pick_up_path": []}, "pick-up path is empty"),
      ({"pick_up_path": [[126.766, 20.139, -1.5], [126.766, 20.139, 8.5]]}, "never moves"),
    ):
      with self.subTest(changed=changed):
        with self.assertRaisesRegex(MantisConfigurationError, message):
          await self.set_up_driver(
            link=FakeLink(answers=make_answers()),
            declared_configuration_json=self.declare(right_carousel={**block, **changed}),
          )
    self.lc3_link.io.setup.assert_not_awaited()


class CarouselUsbTests(unittest.TestCase):
  def test_usb_product_serial_and_interfaces(self):
    """The shared VID/PID must never select the main Mantis in place of the LC3."""
    driver = master_module.MantisDriver(io=mock.Mock())
    with mock.patch.object(master_module, "FTDI") as ftdi:
      driver._build_carousel_io(device_id="L-000143", ftdi_library=None)
      driver._build_carousel_rfid_io(device_id="L-000143", ftdi_library=None)
    self.assertEqual(
      [
        (call.kwargs["usb_product"], call.kwargs["device_id"], call.kwargs["interface_select"])
        for call in ftdi.call_args_list
      ],
      [("LC3", "L-000143", 2), ("LC3", "L-000143", 1)],
    )
