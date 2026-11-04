from typing import Any, Optional, cast
from unittest.mock import patch

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.io.ftdi import FTDI
from pylabrobot.legacy.centrifuge.vspin_backend import Access2Backend, VSpinBackend
from pylabrobot.legacy.machines.backend import MachineBackend
from pylabrobot.testing.concurrency import AnyioTestBase
from pylabrobot.testing.mock_io import MockIO

ACCESS2_SETUP_COMMANDS = [
  "11050003002000006bd4",
  "110500030014000072b1",
  "1105000300100000ae71",
  "110500070024040000008000be89",
  "11050007002404008000800063b1",
  "11050007002404000001800089b9",
  "1105000700240400800180005481",
  "110500070024040000024000c6bd",
  "1105000300400000f0bf",
  "1105000a004607000100000000020235bf",
  "1105000e00440b00000000000000007041020203c7",
]

VSPIN_SETUP_EVENTS = [
  "ftdi.enter",
  "configure",
  "cmd:aa002101ff21",
  "configure",
  "cmd:aa002101ff21",
  "configure",
  "cmd:aa002101ff21",
  "cmd:aa002101ff21",
  "cmd:aa01132034",
  "cmd:aa002102ff22",
  "cmd:aa02132035",
  "cmd:aa002103ff23",
  "cmd:aaff1a142d",
  "baudrate:57600",
  "rts:True",
  "dtr:True",
  "cmd:aa01121f32",
  *(["cmd:aa0220ff0f30"] * 8),
  "cmd:aa0220df0f10",
  "cmd:aa0220df0e0f",
  "cmd:aa0220df0c0d",
  "cmd:aa0220df0809",
  *(["cmd:aa0226000028"] * 4),
  "cmd:aa02120317",
  *(["cmd:aa0226200048", "cmd:aa0226000028"] * 5),
  "lock_door",
  "cmd:aa0226000028",
  "cmd:aa0117021a",
  "cmd:aa01e6c800b00496000f004b00a00f050007",
  "cmd:aa0117041c",
  "cmd:aa01170119",
  "cmd:aa010b0c",
  "cmd:aa010001",
  "cmd:aa01e605006400000000003200e80301006e",
  "cmd:aa0194b61283000012010000f3",
  "cmd:aa01192842",
  "status:89",
  "status:01",
  "cmd:aa0117021a",
  "cmd:aa01e6c800b00496000f004b00a00f050007",
  "cmd:aa0117041c",
  "cmd:aa01170119",
  "cmd:aa010b0c",
  "cmd:aa01e6c800b00496000f004b00a00f050007",
  "cmd:aa01d49700000000c3f52800d71a000049",
  "status:08",
  "status:09",
  "cmd:aa0117021a",
  "lock_door",
]


@pytest.mark.parametrize("backend_type", [Access2Backend, VSpinBackend])
def test_native_backend_uses_managed_lifecycle_methods(
  backend_type: type[MachineBackend],
) -> None:
  assert backend_type.setup is HasLifespan.setup
  assert backend_type.stop is HasLifespan.stop


