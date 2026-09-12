from collections import deque
from typing import Any, Deque, Optional, cast
from unittest.mock import patch

import anyio
import pytest

from pylabrobot.agilent.biotek.el406.el406 import (
  EL406,
  STATE_INITIAL,
  STATE_RUNNING,
  STATE_STOPPED,
)
from pylabrobot.agilent.biotek.el406.peristaltic_dispenser import PeristalticDispenser
from pylabrobot.agilent.biotek.el406.plate_washer import PlateWasher
from pylabrobot.agilent.biotek.el406.syringe_dispenser import SyringeDispenser
from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.io.ftdi import FTDI
from pylabrobot.resources import PlateHolder
from pylabrobot.resources.corning.plates import cor_96_wellplate_360uL_Fb
from pylabrobot.testing.concurrency import AnyioTestBase

EMPTY_RESPONSE_HEADER = bytes.fromhex("010200000100000000fcff")
TEST_COMM = bytes.fromhex("01027300010000000089ff")
INIT_STATE = bytes.fromhex("0102a00001000000005cff")
RESET = bytes.fromhex("0102700001000000008cff")
STATUS_POLL = bytes.fromhex("0102920001000000006aff")
STEP = bytes.fromhex("0102a20001000000005aff")
ABORT_HEADER = bytes.fromhex("01028900010000010072ff")
HOME_HEADER = bytes.fromhex("0102c80001000002002eff")
END_BATCH = bytes.fromhex("01028c00010000000070ff")
PRE_BATCH = [
  bytes.fromhex("0102bf0001000000003dff"),
  bytes.fromhex("0102c10001000000003bff"),
  bytes.fromhex("0102f20001000000000aff"),
  bytes.fromhex("0102f400010000000008ff"),
  bytes.fromhex("010254010100000000a7ff"),
  bytes.fromhex("010202010100000000f9ff"),
  bytes.fromhex("01020a010100000000f1ff"),
]
START_BATCH_HEADER = bytes.fromhex("01028d0001000001006aff")


def status_response(state: int, validity: int = 0, status: int = 0) -> bytes:
  """Build one literal-shaped status response for the fake transport."""
  header = bytes.fromhex("0102920001000009000000")
  data = (
    validity.to_bytes(2, "little")
    + state.to_bytes(2, "little")
    + b"\x00\x00\x00\x00"
    + bytes([status])
  )
  return b"\x06" + header + data


class FakeFTDI(HasLifespan):
  """Replay complete EL406 responses and record hardware-free transport effects."""

  def __init__(
    self,
    *,
    fail_at: Optional[str] = None,
    block_at: Optional[str] = None,
    blocked: Optional[anyio.Event] = None,
    release: Optional[anyio.Event] = None,
  ) -> None:
    super().__init__()
    self.events: list[str] = []
    self.writes: list[bytes] = []
    self.read_sizes: list[int] = []
    self.fail_at = fail_at
    self.block_at = block_at
    self.blocked = blocked
    self.release = release
    self.responses: dict[int, Deque[bytes]] = {}
    self._rx = bytearray()
    self._response_command: Optional[int] = None
    self._rx_purge_count = 0
    self._tx_purge_count = 0
    self._read_counts: dict[int, int] = {}
    self.signal_after_response_command: Optional[int] = None
    self.response_finished: Optional[anyio.Event] = None

  async def _record(self, event: str) -> None:
    await anyio.lowlevel.checkpoint()
    self.events.append(event)
    if event == self.fail_at:
      raise RuntimeError(f"failed at {event}")
    if event == self.block_at:
      if self.blocked is not None:
        self.blocked.set()
      if self.release is None:
        await anyio.sleep_forever()
      else:
        await self.release.wait()

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    if kwargs:
      raise TypeError("FakeFTDI does not accept lifespan options")
    stack.push_shielded_async_callback(self._close)
    await self._record("ftdi.enter")

  async def _close(self) -> None:
    await anyio.lowlevel.checkpoint()
    self.events.append("ftdi.exit")

  async def set_baudrate(self, baudrate: int) -> None:
    await self._record(f"baudrate:{baudrate}")

  async def set_line_property(self, bits: int, stopbits: int, parity: int) -> None:
    await self._record(f"line:{bits}:{stopbits}:{parity}")

  async def set_flowctrl(self, flowctrl: int) -> None:
    await self._record(f"flow:{flowctrl}")

  async def set_rts(self, value: bool) -> None:
    await self._record(f"rts:{value}")

  async def set_dtr(self, value: bool) -> None:
    await self._record(f"dtr:{value}")

  async def usb_purge_rx_buffer(self) -> None:
    self._rx_purge_count += 1
    await self._record(f"purge.rx:{self._rx_purge_count}")

  async def usb_purge_tx_buffer(self) -> None:
    self._tx_purge_count += 1
    await self._record(f"purge.tx:{self._tx_purge_count}")

  async def write(self, data: bytes) -> int:
    await self._record(f"write:{data.hex()}")
    self.writes.append(data)
    if len(data) == 11:
      command = int.from_bytes(data[2:4], "little")
      self._response_command = command
      queued = self.responses.get(command)
      if queued:
        self._rx.extend(queued.popleft())
      elif command == 0x92:
        self._rx.extend(status_response(STATE_INITIAL))
      else:
        self._rx.extend(b"\x06" + EMPTY_RESPONSE_HEADER)
    self.events.append(f"write:{data.hex()}:done")
    return len(data)

  async def read(self, num_bytes: int) -> bytes:
    assert self._response_command is not None
    read_count = self._read_counts.get(self._response_command, 0) + 1
    self._read_counts[self._response_command] = read_count
    read_part = {1: "ack", 2: "header"}.get(read_count, f"data:{read_count - 2}")
    await self._record(f"read:{self._response_command:04x}:{read_part}")
    self.read_sizes.append(num_bytes)
    chunk = bytes(self._rx[:num_bytes])
    del self._rx[:num_bytes]
    self.events.append(f"read:{self._response_command:04x}:{read_part}:done")
    if (
      not self._rx
      and self._response_command == self.signal_after_response_command
      and self.response_finished is not None
    ):
      self.response_finished.set()
    return chunk


