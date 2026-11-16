"""LC3 lifecycle and positioning against captured controller replies."""

import json
import struct
from pathlib import Path
from typing import Any, Tuple
from unittest import mock

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.testing.concurrency import AnyioTestBase

from .carousel import MantisCarousel
from .driver.carousel_configuration import CarouselConfiguration
from .driver.errors import MantisMotionError, MantisNotConnectedError, MantisTimeoutError
from .driver.framing import build_frame, parse_frame
from .driver.master_tests import FakeLink

with Path(__file__).with_name("carousel_test_data.json").open(encoding="utf-8") as file:
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

  def __init__(self) -> None:
    """Start unhomed; accepted commands can withhold their acknowledgement or completion."""
    answers = {}
    for key, replies in CAPTURE["reads"].items():
      command, payload = key.split(":")
      answers[0, int(command), bytes.fromhex(payload)] = [bytes.fromhex(replies[0])]
    super().__init__(answers=answers)
    self.complete_motion = True
    self.acknowledge_motion = True
    self.finish_homed = True
    self.motion_written = anyio.Event()

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

  def commands(self) -> list[Tuple[int, int, bytes]]:
    """The board, command and payload of every request."""
    return [parse_frame(frame)[1:] for frame in self.written]


class TestCarousel(AnyioTestBase):
  """Verify LC3 carousel lifecycle, homing, backlash rotation, and cancellation safety."""

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    """Open a captured LC3 link with the carousel's unit configuration."""
    self.link = CarouselLink()
    self.carousel = MantisCarousel(configuration=CONFIGURATION, io=self.link.io)
    self.carousel._driver.default_read_timeout = 0.05
    await stack.enter_async_context(self.carousel.lifespan())

  def test_inherits_managed_lifespan_methods(self) -> None:
    assert issubclass(MantisCarousel, HasLifespan)
    assert MantisCarousel.setup is HasLifespan.setup
    assert MantisCarousel.stop is HasLifespan.stop
    assert MantisCarousel.__aenter__ is HasLifespan.__aenter__
    assert MantisCarousel.__aexit__ is HasLifespan.__aexit__

  async def test_lifespan_selects_only_the_carousel_motor_board(self) -> None:
    """Discovery does not probe pressure boards or initialize the Mantis arm."""
    assert self.link.commands() == [(0, 1, b""), (0, 10, b"\0\0")]
    assert self.carousel.firmware_version == "4.20.6"
    assert self.link.io.entered_count == 1
    assert self.link.io.exited_count == 0
    with pytest.raises(MantisMotionError, match="not homed"):
      await self.carousel.move_to(15)
    assert not any(command in (21, 22) for _, command, _ in self.link.commands())

  async def test_home_requires_completion_and_is_idempotent(self) -> None:
    """Home uses the configured rates and sends no second home once referenced."""
    await self.carousel.home()
    await self.carousel.home()
    assert self.link.commands().count((0, 21, HOME_PAYLOAD)) == 1
    assert (0, 17, b"\0\0") in self.link.commands()
    assert await self.carousel.request_status() == 4

  async def test_rotation_uses_short_path_and_backlash_approach(self) -> None:
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
      assert actual == pytest.approx(expected)
    assert len(moves) == 3
    assert await self.carousel.request_position() == pytest.approx(345, abs=1e-4)

  async def test_calibrated_slots_and_repeated_selection(self) -> None:
    """Slot numbers include the unit's angular offset; selecting the same slot does not move."""
    await self.carousel.home()
    await self.carousel.move_to_slot(1, speed=30)
    assert await self.carousel.request_position() == pytest.approx(
      CONFIGURATION.slot_one_position, abs=1e-4
    )
    await self.carousel.move_to_slot(2, speed=30)
    await self.carousel.move_to_slot(2, speed=30)
    assert await self.carousel.request_position() == pytest.approx(
      CONFIGURATION.slot_one_position + 15, abs=1e-4
    )
    assert sum(command == 22 for _, command, _ in self.link.commands()) == 2

  async def test_cancellation_stops_an_accepted_move(self) -> None:
    """A move whose completion is pending must be stopped before cancellation propagates."""
    await self.carousel.home()
    self.link.complete_motion = False
    self.link.motion_written = anyio.Event()
    scope = anyio.CancelScope()

    async def run_move() -> None:
      with scope:
        await self.carousel.move_to(15, speed=30)

    async with anyio.create_task_group() as tg:
      tg.start_soon(run_move)
      await self.link.motion_written.wait()
      scope.cancel()
    assert scope.cancelled_caught
    assert (0, 24, STOP_PAYLOAD) in self.link.commands()
    assert await self.carousel.request_status() == 4
    assert self.carousel._driver._event_waiters == []

  async def test_missing_acknowledgement_stops_without_retrying_motion(self) -> None:
    """An unanswered move can already be in motion."""
    await self.carousel.home()
    self.link.acknowledge_motion = False
    self.link.complete_motion = False
    with pytest.raises(MantisTimeoutError):
      await self.carousel.move_to(15, speed=30)
    assert sum(command == 22 for _, command, _ in self.link.commands()) == 1
    assert (0, 24, STOP_PAYLOAD) in self.link.commands()

  async def test_missing_completion_stops_motion(self) -> None:
    """An accepted move without its completion event has a bounded wait."""
    await self.carousel.home()
    self.link.complete_motion = False
    with pytest.raises(MantisTimeoutError):
      await self.carousel.move_to(15, speed=30, timeout=0.02)
    assert (0, 24, STOP_PAYLOAD) in self.link.commands()

  async def test_home_without_homed_bit_stops_and_raises(self) -> None:
    """A completion event alone cannot establish the home reference."""
    self.link.finish_homed = False
    with pytest.raises(MantisMotionError, match="not homed"):
      await self.carousel.home()
    assert (0, 24, STOP_PAYLOAD) in self.link.commands()

  async def test_shutdown_during_motion_closes_after_stopping(self) -> None:
    """Exiting the carousel lifespan stops its motor and fails an outstanding operation."""
    link = CarouselLink()
    carousel = MantisCarousel(configuration=CONFIGURATION, io=link.io)
    carousel._driver.default_read_timeout = 0.05
    caught: MantisNotConnectedError | None = None

    async def run_move() -> None:
      nonlocal caught
      try:
        await carousel.move_to(15, speed=30)
      except MantisNotConnectedError as exc:
        caught = exc

    async with anyio.create_task_group() as tg:
      async with carousel:
        await carousel.home()
        link.complete_motion = False
        link.motion_written = anyio.Event()
        tg.start_soon(run_move)
        await link.motion_written.wait()
    assert caught is not None
    assert (0, 24, STOP_PAYLOAD) in link.commands()
    assert link.io.entered_count == 1
    assert link.io.exited_count == 1

  async def test_shutdown_waits_for_in_flight_operation_lock_and_stops_late_motion(self) -> None:
    """A move_to() that dispatches command 22 as shutdown starts is still stopped before link close."""
    link = CarouselLink()
    carousel = MantisCarousel(configuration=CONFIGURATION, io=link.io)
    carousel._driver.default_read_timeout = 0.05
    entered_send = anyio.Event()
    allow_send = anyio.Event()
    original_send = carousel._send_motion
    caught: BaseException | None = None

    async def delayed_send(*, command: int, payload: bytes) -> None:
      if command == 22:
        entered_send.set()
        await allow_send.wait()
      await original_send(command=command, payload=payload)

    async def release_once_shutdown_starts() -> None:
      while carousel._connected:
        await anyio.sleep(0.005)
      allow_send.set()

    async def run_move() -> None:
      nonlocal caught
      try:
        await carousel.move_to(15, speed=30, timeout=0.02)
      except (MantisNotConnectedError, MantisTimeoutError) as exc:
        caught = exc

    async with anyio.create_task_group() as tg:
      async with carousel:
        await carousel.home()
        link.complete_motion = False
        with mock.patch.object(carousel, "_send_motion", side_effect=delayed_send):
          tg.start_soon(run_move)
          await entered_send.wait()
          tg.start_soon(release_once_shutdown_starts)
    assert caught is not None
    assert (0, 24, STOP_PAYLOAD) in link.commands()

  def test_backlash_path_respects_both_travel_limits(self) -> None:
    """When the nearest negative approach crosses the limit, use the positive equivalent."""
    self.carousel._limits = (0, 2000)
    assert self.carousel._plan_rotation(position=0, current=10) == (
      CONFIGURATION.steps_per_revolution,
    )
    self.carousel._limits = (0, 1000)
    with pytest.raises(MantisMotionError):
      self.carousel._plan_rotation(position=0, current=10)

  async def test_invalid_slots_send_nothing(self) -> None:
    """Reject invalid indices before contacting the controller."""
    before = len(self.link.written)
    invalid_slots: Tuple[Any, ...] = (0, 25, True, 1.5)
    for slot in invalid_slots:
      with pytest.raises(ValueError):
        await self.carousel.move_to_slot(slot)
    assert len(self.link.written) == before


class TestCarouselUsb:
  """Verify USB product and serial filtering on MantisCarousel."""

  def test_usb_product_and_serial_are_required(self) -> None:
    """The shared VID/PID must never select the main Mantis in place of the LC3."""
    with mock.patch("pylabrobot.formulatrix.mantis.carousel.FTDI") as ftdi:
      MantisCarousel(configuration=CONFIGURATION)
    assert ftdi.call_args.kwargs["usb_product"] == "LC3"
    assert ftdi.call_args.kwargs["device_id"] == "L-000143"
    assert ftdi.call_args.kwargs["interface_select"] == 2