class RecordingFTDI(MockIO):
  """Record FTDI lifecycle, configuration, and writes without opening hardware."""

  def __init__(
    self,
    events: list[str],
    *,
    fail_event: Optional[str] = None,
    entry_error: Optional[BaseException] = None,
    exit_error: Optional[BaseException] = None,
  ) -> None:
    super().__init__()
    self.events = events
    self.writes: list[bytes] = []
    self.fail_event = fail_event
    self.entry_error = entry_error
    self.exit_error = exit_error

  def _record(self, event: str) -> None:
    self.events.append(event)
    if event == self.fail_event:
      raise RuntimeError(f"failed at {event}")

  async def _enter_lifespan(
    self,
    stack: AsyncExitStackWithShielding,
    **kwargs: Any,
  ) -> None:
    if kwargs:
      raise TypeError("RecordingFTDI does not accept lifespan options")
    self._record("ftdi.enter")
    if self.entry_error is not None:
      raise self.entry_error
    stack.push_shielded_async_callback(self._exit_ftdi)

  async def _exit_ftdi(self) -> None:
    await anyio.lowlevel.checkpoint()
    self._record("ftdi.exit")
    if self.exit_error is not None:
      raise self.exit_error

  async def write(self, data: bytes, timeout: Optional[float] = None) -> int:  # type: ignore[override]
    del timeout
    await anyio.lowlevel.checkpoint()
    self.writes.append(data)
    self._record(f"write:{data.hex()}")
    return len(data)

  async def set_baudrate(self, baudrate: int) -> None:
    await anyio.lowlevel.checkpoint()
    self._record(f"baudrate:{baudrate}")

  async def set_rts(self, value: bool) -> None:
    await anyio.lowlevel.checkpoint()
    self._record(f"rts:{value}")

  async def set_dtr(self, value: bool) -> None:
    await anyio.lowlevel.checkpoint()
    self._record(f"dtr:{value}")

  async def set_latency_timer(self, latency: int) -> None:
    await anyio.lowlevel.checkpoint()
    self._record(f"latency_timer:{latency}")

  async def set_line_property(self, bits: int, stopbits: int, parity: int) -> None:
    await anyio.lowlevel.checkpoint()
    self._record(f"line_property:{bits}:{stopbits}:{parity}")

  async def set_flowctrl(self, flowctrl: int) -> None:
    await anyio.lowlevel.checkpoint()
    self._record(f"flowctrl:{flowctrl}")

  async def request_serial(self) -> str:
    await anyio.lowlevel.checkpoint()
    self._record("request_serial")
    return "recording-ftdi"


class RespondingAccess2Backend(Access2Backend):
  """Return a valid Access2 response without changing initialization behavior."""

  async def _read(self) -> bytes:
    return bytes.fromhex("1105")


class InvalidStatusAccess2Backend(Access2Backend):
  """Return an invalid Access2 status response."""

  async def _read(self) -> bytes:
    return b"invalid"


class RecordingVSpinBackend(VSpinBackend):
  """Record VSpin protocol phases while preserving the real initialization flow."""

  def __init__(
    self,
    events: list[str],
    *,
    configure_fail_calls: Optional[set[int]] = None,
    status_fail_call: Optional[int] = None,
    block_status_call: Optional[int] = None,
    status_started: Optional[anyio.Event] = None,
  ) -> None:
    super().__init__(device_id=None)
    self.events = events
    self.configure_fail_calls = configure_fail_calls or set()
    self.status_fail_call = status_fail_call
    self.block_status_call = block_status_call
    self.status_started = status_started
    self.configure_calls = 0
    self.status_calls = 0
    self._bucket_1_remainder = 0

  async def configure_and_initialize(self) -> None:
    await anyio.lowlevel.checkpoint()
    self.configure_calls += 1
    self.events.append("configure")
    if self.configure_calls in self.configure_fail_calls:
      raise RuntimeError(f"failed at configure {self.configure_calls}")

  async def _send_command(self, cmd: bytes, read_timeout: float = 0.2) -> bytes:
    del read_timeout
    await anyio.lowlevel.checkpoint()
    self.events.append(f"cmd:{cmd.hex()}")
    return b""

  async def lock_door(self) -> None:
    await anyio.lowlevel.checkpoint()
    self.events.append("lock_door")

  async def _get_positions_and_tachometer(self) -> VSpinBackend._StatusPositionTachometer:
    statuses = [0x89, 0x01, 0x08, 0x09]
    status = statuses[self.status_calls % len(statuses)]
    self.status_calls += 1
    self.events.append(f"status:{status:02x}")
    if self.status_calls == self.status_fail_call:
      raise RuntimeError(f"failed at status {self.status_calls}")
    if self.status_calls == self.block_status_call:
      if self.status_started is not None:
        self.status_started.set()
      await anyio.sleep_forever()
    result = VSpinBackend._StatusPositionTachometer()
    result.status = status
    return result


class RespondingVSpinBackend(VSpinBackend):
  """Use real VSpin command writes with a deterministic response."""

  async def _read_resp(self, timeout: float = 20) -> bytes:
    del timeout
    return b"\r"


