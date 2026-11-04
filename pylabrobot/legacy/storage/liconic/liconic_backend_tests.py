import inspect
import time
import unittest
from collections import deque
from typing import Any, Awaitable, Callable, Deque, Dict, List, Optional, Tuple, cast
from unittest.mock import patch

import anyio
import pytest

pytest.importorskip("serial")

import serial
from trio.testing import MockClock

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.io.serial import Serial
from pylabrobot.legacy.barcode_scanners import BarcodeScanner, BarcodeScannerBackend
from pylabrobot.legacy.storage.incubator import Incubator
from pylabrobot.legacy.storage.liconic import liconic_backend as liconic_module
from pylabrobot.legacy.storage.liconic.constants import LiconicType
from pylabrobot.legacy.storage.liconic.errors import (
  LiconicControllerCommandError,
  LiconicHandlerGeneralError,
)
from pylabrobot.legacy.storage.liconic.liconic_backend import (
  LICONIC_SITE_HEIGHT_TO_STEPS,
  ExperimentalLiconicBackend,
)
from pylabrobot.legacy.storage.liconic.racks import (
  liconic_rack_5mm_42,
  liconic_rack_17mm_22,
  liconic_rack_44mm_10,
)
from pylabrobot.resources import Coordinate, Plate, PlateHolder, Well
from pylabrobot.resources.barcode import Barcode
from pylabrobot.resources.carrier import PlateCarrier
from pylabrobot.testing.concurrency import AnyioTestBase

# A software port name. The Liconic never opens a device in these tests, and a device path has
# no business in a test even when nothing opens it.
_PORT = "COM_LICONIC"

# Deadlock guard for the event-driven real-clock tests. Those finish in milliseconds, so this
# is orders of magnitude larger than any correct run, but finite: a transaction that is never
# handed on fails the test rather than hanging the run.
_REAL_TIME_DEADLOCK_GUARD = 60.0

# A real-clock reply stall longer than the shrunken budgets the overrun tests install. It is
# finite on purpose, so a backend that applies no bound at all answers late and fails an
# assertion rather than hanging the run.
_REAL_TIME_STALL = 3.0

# A reply that never realistically arrives, for the virtual-clock deadline observations. It is
# finite for the same reason, and costs no real time because those tests use a mock clock.
_STALLED_SECONDS = 86400.0

# Deadlock guard for the virtual-clock tests. Ordered bound < guard < stall, so an overlong
# bound is reported by the elapsed-time assertions rather than by the guard, while a bound that
# never fires trips the guard instead of hanging the run.
_VIRTUAL_DEADLOCK_GUARD = 7200.0

# Slack allowed around a deadline observed on a virtual clock.
_DEADLINE_SLACK = 0.05

# A shrunken entry budget for the failure-path tests, paired with a reply stall below it so the
# poll performs a handful of attempts rather than spinning.
_SHRUNKEN_ENTRY_TIMEOUT = 0.2
_SHRUNKEN_LINE_STALL = 0.02

# The cost of one line reply, and it must be strictly positive. The entry sequence polls in
# loops that only a cancel-scope deadline ends, and on the virtual clock those classes use, the
# clock advances only while every task is blocked. Measured: under
# `MockClock(autojump_threshold=0)`, 50k bare checkpoints advance virtual time by 0.000000s and
# a checkpoint-only `move_on_after(1.0)` never fires at all, while the same scope on a real
# clock fires after ~350k iterations. So a double that answers with a checkpoint alone keeps
# its caller runnable, the entry budget never expires, and a backend whose handshake never
# matches spins forever instead of failing -- which would also make `_VIRTUAL_DEADLOCK_GUARD`
# below vacuous, since that guard is itself a deadline on the same stalled clock. A positive
# tick blocks the caller, so the clock advances and the budget is reached. It is far below the
# entry delays under test, so the elapsed-time assertions keep their slack.
_LINE_REPLY_TICK = 0.001

# Real-clock bounds for the dual-backend delay floors below. The virtual-clock classes observe
# these same delays exactly, but they run trio-only, so on their own a delay collapsed to zero
# survives silently under asyncio -- the Task 3 lesson. These bounds are coarse on purpose: they
# only have to separate "the delay happened" from "it did not", on either backend, in real time.
# The floor tolerance absorbs clock granularity; the headroom absorbs scheduling noise and still
# sits far below any plausible inflation of the delays under test.
_REAL_TIME_FLOOR_SLACK = 0.02
_REAL_TIME_CEILING_HEADROOM = 0.25


def _assert_real_delay(elapsed: float, nominal: float) -> None:
  """Assert that a real-clock interval spans one nominal delay and nothing like a longer one.

  Args:
    elapsed: Real seconds the operation took.
    nominal: Delay the operation is supposed to contain.
  """
  assert elapsed >= nominal - _REAL_TIME_FLOOR_SLACK, (
    f"expected at least {nominal}s of real delay, measured {elapsed}s"
  )
  assert elapsed <= nominal + _REAL_TIME_CEILING_HEADROOM, (
    f"expected about {nominal}s of real delay, measured {elapsed}s"
  )


def _command_of(data: bytes) -> str:
  """Return a written command without its carriage return, for transcript assertions."""
  return data.decode("ascii").rstrip("\r\n")


def _write_event(command: str) -> str:
  """Return the recorded event of one command write."""
  return f"write:{command}"


def _exchange_events(command: str) -> List[str]:
  """Return the recorded events of one complete `_send_command` exchange.

  The reply read contributes two events, one when it begins and one when it hands the reply
  back. A read that was entered and then abandoned records only the first, so a transcript
  distinguishes a completed exchange from one cancellation cut short.
  """
  return [_write_event(command), "read", "read-done"]


def _line_events(command: str) -> List[str]:
  """Return the recorded events of one entry-sequence write and its line reply."""
  return [_write_event(command), "readline", "readline-done"]


def _entry_events(ready_polls: int = 1) -> List[str]:
  """Return the recorded events of the legacy entry sequence.

  Args:
    ready_polls: Number of `RD 1915` readiness polls the PLC required.
  """
  return [
    "serial.enter",
    "send_break:0.2",
    "reset_input_buffer",
    "reset_output_buffer",
    *_line_events("CR"),
    *_line_events("ST 1801"),
    *(event for _ in range(ready_polls) for event in _line_events("RD 1915")),
  ]


def _default_line(command: str) -> str:
  """Return the PLC's line reply to one entry-sequence command."""
  if command == "CR":
    return "CC\r\n"
  if command == "RD 1915":
    return "1\r\n"
  return "OK\r\n"


def _default_reply(command: str) -> str:
  """Return the PLC's reply to one `_send_command`.

  Register reads answer with the set flag, so a readiness poll returns on its first attempt;
  every other command answers with the acknowledgement.
  """
  return "1" if command.startswith("RD ") else "OK"


class RecordingSerial(HasLifespan):
  """Software Serial double recording lifecycle and wire events."""

  def __init__(
    self,
    *,
    events: Optional[List[str]] = None,
    entry_error: Optional[BaseException] = None,
    close_error: Optional[BaseException] = None,
    replies: Optional[Dict[str, List[str]]] = None,
    lines: Optional[Dict[str, List[str]]] = None,
    line_defaults: Optional[Dict[str, str]] = None,
    failures: Optional[Dict[str, BaseException]] = None,
    blocked_event: Optional[str] = None,
    write_stall: float = 0.0,
    read_stall: float = 0.0,
    readline_stall: float = 0.0,
    port: str = _PORT,
  ) -> None:
    """Initialize a closed double.

    Args:
      events: Shared ordered event log, or None for a private one.
      entry_error: Exception to raise while the transport opens.
      close_error: Exception to raise when the lifespan closes the transport.
      replies: Scripted `read` replies keyed by command text; each is consumed once, after
        which the command falls back to the synthesized acknowledgement.
      lines: Scripted `readline` replies keyed by command text, consumed once each.
      line_defaults: Persistent `readline` reply per command, used once `lines` is exhausted.
      failures: Exception to raise from the named event, keyed by event.
      blocked_event: Event name whose call waits for `release_blocked`.
      write_stall: Seconds a command write waits before it returns.
      read_stall: Seconds a reply read waits before answering. Unlike `blocked_event`, this
        stall ends on its own, so a caller that never bounds the exchange answers late and
        fails a timing assertion rather than hanging the run.
      readline_stall: Seconds a line read waits before answering, for the same reason.
      port: Software port name this double reports.
    """
    super().__init__()
    self.events = events if events is not None else []
    self.entry_error = entry_error
    self.close_error = close_error
    self.replies: Dict[str, Deque[str]] = {
      command: deque(values) for command, values in (replies or {}).items()
    }
    self.lines: Dict[str, Deque[str]] = {
      command: deque(values) for command, values in (lines or {}).items()
    }
    self.line_defaults = dict(line_defaults or {})
    self.failures = failures or {}
    self.blocked_event = blocked_event
    self.write_stall = write_stall
    self.read_stall = read_stall
    self.readline_stall = readline_stall
    self.port = port
    self.serial_kwargs: Dict[str, Any] = {}
    self.writes: List[bytes] = []
    self.commands: List[str] = []
    self.reads: List[int] = []
    self.entries = 0
    self.exits = 0
    self.blocked_started = anyio.Event()
    self.release_blocked = anyio.Event()
    self.observed_write_completion: Optional[str] = None

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    """Open the double and register its close."""
    if kwargs:
      raise TypeError("RecordingSerial does not accept lifespan options")
    self.entries += 1
    self.events.append("serial.enter")
    if self.entry_error is not None:
      raise self.entry_error
    stack.push_shielded_async_callback(self._close)
    await anyio.lowlevel.checkpoint()

  async def _close(self) -> None:
    """Record the transport close and raise the injected close failure."""
    await anyio.lowlevel.checkpoint()
    self.exits += 1
    self.events.append("serial.exit")
    if self.close_error is not None:
      raise self.close_error

  async def _block_if_requested(self, event: str) -> None:
    """Wait for release when this event is the configured blocking point."""
    if event == self.blocked_event:
      self.blocked_started.set()
      await self.release_blocked.wait()

  async def _fail_if_requested(self, event: str) -> None:
    """Raise the injected failure configured for this event."""
    failure = self.failures.get(event)
    if failure is not None:
      raise failure

  async def write(self, data: bytes) -> None:
    """Record one command write at its entry, then block, stall, or fail as configured.

    The event is appended before this double awaits anything of its own. A transport call is
    the moment work reaches the wire, so recording it after a scheduler hop would report it
    one hop late and hide orderings the caller genuinely produces.
    """
    command = _command_of(data)
    self.writes.append(data)
    self.commands.append(command)
    self.events.append(_write_event(command))
    await anyio.lowlevel.checkpoint()
    await self._block_if_requested(_write_event(command))
    if self.write_stall > 0:
      await anyio.sleep(self.write_stall)
    await self._fail_if_requested(_write_event(command))
    if command == self.observed_write_completion:
      self.events.append(f"write-done:{command}")

  async def read(self, num_bytes: int = 1) -> bytes:
    """Record one reply read at its entry and again when it answers, blocking as configured.

    The entry event is appended before this double awaits anything of its own, for the same
    reason the write's is. The second event is appended only on the way out, so a read that
    was entered and then abandoned leaves the first behind without the second. Without it an
    abandoned read and a completed one produce the same transcript, and an exchange that
    cancellation cut in half would be indistinguishable from one that ran to the end.
    """
    self.reads.append(num_bytes)
    self.events.append("read")
    await anyio.lowlevel.checkpoint()
    await self._block_if_requested("read")
    command = self.commands[-1]
    await self._block_if_requested(f"read:{command}")
    if self.read_stall > 0:
      await anyio.sleep(self.read_stall)
    await self._fail_if_requested("read")
    scripted = self.replies.get(command)
    reply = scripted.popleft() if scripted else _default_reply(command)
    self.events.append("read-done")
    return reply.encode("ascii")

  async def readline(self) -> bytes:
    """Record one line read at its entry and again when it answers, as `read` does.

    The reply costs `_LINE_REPLY_TICK` rather than a bare checkpoint, because the entry
    sequence polls in loops that only a cancel-scope deadline ends. See that constant for the
    measurement: a checkpoint alone leaves the caller runnable and never advances a virtual
    clock, so an entry budget on that clock never expires.
    """
    self.events.append("readline")
    await anyio.sleep(_LINE_REPLY_TICK)
    await self._block_if_requested("readline")
    if self.readline_stall > 0:
      await anyio.sleep(self.readline_stall)
    await self._fail_if_requested("readline")
    command = self.commands[-1]
    scripted = self.lines.get(command)
    if scripted:
      reply = scripted.popleft()
    else:
      reply = self.line_defaults.get(command, _default_line(command))
    self.events.append("readline-done")
    return reply.encode("ascii")

  async def send_break(self, duration: float) -> None:
    """Record the break and its duration at call entry."""
    self.events.append(f"send_break:{duration}")
    await anyio.lowlevel.checkpoint()
    await self._block_if_requested("send_break")
    await self._fail_if_requested("send_break")

  async def reset_input_buffer(self) -> None:
    """Record the input flush at call entry."""
    self.events.append("reset_input_buffer")
    await anyio.lowlevel.checkpoint()
    await self._fail_if_requested("reset_input_buffer")

  async def reset_output_buffer(self) -> None:
    """Record the output flush at call entry."""
    self.events.append("reset_output_buffer")
    await anyio.lowlevel.checkpoint()
    await self._fail_if_requested("reset_output_buffer")