def make_device(**fake_kwargs: Any) -> tuple[EL406, FakeFTDI]:
  """Construct an EL406 with its external FTDI dependency replaced by a fake."""
  with patch("pylabrobot.agilent.biotek.el406.el406.FTDI"):
    device = EL406(name="washer", timeout=0.1)
  io = FakeFTDI(**fake_kwargs)
  device.io = cast(FTDI, io)
  return device, io


EXACT_SETUP_EVENTS = [
  "ftdi.enter",
  "baudrate:38400",
  "line:8:2:0",
  "flow:0",
  "rts:True",
  "dtr:True",
  "purge.rx:1",
  "purge.rx:2",
  "purge.rx:3",
  "purge.rx:4",
  "purge.rx:5",
  "purge.rx:6",
  "purge.tx:1",
  "purge.rx:7",
  "purge.rx:8",
  "purge.rx:9",
  "purge.rx:10",
  "purge.rx:11",
  "purge.rx:12",
  "purge.tx:2",
  f"write:{TEST_COMM.hex()}",
  f"write:{TEST_COMM.hex()}:done",
  "read:0073:ack",
  "read:0073:ack:done",
  "read:0073:header",
  "read:0073:header:done",
  "purge.rx:13",
  "purge.rx:14",
  "purge.rx:15",
  "purge.rx:16",
  "purge.rx:17",
  "purge.rx:18",
  "purge.tx:3",
  f"write:{INIT_STATE.hex()}",
  f"write:{INIT_STATE.hex()}:done",
  "read:00a0:ack",
  "read:00a0:ack:done",
  "read:00a0:header",
  "read:00a0:header:done",
  "purge.rx:19",
  "purge.rx:20",
  "purge.rx:21",
  "purge.rx:22",
  "purge.rx:23",
  "purge.rx:24",
  "purge.tx:4",
  f"write:{RESET.hex()}",
  f"write:{RESET.hex()}:done",
  "read:0070:ack",
  "read:0070:ack:done",
  "read:0070:header",
  "read:0070:header:done",
  "body",
  "ftdi.exit",
]

SETUP_PHASES = [event for event in EXACT_SETUP_EVENTS[:-2] if not event.endswith(":done")]
COMMITTED_RECONCILIATION = {
  f"write:{TEST_COMM.hex()}": [
    f"write:{TEST_COMM.hex()}",
    f"write:{TEST_COMM.hex()}:done",
    "read:0073:ack",
    "read:0073:ack:done",
    "read:0073:header",
    "read:0073:header:done",
  ],
  "read:0073:ack": [
    "read:0073:ack",
    "read:0073:ack:done",
    "read:0073:header",
    "read:0073:header:done",
  ],
  "read:0073:header": ["read:0073:header", "read:0073:header:done"],
  f"write:{INIT_STATE.hex()}": [
    f"write:{INIT_STATE.hex()}",
    f"write:{INIT_STATE.hex()}:done",
    "read:00a0:ack",
    "read:00a0:ack:done",
    "read:00a0:header",
    "read:00a0:header:done",
  ],
  "read:00a0:ack": [
    "read:00a0:ack",
    "read:00a0:ack:done",
    "read:00a0:header",
    "read:00a0:header:done",
  ],
  "read:00a0:header": ["read:00a0:header", "read:00a0:header:done"],
  f"write:{RESET.hex()}": [
    f"write:{RESET.hex()}",
    f"write:{RESET.hex()}:done",
    "read:0070:ack",
    "read:0070:ack:done",
    "read:0070:header",
    "read:0070:header:done",
  ],
  "read:0070:ack": [
    "read:0070:ack",
    "read:0070:ack:done",
    "read:0070:header",
    "read:0070:header:done",
  ],
  "read:0070:header": ["read:0070:header", "read:0070:header:done"],
}