class CancellableGoToPositionVSpinBackend(VSpinBackend):
  """Keep position non-terminal so cancellation must interrupt the polling sleep."""

  def __init__(self, commands: list[bytes], poll_started: anyio.Event) -> None:
    super().__init__(device_id=None)
    self.commands = commands
    self.poll_started = poll_started

  async def close_door(self) -> None:
    pass

  async def lock_door(self) -> None:
    pass

  async def _send_command(self, cmd: bytes, read_timeout: float = 0.2) -> bytes:
    del read_timeout
    self.commands.append(cmd)
    return b""

  async def get_position(self) -> int:
    self.poll_started.set()
    return 0


class CancellableSpinVSpinBackend(VSpinBackend):
  """Keep acceleration non-terminal so cancellation must interrupt the polling sleep."""

  def __init__(self, commands: list[bytes], poll_started: anyio.Event) -> None:
    super().__init__(device_id=None)
    self.commands = commands
    self.poll_started = poll_started

  async def get_door_open(self) -> bool:
    return False

  async def get_door_locked(self) -> bool:
    return True

  async def get_bucket_locked(self) -> bool:
    return False

  async def get_position(self) -> int:
    return 0

  async def get_tachometer(self) -> int:
    self.poll_started.set()
    return 0

  async def _send_command(self, cmd: bytes, read_timeout: float = 0.2) -> bytes:
    del read_timeout
    self.commands.append(cmd)
    return b""


def make_access2_backend(
  backend_type: type[Access2Backend] = RespondingAccess2Backend,
  **io_kwargs: Any,
) -> tuple[Access2Backend, RecordingFTDI]:
  with patch("pylabrobot.legacy.centrifuge.vspin_backend.FTDI"):
    backend = backend_type(device_id="ignored")
  io = RecordingFTDI([], **io_kwargs)
  backend.io = cast(FTDI, io)
  return backend, io


def make_vspin_backend(
  *,
  configure_fail_calls: Optional[set[int]] = None,
  status_fail_call: Optional[int] = None,
  block_status_call: Optional[int] = None,
  status_started: Optional[anyio.Event] = None,
  **io_kwargs: Any,
) -> tuple[RecordingVSpinBackend, RecordingFTDI]:
  events: list[str] = []
  with patch("pylabrobot.legacy.centrifuge.vspin_backend.FTDI"):
    backend = RecordingVSpinBackend(
      events,
      configure_fail_calls=configure_fail_calls,
      status_fail_call=status_fail_call,
      block_status_call=block_status_call,
      status_started=status_started,
    )
  io = RecordingFTDI(events, **io_kwargs)
  backend.io = cast(FTDI, io)
  return backend, io