class RecordingBarcodeScannerBackend(BarcodeScannerBackend):
  """Barcode scanner backend double recording its own lifecycle and scans."""

  def __init__(self, events: Optional[List[str]] = None) -> None:
    super().__init__()
    self.events = events if events is not None else []
    self.entries = 0
    self.exits = 0
    self.scans = 0

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    """Open the double and register its close."""
    if kwargs:
      raise TypeError("RecordingBarcodeScannerBackend does not accept lifespan options")
    self.entries += 1
    self.events.append("scanner.enter")
    stack.push_shielded_async_callback(self._close)
    await anyio.lowlevel.checkpoint()

  async def _close(self) -> None:
    """Record the close of a scanner nothing else owns."""
    await anyio.lowlevel.checkpoint()
    self.exits += 1
    self.events.append("scanner.exit")

  async def scan_barcode(self) -> Barcode:
    """Record one scan at call entry and answer with a fixed barcode."""
    self.scans += 1
    self.events.append("scanner.scan")
    await anyio.lowlevel.checkpoint()
    return Barcode(data="PLATE-1", symbology="Code 39", position_on_resource="front")


def make_backend(
  monkeypatch: pytest.MonkeyPatch,
  *,
  model: LiconicType = LiconicType.STX44_IC,
  barcode_scanner: Optional[BarcodeScanner] = None,
  **double_kwargs: Any,
) -> Tuple[ExperimentalLiconicBackend, RecordingSerial]:
  """Build a Liconic backend whose transport is a software Serial double."""
  io = RecordingSerial(**double_kwargs)

  def build_serial(**kwargs: Any) -> Serial:
    io.serial_kwargs = dict(kwargs)
    return cast(Serial, io)

  monkeypatch.setattr(liconic_module, "Serial", build_serial)
  backend = ExperimentalLiconicBackend(model=model, port=_PORT, barcode_scanner=barcode_scanner)
  return backend, io


def make_scanner(
  events: Optional[List[str]] = None,
) -> Tuple[BarcodeScanner, RecordingBarcodeScannerBackend]:
  """Build a barcode scanner the Liconic borrows and never owns."""
  scanner_backend = RecordingBarcodeScannerBackend(events=events)
  return BarcodeScanner(backend=scanner_backend), scanner_backend


def make_frontend(backend: ExperimentalLiconicBackend) -> Incubator:
  """Build the `Incubator` frontend that competes for the shaking stop."""
  return Incubator(
    backend=backend,
    name="liconic",
    size_x=10,
    size_y=10,
    size_z=10,
    racks=[],
    loading_tray_location=Coordinate.zero(),
  )


def make_plate(name: str) -> Plate:
  """Build a plate used only for movement between software-backed sites."""
  well = Well(name=f"{name}_A1", size_x=8, size_y=8, size_z=10)
  well.location = Coordinate(10, 10, 2)
  return Plate(name=name, size_x=127.76, size_y=85.48, size_z=14.35, ordered_items={"A1": well})


def make_movement_sites(
  backend: ExperimentalLiconicBackend,
) -> Tuple[PlateHolder, PlateHolder]:
  """Install one compatible rack and return distinct origin and destination sites."""
  rack = liconic_rack_17mm_22("rack1")
  backend._racks = [rack]
  return cast(PlateHolder, rack.sites[0]), cast(PlateHolder, rack.sites[1])


def _wire_events(io: RecordingSerial, baseline: int, *markers: str) -> List[str]:
  """Return the transport events recorded since `baseline`, without the test's own markers.

  Tests append their own markers to the shared log to place an observation in the ordering.
  This drops them again, so an assertion about what reached the wire reads only wire events.
  """
  return [event for event in io.events[baseline:] if event not in markers]


async def _settle() -> None:
  """Give every other task room to reach its next transport call."""
  for _ in range(10):
    await anyio.lowlevel.checkpoint()


class TestStepSizeFormula(unittest.TestCase):
  """Verify that the motor step sizes follow the documented formula:
  steps = round(pitch * 1713 / 50), where pitch = site_height + 6."""

  def test_step_sizes_match_formula(self):
    for site_height, claimed_steps in LICONIC_SITE_HEIGHT_TO_STEPS.items():
      if site_height == 104:
        continue  # see test_104mm_step_size_deviates_from_formula
      pitch = site_height + 6
      computed = round(pitch * 1713 / 50)
      with self.subTest(site_height=site_height, pitch=pitch):
        self.assertEqual(
          claimed_steps,
          computed,
          msg=f"site_height={site_height}, pitch={pitch}: "
          f"claimed {claimed_steps}, formula gives {computed}",
        )

  def test_104mm_step_size_deviates_from_formula(self):
    """The 104mm entry (pitch 110mm) claims 3563 steps but the formula gives 3769.
    This is either empirically determined or a data entry error.
    TODO: verify with hardware."""
    pitch = 110
    computed = round(pitch * 1713 / 50)
    self.assertEqual(computed, 3769)
    self.assertEqual(LICONIC_SITE_HEIGHT_TO_STEPS[104], 3563)
    self.assertNotEqual(LICONIC_SITE_HEIGHT_TO_STEPS[104], computed)

  def test_known_reference_points(self):
    """The two reference points from the Liconic documentation."""
    self.assertEqual(LICONIC_SITE_HEIGHT_TO_STEPS[17], 788)  # pitch 23mm
    self.assertEqual(LICONIC_SITE_HEIGHT_TO_STEPS[44], 1713)  # pitch 50mm


class TestRackConstruction(unittest.TestCase):
  def test_rack_site_count(self):
    rack = liconic_rack_17mm_22("test_rack")
    self.assertEqual(len(rack.sites), 22)

  def test_rack_model_name(self):
    rack = liconic_rack_17mm_22("test_rack")
    self.assertEqual(rack.model, "liconic_rack_17mm_22")

  def test_rack_site_height(self):
    rack = liconic_rack_17mm_22("test_rack")
    # All sites except the last should have size_z == site_height
    for i in range(21):
      self.assertEqual(rack.sites[i].get_size_z(), 17)

  def test_rack_default_total_height(self):
    rack = liconic_rack_17mm_22("test_rack")
    self.assertEqual(rack.get_size_z(), 505)

  def test_rack_custom_total_height(self):
    rack = liconic_rack_5mm_42("test_rack")
    self.assertEqual(rack.get_size_z(), 505)


class TestCarrierToStepsPos(unittest.TestCase):
  def setUp(self):
    self.backend = ExperimentalLiconicBackend(model=LiconicType.STX44_IC, port=_PORT)

  def test_parses_model_name(self):
    rack = liconic_rack_17mm_22("test_rack")
    self.backend._racks = [rack]
    site = rack.sites[0]
    steps, pos_num = self.backend._carrier_to_steps_pos(site)
    self.assertEqual(steps, 788)
    self.assertEqual(pos_num, 22)

  def test_5mm_rack(self):
    rack = liconic_rack_5mm_42("test_rack")
    self.backend._racks = [rack]
    site = rack.sites[0]
    steps, pos_num = self.backend._carrier_to_steps_pos(site)
    self.assertEqual(steps, 377)
    self.assertEqual(pos_num, 42)

  def test_44mm_rack(self):
    rack = liconic_rack_44mm_10("test_rack")
    self.backend._racks = [rack]
    site = rack.sites[0]
    steps, pos_num = self.backend._carrier_to_steps_pos(site)
    self.assertEqual(steps, 1713)
    self.assertEqual(pos_num, 10)

  def test_unknown_model_raises(self):
    rack = PlateCarrier(
      name="bad_rack",
      size_x=100,
      size_y=100,
      size_z=500,
      sites={},
      model="some_other_rack_17mm_22",
    )
    self.backend._racks = [rack]
    site = PlateHolder(name="s", size_x=10, size_y=10, size_z=10, pedestal_size_z=0)
    rack.assign_child_resource(site, location=None)
    with self.assertRaises(ValueError):
      self.backend._carrier_to_steps_pos(site)


class TestSiteToMN(unittest.TestCase):
  def setUp(self):
    self.backend = ExperimentalLiconicBackend(model=LiconicType.STX44_IC, port=_PORT)

  def test_first_rack_first_site(self):
    rack = liconic_rack_17mm_22("rack1")
    self.backend._racks = [rack]
    m, n = self.backend._site_to_m_n(rack.sites[0])
    self.assertEqual(m, 1)  # 1-indexed rack
    self.assertEqual(n, 1)  # 1-indexed site

  def test_first_rack_last_site(self):
    rack = liconic_rack_17mm_22("rack1")
    self.backend._racks = [rack]
    m, n = self.backend._site_to_m_n(rack.sites[21])
    self.assertEqual(m, 1)
    self.assertEqual(n, 22)

  def test_second_rack(self):
    rack1 = liconic_rack_17mm_22("rack1")
    rack2 = liconic_rack_17mm_22("rack2")
    self.backend._racks = [rack1, rack2]
    m, n = self.backend._site_to_m_n(rack2.sites[0])
    self.assertEqual(m, 2)
    self.assertEqual(n, 1)


class TestSerialization(unittest.TestCase):
  def test_serialize_roundtrip(self):
    backend = ExperimentalLiconicBackend(model=LiconicType.STX44_IC, port=_PORT)
    data = backend.serialize()
    self.assertEqual(data["port"], _PORT)
    self.assertEqual(data["model"], "STX44_IC")

    restored = ExperimentalLiconicBackend.deserialize(data)
    self.assertEqual(restored.io.port, _PORT)
    self.assertEqual(restored.model, LiconicType.STX44_IC)

  def test_deserialize_string_model(self):
    restored = ExperimentalLiconicBackend.deserialize({"port": _PORT, "model": "STX44_IC"})
    self.assertEqual(restored.model, LiconicType.STX44_IC)