async def enter_device(device: EL406) -> None:
  """Enter a device until completion or cancellation."""
  async with device:
    pytest.fail("blocked device entry reached the body")


class TestEL406Lifecycle(AnyioTestBase):
  def test_uses_managed_lifecycle_and_preserves_public_composites(self) -> None:
    device, _ = make_device()

    assert EL406.__aenter__ is HasLifespan.__aenter__
    assert EL406.__aexit__ is HasLifespan.__aexit__
    assert EL406.setup is HasLifespan.setup
    assert EL406.stop is HasLifespan.stop
    assert isinstance(device.washer, PlateWasher)
    assert device.warsher is device.washer
    assert isinstance(device.syringe_dispenser, SyringeDispenser)
    assert isinstance(device.peristaltic_dispenser, PeristalticDispenser)
    assert isinstance(device.plate_holder, PlateHolder)

  async def test_exact_setup_and_cleanup_transcript(self) -> None:
    device, io = make_device()

    async with device:
      io.events.append("body")

    assert io.events == EXACT_SETUP_EVENTS
    assert io.writes == [TEST_COMM, INIT_STATE, RESET]
    assert not device.is_active
    assert not io.is_active

  async def test_skip_reset_is_the_only_supported_lifespan_option(self) -> None:
    device, io = make_device()

    async with device.lifespan(skip_reset=True):
      pass

    assert io.writes == [TEST_COMM, INIT_STATE]

    with pytest.raises(TypeError, match="unexpected keyword argument"):
      async with device.lifespan(unknown=True):
        pytest.fail("invalid options reached body")

  @pytest.mark.parametrize("phase", SETUP_PHASES)
  async def test_failure_at_each_setup_phase_closes_ftdi(self, phase: str) -> None:
    device, io = make_device(fail_at=phase)

    with pytest.raises(Exception):
      async with device:
        pytest.fail("failed setup reached body")

    assert io.events[-2:] == [phase, "ftdi.exit"]
    assert not device.is_active
    assert not io.is_active

  @pytest.mark.parametrize("phase", SETUP_PHASES)
  async def test_cancellation_at_each_setup_phase_closes_ftdi(self, phase: str) -> None:
    blocked = anyio.Event()
    release = anyio.Event()
    device, io = make_device(block_at=phase, blocked=blocked, release=release)
    scope_ready = anyio.Event()
    scopes: list[anyio.CancelScope] = []

    async def enter_in_scope() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        scope_ready.set()
        try:
          await enter_device(device)
        except anyio.get_cancelled_exc_class():
          io.events.append("cancelled")
          raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(enter_in_scope)
      await scope_ready.wait()
      await blocked.wait()
      scopes[0].cancel()
      await anyio.lowlevel.checkpoint()
      release.set()

    assert io.events[-2:] == ["ftdi.exit", "cancelled"]
    assert not device.is_active
    assert not io.is_active

    reconciliation = COMMITTED_RECONCILIATION.get(phase)
    if reconciliation is None:
      assert io.events[-3] == phase
    else:
      assert io.events[-len(reconciliation) - 2 : -2] == reconciliation