class TestAccess2Backend(AnyioTestBase):
  async def test_exact_setup_transcript(self) -> None:
    backend, io = make_access2_backend()

    async with backend:
      assert backend.is_active

    assert io.events[0:2] == ["ftdi.enter", "baudrate:115384"]
    assert [data.hex() for data in io.writes] == ACCESS2_SETUP_COMMANDS
    assert io.events[-1] == "ftdi.exit"
    assert not backend.is_active

  @pytest.mark.parametrize("grip_steps", [1, 2, 3, 4])
  async def test_load_accepts_valid_grip_steps(self, grip_steps: int) -> None:
    backend, io = make_access2_backend()

    await backend.load(grip_steps=grip_steps)  # type: ignore[arg-type]

    grip_command = bytes.fromhex("1105000a00420700010000803f02008c64")
    assert io.writes.count(grip_command) == grip_steps

  @pytest.mark.parametrize("grip_steps", [0, 5, -1])
  async def test_load_rejects_invalid_grip_steps(self, grip_steps: int) -> None:
    backend, io = make_access2_backend()

    with pytest.raises(ValueError, match="grip_steps must be between 1 and 4"):
      await backend.load(grip_steps=grip_steps)  # type: ignore[arg-type]

    assert io.writes == []

  async def test_ftdi_open_failure_issues_no_commands_or_exit(self) -> None:
    backend, io = make_access2_backend(entry_error=RuntimeError("ftdi open failed"))

    with pytest.raises(RuntimeError, match="ftdi open failed"):
      async with backend:
        pytest.fail("failed entry must not reach the body")

    assert io.events == ["ftdi.enter"]
    assert io.writes == []
    assert not backend.is_active

  async def test_invalid_status_closes_ftdi_without_extra_command(self) -> None:
    backend, io = make_access2_backend(InvalidStatusAccess2Backend)

    with pytest.raises(RuntimeError, match="Failed to get status"):
      async with backend:
        pytest.fail("failed entry must not reach the body")

    assert [data.hex() for data in io.writes] == ACCESS2_SETUP_COMMANDS[:1]
    assert io.events[-1] == "ftdi.exit"
    assert not backend.is_active

  async def test_fifth_configuration_write_failure_closes_ftdi(self) -> None:
    failed_write = f"write:{ACCESS2_SETUP_COMMANDS[5]}"
    backend, io = make_access2_backend(fail_event=failed_write)

    with pytest.raises(RuntimeError, match=f"failed at {failed_write}"):
      async with backend:
        pytest.fail("failed entry must not reach the body")

    assert [data.hex() for data in io.writes] == ACCESS2_SETUP_COMMANDS[:6]
    assert io.events[-1] == "ftdi.exit"
    assert not backend.is_active

  async def test_cancellation_waiting_for_first_byte_completes_ftdi_exit(self) -> None:
    backend, io = make_access2_backend(Access2Backend)
    read_started = anyio.Event()

    async def block_read(*args: Any, **kwargs: Any) -> bytes:
      del args, kwargs
      read_started.set()
      await anyio.sleep_forever()
      raise AssertionError("unreachable")

    io.read.side_effect = block_read

    async def run_backend() -> None:
      try:
        async with backend:
          pytest.fail("cancelled entry must not reach the body")
      except anyio.get_cancelled_exc_class():
        io.events.append("cancelled")
        raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(run_backend)
      await read_started.wait()
      task_group.cancel_scope.cancel()

    assert io.events[-2:] == ["ftdi.exit", "cancelled"]
    assert not backend.is_active

  async def test_cancellation_from_body_completes_ftdi_exit(self) -> None:
    backend, io = make_access2_backend()
    body_started = anyio.Event()

    async def run_backend() -> None:
      try:
        async with backend:
          body_started.set()
          await anyio.sleep_forever()
      except anyio.get_cancelled_exc_class():
        io.events.append("cancelled")
        raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(run_backend)
      await body_started.wait()
      task_group.cancel_scope.cancel()

    assert io.events[-2:] == ["ftdi.exit", "cancelled"]
    assert not backend.is_active

  async def test_sequential_reuse_runs_two_complete_setup_transcripts(self) -> None:
    backend, io = make_access2_backend()

    async with backend:
      pass
    async with backend:
      pass

    assert [data.hex() for data in io.writes] == ACCESS2_SETUP_COMMANDS * 2
    assert io.events.count("ftdi.enter") == 2
    assert io.events.count("ftdi.exit") == 2
    assert not backend.is_active

  async def test_ftdi_close_failure_propagates_and_backend_becomes_inactive(self) -> None:
    backend, io = make_access2_backend(exit_error=RuntimeError("ftdi close failed"))

    with pytest.raises(RuntimeError, match="ftdi close failed"):
      async with backend:
        pass

    assert io.events[-1] == "ftdi.exit"
    assert not backend.is_active

  async def test_rejects_lifespan_options_before_ftdi_entry(self) -> None:
    backend, io = make_access2_backend()

    with pytest.raises(
      TypeError,
      match="unexpected keyword argument 'unknown'",
    ):
      async with backend.lifespan(unknown=True):
        pytest.fail("invalid options must not reach the body")

    assert io.events == []

  async def test_read_waits_for_first_byte_then_quiet_gap(self) -> None:
    backend, io = make_access2_backend(Access2Backend)

    async def wait_for_data(num_bytes: int) -> bytes:
      await anyio.sleep_forever()
      raise AssertionError("unreachable")

    io.read.side_effect = [b"A", b"B", wait_for_data]

    with anyio.fail_after(1):
      assert await backend._read() == b"AB"
    assert io.read.calls == [((1,), {})] * 3

  async def test_read_times_out_when_no_byte_arrives(self) -> None:
    backend, io = make_access2_backend(Access2Backend)
    backend.timeout = 0.01

    async def wait_for_data(num_bytes: int) -> bytes:
      await anyio.sleep_forever()
      raise AssertionError("unreachable")

    io.read.side_effect = wait_for_data

    with pytest.raises(
      TimeoutError,
      match="No data received within the specified timeout period",
    ):
      await backend._read()