class TestLiconicBackendLifecycle(AnyioTestBase):
  _anyio_backends = {"asyncio": None, "trio": {"clock": MockClock(autojump_threshold=0)}}

  def test_managed_methods_are_inherited(self) -> None:
    assert ExperimentalLiconicBackend.setup is HasLifespan.setup
    assert ExperimentalLiconicBackend.stop is HasLifespan.stop
    assert ExperimentalLiconicBackend.__aenter__ is HasLifespan.__aenter__
    assert ExperimentalLiconicBackend.__aexit__ is HasLifespan.__aexit__

  def test_serial_configuration_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
    _, io = make_backend(monkeypatch)

    assert io.serial_kwargs == {
      "human_readable_device_name": "Liconic STX44_IC",
      "port": _PORT,
      "baudrate": 9600,
      "bytesize": serial.EIGHTBITS,
      "parity": serial.PARITY_EVEN,
      "stopbits": serial.STOPBITS_ONE,
      "write_timeout": 1,
      "timeout": 1,
      "rtscts": True,
    }

  async def test_rejects_lifespan_options_before_serial_entry(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch)

    with pytest.raises(TypeError, match="unexpected keyword argument"):
      async with backend.lifespan(unknown=True):
        pytest.fail("invalid entry reached the body")

    assert io.events == []
    assert io.entries == 0

  async def test_entry_sequence_matches_the_legacy_setup(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch)

    async with backend:
      # The break duration, the two flushes and their order, the handshake bytes and the
      # readiness poll are the legacy sequence unchanged.
      assert io.events == _entry_events()

  async def test_entry_polls_readiness_until_the_flag_is_set(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch, lines={"RD 1915": ["0\r\n", "0\r\n", "1\r\n"]})

    async with backend:
      assert io.events == _entry_events(ready_polls=3)

  async def test_a_serial_exception_at_entry_is_wrapped(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    """The wrapper stays on the Serial entry boundary, where the port is opened."""
    failure = serial.SerialException("port busy")
    backend, io = make_backend(monkeypatch, entry_error=failure)

    with pytest.raises(RuntimeError) as error_info:
      async with backend:
        pytest.fail("failed Serial entry reached the body")

    assert str(error_info.value) == f"Could not open {_PORT}: port busy"
    assert error_info.value.__cause__ is failure
    # Nothing opened, so nothing is closed and no command reached the device.
    assert io.events == ["serial.enter"]
    assert io.exits == 0
    assert not backend.is_active

  async def test_a_non_serial_entry_failure_is_not_wrapped(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    failure = RuntimeError("serial entry failed")
    backend, io = make_backend(monkeypatch, entry_error=failure)

    with pytest.raises(RuntimeError) as error_info:
      async with backend:
        pytest.fail("failed Serial entry reached the body")

    assert error_info.value is failure
    assert io.events == ["serial.enter"]
    assert io.exits == 0

  async def test_a_handshake_timeout_closes_the_transport_exactly_once(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    """The exit stack owns the close, so the branch raises and closes the port once.

    `exits == 1` is the invariant pinned here, and it does not separate a reinstated manual
    `await self.io.stop()` in the branch from its absence: measured, `exits` reads 1 either
    way, because that call is a no-op against the global interactive manager, which does not
    own this Serial. So this node cannot observe such a reinstatement on asyncio -- the
    mutation survives here, with an event log identical to the green one. Its trio twin
    fails instead, and for a wrong-exception reason rather than a doubled close: interactive
    stop is asyncio-only, so the branch raises `RuntimeError` in place of `TimeoutError`.
    """
    backend, io = make_backend(
      monkeypatch,
      line_defaults={"CR": "XX\r\n"},
      readline_stall=_SHRUNKEN_LINE_STALL,
    )
    backend.init_timeout = _SHRUNKEN_ENTRY_TIMEOUT

    with pytest.raises(TimeoutError) as error_info:
      async with backend:
        pytest.fail("failed handshake reached the body")

    assert (
      str(error_info.value)
      == f"No CC response from Liconic PLC within {_SHRUNKEN_ENTRY_TIMEOUT} seconds"
    )
    assert io.exits == 1
    assert io.events[-1] == "serial.exit"
    # No shaking was ever started, so the registered stop is a guarded no-op and adds no
    # command of its own before the transport closes.
    assert io.commands.count("RS 1913") == 0
    assert not backend.is_active
    assert backend._reply_alignment_unknown
    entries = io.entries
    with pytest.raises(RuntimeError, match="reply alignment unknown"):
      async with backend:
        pytest.fail("uncertain handshake reopened Serial")
    assert io.entries == entries

  async def test_activation_reply_timeout_quarantines_partial_entry(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch)
    original_readline = io.readline
    failure = TimeoutError("activation response lost")

    async def read_line() -> bytes:
      if io.commands[-1] == "ST 1801":
        raise failure
      return await original_readline()

    monkeypatch.setattr(io, "readline", read_line)
    with pytest.raises(TimeoutError) as caught:
      async with backend:
        pytest.fail("activation timeout reached body")
    assert caught.value is failure
    assert io.commands == ["CR", "ST 1801"]
    assert backend._reply_alignment_unknown
    assert io.exits == 1
    with pytest.raises(RuntimeError, match="reply alignment unknown"):
      async with backend:
        pytest.fail("uncertain activation reopened Serial")
    assert io.entries == 1

  @pytest.mark.parametrize("command", ["CR", "RD 1915"])
  async def test_cancelled_setup_with_pending_reply_refuses_reentry(
    self, monkeypatch: pytest.MonkeyPatch, command: str
  ) -> None:
    backend, io = make_backend(monkeypatch)
    original_readline = io.readline
    waiting = anyio.Event()
    finished: List[str] = []

    async def pending_reply() -> bytes:
      if io.commands[-1] == command:
        waiting.set()
        await anyio.sleep_forever()
      return await original_readline()

    monkeypatch.setattr(io, "readline", pending_reply)

    async def enter() -> None:
      try:
        async with backend:
          pytest.fail("pending reply reached body")
      except anyio.get_cancelled_exc_class():
        finished.append("cancelled")
        raise

    with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
      async with anyio.create_task_group() as group:
        group.start_soon(enter)
        await waiting.wait()
        assert command in io.commands
        group.cancel_scope.cancel()

    assert finished == ["cancelled"]
    assert backend._reply_alignment_unknown
    assert io.exits == 1
    with pytest.raises(RuntimeError, match="reply alignment unknown"):
      async with backend:
        pytest.fail("uncertain link reopened")
    assert io.entries == 1

  async def test_ready_timeout_after_completed_reply_allows_reentry(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch, line_defaults={"RD 1915": "0\r\n"})
    backend.start_timeout = 0.05
    backend.poll_interval = 1
    with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
      with pytest.raises(TimeoutError, match="PLC did not signal ready"):
        async with backend:
          pytest.fail("timed-out readiness reached body")
    assert io.commands.count("RD 1915") == 1
    assert not backend._reply_alignment_unknown
    assert io.exits == 1
    io.line_defaults["RD 1915"] = "1\r\n"
    async with backend:
      assert backend.is_active
    assert io.entries == io.exits == 2

  async def test_timeout_before_handshake_write_does_not_quarantine(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch)
    failure = TimeoutError("before CR dispatch")
    io.failures["reset_output_buffer"] = failure
    with pytest.raises(TimeoutError) as caught:
      async with backend:
        pytest.fail("pre-dispatch timeout reached body")
    assert caught.value is failure
    assert "CR" not in io.commands
    assert not backend._reply_alignment_unknown
    assert io.exits == 1
    io.failures.clear()
    async with backend:
      assert backend.is_active

  @pytest.mark.parametrize("completed_commands", [0, 2])
  async def test_timeout_at_setup_prewrite_checkpoint_does_not_quarantine(
    self, monkeypatch: pytest.MonkeyPatch, completed_commands: int
  ) -> None:
    backend, io = make_backend(monkeypatch)
    failure = TimeoutError("pre-write deadline")
    original_checkpoint = anyio.lowlevel.checkpoint_if_cancelled

    async def prewrite_checkpoint() -> None:
      if len(io.commands) == completed_commands:
        raise failure
      await original_checkpoint()

    with monkeypatch.context() as patcher:
      patcher.setattr(anyio.lowlevel, "checkpoint_if_cancelled", prewrite_checkpoint)
      with pytest.raises(TimeoutError) as caught:
        async with backend:
          pytest.fail("pre-write timeout reached body")
    assert caught.value is failure
    assert io.commands == ["CR", "ST 1801"][:completed_commands]
    assert not backend._reply_alignment_unknown
    assert io.exits == 1
    async with backend:
      assert backend.is_active

  async def test_an_activation_failure_closes_the_transport_exactly_once(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch, line_defaults={"ST 1801": "NO\r\n"})

    with pytest.raises(RuntimeError) as error_info:
      async with backend:
        pytest.fail("failed activation reached the body")

    assert str(error_info.value) == "Unexpected reply to ST 1801: b'NO\\r\\n'"
    assert io.exits == 1
    assert io.events == [*_entry_events(ready_polls=0), "serial.exit"]
    assert not backend.is_active

  async def test_a_ready_timeout_closes_the_transport_exactly_once(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(
      monkeypatch,
      line_defaults={"RD 1915": "0\r\n"},
      readline_stall=_SHRUNKEN_LINE_STALL,
    )
    backend.start_timeout = _SHRUNKEN_ENTRY_TIMEOUT
    backend.poll_interval = 0.01

    with pytest.raises(TimeoutError) as error_info:
      async with backend:
        pytest.fail("failed readiness poll reached the body")

    assert (
      str(error_info.value) == f"PLC did not signal ready within {_SHRUNKEN_ENTRY_TIMEOUT} seconds"
    )
    assert io.exits == 1
    assert io.events[-1] == "serial.exit"
    assert io.commands.count("RD 1915") >= 1
    assert not backend.is_active
    assert backend._reply_alignment_unknown
    entries = io.entries
    with pytest.raises(RuntimeError, match="reply alignment unknown"):
      async with backend:
        pytest.fail("uncertain readiness reopened Serial")
    assert io.entries == entries

  async def test_bare_exit_without_shaking_closes_serial_only(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch)

    async with backend:
      baseline = len(io.events)

    assert _wire_events(io, baseline) == ["serial.exit"]

  async def test_exit_stops_shaking_before_serial_closes(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch)

    async with backend:
      await backend.start_shaking(12)
      baseline = len(io.events)

    # LIFO runs the registered stop before the transport close, reproducing the legacy
    # `stop()`, which stopped shaking and then closed the port.
    assert _wire_events(io, baseline) == [
      *_exchange_events("RS 1913"),
      *_exchange_events("RD 1915"),
      "serial.exit",
    ]

  async def test_a_failing_exit_stop_is_attempted_exactly_once(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    """The lifespan registers the shaking stop once, and a failure does not multiply it.

    A stop that succeeds clears the tracked shaking state, so a second registration of the
    same callback would find nothing to do and put nothing extra on the wire. A stop that
    fails is the case that tells them apart: `_stop_shaking_transition` retains the state as
    active and unknown precisely so the stop stays retryable, so every further registered
    copy dispatches its own `RS 1913`.
    """
    failure = RuntimeError("shake off failed")
    backend, io = make_backend(monkeypatch)

    with pytest.raises(RuntimeError) as error_info:
      async with backend:
        await backend.start_shaking(12)
        baseline = len(io.events)
        io.failures[_write_event("RS 1913")] = failure

    assert error_info.value is failure
    assert io.exits == 1
    assert io.commands.count("RS 1913") == 1
    assert _wire_events(io, baseline) == [_write_event("RS 1913"), "serial.exit"]
    # The failed stop left the state retryable, so a later caller can still stop the device.
    assert backend._shaking_active
    assert backend._shaking_state_unknown

  async def test_body_cancellation_closes_serial(self, monkeypatch: pytest.MonkeyPatch) -> None:
    body_started = anyio.Event()
    backend, io = make_backend(monkeypatch)

    async def use_backend() -> None:
      try:
        async with backend:
          body_started.set()
          await anyio.sleep_forever()
      except anyio.get_cancelled_exc_class():
        io.events.append("cancelled")
        raise

    with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(use_backend)
        await body_started.wait()
        task_group.cancel_scope.cancel()

    assert io.events[-2:] == ["serial.exit", "cancelled"]
    assert not backend.is_active

  async def test_close_failure_propagates(self, monkeypatch: pytest.MonkeyPatch) -> None:
    failure = RuntimeError("serial close failed")
    backend, io = make_backend(monkeypatch, close_error=failure)

    with pytest.raises(RuntimeError) as error_info:
      async with backend:
        pass

    assert error_info.value is failure
    assert io.exits == 1
    assert not backend.is_active

  async def test_sequential_reuse(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, io = make_backend(monkeypatch, lines={"RD 1915": ["1\r\n", "1\r\n"]})

    async with backend:
      pass
    async with backend:
      pass

    assert io.entries == 2
    assert io.exits == 2

  async def test_cleanup_bound_preserves_the_existing_fixed_duration(self) -> None:
    # Pin both the relationship from which this established duration was derived and its
    # literal value. The cleanup spends it as one absolute budget and never derives it from a
    # frequency or shake duration.
    assert liconic_module._CLEANUP_TIMEOUT == 2 * liconic_module._COMMAND_TIMEOUT + 4.0
    assert liconic_module._CLEANUP_TIMEOUT == 8.0

  async def test_cleanup_deadline_bounds_blocked_ready_reconciliation(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    """A blocked ready response cannot outlive the cleanup's one absolute deadline."""
    backend, io = make_backend(monkeypatch)

    with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
      with patch.object(liconic_module, "_CLEANUP_TIMEOUT", 0.05):
        lifespan = backend.lifespan()
        await lifespan.__aenter__()
        backend._shaking_active = True
        io.blocked_event = "read:RD 1915"
        started = anyio.current_time()
        real_started = time.monotonic()
        with pytest.raises(TimeoutError):
          await lifespan.__aexit__(None, None, None)
        elapsed = anyio.current_time() - started
        real_elapsed = time.monotonic() - real_started

    assert elapsed == pytest.approx(0.05, abs=_DEADLINE_SLACK)
    assert real_elapsed < 0.5
    assert io.commands[-2:] == ["RS 1913", "RD 1915"]
    assert io.exits == 1

  @pytest.mark.parametrize("blocked_event", ["write:RS 1913", "read:RS 1913"])
  async def test_cleanup_exchange_timeout_quarantines_before_another_command(
    self, monkeypatch: pytest.MonkeyPatch, blocked_event: str
  ) -> None:
    backend, io = make_backend(monkeypatch)
    with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
      async with backend:
        backend._shaking_active = True
        io.blocked_event = blocked_event
        deadline = anyio.current_time() + 0.05
        with pytest.raises(TimeoutError):
          await backend._send_cleanup_command("RS 1913", deadline)
        assert backend._reply_alignment_unknown
        with pytest.raises(RuntimeError, match="reply alignment unknown"):
          await backend._send_command("RD 1915")
    assert io.commands[-1] == "RS 1913"
    assert io.exits == 1
    assert backend._shaking_active
    assert backend._shaking_state_unknown
    with pytest.raises(RuntimeError, match="reply alignment unknown"):
      async with backend:
        pytest.fail("uncertain cleanup reopened Serial")
    assert io.entries == 1

  @pytest.mark.timeout(2)
  async def test_cleanup_deadline_includes_transition_lock_wait(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    """Waiting for the shaking transition owner consumes the cleanup budget."""
    backend, io = make_backend(monkeypatch)
    lock_held = anyio.Event()
    release_lock = anyio.Event()

    async def hold_transition_lock() -> None:
      async with backend._shaking_transition_lock:
        lock_held.set()
        await release_lock.wait()

    with patch.object(liconic_module, "_CLEANUP_TIMEOUT", 0.05):
      lifespan = backend.lifespan()
      await lifespan.__aenter__()
      backend._shaking_active = True
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(hold_transition_lock)
        await lock_held.wait()
        started = anyio.current_time()
        real_started = time.monotonic()
        with pytest.raises(TimeoutError):
          await lifespan.__aexit__(None, None, None)
        elapsed = anyio.current_time() - started
        real_elapsed = time.monotonic() - real_started
        release_lock.set()

    assert elapsed == pytest.approx(0.05, abs=_DEADLINE_SLACK)
    assert real_elapsed < 0.5
    assert "RS 1913" not in io.commands
    assert io.exits == 1

  @pytest.mark.timeout(2)
  async def test_cleanup_deadline_includes_command_transaction_lock_wait(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    """Cleanup bounds the transaction wait that ordinary command timeouts exclude."""
    backend, io = make_backend(monkeypatch)
    lock_held = anyio.Event()
    release_lock = anyio.Event()

    async def hold_transaction_lock() -> None:
      async with backend._transaction_lock:
        lock_held.set()
        await release_lock.wait()

    with patch.object(liconic_module, "_CLEANUP_TIMEOUT", 0.05):
      lifespan = backend.lifespan()
      await lifespan.__aenter__()
      backend._shaking_active = True
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(hold_transaction_lock)
        await lock_held.wait()
        with anyio.move_on_after(0.02) as ordinary_guard:
          await backend._send_command("RD 1813", timeout=0.01)
        assert ordinary_guard.cancelled_caught
        started = anyio.current_time()
        real_started = time.monotonic()
        with pytest.raises(TimeoutError):
          await lifespan.__aexit__(None, None, None)
        elapsed = anyio.current_time() - started
        real_elapsed = time.monotonic() - real_started
        assert "RS 1913" not in io.commands
        release_lock.set()

    assert elapsed == pytest.approx(0.05, abs=_DEADLINE_SLACK)
    assert real_elapsed < 0.5
    assert io.exits == 1

  async def test_cleanup_deadline_bounds_each_ready_poll(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    """A readiness poll gets only the budget left after the stop command."""
    backend, io = make_backend(monkeypatch)

    with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
      with patch.object(liconic_module, "_CLEANUP_TIMEOUT", 0.05):
        lifespan = backend.lifespan()
        await lifespan.__aenter__()
        backend._shaking_active = True
        io.blocked_event = "read:RD 1915"
        started = anyio.current_time()
        with pytest.raises(TimeoutError):
          await lifespan.__aexit__(None, None, None)
        elapsed = anyio.current_time() - started

    assert elapsed == pytest.approx(0.05, abs=_DEADLINE_SLACK)
    assert io.events.count(_write_event("RD 1915")) == 2
    assert io.exits == 1

  async def test_cleanup_deadline_bounds_error_flag_reconciliation(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    """The error-flag query consumes only the tail reserved inside the same deadline."""
    backend, io = make_backend(monkeypatch, replies={"RD 1915": ["0"]})

    with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
      with patch.object(liconic_module, "_CLEANUP_TIMEOUT", 0.05):
        lifespan = backend.lifespan()
        await lifespan.__aenter__()
        backend._shaking_active = True
        io.blocked_event = "read:RD 1814"
        started = anyio.current_time()
        with pytest.raises(TimeoutError):
          await lifespan.__aexit__(None, None, None)
        elapsed = anyio.current_time() - started

    assert elapsed == pytest.approx(0.05, abs=_DEADLINE_SLACK)
    assert "RD 1814" in io.commands
    assert io.exits == 1

  async def test_cleanup_deadline_bounds_error_register_reconciliation(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    """The error-register query shares the original deadline with all earlier cleanup work."""
    backend, io = make_backend(monkeypatch, replies={"RD 1915": ["0"], "RD 1814": ["1"]})

    with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
      with patch.object(liconic_module, "_CLEANUP_TIMEOUT", 0.05):
        lifespan = backend.lifespan()
        await lifespan.__aenter__()
        backend._shaking_active = True
        io.blocked_event = "read:RD DM200"
        started = anyio.current_time()
        with pytest.raises(TimeoutError):
          await lifespan.__aexit__(None, None, None)
        elapsed = anyio.current_time() - started

    assert elapsed == pytest.approx(0.05, abs=_DEADLINE_SLACK)
    assert io.commands[-2:] == ["RD 1814", "RD DM200"]
    assert io.exits == 1

  async def test_cleanup_timeout_retains_unknown_state_and_invalidates_scope(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    """A timed-out cleanup stays retryable but invalidates stale scope ownership."""
    backend, io = make_backend(monkeypatch)

    with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
      with patch.object(liconic_module, "_CLEANUP_TIMEOUT", 0.05):
        lifespan = backend.lifespan()
        await lifespan.__aenter__()
        backend._shaking_active = True
        backend._shaking_scope_token = object()
        io.blocked_event = "read:RD 1915"
        with pytest.raises(TimeoutError):
          await lifespan.__aexit__(None, None, None)

    assert backend._shaking_active
    assert backend._shaking_state_unknown
    assert backend._shaking_scope_token is None
    assert io.exits == 1

  async def test_successful_cleanup_preserves_rs_then_ready_order(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    """The bounded path preserves the established stop-then-ready wire transcript."""
    backend, io = make_backend(monkeypatch)

    with patch.object(liconic_module, "_CLEANUP_TIMEOUT", 0.05):
      lifespan = backend.lifespan()
      await lifespan.__aenter__()
      backend._shaking_active = True
      baseline = len(io.events)
      await lifespan.__aexit__(None, None, None)

    assert _wire_events(io, baseline) == [
      *_exchange_events("RS 1913"),
      *_exchange_events("RD 1915"),
      "serial.exit",
    ]
    assert not backend._shaking_active
    assert not backend._shaking_state_unknown


class TestLiconicBorrowedBarcodeScanner(AnyioTestBase):
  """The optional scanner is borrowed: the Liconic never enters, stops or closes it."""

  async def test_the_scanner_is_never_entered_or_closed(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    scanner, scanner_backend = make_scanner()
    backend, _ = make_backend(monkeypatch, barcode_scanner=scanner)

    async with backend:
      assert scanner_backend.entries == 0

    assert scanner_backend.entries == 0
    assert scanner_backend.exits == 0
    assert scanner_backend.events == []

  async def test_the_scanner_stays_usable_after_the_liconic_lifespan_exits(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    scanner, scanner_backend = make_scanner()
    backend, _ = make_backend(monkeypatch, barcode_scanner=scanner)

    async with scanner:
      async with backend:
        assert scanner.is_active
      # The Liconic lifespan is over and the scanner it borrowed is still open and usable.
      assert scanner.is_active
      assert scanner_backend.exits == 0
      barcode = await scanner.scan()

    assert barcode.data == "PLATE-1"
    assert scanner_backend.entries == 1
    assert scanner_backend.exits == 1

  async def test_an_inline_read_uses_the_borrowed_scanner(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    events: List[str] = []
    scanner, scanner_backend = make_scanner(events=events)
    backend, io = make_backend(monkeypatch, barcode_scanner=scanner, events=events)

    async with scanner:
      async with backend:
        baseline = len(io.events)
        barcode = await backend.read_barcode_inline(1, 1)

    assert barcode.data == "PLATE-1"
    # The shovel reaches the read position before the beam fires, and is returned afterwards.
    assert _wire_events(io, baseline, "scanner.scan", "scanner.exit") == [
      *_exchange_events("ST 1910"),
      *_exchange_events("RD 1915"),
      *_exchange_events("RS 1910"),
      *_exchange_events("RD 1915"),
      "serial.exit",
    ]
    assert events.index("scanner.scan") > events.index(_write_event("ST 1910"))
    assert events.index("scanner.scan") < events.index(_write_event("RS 1910"))
    assert scanner_backend.scans == 1

  async def test_an_inline_read_without_a_scanner_is_rejected(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch)

    async with backend:
      baseline = len(io.events)
      with pytest.raises(RuntimeError, match="Barcode scanner not configured"):
        await backend.read_barcode_inline(1, 1)
      assert _wire_events(io, baseline) == []

  async def test_a_failed_shovel_reset_is_reported(self, monkeypatch: pytest.MonkeyPatch) -> None:
    scanner, _ = make_scanner()
    backend, _ = make_backend(monkeypatch, barcode_scanner=scanner, replies={"RS 1910": ["NO"]})

    async with scanner:
      async with backend:
        with pytest.raises(RuntimeError, match="Failed to reset shovel position"):
          await backend.read_barcode_inline(1, 1)

  async def test_scan_barcode_waits_for_the_lift_before_the_beam_fires(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    """`scan_barcode` must wait for the z-lift to reach the read position (ST 1910) before
    triggering the scan, otherwise the beam fires while the lift is still travelling and
    reads a plate it passes en route, not the target."""
    events: List[str] = []
    scanner, _ = make_scanner(events=events)
    backend, io = make_backend(monkeypatch, barcode_scanner=scanner, events=events)

    async with scanner:
      async with backend:
        backend._racks = [liconic_rack_17mm_22("rack1")]
        baseline = len(io.events)
        barcode = await backend.scan_barcode(backend._racks[0].sites[0])

    assert barcode.data == "PLATE-1"
    assert _wire_events(io, baseline, "scanner.scan", "scanner.exit") == [
      *_exchange_events("WR DM0 1"),
      *_exchange_events("WR DM23 788"),
      *_exchange_events("WR DM25 22"),
      *_exchange_events("WR DM5 1"),
      *_exchange_events("ST 1910"),
      *_exchange_events("RD 1915"),
      "serial.exit",
    ]
    # The readiness poll that follows `ST 1910` completes before the beam fires.
    assert events.index("scanner.scan") > events.index("read-done")

  async def test_scan_barcode_without_a_scanner_is_rejected(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch)

    async with backend:
      backend._racks = [liconic_rack_17mm_22("rack1")]
      baseline = len(io.events)
      with pytest.raises(RuntimeError, match="Barcode scanner not configured"):
        await backend.scan_barcode(backend._racks[0].sites[0])
      assert _wire_events(io, baseline) == []


class TestLiconicFrontendComposition(AnyioTestBase):
  async def test_incubator_exit_issues_exactly_one_global_stop(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch)
    frontend = make_frontend(backend)

    with pytest.warns(UserWarning, match="Liconic racks need to be configured"):
      async with frontend:
        await frontend.start_shaking(12)
        baseline = len(io.events)

    # `Incubator._pre_backend_stop` force-stops shaking and the backend lifespan registers the
    # same stop, but the second call is guarded on tracked state, so only one `RS 1913`
    # reaches the wire across a full frontend exit.
    assert _wire_events(io, baseline) == [
      *_exchange_events("RS 1913"),
      *_exchange_events("RD 1915"),
      "serial.exit",
    ]
    assert io.commands.count("RS 1913") == 1

  async def test_incubator_exit_without_shaking_issues_no_stop(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch)
    frontend = make_frontend(backend)

    with pytest.warns(UserWarning, match="Liconic racks need to be configured"):
      async with frontend:
        baseline = len(io.events)

    assert _wire_events(io, baseline) == ["serial.exit"]


class TestLiconicCommandTransactions(AnyioTestBase):
  async def test_one_command_at_a_time_reaches_the_wire(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch)

    async def blocked_command() -> None:
      await backend._send_command("RD 1813")

    async def waiting_command() -> None:
      await backend._send_command("RD 1807")

    async with backend:
      io.blocked_event = "read"
      baseline = len(io.events)
      with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(blocked_command)
          await io.blocked_started.wait()
          task_group.start_soon(waiting_command)
          await _settle()
          # The second command is holding at the transaction, not at the wire.
          assert _wire_events(io, baseline) == [_write_event("RD 1813"), "read"]
          io.blocked_event = None
          io.release_blocked.set()
      assert _wire_events(io, baseline) == [
        *_exchange_events("RD 1813"),
        *_exchange_events("RD 1807"),
      ]

  async def test_a_cancelled_transaction_waiter_writes_nothing(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch)
    observed: List[str] = []

    async def blocked_command() -> None:
      await backend._send_command("RD 1813")

    async def cancelled_command() -> None:
      try:
        await backend._send_command("RD 1807")
      except anyio.get_cancelled_exc_class():
        observed.append("cancelled")
        raise

    async with backend:
      io.blocked_event = "read"
      baseline = len(io.events)
      with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
        async with anyio.create_task_group() as holder_group:
          holder_group.start_soon(blocked_command)
          await io.blocked_started.wait()
          async with anyio.create_task_group() as task_group:
            task_group.start_soon(cancelled_command)
            await _settle()
            task_group.cancel_scope.cancel()
          io.blocked_event = None
          io.release_blocked.set()

      assert observed == ["cancelled"]
      # The cancelled waiter never reached the wire, so `RD 1807` was never written.
      assert _wire_events(io, baseline) == _exchange_events("RD 1813")

  async def test_a_committed_exchange_completes_before_cancellation(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch)
    observed: List[str] = []

    async def read_register() -> None:
      try:
        await backend._send_command("RD 1813")
      except anyio.get_cancelled_exc_class():
        observed.append("cancelled")
        raise

    async with backend:
      io.blocked_event = "read"
      baseline = len(io.events)
      with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(read_register)
          await io.blocked_started.wait()
          task_group.cancel_scope.cancel()
          io.blocked_event = None
          io.release_blocked.set()

      assert observed == ["cancelled"]
      # The write had committed, so the reply was read rather than abandoned.
      assert _wire_events(io, baseline) == _exchange_events("RD 1813")

  async def test_the_transaction_is_handed_on_before_its_holder_observes_cancellation(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    """A cancelled holder hands the transaction on before it observes its own cancellation.

    The final checkpoint sits after the release, so the next command is already free to
    reach the wire when the cancelled holder raises. Moving that checkpoint ahead of the
    release reverses the two and makes every queued command wait for one cancelled caller
    to finish unwinding.
    """
    backend, io = make_backend(monkeypatch)

    async def cancelled_holder() -> None:
      try:
        await backend._send_command("RD 1813")
      except anyio.get_cancelled_exc_class():
        io.events.append("holder-cancelled")
        raise

    async def waiting_command() -> None:
      await backend._send_command("RD 1807")

    async with backend:
      io.blocked_event = "read"
      baseline = len(io.events)
      with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
        async with anyio.create_task_group() as waiter_group:
          async with anyio.create_task_group() as holder_group:
            holder_group.start_soon(cancelled_holder)
            # The holder's write has committed and its exchange is shielded, so the
            # cancellation below cannot reach it until the exchange is over.
            await io.blocked_started.wait()
            waiter_group.start_soon(waiting_command)
            await _settle()
            holder_group.cancel_scope.cancel()
            io.blocked_event = None
            io.release_blocked.set()

      ordering = [
        event
        for event in io.events[baseline:]
        if event in ("holder-cancelled", _write_event("RD 1807"))
      ]

    assert ordering == [_write_event("RD 1807"), "holder-cancelled"]

  async def test_overrun_yields_to_a_pending_cancellation(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    """An overrun exchange whose caller was cancelled reports the cancellation, not the overrun.

    The transaction is released before the final checkpoint, and that checkpoint is what
    delivers a cancellation which arrived while the exchange was shielded. Raising the stored
    overrun ahead of it instead would hand the caller a `TimeoutError`, so an
    `except TimeoutError` around the call would swallow a cancellation that must propagate.
    """
    backend, io = make_backend(monkeypatch)
    observed: List[str] = []

    async def read_register() -> None:
      try:
        await backend._send_command("RD 1813")
      except anyio.get_cancelled_exc_class():
        observed.append("cancelled")
        raise
      except TimeoutError as error:
        observed.append(f"timeout:{error}")
      else:
        observed.append("returned")

    async def answer_late() -> None:
      """Answer the stalled reply long after the budget below should have abandoned it.

      A backend that bounds the exchange never reaches this, because the budget expires
      first and the abandoned read is cancelled where it waits. It is here so a backend that
      applies no bound answers late and fails the assertions below, rather than hanging the
      run inside the shield where no guard can reach it.
      """
      await io.blocked_started.wait()
      await anyio.sleep(_REAL_TIME_STALL)
      io.release_blocked.set()

    async with backend:
      io.blocked_event = "read"
      # Shrunk so the shielded exchange expires on its own budget while its caller's
      # cancellation is already pending: the reply this double waits for arrives, if at all,
      # only long after that.
      with patch.object(liconic_module, "_COMMAND_TIMEOUT", 0.05):
        # A `move_on_after` guard rather than `fail_after`, because a guard that raised
        # `TimeoutError` could not be told apart from the overrun this test is about.
        with anyio.move_on_after(_REAL_TIME_DEADLOCK_GUARD) as guard_scope:
          async with anyio.create_task_group() as late_reply_group:
            late_reply_group.start_soon(answer_late)
            async with anyio.create_task_group() as task_group:
              task_group.start_soon(read_register)
              # The write has committed and the exchange is shielded once the reply read has
              # begun, so the cancellation below cannot reach the caller until the exchange
              # is over and its overrun has been stored.
              await io.blocked_started.wait()
              task_group.cancel_scope.cancel()
            late_reply_group.cancel_scope.cancel()

      # Otherwise the assertions below could be satisfied by a run that never got this far.
      assert not guard_scope.cancelled_caught
      io.blocked_event = None

    assert observed == ["cancelled"]

  async def test_command_overrun_reports_the_command(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, io = make_backend(monkeypatch)

    async with backend:
      io.read_stall = _REAL_TIME_STALL
      with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
        with patch.object(liconic_module, "_COMMAND_TIMEOUT", 0.05):
          with pytest.raises(TimeoutError) as error_info:
            await backend._send_command("RD 1813")
      io.read_stall = 0.0

    assert str(error_info.value) == "Timed out waiting for a response to 'RD 1813'"

  def test_command_timeout_is_the_sum_of_the_transport_budgets(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    _, io = make_backend(monkeypatch)

    # Both against the transport budgets it replaces, so one deadline can never make a command
    # that used to succeed newly fail, and against its literal value, so the two cannot be
    # moved together.
    assert liconic_module._COMMAND_TIMEOUT == (
      io.serial_kwargs["write_timeout"] + io.serial_kwargs["timeout"]
    )
    assert liconic_module._COMMAND_TIMEOUT == 2.0

  async def test_dispatch_is_published_after_the_write_and_before_the_reply(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch)

    async def read_register() -> None:
      await backend._send_command("RD 1813")

    async with backend:
      io.blocked_event = "read"
      dispatched_before = backend._commands_dispatched
      with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(read_register)
          await io.blocked_started.wait()
          # The write has returned and the reply has not been interpreted, and ownership of
          # the dispatched command is already published.
          assert backend._commands_dispatched == dispatched_before + 1
          io.blocked_event = None
          io.release_blocked.set()

  async def test_a_cancelled_transaction_waiter_publishes_no_dispatch(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch)

    async def blocked_command() -> None:
      await backend._send_command("RD 1813")

    async def cancelled_command() -> None:
      await backend._send_command("RD 1807")

    async with backend:
      io.blocked_event = "read"
      with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
        async with anyio.create_task_group() as holder_group:
          holder_group.start_soon(blocked_command)
          await io.blocked_started.wait()
          dispatched_before = backend._commands_dispatched
          async with anyio.create_task_group() as task_group:
            task_group.start_soon(cancelled_command)
            await _settle()
            task_group.cancel_scope.cancel()
          assert backend._commands_dispatched == dispatched_before
          io.blocked_event = None
          io.release_blocked.set()


class TestLiconicCommandDeadline(AnyioTestBase):
  """Observe the command budget on a clock that costs no real time.

  A blocked exchange is abandoned exactly when its budget expires, so the time between
  dispatch and abandonment *is* the budget the backend applied. That budget is a cancel-scope
  deadline, which resolves its clock through the running backend, so only a backend-native
  virtual clock can advance it without real waiting. Trio supplies one; asyncio has no
  packaged equivalent, so this observation runs trio-only. The budget is backend independent,
  and dual-backend coverage of overrunning it lives in
  :class:`TestLiconicCommandTransactions`.
  """

  # This class-level clock is shared by every node in the class and its virtual time
  # accumulates across them, so the assertions below measure intervals rather than absolute
  # instants.
  _anyio_backends = {"trio": {"clock": MockClock(autojump_threshold=0)}}

  async def test_one_budget_covers_the_write_and_the_read(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch)

    async with backend:
      # The write consumes most of the budget before the read even begins. One deadline over
      # the whole exchange therefore expires while the read waits; a budget per phase would
      # restart for the read and take nearly twice as long.
      io.write_stall = liconic_module._COMMAND_TIMEOUT - 0.5
      io.read_stall = _STALLED_SECONDS
      start = anyio.current_time()
      with anyio.fail_after(_VIRTUAL_DEADLOCK_GUARD):
        with pytest.raises(TimeoutError, match="Timed out waiting for a response to 'RD 1813'"):
          await backend._send_command("RD 1813")
      elapsed = anyio.current_time() - start
      io.write_stall = 0.0
      io.read_stall = 0.0

    assert elapsed == pytest.approx(liconic_module._COMMAND_TIMEOUT, abs=_DEADLINE_SLACK)
    assert elapsed == pytest.approx(2.0, abs=_DEADLINE_SLACK)


class TestLiconicEntryTiming(AnyioTestBase):
  """Observe the entry sequence's fixed delays on a clock that costs no real time.

  Dual-backend coverage of the entry sequence's bytes and order lives in
  :class:`TestLiconicBackendLifecycle`; only the delays need a virtual clock, and they are
  backend independent.
  """

  _anyio_backends = {"trio": {"clock": MockClock(autojump_threshold=0)}}

  async def test_the_settle_delay_follows_the_break_and_precedes_the_flush(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch)

    start = anyio.current_time()
    with anyio.fail_after(_VIRTUAL_DEADLOCK_GUARD):
      async with backend:
        elapsed = anyio.current_time() - start

    # The PLC is ready on its first poll, so the settle delay is the only wait in the entry
    # sequence and the whole entry measures it.
    assert elapsed == pytest.approx(0.15, abs=_DEADLINE_SLACK)
    assert io.events[:4] == [
      "serial.enter",
      "send_break:0.2",
      "reset_input_buffer",
      "reset_output_buffer",
    ]

  async def test_the_readiness_poll_waits_the_class_poll_interval(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch, lines={"RD 1915": ["0\r\n", "1\r\n"]})

    start = anyio.current_time()
    with anyio.fail_after(_VIRTUAL_DEADLOCK_GUARD):
      async with backend:
        elapsed = anyio.current_time() - start
        # Read inside the lifespan, so the transcript is the entry sequence alone and does
        # not yet carry the close this exit appends.
        entry_events = list(io.events)

    # One failed poll, so the entry cost the settle delay plus exactly one poll interval.
    assert elapsed == pytest.approx(0.15 + 0.2, abs=_DEADLINE_SLACK)
    assert ExperimentalLiconicBackend.poll_interval == 0.2
    assert entry_events == _entry_events(ready_polls=2)


class TestLiconicWaitLoops(AnyioTestBase):
  async def test_wait_ready_returns_on_the_first_set_flag(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch)

    async with backend:
      baseline = len(io.events)
      await backend._wait_ready()
      assert _wire_events(io, baseline) == _exchange_events("RD 1915")

  async def test_wait_ready_polls_until_the_flag_is_set(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch, replies={"RD 1915": ["0", "0", "1"]})

    async with backend:
      baseline = len(io.events)
      await backend._wait_ready()
      assert _wire_events(io, baseline) == [
        *_exchange_events("RD 1915"),
        *_exchange_events("RD 1915"),
        *_exchange_events("RD 1915"),
      ]

  async def test_a_zero_timeout_polls_nothing_and_reconciles(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    """The budget is checked before each poll, exactly as the legacy loop did.

    A zero budget therefore performs no readiness poll at all and goes straight to the
    error-flag reconciliation, which is the legacy wire behaviour.
    """
    backend, io = make_backend(monkeypatch, replies={"RD 1814": ["0"]})

    async with backend:
      baseline = len(io.events)
      with pytest.raises(TimeoutError) as error_info:
        await backend._wait_ready(timeout=0)

      assert str(error_info.value) == "Incubator did not become ready within 0 seconds"
      assert _wire_events(io, baseline) == _exchange_events("RD 1814")

  async def test_an_exhausted_budget_maps_the_handling_error(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch, replies={"RD 1814": ["1"], "RD DM200": ["00007"]})

    async with backend:
      baseline = len(io.events)
      with pytest.raises(LiconicHandlerGeneralError):
        await backend._wait_ready(timeout=0)

      assert _wire_events(io, baseline) == [
        *_exchange_events("RD 1814"),
        *_exchange_events("RD DM200"),
      ]

  async def test_an_exhausted_budget_reports_an_unknown_error_code(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, _ = make_backend(monkeypatch, replies={"RD 1814": ["1"], "RD DM200": ["99999"]})

    async with backend:
      with pytest.raises(RuntimeError) as error_info:
        await backend._wait_ready(timeout=0)

    assert (
      str(error_info.value) == "Liconic Handler in unknown error state with memory showing 99999"
    )

  async def test_the_reconciliation_runs_as_its_own_transaction(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    """Each reconciliation read takes the command transaction in turn.

    `RD 1814` and `RD DM200` are themselves `_send_command` calls, so they cannot run inside
    another command's transaction. Measured on both backends, re-acquiring a held
    `anyio.Lock` from the task that already holds it raises `RuntimeError` immediately rather
    than blocking, so a reconciliation nested inside the transaction would fail fast rather
    than hang. It reaches the wire here, and the transaction is free again afterwards.
    """
    backend, io = make_backend(monkeypatch, replies={"RD 1814": ["1"], "RD DM200": ["00007"]})

    async with backend:
      with pytest.raises(LiconicHandlerGeneralError):
        await backend._wait_ready(timeout=0)

      assert io.commands[-2:] == ["RD 1814", "RD DM200"]
      # The transaction was released by every reconciliation, so a later command still runs.
      assert not backend._transaction_lock.locked()
      assert await backend._send_command("RD 1813") == "1"

  async def test_wait_plate_ready_polls_the_plate_flag(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch, replies={"RD 1914": ["0", "1"]})

    async with backend:
      baseline = len(io.events)
      await backend._wait_plate_ready()
      assert _wire_events(io, baseline) == [
        *_exchange_events("RD 1914"),
        *_exchange_events("RD 1914"),
      ]

  async def test_wait_plate_ready_reports_its_own_timeout(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch)

    async with backend:
      baseline = len(io.events)
      with pytest.raises(TimeoutError) as error_info:
        await backend._wait_plate_ready(timeout=0)

      assert str(error_info.value) == "Plate did not become ready within 0 seconds"
      # Unlike the readiness wait, an exhausted plate budget reconciles nothing.
      assert _wire_events(io, baseline) == []

  async def test_the_waits_reject_a_none_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
    """Neither wait has ever accepted `None`, and neither may start accepting it.

    An unbounded wait defeats the single-deadline rule for exactly the lost-connection case
    it exists to catch, so the deadline arithmetic must keep failing fast.
    """
    backend, io = make_backend(monkeypatch)

    async with backend:
      baseline = len(io.events)
      with pytest.raises(TypeError):
        await backend._wait_ready(timeout=cast(int, None))
      with pytest.raises(TypeError):
        await backend._wait_plate_ready(timeout=cast(int, None))
      assert _wire_events(io, baseline) == []


class TestLiconicWaitLoopClock(AnyioTestBase):
  """Observe the wait loops' poll interval on a clock that costs no real time."""

  _anyio_backends = {"trio": {"clock": MockClock(autojump_threshold=0)}}

  async def test_the_readiness_poll_gap_is_one_tenth_of_a_second(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, _ = make_backend(monkeypatch, replies={"RD 1915": ["0", "1"]})

    async with backend:
      start = anyio.current_time()
      with anyio.fail_after(_VIRTUAL_DEADLOCK_GUARD):
        await backend._wait_ready()
      elapsed = anyio.current_time() - start

    # One failed poll, so the wait cost exactly one poll gap.
    assert elapsed == pytest.approx(0.1, abs=_DEADLINE_SLACK)

  async def test_the_plate_poll_gap_is_one_tenth_of_a_second(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, _ = make_backend(monkeypatch, replies={"RD 1914": ["0", "1"]})

    async with backend:
      start = anyio.current_time()
      with anyio.fail_after(_VIRTUAL_DEADLOCK_GUARD):
        await backend._wait_plate_ready()
      elapsed = anyio.current_time() - start

    assert elapsed == pytest.approx(0.1, abs=_DEADLINE_SLACK)

  async def test_the_shovel_sensor_settles_before_it_is_read(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch, replies={"RD 1812": ["1"]})

    async with backend:
      baseline = len(io.events)
      start = anyio.current_time()
      with anyio.fail_after(_VIRTUAL_DEADLOCK_GUARD):
        assert await backend.check_shovel_sensor() is True
      elapsed = anyio.current_time() - start

    assert elapsed == pytest.approx(0.1, abs=_DEADLINE_SLACK)
    assert _wire_events(io, baseline) == [
      *_exchange_events("ST 1911"),
      *_exchange_events("RD 1812"),
      "serial.exit",
    ]


class TestLiconicDelayFloors(AnyioTestBase):
  """Pin that each fixed delay actually happens, on both backends, in real time.

  The exact values are observed on the virtual clock in :class:`TestLiconicEntryTiming` and
  :class:`TestLiconicWaitLoopClock`, but those classes are trio-only because only trio ships a
  backend-native clock. Measured, that leaves a real gap: collapsing the settle delay or a poll
  gap to zero is killed under trio and SURVIVES SILENTLY under asyncio. These nodes run on both
  backends against the real clock and only separate "the delay happened" from "it did not", so
  a delay deleted on either backend fails here. They are deliberately coarse; drift in the
  value is the virtual clock's job, not theirs.
  """

  async def test_the_entry_settle_delay_happens(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, _ = make_backend(monkeypatch)

    start = anyio.current_time()
    with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
      async with backend:
        elapsed = anyio.current_time() - start

    # The PLC answers its first readiness poll, so the settle delay is the only wait in the
    # entry sequence.
    _assert_real_delay(elapsed, 0.15)

  async def test_the_entry_readiness_poll_gap_happens(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, _ = make_backend(monkeypatch, lines={"RD 1915": ["0\r\n", "1\r\n"]})

    start = anyio.current_time()
    with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
      async with backend:
        elapsed = anyio.current_time() - start

    # One failed poll, so the entry spans the settle delay plus one poll interval.
    _assert_real_delay(elapsed, 0.15 + 0.2)

  async def test_the_readiness_wait_poll_gap_happens(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, _ = make_backend(monkeypatch, replies={"RD 1915": ["0", "1"]})

    async with backend:
      start = anyio.current_time()
      with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
        await backend._wait_ready()
      elapsed = anyio.current_time() - start

    _assert_real_delay(elapsed, 0.1)

  async def test_the_plate_wait_poll_gap_happens(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, _ = make_backend(monkeypatch, replies={"RD 1914": ["0", "1"]})

    async with backend:
      start = anyio.current_time()
      with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
        await backend._wait_plate_ready()
      elapsed = anyio.current_time() - start

    _assert_real_delay(elapsed, 0.1)

  async def test_the_shovel_sensor_settle_happens(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, _ = make_backend(monkeypatch, replies={"RD 1812": ["1"]})

    async with backend:
      start = anyio.current_time()
      with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
        assert await backend.check_shovel_sensor() is True
      elapsed = anyio.current_time() - start

    _assert_real_delay(elapsed, 0.1)


class TestLiconicSendCommandErrors(AnyioTestBase):
  async def test_an_empty_reply_reports_a_silent_plc(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, _ = make_backend(monkeypatch, replies={"RD 1915": [""]})

    async with backend:
      with pytest.raises(RuntimeError) as error_info:
        await backend._send_command("RD 1915")

    assert str(error_info.value) == "No response from Liconic PLC for command 'RD 1915'"

  async def test_a_controller_error_code_is_mapped(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, _ = make_backend(monkeypatch, replies={"ST 1801": ["E1"]})

    async with backend:
      with pytest.raises(LiconicControllerCommandError):
        await backend._send_command("ST 1801")

  async def test_an_unknown_error_code_is_reported(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, _ = make_backend(monkeypatch, replies={"ST 1801": ["E9"]})

    async with backend:
      with pytest.raises(RuntimeError) as error_info:
        await backend._send_command("ST 1801")

    assert str(error_info.value) == "Unknown error E9 when sending command ST 1801"

  async def test_the_command_is_stripped_and_carriage_return_terminated(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch)

    async with backend:
      baseline = len(io.writes)
      await backend._send_command("  RD 1813  ")

    assert io.writes[baseline] == b"RD 1813\r"

  async def test_the_reply_read_asks_for_the_legacy_buffer_size(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch)

    async with backend:
      baseline = len(io.reads)
      await backend._send_command("RD 1813")

    assert io.reads[baseline] == 128


class TestValueConversions(AnyioTestBase):
  """Test the PLC register value conversions against recorded wire bytes."""

  async def test_set_temperature_conversion(self, monkeypatch: pytest.MonkeyPatch) -> None:
    """37.5°C should become '00375' (0.1°C units)."""
    backend, io = make_backend(monkeypatch)

    async with backend:
      baseline = len(io.events)
      await backend.set_temperature(37.5)
      assert _wire_events(io, baseline) == [
        *_exchange_events("WR DM890 00375"),
        *_exchange_events("RD 1915"),
      ]

  async def test_get_temperature_conversion(self, monkeypatch: pytest.MonkeyPatch) -> None:
    """PLC value 370 should return 37.0°C."""
    backend, _ = make_backend(monkeypatch, replies={"RD DM982": ["370"]})

    async with backend:
      assert await backend.get_temperature() == pytest.approx(37.0)

  async def test_an_unparseable_temperature_is_reported(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, _ = make_backend(monkeypatch, replies={"RD DM982": ["warm"]})

    async with backend:
      with pytest.raises(RuntimeError, match="Invalid temperature value"):
        await backend.get_temperature()

  async def test_set_humidity_conversion(self, monkeypatch: pytest.MonkeyPatch) -> None:
    """0.9 fraction should become '00900' (0.1% units)."""
    backend, io = make_backend(monkeypatch)

    async with backend:
      baseline = len(io.events)
      await backend.set_humidity(0.9)
      assert _wire_events(io, baseline) == [
        *_exchange_events("WR DM893 00900"),
        *_exchange_events("RD 1915"),
      ]

  async def test_get_humidity_conversion(self, monkeypatch: pytest.MonkeyPatch) -> None:
    """PLC value 900 should return 0.9 fraction."""
    backend, _ = make_backend(monkeypatch, replies={"RD DM983": ["900"]})

    async with backend:
      assert await backend.get_humidity() == pytest.approx(0.9)

  async def test_set_co2_conversion(self, monkeypatch: pytest.MonkeyPatch) -> None:
    """0.05 fraction (5%) should become '00500' (0.01% units)."""
    backend, io = make_backend(monkeypatch)

    async with backend:
      baseline = len(io.events)
      await backend.set_co2_level(0.05)
      assert _wire_events(io, baseline) == [
        *_exchange_events("WR DM894 00500"),
        *_exchange_events("RD 1915"),
      ]

  async def test_get_co2_conversion(self, monkeypatch: pytest.MonkeyPatch) -> None:
    """PLC value 500 should return 0.05 fraction."""
    backend, _ = make_backend(monkeypatch, replies={"RD DM984": ["500"]})

    async with backend:
      assert await backend.get_co2_level() == pytest.approx(0.05)

  async def test_set_n2_conversion(self, monkeypatch: pytest.MonkeyPatch) -> None:
    """0.9 fraction (90%) should become '09000' (0.01% units)."""
    backend, io = make_backend(monkeypatch)

    async with backend:
      baseline = len(io.events)
      await backend.set_n2_level(0.9)
      assert _wire_events(io, baseline) == [
        *_exchange_events("WR DM895 09000"),
        *_exchange_events("RD 1915"),
      ]

  async def test_start_shaking_conversion(self, monkeypatch: pytest.MonkeyPatch) -> None:
    """25.0 Hz should become '00250' (0.1 Hz units)."""
    backend, io = make_backend(monkeypatch)

    async with backend:
      baseline = len(io.events)
      await backend.start_shaking(25.0)
      assert _wire_events(io, baseline) == [
        *_exchange_events("WR DM39 00250"),
        *_exchange_events("ST 1913"),
        *_exchange_events("RD 1915"),
      ]

  async def test_start_shaking_fractional(self, monkeypatch: pytest.MonkeyPatch) -> None:
    """10.5 Hz should become '00105' (0.1 Hz units)."""
    backend, io = make_backend(monkeypatch)

    async with backend:
      baseline = len(io.events)
      await backend.start_shaking(10.5)
      assert io.commands[len(io.commands) - 3] == "WR DM39 00105"
      assert _wire_events(io, baseline)[0] == _write_event("WR DM39 00105")

  async def test_start_shaking_range_low(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, io = make_backend(monkeypatch)

    async with backend:
      baseline = len(io.events)
      with pytest.raises(ValueError):
        await backend.start_shaking(0.5)
      # The frequency is validated before anything reaches the wire.
      assert _wire_events(io, baseline) == []

  async def test_start_shaking_range_high(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, io = make_backend(monkeypatch)

    async with backend:
      baseline = len(io.events)
      with pytest.raises(ValueError):
        await backend.start_shaking(51.0)
      assert _wire_events(io, baseline) == []

  async def test_nc_model_rejects_climate(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, io = make_backend(monkeypatch, model=LiconicType.STX44_NC)

    async with backend:
      baseline = len(io.events)
      with pytest.raises(NotImplementedError):
        await backend.set_temperature(37.0)
      with pytest.raises(NotImplementedError):
        await backend.get_temperature()
      with pytest.raises(NotImplementedError):
        await backend.set_humidity(0.5)
      assert _wire_events(io, baseline) == []


class TestShaking(AnyioTestBase):
  async def test_stop_shaking(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, io = make_backend(monkeypatch)

    async with backend:
      baseline = len(io.events)
      await backend.stop_shaking()
      assert _wire_events(io, baseline) == [
        *_exchange_events("RS 1913"),
        *_exchange_events("RD 1915"),
      ]

  async def test_get_shaker_speed(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, io = make_backend(monkeypatch, replies={"RD DM39": ["250"]})

    async with backend:
      baseline = len(io.events)
      assert await backend.get_shaker_speed() == pytest.approx(25.0)
      assert _wire_events(io, baseline) == [
        *_exchange_events("RD DM39"),
        *_exchange_events("RD 1915"),
      ]

  async def test_shaker_status_not_implemented(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, io = make_backend(monkeypatch)

    async with backend:
      baseline = len(io.events)
      with pytest.raises(NotImplementedError):
        await backend.shaker_status()
      assert _wire_events(io, baseline) == []


class TestDoorControl(AnyioTestBase):
  async def test_open_door(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, io = make_backend(monkeypatch)

    async with backend:
      baseline = len(io.events)
      await backend.open_door()
      assert _wire_events(io, baseline) == [
        *_exchange_events("ST 1901"),
        *_exchange_events("RD 1915"),
      ]

  async def test_close_door(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, io = make_backend(monkeypatch)

    async with backend:
      baseline = len(io.events)
      await backend.close_door()
      assert _wire_events(io, baseline) == [
        *_exchange_events("ST 1902"),
        *_exchange_events("RD 1915"),
      ]


class TestSensors(AnyioTestBase):
  async def test_check_shovel_sensor_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, _ = make_backend(monkeypatch, replies={"RD 1812": ["1"]})

    async with backend:
      assert await backend.check_shovel_sensor() is True

  async def test_check_shovel_sensor_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, _ = make_backend(monkeypatch, replies={"RD 1812": ["0"]})

    async with backend:
      assert await backend.check_shovel_sensor() is False

  async def test_check_shovel_sensor_unexpected(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, _ = make_backend(monkeypatch, replies={"RD 1812": ["X"]})

    async with backend:
      with pytest.raises(RuntimeError, match="read shovel sensor"):
        await backend.check_shovel_sensor()

  async def test_check_transfer_sensor_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, io = make_backend(monkeypatch, replies={"RD 1813": ["1"]})

    async with backend:
      baseline = len(io.events)
      assert await backend.check_transfer_sensor() is True
      assert _wire_events(io, baseline) == _exchange_events("RD 1813")

  async def test_check_transfer_sensor_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, _ = make_backend(monkeypatch, replies={"RD 1813": ["0"]})

    async with backend:
      assert await backend.check_transfer_sensor() is False

  async def test_check_transfer_sensor_unexpected(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, _ = make_backend(monkeypatch, replies={"RD 1813": ["X"]})

    async with backend:
      with pytest.raises(RuntimeError, match="read transfer station sensor"):
        await backend.check_transfer_sensor()

  async def test_check_second_transfer_sensor_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, io = make_backend(monkeypatch, replies={"RD 1807": ["1"]})

    async with backend:
      baseline = len(io.events)
      assert await backend.check_second_transfer_sensor() is True
      assert _wire_events(io, baseline) == _exchange_events("RD 1807")

  async def test_check_second_transfer_sensor_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, _ = make_backend(monkeypatch, replies={"RD 1807": ["0"]})

    async with backend:
      assert await backend.check_second_transfer_sensor() is False

  async def test_check_second_transfer_sensor_unexpected(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, _ = make_backend(monkeypatch, replies={"RD 1807": ["X"]})

    async with backend:
      with pytest.raises(RuntimeError, match="read 2nd transfer station sensor"):
        await backend.check_second_transfer_sensor()


class TestClimateGetters(AnyioTestBase):
  async def test_get_target_temperature(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, io = make_backend(monkeypatch, replies={"RD DM890": ["375"]})

    async with backend:
      baseline = len(io.events)
      assert await backend.get_target_temperature() == pytest.approx(37.5)
      assert _wire_events(io, baseline) == _exchange_events("RD DM890")

  async def test_get_target_humidity(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, io = make_backend(monkeypatch, replies={"RD DM893": ["900"]})

    async with backend:
      baseline = len(io.events)
      assert await backend.get_target_humidity() == pytest.approx(0.9)
      assert _wire_events(io, baseline) == _exchange_events("RD DM893")

  async def test_get_target_co2(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, io = make_backend(monkeypatch, replies={"RD DM894": ["500"]})

    async with backend:
      baseline = len(io.events)
      assert await backend.get_target_co2_level() == pytest.approx(0.05)
      assert _wire_events(io, baseline) == _exchange_events("RD DM894")

  async def test_get_n2_level(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, io = make_backend(monkeypatch, replies={"RD DM985": ["9000"]})

    async with backend:
      baseline = len(io.events)
      assert await backend.get_n2_level() == pytest.approx(0.9)
      assert _wire_events(io, baseline) == _exchange_events("RD DM985")

  async def test_get_target_n2(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, io = make_backend(monkeypatch, replies={"RD DM895": ["9000"]})

    async with backend:
      baseline = len(io.events)
      assert await backend.get_target_n2_level() == pytest.approx(0.9)
      assert _wire_events(io, baseline) == _exchange_events("RD DM895")

  async def test_nc_model_rejects_humidity(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, io = make_backend(monkeypatch, model=LiconicType.STX44_NC)

    async with backend:
      baseline = len(io.events)
      with pytest.raises(NotImplementedError):
        await backend.get_humidity()
      with pytest.raises(NotImplementedError):
        await backend.get_target_humidity()
      with pytest.raises(NotImplementedError):
        await backend.get_target_temperature()
      assert _wire_events(io, baseline) == []


class TestLiconicPlateMovement(AnyioTestBase):
  async def test_transport_reply_timeout_quarantines_dispatched_motion(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch)
    failure = TimeoutError("serial read timed out")
    async with backend:
      baseline = len(io.commands)
      io.failures["read"] = failure
      with pytest.raises(TimeoutError) as caught:
        await backend._complete_transfer("ST 1905")
      assert caught.value is failure
      with pytest.raises(RuntimeError, match="reply alignment unknown"):
        await backend._send_command("RD 1915")
      assert io.commands[baseline:] == ["ST 1905"]

  async def test_late_motion_reply_quarantines_connection_before_poll(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch)
    original_read = io.read
    late_reply = [b"OK", b"1"]

    async with backend:
      baseline = len(io.commands)

      async def delayed_read(num_bytes: int = 1) -> bytes:
        if io.commands[-1] == "ST 1905":
          await anyio.sleep_forever()
        if late_reply:
          return late_reply.pop(0)
        return await original_read(num_bytes)

      monkeypatch.setattr(io, "read", delayed_read)
      monkeypatch.setattr(liconic_module, "_COMMAND_TIMEOUT", 0.05)
      with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
        with pytest.raises(TimeoutError, match="Timed out waiting for a response to 'ST 1905'"):
          await backend._complete_transfer("ST 1905")
      assert io.commands[baseline:] == ["ST 1905"]
      with pytest.raises(RuntimeError, match="reply alignment unknown"):
        await backend._send_command("RD 1915")
      assert io.commands[baseline:] == ["ST 1905"]
      assert late_reply == [b"OK", b"1"]

    entries = io.entries
    with pytest.raises(RuntimeError, match="reply alignment unknown"):
      async with backend:
        pytest.fail("uncertain instance entered a new Serial session")
    assert io.entries == entries

  async def test_unknown_reply_skips_unverifiable_shaking_stop_on_exit(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch)
    async with backend:
      backend._shaking_active = True
      io.blocked_event = "read:ST 1905"
      monkeypatch.setattr(liconic_module, "_COMMAND_TIMEOUT", 0.05)
      with pytest.raises(TimeoutError, match="Timed out waiting for a response"):
        await backend._complete_transfer("ST 1905")
    assert "RS 1913" not in io.commands
    assert backend._shaking_active
    assert backend._shaking_state_unknown
    assert io.exits == 1

  @pytest.mark.parametrize("command", ["ST 1905", "ST 1904"])
  async def test_timed_out_motion_reply_still_terminates_access(
    self, monkeypatch: pytest.MonkeyPatch, command: str
  ) -> None:
    backend, io = make_backend(monkeypatch)
    async with backend:
      baseline = len(io.commands)
      io.blocked_event = f"read:{command}"
      monkeypatch.setattr(liconic_module, "_COMMAND_TIMEOUT", 0.05)
      with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
        with pytest.raises(TimeoutError, match="Timed out waiting for a response"):
          await backend._complete_transfer(command)
      assert io.commands[baseline:] == [command]
      with pytest.raises(RuntimeError, match="reply alignment unknown"):
        await backend._send_command("RD 1915")

  async def test_timed_out_pick_reply_still_places_and_terminates_access(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch)
    origin, destination = make_movement_sites(backend)
    plate = make_plate("plate-timeout")
    origin.assign_child_resource(plate)
    async with backend:
      baseline = len(io.commands)
      io.blocked_event = "read:ST 1908"
      monkeypatch.setattr(liconic_module, "_COMMAND_TIMEOUT", 0.05)
      with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
        with pytest.raises(TimeoutError, match="Timed out waiting for a response"):
          await backend.move_position_to_position(plate, destination)
      assert io.commands[baseline:][-1] == "ST 1908"
      assert "ST 1909" not in io.commands[baseline:]
      with pytest.raises(RuntimeError, match="reply alignment unknown"):
        await backend._send_command("WR DM5 2")

  async def _cancel_at_dispatch_boundary(
    self,
    monkeypatch: pytest.MonkeyPatch,
    backend: ExperimentalLiconicBackend,
    io: RecordingSerial,
    command: str,
    operation: Callable[[], Awaitable[None]],
    *,
    after_dispatch: bool,
  ) -> List[str]:
    observed: List[str] = []
    before_dispatch = anyio.Event()
    release_dispatch = anyio.Event()
    dispatched = anyio.Event()
    original_send_command = backend._send_command
    io.observed_write_completion = command
    if after_dispatch:
      release_dispatch.set()

    async def synchronized_send_command(
      sent_command: str,
      *,
      _on_dispatch: Optional[Callable[[], None]] = None,
      timeout: Optional[float] = None,
    ) -> str:
      if sent_command != command:
        return await original_send_command(sent_command, _on_dispatch=_on_dispatch, timeout=timeout)

      before_dispatch.set()
      await release_dispatch.wait()

      def observe_dispatch() -> None:
        assert io.events[-1] == f"write-done:{command}"
        if _on_dispatch is not None:
          _on_dispatch()
        io.events.append(f"dispatch:{command}")
        dispatched.set()

      return await original_send_command(
        sent_command, _on_dispatch=observe_dispatch, timeout=timeout
      )

    monkeypatch.setattr(backend, "_send_command", synchronized_send_command)

    async def run_operation() -> None:
      try:
        await operation()
      except anyio.get_cancelled_exc_class():
        observed.append("cancelled")
        io.events.append("task-cancelled")
        raise

    with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(run_operation)
        if after_dispatch:
          await dispatched.wait()
        else:
          await before_dispatch.wait()
        task_group.cancel_scope.cancel()

    return observed

  async def test_fetch_cancellation_before_motion_dispatch_writes_no_motion(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch)
    site, _ = make_movement_sites(backend)
    plate = make_plate("plate1")
    site.assign_child_resource(plate)

    async with backend:
      baseline = len(io.commands)
      observed = await self._cancel_at_dispatch_boundary(
        monkeypatch,
        backend,
        io,
        "ST 1905",
        lambda: backend.fetch_plate_to_loading_tray(plate),
        after_dispatch=False,
      )

    assert observed == ["cancelled"]
    assert io.commands[baseline:] == ["WR DM0 1", "WR DM23 788", "WR DM25 22", "WR DM5 1"]

  async def test_fetch_cancellation_after_motion_dispatch_reconciles_and_terminates_access(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch)
    site, _ = make_movement_sites(backend)
    plate = make_plate("plate1")
    site.assign_child_resource(plate)

    async with backend:
      baseline = len(io.commands)
      observed = await self._cancel_at_dispatch_boundary(
        monkeypatch,
        backend,
        io,
        "ST 1905",
        lambda: backend.fetch_plate_to_loading_tray(plate),
        after_dispatch=True,
      )

    assert observed == ["cancelled"]
    assert io.commands[baseline:] == [
      "WR DM0 1",
      "WR DM23 788",
      "WR DM25 22",
      "WR DM5 1",
      "ST 1905",
      "RD 1915",
      "ST 1903",
    ]
    assert io.events.index("write-done:ST 1905") < io.events.index("dispatch:ST 1905")
    assert io.events[io.events.index("dispatch:ST 1905") + 1] == "read"
    assert io.events.index(_write_event("ST 1903")) < io.events.index("task-cancelled")

  async def test_take_in_cancellation_before_motion_dispatch_writes_no_motion(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch)
    site, _ = make_movement_sites(backend)
    plate = make_plate("plate2")

    async with backend:
      baseline = len(io.commands)
      observed = await self._cancel_at_dispatch_boundary(
        monkeypatch,
        backend,
        io,
        "ST 1904",
        lambda: backend.take_in_plate(plate, site),
        after_dispatch=False,
      )

    assert observed == ["cancelled"]
    assert io.commands[baseline:] == ["WR DM0 1", "WR DM23 788", "WR DM25 22", "WR DM5 1"]

  async def test_take_in_cancellation_after_motion_dispatch_reconciles_and_terminates_access(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch)
    site, _ = make_movement_sites(backend)
    plate = make_plate("plate2")

    async with backend:
      baseline = len(io.commands)
      observed = await self._cancel_at_dispatch_boundary(
        monkeypatch,
        backend,
        io,
        "ST 1904",
        lambda: backend.take_in_plate(plate, site),
        after_dispatch=True,
      )

    assert observed == ["cancelled"]
    assert io.commands[baseline:] == [
      "WR DM0 1",
      "WR DM23 788",
      "WR DM25 22",
      "WR DM5 1",
      "ST 1904",
      "RD 1915",
      "ST 1903",
    ]
    assert io.events.index("write-done:ST 1904") < io.events.index("dispatch:ST 1904")
    assert io.events[io.events.index("dispatch:ST 1904") + 1] == "read"
    assert io.events.index(_write_event("ST 1903")) < io.events.index("task-cancelled")

  async def test_internal_move_cancellation_before_pick_dispatch_writes_no_pick(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch)
    origin, destination = make_movement_sites(backend)
    plate = make_plate("plate3")
    origin.assign_child_resource(plate)

    async with backend:
      baseline = len(io.commands)
      observed = await self._cancel_at_dispatch_boundary(
        monkeypatch,
        backend,
        io,
        "ST 1908",
        lambda: backend.move_position_to_position(plate, destination),
        after_dispatch=False,
      )

    assert observed == ["cancelled"]
    assert io.commands[baseline:] == [
      "WR DM0 1",
      "WR DM0 1",
      "WR DM23 788",
      "WR DM25 22",
      "WR DM5 1",
    ]

  async def test_internal_move_cancellation_after_pick_dispatch_completes_place_and_terminates_access(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch)
    origin, destination = make_movement_sites(backend)
    plate = make_plate("plate3")
    origin.assign_child_resource(plate)

    async with backend:
      baseline = len(io.commands)
      observed = await self._cancel_at_dispatch_boundary(
        monkeypatch,
        backend,
        io,
        "ST 1908",
        lambda: backend.move_position_to_position(plate, destination),
        after_dispatch=True,
      )

    assert observed == ["cancelled"]
    assert io.commands[baseline:] == [
      "WR DM0 1",
      "WR DM0 1",
      "WR DM23 788",
      "WR DM25 22",
      "WR DM5 1",
      "ST 1908",
      "RD 1915",
      "WR DM23 788",
      "WR DM25 22",
      "WR DM5 2",
      "ST 1909",
      "RD 1915",
      "ST 1903",
    ]
    assert io.events.index("write-done:ST 1908") < io.events.index("dispatch:ST 1908")
    assert io.events[io.events.index("dispatch:ST 1908") + 1] == "read"
    assert io.events.index(_write_event("ST 1903")) < io.events.index("task-cancelled")

  async def test_reconciliation_failure_wins_pending_cancellation_and_preserves_context(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    failure = RuntimeError("readiness reconciliation failed")
    backend, io = make_backend(monkeypatch)
    site, _ = make_movement_sites(backend)
    plate = make_plate("plate4")
    site.assign_child_resource(plate)
    observed: List[BaseException] = []

    async def fetch_plate() -> None:
      try:
        await backend.fetch_plate_to_loading_tray(plate)
      except BaseException as error:
        observed.append(error)

    async with backend:
      io.failures[_write_event("RD 1915")] = failure
      io.blocked_event = _write_event("RD 1915")
      with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(fetch_plate)
          await io.blocked_started.wait()
          task_group.cancel_scope.cancel()
          io.release_blocked.set()

    assert observed == [failure]
    assert isinstance(failure.__context__, anyio.get_cancelled_exc_class())

  async def test_send_command_uses_monkeypatched_default_timeout_at_call_time(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch)

    async with backend:
      io.blocked_event = "read"
      monkeypatch.setattr(liconic_module, "_COMMAND_TIMEOUT", 0.05)
      start = anyio.current_time()
      with pytest.raises(TimeoutError, match="Timed out waiting for a response to 'RD 1813'"):
        await backend._send_command("RD 1813")
      elapsed = anyio.current_time() - start

    assert elapsed < 1.0


class TestSwapStation(AnyioTestBase):
  async def test_turn_swap_station_home_when_swapped(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, io = make_backend(monkeypatch, replies={"RD 1912": ["1"]})

    async with backend:
      baseline = len(io.events)
      await backend.turn_swap_station(home=True)
      assert _wire_events(io, baseline) == [
        *_exchange_events("RD 1912"),
        *_exchange_events("RS 1912"),
      ]

  async def test_turn_swap_station_swap_when_home(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, io = make_backend(monkeypatch, replies={"RD 1912": ["0"]})

    async with backend:
      baseline = len(io.events)
      await backend.turn_swap_station(home=False)
      assert _wire_events(io, baseline) == [
        *_exchange_events("RD 1912"),
        *_exchange_events("ST 1912"),
      ]


class TestInitialize(AnyioTestBase):
  async def test_initialize(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, io = make_backend(monkeypatch)

    async with backend:
      baseline = len(io.events)
      await backend.initialize()
      assert _wire_events(io, baseline) == [
        *_exchange_events("ST 1900"),
        *_exchange_events("ST 1801"),
        *_exchange_events("RD 1915"),
      ]


class TestLiconicShakingScope(AnyioTestBase):
  def test_preserves_public_signatures(self) -> None:
    start = inspect.signature(ExperimentalLiconicBackend.start_shaking).parameters
    assert tuple(start) == ("self", "frequency")
    assert start["frequency"].annotation is inspect.Parameter.empty
    assert start["frequency"].default is inspect.Parameter.empty
    assert tuple(inspect.signature(ExperimentalLiconicBackend.stop_shaking).parameters) == ("self",)

  async def test_inherited_scope_uses_liconic_start_and_stop_commands(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch)

    async with backend:
      baseline = len(io.commands)
      async with backend.shaking(10.5):
        assert backend._shaking_active

    assert [command for command in io.commands[baseline:] if command != "RD 1915"] == [
      "WR DM39 00105",
      "ST 1913",
      "RS 1913",
    ]

  async def test_durable_calls_preserve_commands_and_scoped_calls_are_pre_mutation(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch)

    async with backend:
      baseline = len(io.commands)
      await backend.start_shaking(10.5)
      await backend.stop_shaking()
      async with backend.shaking(12):
        commands_before_rejections = list(io.commands)
        with pytest.raises(RuntimeError, match="scope"):
          await backend.start_shaking(20)
        with pytest.raises(RuntimeError, match="scope"):
          await backend.stop_shaking()
        assert io.commands == commands_before_rejections

    assert [command for command in io.commands[baseline:] if command != "RD 1915"] == [
      "WR DM39 00105",
      "ST 1913",
      "RS 1913",
      "WR DM39 00120",
      "ST 1913",
      "RS 1913",
    ]

  async def test_direct_lifespan_cleanup_stops_and_invalidates_live_scope(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch)
    lifespan = backend.lifespan()
    scope = backend.shaking(10)

    await lifespan.__aenter__()
    baseline = len(io.commands)
    await scope.__aenter__()
    await lifespan.__aexit__(None, None, None)
    assert backend._shaking_scope_token is None
    commands_after_cleanup = list(io.commands)
    await scope.__aexit__(None, None, None)

    assert [command for command in commands_after_cleanup[baseline:] if command != "RD 1915"] == [
      "WR DM39 00100",
      "ST 1913",
      "RS 1913",
    ]
    assert io.commands == commands_after_cleanup


if __name__ == "__main__":
  unittest.main()