class TestEL406Protocol(AnyioTestBase):
  async def test_ack_and_header_share_one_reply_deadline(self) -> None:
    """A slow ACK cannot restart the full response budget."""
    device, io = make_device()
    original_read = io.read

    async def delayed_read(num_bytes: int) -> bytes:
      await anyio.sleep(0.015)
      return await original_read(num_bytes)

    io.read = delayed_read  # type: ignore[method-assign]
    assert await device._exchange_framed_command(TEST_COMM, timeout=0.02) == b"\x06"

  async def test_framing_ack_and_query_read_strategy_are_preserved(self) -> None:
    device, io = make_device()
    query_header = bytes.fromhex("01020001010000020008ff")
    io.responses[0x100] = deque([b"\x06" + bytes.fromhex("0102000101000003000000") + b"abc"])

    async with device.lifespan(skip_reset=True):
      io.writes.clear()
      io.read_sizes.clear()
      result = await device._send_framed_query(0x100, data=b"xy")

    assert result == b"abc"
    assert io.writes == [query_header, b"xy"]
    assert io.read_sizes == [1, 11, 3]

  async def test_negative_acknowledgement_is_rejected(self) -> None:
    device, io = make_device()
    io.responses[0xD8] = deque([b"\x15"])

    async with device.lifespan(skip_reset=True):
      with pytest.raises(RuntimeError, match="rejected command"):
        await device._send_framed_command(bytes.fromhex("0102d800010000000024ff"))

  async def test_status_poll_parses_validity_state_and_status(self) -> None:
    device, io = make_device()
    io.responses[0x92] = deque([status_response(STATE_RUNNING, validity=7, status=9)])

    async with device.lifespan(skip_reset=True):
      poll = await device._poll_device_state()

    assert poll.validity == 7
    assert poll.state == STATE_RUNNING
    assert poll.status == 9
    assert poll.raw_response == status_response(STATE_RUNNING, validity=7, status=9)

  async def test_batch_markers_abort_and_cleanup_keep_exact_wire_order(self) -> None:
    device, io = make_device()
    device.set_plate(cor_96_wellplate_360uL_Fb("plate"))

    async with device.lifespan(skip_reset=True):
      io.writes.clear()
      async with device.batch():
        await device.abort()

    assert io.writes == [
      *PRE_BATCH,
      START_BATCH_HEADER,
      b"\x04",
      ABORT_HEADER,
      b"\x00",
      HOME_HEADER,
      b"\x04\x00",
      END_BATCH,
    ]


class TestEL406Cancellation(AnyioTestBase):
  async def test_cancelled_framed_query_consumes_response_before_next_query(self) -> None:
    blocked = anyio.Event()
    release = anyio.Event()
    device, io = make_device(
      block_at="read:0100:ack",
      blocked=blocked,
      release=release,
    )
    io.responses[0x100] = deque(
      [
        b"\x06" + bytes.fromhex("0102000101000003000000") + b"one",
        b"\x06" + bytes.fromhex("0102000101000003000000") + b"two",
      ]
    )
    scopes: list[anyio.CancelScope] = []
    events: list[str] = []

    async def first_query() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          await device._send_framed_query(0x100)
        except anyio.get_cancelled_exc_class():
          events.append("cancelled")
          raise

    async with device.lifespan(skip_reset=True):
      io.writes.clear()
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(first_query)
        await blocked.wait()
        scopes[0].cancel()
        await anyio.lowlevel.checkpoint()
        assert events == []
        io.block_at = None
        release.set()

      assert await device._send_framed_query(0x100) == b"two"

    assert events == ["cancelled"]

  async def test_step_cancellation_during_precommit_purge_writes_no_step(self) -> None:
    blocked = anyio.Event()
    release = anyio.Event()
    device, io = make_device(
      block_at="purge.rx:25",
      blocked=blocked,
      release=release,
    )
    io.responses[0x92] = deque([status_response(STATE_INITIAL)])
    scopes: list[anyio.CancelScope] = []

    async def run_step() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        await device._send_step_command(STEP, poll_interval=0)

    async with device.lifespan(skip_reset=True):
      io.writes.clear()
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(run_step)
        await blocked.wait()
        scopes[0].cancel()
        await anyio.lowlevel.checkpoint()
        release.set()

    assert io.writes == [STATUS_POLL]

  async def test_batch_cancellation_completes_home_and_end_marker(self) -> None:
    device, io = make_device()
    device.set_plate(cor_96_wellplate_360uL_Fb("plate"))
    body_started = anyio.Event()

    async def run_batch() -> None:
      async with device.batch():
        body_started.set()
        await anyio.sleep_forever()

    async with device.lifespan(skip_reset=True):
      io.writes.clear()
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(run_batch)
        await body_started.wait()
        task_group.cancel_scope.cancel()

    assert io.writes[-3:] == [HOME_HEADER, b"\x04\x00", END_BATCH]

  async def test_acknowledged_step_cancellation_aborts_and_waits_until_ready(self) -> None:
    device, io = make_device()
    acknowledged = anyio.Event()
    io.signal_after_response_command = 0xA2
    io.response_finished = acknowledged
    io.responses[0x92] = deque([status_response(STATE_INITIAL), status_response(STATE_STOPPED)])

    async def run_step() -> None:
      await device._send_step_command(STEP, poll_interval=0)

    async with device.lifespan(skip_reset=True):
      io.writes.clear()
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(run_step)
        await acknowledged.wait()
        task_group.cancel_scope.cancel()

    assert io.writes == [STATUS_POLL, STEP, ABORT_HEADER, b"\x00", STATUS_POLL]