class TestVSpinBackend(AnyioTestBase):
  async def test_exact_setup_transcript(self) -> None:
    backend, io = make_vspin_backend()

    async with backend:
      backend.events.append("body")

    assert backend.events == VSPIN_SETUP_EVENTS + ["body", "configure", "ftdi.exit"]
    assert not backend.is_active
    assert not io.is_active

  async def test_configure_and_initialize_writes_exact_packets(self) -> None:
    events: list[str] = []
    with patch("pylabrobot.legacy.centrifuge.vspin_backend.FTDI"):
      backend = RespondingVSpinBackend(device_id=None)
    io = RecordingFTDI(events)
    backend.io = cast(FTDI, io)

    await backend.configure_and_initialize()

    assert events[:4] == [
      "latency_timer:16",
      "line_property:8:1:0",
      "flowctrl:0",
      "baudrate:19200",
    ]
    assert len(io.writes) == 35
    assert io.writes[0] == b"\x00" * 20
    for index, packet in enumerate(io.writes[1:34]):
      assert packet == b"\xaa" + bytes([index, 0x0E, 0x0E + index]) + b"\x00" * 8
    assert io.writes[-1] == bytes.fromhex("aaff0f0e")

  async def test_ftdi_entry_failure_issues_no_reset_or_exit(self) -> None:
    backend, io = make_vspin_backend(entry_error=RuntimeError("ftdi open failed"))

    with pytest.raises(RuntimeError, match="ftdi open failed"):
      async with backend:
        pytest.fail("failed entry must not reach the body")

    assert backend.events == ["ftdi.enter"]
    assert not backend.is_active
    assert not io.is_active

  @pytest.mark.parametrize(
    ("failure", "message"),
    [
      ("first configure", "failed at configure 1"),
      ("set rts", "failed at rts:True"),
      ("first status poll", "failed at status 1"),
      ("second motion poll", "failed at status 4"),
    ],
  )
  async def test_partial_entry_failure_resets_then_closes_ftdi(
    self,
    failure: str,
    message: str,
  ) -> None:
    if failure == "first configure":
      backend, io = make_vspin_backend(configure_fail_calls={1})
    elif failure == "set rts":
      backend, io = make_vspin_backend(fail_event="rts:True")
    elif failure == "first status poll":
      backend, io = make_vspin_backend(status_fail_call=1)
    else:
      backend, io = make_vspin_backend(status_fail_call=4)

    with pytest.raises(RuntimeError, match=message):
      async with backend:
        pytest.fail("failed entry must not reach the body")

    assert backend.events[-2:] == ["configure", "ftdi.exit"]
    assert not backend.is_active
    assert not io.is_active

  async def test_exit_reset_failure_still_closes_ftdi(self) -> None:
    backend, io = make_vspin_backend(configure_fail_calls={4})

    with pytest.raises(RuntimeError, match="failed at configure 4"):
      async with backend:
        pass

    assert backend.events[-2:] == ["configure", "ftdi.exit"]
    assert not backend.is_active
    assert not io.is_active

  async def test_reset_and_ftdi_close_failures_remain_in_exception_chain(self) -> None:
    backend, io = make_vspin_backend(
      configure_fail_calls={4},
      exit_error=RuntimeError("ftdi close failed"),
    )

    with pytest.raises(RuntimeError, match="ftdi close failed") as error_info:
      async with backend:
        pass

    messages: list[str] = []
    error: Optional[BaseException] = error_info.value
    while error is not None:
      messages.append(str(error))
      error = error.__context__
    assert "failed at configure 4" in messages
    assert "ftdi close failed" in messages
    assert backend.events[-2:] == ["configure", "ftdi.exit"]
    assert not backend.is_active
    assert not io.is_active

  async def test_cancellation_during_entry_completes_reset_and_ftdi_exit(self) -> None:
    status_started = anyio.Event()
    backend, io = make_vspin_backend(block_status_call=1, status_started=status_started)

    async def run_backend() -> None:
      try:
        async with backend:
          pytest.fail("cancelled entry must not reach the body")
      except anyio.get_cancelled_exc_class():
        backend.events.append("cancelled")
        raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(run_backend)
      await status_started.wait()
      task_group.cancel_scope.cancel()

    assert backend.events[-3:] == ["configure", "ftdi.exit", "cancelled"]
    assert not backend.is_active
    assert not io.is_active

  async def test_cancellation_from_body_completes_reset_and_ftdi_exit(self) -> None:
    backend, io = make_vspin_backend()
    body_started = anyio.Event()

    async def run_backend() -> None:
      try:
        async with backend:
          body_started.set()
          await anyio.sleep_forever()
      except anyio.get_cancelled_exc_class():
        backend.events.append("cancelled")
        raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(run_backend)
      await body_started.wait()
      task_group.cancel_scope.cancel()

    assert backend.events[-3:] == ["configure", "ftdi.exit", "cancelled"]
    assert not backend.is_active
    assert not io.is_active

  async def test_sequential_reuse_runs_two_complete_setup_transcripts(self) -> None:
    backend, io = make_vspin_backend()

    async with backend:
      pass
    async with backend:
      pass

    cycle = VSPIN_SETUP_EVENTS + ["configure", "ftdi.exit"]
    assert backend.events == cycle + cycle
    assert not backend.is_active
    assert not io.is_active

  async def test_rejects_lifespan_options_before_ftdi_entry(self) -> None:
    backend, _ = make_vspin_backend()

    with pytest.raises(TypeError, match="unexpected keyword argument"):
      async with backend.lifespan(unknown=True):
        pytest.fail("invalid options must not reach the body")

    assert backend.events == []

  async def test_read_resp_returns_partial_data_at_deadline(self) -> None:
    with patch("pylabrobot.legacy.centrifuge.vspin_backend.FTDI"):
      backend = VSpinBackend(device_id=None)
    io = RecordingFTDI([])
    backend.io = cast(FTDI, io)
    read_count = 0

    async def read_then_block(*args: Any, **kwargs: Any) -> bytes:
      nonlocal read_count
      del args, kwargs
      read_count += 1
      if read_count == 1:
        return b"partial"
      await anyio.sleep_forever()
      raise AssertionError("unreachable")

    io.read.side_effect = read_then_block

    with anyio.fail_after(1):
      assert await backend._read_resp(timeout=0.01) == b"partial"

  async def test_go_to_position_poll_is_cancellable(self) -> None:
    commands: list[bytes] = []
    poll_started = anyio.Event()
    with patch("pylabrobot.legacy.centrifuge.vspin_backend.FTDI"):
      backend = CancellableGoToPositionVSpinBackend(commands, poll_started)
    backend.io = cast(FTDI, RecordingFTDI([]))
    cancelled = anyio.Event()

    async def run_operation() -> None:
      try:
        await backend.go_to_position(100)
      except anyio.get_cancelled_exc_class():
        cancelled.set()
        raise

    with anyio.fail_after(1):
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(run_operation)
        await poll_started.wait()
        command_count = len(commands)
        task_group.cancel_scope.cancel()

    assert cancelled.is_set()
    assert command_count == 8
    assert len(commands) == command_count

  async def test_spin_acceleration_poll_is_cancellable(self) -> None:
    commands: list[bytes] = []
    poll_started = anyio.Event()
    with patch("pylabrobot.legacy.centrifuge.vspin_backend.FTDI"):
      backend = CancellableSpinVSpinBackend(commands, poll_started)
    backend.io = cast(FTDI, RecordingFTDI([]))
    cancelled = anyio.Event()

    async def run_operation() -> None:
      try:
        await backend.spin(g=1, duration=1)
      except anyio.get_cancelled_exc_class():
        cancelled.set()
        raise

    with anyio.fail_after(1):
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(run_operation)
        await poll_started.wait()
        command_count = len(commands)
        task_group.cancel_scope.cancel()

    assert cancelled.is_set()
    assert command_count == 8
    assert len(commands) == command_count
