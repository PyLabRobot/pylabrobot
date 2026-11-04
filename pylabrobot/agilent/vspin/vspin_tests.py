from __future__ import annotations

import asyncio
import dataclasses
import unittest
from collections import deque
from contextlib import asynccontextmanager
from functools import partial
from typing import Any, AsyncIterator, Awaitable, Callable, Deque, Optional, Union, cast
from unittest.mock import ANY, AsyncMock, call, patch

import anyio
import pytest

from pylabrobot.agilent.vspin import _access2_protocol as protocol
from pylabrobot.agilent.vspin import _nmc
from pylabrobot.agilent.vspin import vspin as vspin_module
from pylabrobot.agilent.vspin._state import (
  ConnectionState,
  TransitionToken,
  VSpinActivity,
  VSpinHomingState,
  VSpinInitializationState,
)
from pylabrobot.agilent.vspin.access2 import Access2, Access2Driver
from pylabrobot.agilent.vspin.errors import CentrifugeDoorError
from pylabrobot.agilent.vspin.vspin import VSpin
from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.events import EventBus, PLREvent, resource_reference, use_event_bus
from pylabrobot.io.binary import Writer
from pylabrobot.io.ftdi import FTDI
from pylabrobot.resources import Coordinate, Resource, ResourceHolder
from pylabrobot.testing.concurrency import AnyioTestBase

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

ACCESS2_LOAD_COMMANDS = [
  "1105000a004607000100000000020235bf",
  "1105000e00440b000100004040000020410200a5cb",
  "1105000300500000b3dc",
  "1105000a00460700018fc2b540020023dc",
  "1105000e00440b000200004040000020410300ee00",
  "1105000a004607000100000000020015fd",
  "1105000e00440b0000000040400000204102007d82",
]

ACCESS2_UNLOAD_COMMANDS = [
  "1105000a004607000100000000020235bf",
  "1105000e00440b000200004040000020410200dd31",
  "1105000300500000b3dc",
  "1105000a00460700017b14b6400200d57a",
  "1105000e00440b00010000404000002041030096fa",
  "1105000a004607000100000000020015fd",
  "1105000e00440b00000000000000002041020056be",
]

ReadEffect = Union[bytes, Callable[[], Awaitable[bytes]]]

_SERVO_STATUS_MASK = (
  _nmc.SEND_POSITION | _nmc.SEND_ANALOG | _nmc.SEND_VELOCITY | _nmc.SEND_AUXILIARY | _nmc.SEND_HOME
)
_IO_STATUS_MASK = _nmc.SEND_INPUTS | _nmc.SEND_ANALOG_1


def _mark_vspin_ready(vspin: VSpin) -> None:
  """Put a mock-backed VSpin at the verified lifecycle boundary under test."""
  vspin._state = dataclasses.replace(
    vspin.state,
    connection=ConnectionState.CONNECTED,
    initialization=VSpinInitializationState.INITIALIZED,
    homing=VSpinHomingState.HOMED,
  )


def _nmc_response(status: int, data: bytes = b"") -> bytes:
  return bytes([status]) + data + bytes([(status + sum(data)) & 0xFF])


def _access2_reply(command: bytes, data: bytes = b"") -> bytes:
  """Build a complete controller reply for FTDI transaction tests."""
  inner = Writer().u8((command[0] + 1) & 0xFF).u16(len(data) + 1).u8(0).raw_bytes(data).finish()
  return protocol.build_ftdi_frame(inner)


def _servo_status_data(
  *,
  position: int = 0,
  velocity: int = 0,
  home_position: int = 0,
) -> bytes:
  return Writer().i32(position).u8(0).i16(velocity).u8(0).i32(home_position).finish()


def _io_status_data(*, inputs: int = 0) -> bytes:
  return Writer().u16(inputs).u8(0).finish()


def _servo_step(
  command: bytes,
  *,
  status: int = _nmc.STATUS_MOVE_DONE,
  position: int = 0,
  velocity: int = 0,
  home_position: int = 0,
) -> "_VSpinScriptStep":
  return _VSpinScriptStep(
    command,
    _nmc_response(
      status,
      _servo_status_data(
        position=position,
        velocity=velocity,
        home_position=home_position,
      ),
    ),
  )


def _io_step(
  command: bytes,
  *,
  status: int = _nmc.STATUS_MOVE_DONE,
  inputs: int = 0,
) -> "_VSpinScriptStep":
  return _VSpinScriptStep(command, _nmc_response(status, _io_status_data(inputs=inputs)))


def _empty_step(
  command: bytes,
  *,
  status: int = _nmc.STATUS_MOVE_DONE,
) -> "_VSpinScriptStep":
  return _VSpinScriptStep(command, _nmc_response(status))


@dataclasses.dataclass(frozen=True)
class _VSpinScriptStep:
  command: bytes
  response: bytes | None


class _ScriptedVSpinFTDI(HasLifespan):
  """Validate VSpin writes and replay partial NMC responses from a fixed script."""

  def __init__(self, steps: list[_VSpinScriptStep], max_read_size: int = 3):
    super().__init__()
    self._steps = deque(steps)
    self._response = bytearray()
    self._max_read_size = max_read_size
    self.setup_called = False
    self.setup_call_count = 0
    self.stopped = False
    self.stop_call_count = 0
    self.reconnect_calls = 0
    self.writes: list[bytes] = []
    self.latency_timers: list[int] = []
    self.line_properties: list[tuple[int, int, int]] = []
    self.flow_controls: list[int] = []
    self.baudrates: list[int] = []
    self.rts_levels: list[bool] = []
    self.dtr_levels: list[bool] = []
    self.rx_purge_count = 0

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    if kwargs:
      raise TypeError("Scripted FTDI does not accept options")
    self.setup_called = True
    self.setup_call_count += 1
    self.stopped = False
    stack.push_shielded_async_callback(self._close)

  async def _close(self) -> None:
    self.stopped = True
    self.stop_call_count += 1
    self._response.clear()

  async def reconnect(self) -> None:
    """Discard host-side response bytes while retaining this FTDI lifespan."""
    if not self.is_active:
      raise RuntimeError("FTDI reconnect requires an active lifespan")
    self.reconnect_calls += 1
    self._response.clear()

  async def set_latency_timer(self, latency: int) -> None:
    self.latency_timers.append(latency)

  async def set_line_property(self, bits: int, stopbits: int, parity: int) -> None:
    self.line_properties.append((bits, stopbits, parity))

  async def set_flowctrl(self, flowctrl: int) -> None:
    self.flow_controls.append(flowctrl)

  async def set_baudrate(self, baudrate: int) -> None:
    self.baudrates.append(baudrate)

  async def set_rts(self, level: bool) -> None:
    self.rts_levels.append(level)

  async def set_dtr(self, level: bool) -> None:
    self.dtr_levels.append(level)

  async def usb_purge_rx_buffer(self) -> None:
    self.rx_purge_count += 1

  async def write(self, data: bytes) -> int:
    if self._response:
      raise AssertionError(f"VSpin wrote before consuming response {self._response.hex()}")
    if not self._steps:
      raise AssertionError(f"Unexpected VSpin write: {data.hex()}")
    step = self._steps.popleft()
    if data != step.command:
      raise AssertionError(f"VSpin wrote {data.hex()}, expected {step.command.hex()}")
    self.writes.append(data)
    if step.response is not None:
      self._response.extend(step.response)
    return len(data)

  async def read(self, length: int) -> bytes:
    count = min(length, self._max_read_size, len(self._response))
    if count == 0:
      await anyio.sleep_forever()
      raise AssertionError("unreachable")
    chunk = bytes(self._response[:count])
    del self._response[:count]
    return chunk

  def assert_complete(self, test: unittest.TestCase) -> None:
    test.assertEqual(list(self._steps), [])
    test.assertEqual(bytes(self._response), b"")


class _TaskGroupVSpinFTDI(_ScriptedVSpinFTDI):
  """Represent the task group owned by the real FTDI lifespan."""

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    group = await stack.enter_task_group()
    stack.callback(group.cancel_scope.cancel)
    await super()._enter_lifespan(stack, **kwargs)


class _PausedVSpinFTDI(_ScriptedVSpinFTDI):
  """Pause after the first NMC write, before consuming its response."""

  def __init__(self, steps: list[_VSpinScriptStep], *, pause_on_write: int = 1) -> None:
    super().__init__(steps)
    self.pause_on_write = pause_on_write
    self.read_started = anyio.Event()
    self.release_read = anyio.Event()
    self.read_finished = anyio.Event()

  async def read(self, length: int) -> bytes:
    if len(self.writes) == self.pause_on_write and not self.read_started.is_set():
      self.read_started.set()
      await self.release_read.wait()
    result = await super().read(length)
    self.read_finished.set()
    return result


class _PausedPositionRecoveryFTDI(_PausedVSpinFTDI):
  """Hold the spinning-input reply while positioning cleanup owns FTDI."""

  def __init__(self, steps: list[_VSpinScriptStep]) -> None:
    super().__init__(steps, pause_on_write=2)
    self.sensor_started = anyio.Event()
    self.release_sensor = anyio.Event()

  async def read(self, length: int) -> bytes:
    if (
      self.writes
      and self.writes[-1] == _nmc.build_no_op(_nmc.PIC_IO_ADDRESS)
      and not self.sensor_started.is_set()
    ):
      self.sensor_started.set()
      await self.release_sensor.wait()
    return await super().read(length)


class TestScopedNMCTransactions(AnyioTestBase):
  @pytest.mark.parametrize("boundary", ["before_motor_off", "after_motor_off"])
  async def test_cancelled_final_servo_shutdown_confirms_stop_before_propagating(
    self, boundary: str
  ) -> None:
    """The vendor motor-off transition remains owned through both settling sleeps."""
    position = 100
    gain = _nmc.build_set_gain(_nmc.PIC_SERVO_ADDRESS, vspin_module._POSITION_GAINS)
    trajectory = _nmc.build_load_trajectory(
      _nmc.PIC_SERVO_ADDRESS,
      vspin_module._POSITION_TRAJECTORY_MODE,
      position=position,
      velocity=0x28F5C3,
      acceleration=0x1AD7,
    )
    motor_off = _nmc.build_stop_motor(_nmc.PIC_SERVO_ADDRESS, _nmc.MOTOR_OFF)
    servo_status = _nmc.build_no_op(_nmc.PIC_SERVO_ADDRESS)
    io_status = _nmc.build_no_op(_nmc.PIC_IO_ADDRESS)
    io = _PausedPositionRecoveryFTDI(
      [
        _servo_step(gain),
        _servo_step(trajectory, status=0, position=0),
        _servo_step(servo_status, position=position),
        _servo_step(motor_off, position=position),
        _servo_step(servo_status, position=position),
        _io_step(io_status, inputs=1 << _nmc.INPUT_SPINNING),
        _servo_step(servo_status, position=position),
        _io_step(io_status, inputs=0),
      ]
    )
    io.read_started.set()
    with patch("pylabrobot.agilent.vspin.vspin.FTDI"):
      device = VSpin(name="centrifuge")
    device.io = cast(FTDI, io)
    device._setup = AsyncMock()  # type: ignore[method-assign]
    device._close_door = AsyncMock()  # type: ignore[method-assign]
    device._lock_door = AsyncMock()  # type: ignore[method-assign]
    device._unlock_bucket = AsyncMock()  # type: ignore[method-assign]
    device._enable_amplifier_and_reset_servo_status = AsyncMock()  # type: ignore[method-assign]
    shutdown_started = anyio.Event()
    release_shutdown = anyio.Event()
    cancelled = anyio.Event()
    scopes: list[anyio.CancelScope] = []
    settle_calls = 0
    original_sleep = anyio.sleep

    async def pause_settle(delay: float) -> None:
      nonlocal settle_calls
      if delay == vspin_module._SERVO_TRANSITION_SETTLE_TIME:
        settle_calls += 1
        if settle_calls == (1 if boundary == "before_motor_off" else 2):
          shutdown_started.set()
          await release_shutdown.wait()
          return
      await original_sleep(delay)

    async def position_rotor() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          await device.go_to_position(position)
        except anyio.get_cancelled_exc_class():
          cancelled.set()
          raise

    with (
      patch("pylabrobot.agilent.vspin.vspin.anyio.sleep", side_effect=pause_settle),
      patch("pylabrobot.agilent.vspin.vspin._STATUS_POLL_INTERVAL", 0),
    ):
      async with device:
        _mark_vspin_ready(device)
        device._servo_status_mask = _SERVO_STATUS_MASK
        device._io_status_mask = _IO_STATUS_MASK
        device._at_bucket = device.bucket1
        async with anyio.create_task_group() as group:
          group.start_soon(position_rotor)
          await shutdown_started.wait()
          scopes[0].cancel()
          release_shutdown.set()
          with anyio.fail_after(2):
            await io.sensor_started.wait()
          assert io.writes[:4] == [gain, trajectory, servo_status, motor_off]
          assert io.is_active
          assert not cancelled.is_set()
          io.release_sensor.set()
        assert cancelled.is_set()
        assert io.writes == [
          gain,
          trajectory,
          servo_status,
          motor_off,
          servo_status,
          io_status,
          servo_status,
          io_status,
        ]
        assert device.state.recovery_required
        assert device.at_bucket is None
      assert io.stopped

  async def test_cancelled_position_confirms_servo_and_spinning_input_before_closure(self) -> None:
    """Motor-off alone cannot establish that the physical rotor has stopped."""
    position = 100
    gain = _nmc.build_set_gain(_nmc.PIC_SERVO_ADDRESS, vspin_module._POSITION_GAINS)
    trajectory = _nmc.build_load_trajectory(
      _nmc.PIC_SERVO_ADDRESS,
      vspin_module._POSITION_TRAJECTORY_MODE,
      position=position,
      velocity=0x28F5C3,
      acceleration=0x1AD7,
    )
    motor_off = _nmc.build_stop_motor(_nmc.PIC_SERVO_ADDRESS, _nmc.MOTOR_OFF)
    servo_status = _nmc.build_no_op(_nmc.PIC_SERVO_ADDRESS)
    io_status = _nmc.build_no_op(_nmc.PIC_IO_ADDRESS)
    io = _PausedPositionRecoveryFTDI(
      [
        _servo_step(gain),
        _servo_step(trajectory, status=0, position=0),
        _servo_step(motor_off),
        _servo_step(servo_status),
        _io_step(io_status, inputs=1 << _nmc.INPUT_SPINNING),
        _servo_step(servo_status),
        _io_step(io_status, inputs=0),
      ]
    )
    with patch("pylabrobot.agilent.vspin.vspin.FTDI"):
      device = VSpin(name="centrifuge")
    device.io = cast(FTDI, io)
    device._setup = AsyncMock()  # type: ignore[method-assign]
    device._close_door = AsyncMock()  # type: ignore[method-assign]
    device._lock_door = AsyncMock()  # type: ignore[method-assign]
    device._unlock_bucket = AsyncMock()  # type: ignore[method-assign]
    device._enable_amplifier_and_reset_servo_status = AsyncMock()  # type: ignore[method-assign]
    cancelled = anyio.Event()
    scopes: list[anyio.CancelScope] = []

    async def position_rotor() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          await device.go_to_position(position)
        except anyio.get_cancelled_exc_class():
          cancelled.set()
          raise

    with patch("pylabrobot.agilent.vspin.vspin._STATUS_POLL_INTERVAL", 0):
      async with device:
        _mark_vspin_ready(device)
        device._servo_status_mask = _SERVO_STATUS_MASK
        device._io_status_mask = _IO_STATUS_MASK
        device._at_bucket = device.bucket1
        async with anyio.create_task_group() as group:
          group.start_soon(position_rotor)
          await io.read_started.wait()
          scopes[0].cancel()
          io.release_read.set()
          with anyio.fail_after(2):
            await io.sensor_started.wait()
          assert io.writes[:5] == [gain, trajectory, motor_off, servo_status, io_status]
          assert io.is_active
          assert not cancelled.is_set()
          io.release_sensor.set()
        assert cancelled.is_set()
        assert io.writes == [
          gain,
          trajectory,
          motor_off,
          servo_status,
          io_status,
          servo_status,
          io_status,
        ]
        assert device.state.recovery_required
        assert device.at_bucket is None
      assert io.stopped

  @pytest.mark.parametrize("direction", ["load", "unload"])
  async def test_confirmed_transfer_publishes_plate_before_releasing_vspin(
    self, direction: str
  ) -> None:
    """A concurrent rotor operation cannot see an old plate model after loader success."""
    with (
      patch("pylabrobot.agilent.vspin.vspin.FTDI"),
      patch("pylabrobot.agilent.vspin.access2.FTDI"),
    ):
      vspin = VSpin(name="centrifuge")
      loader = Access2(name="loader", device_id="test", vspin=vspin)
    vspin.io = cast(FTDI, _ScriptedVSpinFTDI([]))
    loader.driver.io = cast(FTDI, _ScriptedVSpinFTDI([]))
    vspin._setup = AsyncMock()  # type: ignore[method-assign]
    loader.driver._initialize = AsyncMock()  # type: ignore[method-assign]
    vspin.request_door_open = AsyncMock(return_value=True)  # type: ignore[method-assign]
    vspin.request_bucket_locked = AsyncMock(return_value=True)  # type: ignore[method-assign]
    vspin._request_rotor_safe_to_access = AsyncMock(return_value=True)  # type: ignore[method-assign]
    loader.driver.load = AsyncMock()  # type: ignore[method-assign]
    loader.driver.unload = AsyncMock()  # type: ignore[method-assign]
    plate = Resource("plate", size_x=1, size_y=1, size_z=1)
    source = loader if direction == "load" else vspin.bucket1
    destination = vspin.bucket1 if direction == "load" else loader
    source.assign_child_resource(plate, location=Coordinate.zero())
    reserve_transfer = vspin.reserve_transfer

    @asynccontextmanager
    async def verify_publication(bucket: ResourceHolder) -> AsyncIterator[TransitionToken]:
      async with reserve_transfer(bucket) as token:
        try:
          yield token
        finally:
          assert vspin._command_lock.locked()
          assert plate.parent is destination

    vspin.reserve_transfer = verify_publication  # type: ignore[method-assign]
    async with vspin, loader:
      _mark_vspin_ready(vspin)
      vspin._at_bucket = vspin.bucket1
      await (loader.load() if direction == "load" else loader.unload())
      assert plate.parent is destination

  async def test_loader_transfer_borrows_active_vspin_and_reserves_its_operation(self) -> None:
    """The loader cannot exit or reposition the VSpin it borrows mid-transfer."""
    with (
      patch("pylabrobot.agilent.vspin.vspin.FTDI"),
      patch("pylabrobot.agilent.vspin.access2.FTDI"),
    ):
      vspin = VSpin(name="centrifuge")
      loader = Access2(name="loader", device_id="test", vspin=vspin)
    vspin.io = cast(FTDI, _ScriptedVSpinFTDI([]))
    loader.driver.io = cast(FTDI, _ScriptedVSpinFTDI([]))
    vspin._setup = AsyncMock()  # type: ignore[method-assign]
    loader.driver._initialize = AsyncMock()  # type: ignore[method-assign]
    vspin.request_door_open = AsyncMock(return_value=True)  # type: ignore[method-assign]
    vspin.request_bucket_locked = AsyncMock(return_value=True)  # type: ignore[method-assign]
    vspin._request_rotor_safe_to_access = AsyncMock(return_value=True)  # type: ignore[method-assign]
    started = anyio.Event()
    release = anyio.Event()

    async def transfer(*args: Any, **kwargs: Any) -> None:
      del args, kwargs
      assert vspin._command_lock.locked()
      started.set()
      await release.wait()

    loader.driver.load = transfer  # type: ignore[method-assign]
    plate = Resource("plate", size_x=1, size_y=1, size_z=1)
    loader.assign_child_resource(plate, location=Coordinate.zero())
    vspin._at_bucket = vspin.bucket1

    with pytest.raises(RuntimeError, match="active lifespans"):
      await loader.load()
    async with vspin:
      _mark_vspin_ready(vspin)
      vspin._at_bucket = vspin.bucket1
      async with loader:
        async with anyio.create_task_group() as group:
          group.start_soon(loader.load)
          await started.wait()
          assert plate.parent is loader
          with pytest.raises(RuntimeError, match="another VSpin command"):
            await vspin.go_to_bucket2()
          release.set()
        assert plate.parent is vspin.bucket1
      assert vspin.is_active
      assert not loader.driver.is_active
      assert vspin.io.is_active

  async def test_prewrite_cancellation_sends_no_nmc_frame(self) -> None:
    """A pending cancellation must not dispatch even a no-reply command."""
    with patch("pylabrobot.agilent.vspin.vspin.FTDI"):
      device = VSpin(name="centrifuge")
    io = _ScriptedVSpinFTDI([])
    device.io = cast(FTDI, io)
    async with io:
      with anyio.CancelScope() as scope:
        scope.cancel()
        await device._send_nmc(_nmc.build_hard_reset(), expect_response=False)
      assert io.writes == []

  async def test_reopen_retains_golden_reset_sequence_on_both_backends(self) -> None:
    """FTDI reconnection discards stale NMC replies without changing reset bytes."""
    with patch("pylabrobot.agilent.vspin.vspin.FTDI"):
      device = VSpin(name="centrifuge")
    io = _ScriptedVSpinFTDI(TestVSpinScriptedFTDI._setup_steps())
    device.io = cast(FTDI, io)
    with (
      patch("pylabrobot.agilent.vspin.vspin._NETWORK_INPUT_QUIET_TIME", 0),
      patch("pylabrobot.agilent.vspin.vspin._SERVO_TRANSITION_SETTLE_TIME", 0),
      patch("pylabrobot.agilent.vspin.vspin._NMC_RESET_SETTLE_TIME", 0),
      patch("pylabrobot.agilent.vspin.vspin._NMC_BAUD_SETTLE_TIME", 0),
      patch("pylabrobot.agilent.vspin.vspin._STATUS_POLL_INTERVAL", 0),
      patch("pylabrobot.agilent.vspin.vspin._NETWORK_PROBE_TIMEOUT", 0),
    ):
      async with device:
        assert io.is_active
        assert io.setup_call_count == 1
        assert io.reconnect_calls == 3
        assert io.stop_call_count == 0
        assert device.state.connection is ConnectionState.CONNECTED
        with pytest.raises(RuntimeError, match="active lifespan"):
          async with device:
            pytest.fail("nested entry reached body")
      assert io.stop_call_count == 1
      assert not io.is_active
      assert device.state.connection is ConnectionState.DISCONNECTED
      assert list(io._steps) == []
      assert bytes(io._response) == b""

  async def test_reopen_preserves_ftdi_task_group_scope(self) -> None:
    with patch("pylabrobot.agilent.vspin.vspin.FTDI"):
      device = VSpin(name="centrifuge")
    io = _TaskGroupVSpinFTDI([])
    device.io = cast(FTDI, io)
    device._setup = AsyncMock()  # type: ignore[method-assign]

    async with device:
      with anyio.CancelScope():
        await device._reopen_ftdi(57600)
      assert io.is_active
      assert io.baudrates == [57600]
    assert io.reconnect_calls == 1
    assert io.stop_call_count == 1
    assert not io.is_active

  async def test_normal_exit_closes_ftdi_task_group_in_scope_order(self) -> None:
    with patch("pylabrobot.agilent.vspin.vspin.FTDI"):
      device = VSpin(name="centrifuge")
    io = _TaskGroupVSpinFTDI([])
    device.io = cast(FTDI, io)
    device._setup = AsyncMock()  # type: ignore[method-assign]

    async with device:
      assert io.is_active
    assert not io.is_active
    assert io.stop_call_count == 1

  async def test_vspin_scoped_events_bracket_only_owned_ftdi(self) -> None:
    """Normal exit emits one setup/stop pair, regardless of body activity."""
    with patch("pylabrobot.agilent.vspin.vspin.FTDI"):
      device = VSpin(name="centrifuge")
    io = _ScriptedVSpinFTDI([])
    device.io = cast(FTDI, io)
    device._setup = AsyncMock()  # type: ignore[method-assign]
    events: list[PLREvent] = []
    bus = EventBus()
    bus.subscribe(events.append)

    with use_event_bus(bus):
      async with device:
        assert io.is_active

    assert not io.is_active
    assert device.state.connection is ConnectionState.DISCONNECTED
    assert [event.name for event in events] == [
      "centrifuge.setup.started",
      "centrifuge.setup.completed",
      "centrifuge.stop.started",
      "centrifuge.stop.completed",
    ]
    for start, end in ((events[0], events[1]), (events[2], events[3])):
      assert start.context["operation_id"] == end.context["operation_id"]
      assert start.data["device"]["name"] == "centrifuge"

  async def test_cancelled_post_write_response_finishes_before_next_command(self) -> None:
    """A cancelled writer cannot release the NMC lock with a reply still pending."""
    first = _nmc.build_no_op(_nmc.PIC_SERVO_ADDRESS)
    second = _nmc.build_no_op(_nmc.PIC_IO_ADDRESS)
    io = _PausedVSpinFTDI([_empty_step(first), _empty_step(second)])
    with patch("pylabrobot.agilent.vspin.vspin.FTDI"):
      device = VSpin(name="centrifuge")
    device.io = cast(FTDI, io)
    cancelled = anyio.Event()

    async def first_command() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          await device._send_nmc(first)
        except anyio.get_cancelled_exc_class():
          cancelled.set()
          raise

    scopes: list[anyio.CancelScope] = []
    async with io:
      async with anyio.create_task_group() as group:
        group.start_soon(first_command)
        await io.read_started.wait()
        scopes[0].cancel()
        group.start_soon(device._send_nmc, second)
        await anyio.lowlevel.checkpoint()
        assert io.writes == [first]
        assert not cancelled.is_set()
        io.release_read.set()
      assert io.read_finished.is_set()
      assert cancelled.is_set()
      assert io.writes == [first, second]


class RecordingFTDI(HasLifespan):
  """Hardware-free FTDI lifespan with captured protocol effects."""

  def __init__(
    self,
    events: Optional[list[str]] = None,
    reads: Optional[list[ReadEffect]] = None,
    *,
    entry_error: Optional[BaseException] = None,
    exit_error: Optional[BaseException] = None,
  ) -> None:
    super().__init__()
    self.events = events if events is not None else []
    self.reads: Deque[ReadEffect] = deque(reads or [])
    self.writes: list[bytes] = []
    self.entry_error = entry_error
    self.exit_error = exit_error

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    if kwargs:
      raise TypeError("RecordingFTDI does not accept lifespan options")
    self.events.append("ftdi.enter")
    stack.push_shielded_async_callback(self._close)
    if self.entry_error is not None:
      raise self.entry_error

  async def _close(self) -> None:
    await anyio.lowlevel.checkpoint()
    self.events.append("ftdi.exit")
    if self.exit_error is not None:
      raise self.exit_error

  async def write(self, data: bytes, timeout: Optional[float] = None) -> int:
    del timeout
    await anyio.lowlevel.checkpoint()
    self.writes.append(data)
    self.events.append(f"write:{data.hex()}")
    return len(data)

  async def read(self, num_bytes: int, timeout: Optional[float] = None) -> bytes:
    del num_bytes, timeout
    await anyio.lowlevel.checkpoint()
    if not self.reads:
      return b""
    effect = self.reads.popleft()
    return await effect() if callable(effect) else effect

  async def set_baudrate(self, baudrate: int) -> None:
    await anyio.lowlevel.checkpoint()
    self.events.append(f"baudrate:{baudrate}")

  async def set_rts(self, value: bool) -> None:
    await anyio.lowlevel.checkpoint()
    self.events.append(f"rts:{value}")

  async def set_dtr(self, value: bool) -> None:
    await anyio.lowlevel.checkpoint()
    self.events.append(f"dtr:{value}")

  async def set_latency_timer(self, latency: int) -> None:
    await anyio.lowlevel.checkpoint()
    self.events.append(f"latency:{latency}")

  async def set_line_property(self, bits: int, stopbits: int, parity: int) -> None:
    await anyio.lowlevel.checkpoint()
    self.events.append(f"line:{bits}:{stopbits}:{parity}")

  async def set_flowctrl(self, flowctrl: int) -> None:
    await anyio.lowlevel.checkpoint()
    self.events.append(f"flow:{flowctrl}")

  async def request_serial(self) -> str:
    return "recording-ftdi"


class RecordingVSpin(VSpin):
  """Exercise lifecycle ownership without running the separate NMC transcript."""

  def __init__(
    self,
    events: list[str],
    *,
    configure_fail_call: Optional[int] = None,
    block_status_call: Optional[int] = None,
    status_started: Optional[anyio.Event] = None,
  ) -> None:
    with patch("pylabrobot.agilent.vspin.vspin.FTDI"):
      super().__init__(name="centrifuge")
    self.events = events
    self.configure_fail_call = configure_fail_call
    self.block_status_call = block_status_call
    self.status_started = status_started
    self.setup_calls = 0

  async def _setup(self, *, transition: TransitionToken) -> None:
    del transition
    await anyio.lowlevel.checkpoint()
    self.setup_calls += 1
    self.events.append("setup")
    if self.setup_calls == self.configure_fail_call:
      raise RuntimeError(f"failed at setup {self.setup_calls}")
    if self.setup_calls == self.block_status_call:
      if self.status_started is not None:
        self.status_started.set()
      await anyio.sleep_forever()


def make_vspin(**kwargs: Any) -> tuple[RecordingVSpin, RecordingFTDI]:
  events: list[str] = []
  device = RecordingVSpin(events, **kwargs)
  io = RecordingFTDI(events)
  device.io = cast(FTDI, io)
  return device, io


def make_access2_driver(
  *,
  entry_error: Optional[BaseException] = None,
  exit_error: Optional[BaseException] = None,
) -> tuple[Access2Driver, RecordingFTDI]:
  with patch("pylabrobot.agilent.vspin.access2.FTDI"):
    driver = Access2Driver(device_id="ignored", timeout=1)
  driver._initialize = AsyncMock()  # type: ignore[method-assign]
  io = RecordingFTDI(entry_error=entry_error, exit_error=exit_error)
  driver.io = cast(FTDI, io)
  return driver, io


class TestVSpinLifecycle(AnyioTestBase):
  def test_uses_managed_lifecycle_methods(self) -> None:
    assert VSpin.__aenter__ is HasLifespan.__aenter__
    assert VSpin.__aexit__ is HasLifespan.__aexit__
    assert VSpin.setup is HasLifespan.setup
    assert VSpin.stop is HasLifespan.stop

  async def test_exact_initialization_and_cleanup_transcript(self) -> None:
    device, io = make_vspin()

    async with device:
      device.events.append("body")

    assert device.events == ["ftdi.enter", "setup", "body", "ftdi.exit"]
    assert not device.is_active
    assert not io.is_active

  async def test_ftdi_entry_failure_issues_no_initialization(self) -> None:
    device, _ = make_vspin()
    failure = RuntimeError("ftdi entry failed")
    device.io = cast(FTDI, RecordingFTDI(device.events, entry_error=failure))

    with pytest.raises(RuntimeError) as error_info:
      async with device:
        pytest.fail("failed entry reached body")

    assert error_info.value is failure
    assert device.events == ["ftdi.enter", "ftdi.exit"]
    assert not device.is_active

  async def test_partial_entry_failure_resets_before_ftdi_close(self) -> None:
    device, _ = make_vspin(configure_fail_call=1)

    with pytest.raises(RuntimeError, match="failed at setup 1"):
      async with device:
        pytest.fail("failed entry reached body")

    assert device.events == ["ftdi.enter", "setup", "ftdi.exit"]
    assert device.state.connection is ConnectionState.DISCONNECTED

  async def test_entry_cancellation_completes_reset_and_ftdi_close(self) -> None:
    status_started = anyio.Event()
    device, _ = make_vspin(block_status_call=1, status_started=status_started)

    async def enter() -> None:
      try:
        async with device:
          pytest.fail("cancelled entry reached body")
      except anyio.get_cancelled_exc_class():
        device.events.append("cancelled")
        raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(enter)
      await status_started.wait()
      task_group.cancel_scope.cancel()

    assert device.events == ["ftdi.enter", "setup", "ftdi.exit", "cancelled"]

  async def test_body_cancellation_completes_reset_and_ftdi_close(self) -> None:
    device, _ = make_vspin()
    body_started = anyio.Event()

    async def use_device() -> None:
      try:
        async with device:
          body_started.set()
          await anyio.sleep_forever()
      except anyio.get_cancelled_exc_class():
        device.events.append("cancelled")
        raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(use_device)
      await body_started.wait()
      task_group.cancel_scope.cancel()

    assert device.events == ["ftdi.enter", "setup", "ftdi.exit", "cancelled"]

  async def test_sequential_reuse_runs_two_complete_transcripts(self) -> None:
    device, _ = make_vspin()

    async with device:
      pass
    async with device:
      pass

    cycle = ["ftdi.enter", "setup", "ftdi.exit"]
    assert device.events == cycle + cycle

  async def test_rejects_lifespan_options_before_ftdi_entry(self) -> None:
    device, _ = make_vspin()

    with pytest.raises(TypeError, match="unexpected keyword argument"):
      async with device.lifespan(unknown=True):
        pytest.fail("invalid options reached body")

    assert device.events == []


class TestAccess2DriverLifecycle(AnyioTestBase):
  def test_uses_managed_lifecycle_methods(self) -> None:
    assert Access2Driver.__aenter__ is HasLifespan.__aenter__
    assert Access2Driver.__aexit__ is HasLifespan.__aexit__
    assert Access2Driver.setup is HasLifespan.setup
    assert Access2Driver.stop is HasLifespan.stop

  async def test_exact_setup_transcript_and_ftdi_ownership(self) -> None:
    driver, io = make_access2_driver()

    async with driver:
      assert driver.is_active

    assert io.events[0:2] == ["ftdi.enter", "baudrate:115384"]
    assert io.writes == []
    assert io.events[-1] == "ftdi.exit"

  async def test_invalid_status_closes_ftdi(self) -> None:
    driver, io = make_access2_driver()

    async def read_status(*, transition: TransitionToken) -> None:
      del transition
      await driver.request_status()

    driver._initialize = read_status  # type: ignore[method-assign]
    io.reads.append(b"invalid header")

    with pytest.raises(protocol.Access2ProtocolError, match="header has"):
      async with driver:
        pytest.fail("invalid status reached body")

    assert io.writes == [protocol.build_ftdi_frame(protocol.build_get_status())]
    assert io.events[-1] == "ftdi.exit"

  async def test_body_cancellation_closes_ftdi_before_propagating(self) -> None:
    driver, io = make_access2_driver()
    body_started = anyio.Event()

    async def use_driver() -> None:
      try:
        async with driver:
          body_started.set()
          await anyio.sleep_forever()
      except anyio.get_cancelled_exc_class():
        io.events.append("cancelled")
        raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(use_driver)
      await body_started.wait()
      task_group.cancel_scope.cancel()

    assert io.events[-2:] == ["ftdi.exit", "cancelled"]

  async def test_sequential_reuse_runs_setup_twice(self) -> None:
    driver, io = make_access2_driver()

    async with driver:
      pass
    async with driver:
      pass

    assert io.writes == []
    assert io.events.count("ftdi.enter") == 2
    assert io.events.count("ftdi.exit") == 2

  async def test_close_failure_propagates(self) -> None:
    failure = RuntimeError("ftdi close failed")
    driver, _ = make_access2_driver(exit_error=failure)

    with pytest.raises(RuntimeError) as error_info:
      async with driver:
        pass

    assert error_info.value is failure
    assert not driver.is_active

  async def test_rejects_lifespan_options_before_ftdi_entry(self) -> None:
    driver, io = make_access2_driver()

    with pytest.raises(TypeError, match="unexpected keyword argument"):
      async with driver.lifespan(unknown=True):
        pytest.fail("invalid options reached body")

    assert io.events == []


class TestCommandCancellation(AnyioTestBase):
  async def test_vspin_read_reports_partial_fixed_length_response_at_deadline(self) -> None:
    with patch("pylabrobot.agilent.vspin.vspin.FTDI"):
      device = VSpin(name="centrifuge")
    reads = 0

    async def read_then_block() -> bytes:
      nonlocal reads
      reads += 1
      if reads == 1:
        return b"partial"
      await anyio.sleep_forever()
      raise AssertionError("unreachable")

    device.io = cast(FTDI, RecordingFTDI(reads=[read_then_block, read_then_block]))

    with anyio.fail_after(1):
      with pytest.raises(TimeoutError, match="7 of 8 expected response bytes"):
        await device._read_exact_response(length=8, timeout=0.01)

  @pytest.mark.parametrize("driver_type", [VSpin, Access2Driver])
  async def test_pending_cancellation_writes_no_command(self, driver_type: type[Any]) -> None:
    with (
      patch("pylabrobot.agilent.vspin.vspin.FTDI"),
      patch("pylabrobot.agilent.vspin.access2.FTDI"),
    ):
      driver = (
        driver_type(name="centrifuge") if driver_type is VSpin else driver_type(device_id="ignored")
      )
    io = RecordingFTDI()
    driver.io = cast(FTDI, io)

    with anyio.CancelScope() as scope:
      scope.cancel()
      if isinstance(driver, VSpin):
        await driver._send_nmc(_nmc.build_no_op(_nmc.PIC_SERVO_ADDRESS))
      else:
        await driver.send_command(protocol.build_ping())

    assert io.writes == []

  @pytest.mark.parametrize("driver_type", [VSpin, Access2Driver])
  async def test_committed_write_reconciles_response_before_cancellation(
    self, driver_type: type[Any]
  ) -> None:
    read_started = anyio.Event()
    release = anyio.Event()
    scopes: list[anyio.CancelScope] = []
    events: list[str] = []

    async def blocked_response() -> bytes:
      read_started.set()
      await release.wait()
      events.append("response")
      if driver_type is VSpin:
        return _nmc_response(_nmc.STATUS_MOVE_DONE)
      return _access2_reply(protocol.build_ping())[:5]

    with (
      patch("pylabrobot.agilent.vspin.vspin.FTDI"),
      patch("pylabrobot.agilent.vspin.access2.FTDI"),
    ):
      driver = (
        driver_type(name="centrifuge") if driver_type is VSpin else driver_type(device_id="ignored")
      )
    reads: list[ReadEffect] = [blocked_response]
    if driver_type is Access2Driver:
      reads.append(_access2_reply(protocol.build_ping())[5:])
    io = RecordingFTDI(events=events, reads=reads)
    driver.io = cast(FTDI, io)

    async def command() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          if isinstance(driver, VSpin):
            await driver._send_nmc(_nmc.build_no_op(_nmc.PIC_SERVO_ADDRESS))
          else:
            await driver.send_command(protocol.build_ping())
        except anyio.get_cancelled_exc_class():
          events.append("cancelled")
          raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(command)
      await read_started.wait()
      scopes[0].cancel()
      await anyio.lowlevel.checkpoint()
      assert "cancelled" not in events
      release.set()

    assert events.index("response") < events.index("cancelled")

  async def test_access2_commands_are_serialized_as_transactions(self) -> None:
    first_read_started = anyio.Event()
    release = anyio.Event()

    async def first_response() -> bytes:
      first_read_started.set()
      await release.wait()
      return _access2_reply(protocol.build_ping())[:5]

    with patch("pylabrobot.agilent.vspin.access2.FTDI"):
      driver = Access2Driver(device_id="ignored")
    first = protocol.build_ping()
    second = protocol.build_get_status()
    io = RecordingFTDI(
      reads=[
        first_response,
        _access2_reply(first)[5:],
        _access2_reply(second, b"\x03\x00\x00\x00")[:5],
        _access2_reply(second, b"\x03\x00\x00\x00")[5:],
      ]
    )
    driver.io = cast(FTDI, io)

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(driver.send_command, first)
      await first_read_started.wait()
      task_group.start_soon(driver.send_command, second)
      await anyio.lowlevel.checkpoint()
      assert io.writes == [protocol.build_ftdi_frame(first)]
      release.set()

    assert io.writes == [protocol.build_ftdi_frame(first), protocol.build_ftdi_frame(second)]

  async def test_vspin_commands_are_serialized_with_correlated_replies(self) -> None:
    first_read_started = anyio.Event()
    release = anyio.Event()
    results: dict[str, _nmc.NMCResponse] = {}

    async def first_response() -> bytes:
      first_read_started.set()
      await release.wait()
      return _nmc_response(_nmc.STATUS_MOVE_DONE)

    with patch("pylabrobot.agilent.vspin.vspin.FTDI"):
      device = VSpin(name="centrifuge")
    io = RecordingFTDI(reads=[first_response, _nmc_response(_nmc.STATUS_MOVE_DONE)])
    device.io = cast(FTDI, io)

    async def command(name: str, payload: bytes) -> None:
      results[name] = await device._send_nmc(payload)

    async with anyio.create_task_group() as task_group:
      first = _nmc.build_no_op(_nmc.PIC_SERVO_ADDRESS)
      second = _nmc.build_no_op(_nmc.PIC_IO_ADDRESS)
      task_group.start_soon(command, "first", first)
      await first_read_started.wait()
      task_group.start_soon(command, "second", second)
      await anyio.sleep(0.01)
      assert io.writes == [first]
      release.set()

    assert io.writes == [first, second]
    assert results == {
      "first": _nmc.NMCResponse(status=_nmc.STATUS_MOVE_DONE, data=b""),
      "second": _nmc.NMCResponse(status=_nmc.STATUS_MOVE_DONE, data=b""),
    }

  async def test_load_cancellation_reconciles_complete_command_sequence(self) -> None:
    await self._assert_access2_motion_reconciles_after_cancellation(
      operation="load", expected_commands=ACCESS2_LOAD_COMMANDS
    )

  async def test_unload_cancellation_reconciles_complete_command_sequence(self) -> None:
    await self._assert_access2_motion_reconciles_after_cancellation(
      operation="unload", expected_commands=ACCESS2_UNLOAD_COMMANDS
    )

  async def _assert_access2_motion_reconciles_after_cancellation(
    self, *, operation: str, expected_commands: list[str]
  ) -> None:
    """Cancel source travel after dispatch; reconcile status before stopping the transfer."""
    blocked_response_started = anyio.Event()
    release_response = anyio.Event()
    scopes: list[anyio.CancelScope] = []
    events: list[str] = []
    status = Writer().u8(0x03).u8(0).u8(0).u8(0).finish()
    full_status = Writer().u8(0x03).u8(0).u8(1).f32(0).u8(1).f32(100).u8(1).f32(20).finish()
    commands = [
      ACCESS2_SETUP_COMMANDS[0],
      expected_commands[0],
      ACCESS2_SETUP_COMMANDS[0],
      expected_commands[1],
      ACCESS2_SETUP_COMMANDS[0],
    ]
    reads: list[ReadEffect] = []
    for index, command_hex in enumerate(commands):
      command = bytes.fromhex(command_hex)[5:-2]
      data = status if index == 0 else full_status if index in (2, 4) else b""
      reply = _access2_reply(command, data)

      async def header(response_index: int = index, frame: bytes = reply) -> bytes:
        if response_index == 3:
          blocked_response_started.set()
          await release_response.wait()
        events.append(f"reply:{response_index}")
        return frame[:5]

      reads.extend([header, reply[5:]])

    with patch("pylabrobot.agilent.vspin.access2.FTDI"):
      driver = Access2Driver(device_id="ignored")
    driver._state = dataclasses.replace(
      driver.state, connection=ConnectionState.CONNECTED, last_teachpoint=protocol.TEACHPOINT_PARK
    )
    io = RecordingFTDI(events=events, reads=reads)
    driver.io = cast(FTDI, io)

    async def move() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          if operation == "load":
            await driver.load()
          else:
            await driver.unload()
        except anyio.get_cancelled_exc_class():
          events.append("cancelled")
          raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(move)
      await blocked_response_started.wait()
      assert [command.hex() for command in io.writes] == commands[:4]
      scopes[0].cancel()
      await anyio.lowlevel.checkpoint()
      assert "cancelled" not in events
      release_response.set()

    assert [command.hex() for command in io.writes] == commands
    assert [event for event in events if event.startswith("reply:")] == [
      f"reply:{index}" for index in range(len(commands))
    ]
    assert events[-1] == "cancelled"
    assert driver.state.recovery_required
    assert driver.state.last_teachpoint == (
      protocol.TEACHPOINT_PICK if operation == "load" else protocol.TEACHPOINT_BUCKET_1
    )


class PollingVSpin(VSpin):
  def __init__(self, poll_started: anyio.Event, *, spin: bool) -> None:
    with patch("pylabrobot.agilent.vspin.vspin.FTDI"):
      super().__init__(name="centrifuge")
    self.poll_started = poll_started
    self.spin_poll = spin
    self.commands: list[bytes] = []

  async def close_door(self) -> None:
    pass

  async def lock_door(self) -> None:
    pass

  async def request_door_open(self) -> bool:
    return False

  async def request_door_locked(self) -> bool:
    return True

  async def request_bucket_locked(self) -> bool:
    return False

  async def request_position(self) -> int:
    return 0

  async def request_positions_and_tachometer(self) -> _nmc.ServoStatus:
    self.poll_started.set()
    return _nmc.ServoStatus(
      status=_nmc.STATUS_MOVE_DONE if self.spin_poll else 0,
      position=0,
      velocity=0,
    )

  async def request_spinning(self) -> bool:
    return False

  async def _request_input_flags(self) -> int:
    return 0

  async def _send_nmc(self, command: bytes, **kwargs: Any) -> _nmc.NMCResponse:
    del kwargs
    self.commands.append(command)
    return _nmc.NMCResponse(status=_nmc.STATUS_MOVE_DONE, data=b"")


class TestVSpinOperationCancellation(AnyioTestBase):
  @pytest.mark.parametrize("operation", ["position", "acceleration"])
  async def test_position_and_acceleration_polls_are_cancellable(self, operation: str) -> None:
    poll_started = anyio.Event()
    device = PollingVSpin(poll_started, spin=operation == "acceleration")
    _mark_vspin_ready(device)
    cancelled = anyio.Event()

    async def run() -> None:
      try:
        if operation == "position":
          await device.go_to_position(100)
        else:
          await device.spin(g=1, duration=1)
      except anyio.get_cancelled_exc_class():
        cancelled.set()
        raise

    with (
      patch("pylabrobot.agilent.vspin.vspin._IO_TRANSITION_TIMEOUT", 0),
      anyio.fail_after(1),
    ):
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(run)
        await poll_started.wait()
        command_count = len(device.commands)
        task_group.cancel_scope.cancel()

    assert cancelled.is_set()
    assert len(device.commands) >= command_count
    assert device.state.recovery_required
    if operation == "position":
      assert device.at_bucket is None
      assert device.commands[-1] == _nmc.build_stop_motor(_nmc.PIC_SERVO_ADDRESS, _nmc.MOTOR_OFF)
    else:
      assert (
        _nmc.build_load_trajectory(
          _nmc.PIC_SERVO_ADDRESS,
          vspin_module._VELOCITY_TRAJECTORY_MODE,
          velocity=0,
          acceleration=_nmc.acceleration_to_nmc(0.8),
        )
        in device.commands
      )

  async def test_deceleration_command_reconciles_before_cancellation(self) -> None:
    device = PollingVSpin(anyio.Event(), spin=True)
    _mark_vspin_ready(device)
    device.request_tachometer = AsyncMock(return_value=10**9)  # type: ignore[method-assign]
    device.request_position = AsyncMock(side_effect=[0, 0, 10**9])  # type: ignore[method-assign]
    deceleration_started = anyio.Event()
    events: list[str] = []
    wait_count = 0

    async def wait_for_stop(initial_rpm: float, deceleration: float) -> None:
      nonlocal wait_count
      del initial_rpm, deceleration
      wait_count += 1
      if wait_count == 1:
        deceleration_started.set()
        await anyio.sleep_forever()
      events.append("rotor confirmed stopped")

    device._wait_until_stopped = wait_for_stop  # type: ignore[method-assign]

    async def run_spin() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          await device.spin(g=1, duration=1)
        except anyio.get_cancelled_exc_class():
          events.append("cancelled")
          raise

    scopes: list[anyio.CancelScope] = []
    async with anyio.create_task_group() as task_group:
      task_group.start_soon(run_spin)
      await deceleration_started.wait()
      scopes[0].cancel()
    assert events == ["rotor confirmed stopped", "cancelled"]
    assert wait_count == 2
    assert device.state.recovery_required


async def _return_false() -> bool:
  return False


async def _return_true() -> bool:
  return True


async def _return_large_speed() -> int:
  return 10**9


async def _pop_position(positions: Deque[int]) -> int:
  return positions.popleft()


def make_parent() -> tuple[Access2, VSpin, Access2Driver, RecordingFTDI]:
  with patch("pylabrobot.agilent.vspin.vspin.FTDI"):
    vspin = VSpin(name="centrifuge")
  vspin.io = cast(FTDI, RecordingFTDI())
  vspin._setup = AsyncMock()  # type: ignore[method-assign]
  vspin.request_door_open = AsyncMock(return_value=True)  # type: ignore[method-assign]
  vspin.request_bucket_locked = AsyncMock(return_value=True)  # type: ignore[method-assign]
  vspin._request_rotor_safe_to_access = AsyncMock(return_value=True)  # type: ignore[method-assign]
  driver, io = make_access2_driver()
  with patch("pylabrobot.agilent.vspin.access2.FTDI"):
    loader = Access2(name="loader", device_id="ignored", vspin=vspin)
  loader.driver = driver
  return loader, vspin, driver, io


class TestAccess2Lifecycle(AnyioTestBase):
  def test_uses_managed_lifecycle_methods_and_resource_base(self) -> None:
    assert Access2.__aenter__ is HasLifespan.__aenter__
    assert Access2.__aexit__ is HasLifespan.__aexit__
    assert Access2.setup is HasLifespan.setup
    assert Access2.stop is HasLifespan.stop

  async def test_parent_owns_driver_and_borrows_vspin(self) -> None:
    loader, vspin, driver, io = make_parent()

    async with loader:
      assert loader.is_active
      assert driver.is_active
      assert not vspin.is_active

    assert io.events.count("ftdi.enter") == 1
    assert io.events.count("ftdi.exit") == 1
    assert not vspin.is_active

  async def test_body_cancellation_closes_only_driver(self) -> None:
    loader, vspin, _, io = make_parent()
    body_started = anyio.Event()

    async def use_loader() -> None:
      try:
        async with loader:
          body_started.set()
          await anyio.sleep_forever()
      except anyio.get_cancelled_exc_class():
        io.events.append("cancelled")
        raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(use_loader)
      await body_started.wait()
      task_group.cancel_scope.cancel()

    assert io.events[-2:] == ["ftdi.exit", "cancelled"]
    assert not vspin.is_active

  async def test_rejects_lifespan_options_before_driver_entry(self) -> None:
    loader, _, _, io = make_parent()

    with pytest.raises(TypeError, match="unexpected keyword argument"):
      async with loader.lifespan(unknown=True):
        pytest.fail("invalid options reached body")

    assert io.events == []

  async def test_exit_does_not_park_or_release_gripper(self) -> None:
    loader, _, _, io = make_parent()

    async with loader:
      pass

    assert bytes.fromhex("1105000e00440b0000000000410000704103007539") not in io.writes
    assert bytes.fromhex("1105000a00420700010000803f02008c64") not in io.writes


class TestAccess2TransferCancellation(AnyioTestBase):
  @pytest.mark.parametrize("operation", ["load", "unload"])
  async def test_loader_motion_reconciles_then_commits_model_before_cancellation(
    self, operation: str
  ) -> None:
    loader, vspin, _, _ = make_parent()
    plate = Resource("plate", size_x=1, size_y=1, size_z=1)
    source = loader if operation == "load" else vspin.bucket1
    destination = vspin.bucket1 if operation == "load" else loader
    source.assign_child_resource(plate, location=Coordinate.zero())
    motion_started = anyio.Event()
    release = anyio.Event()
    scopes: list[anyio.CancelScope] = []
    events: list[str] = []

    async def motion(bucket_teachpoint: int, **parameters: float | str) -> None:
      del parameters
      assert bucket_teachpoint == protocol.TEACHPOINT_BUCKET_1
      loader.driver._mark_recovery_required(position_uncertain=True)
      motion_started.set()
      await release.wait()
      events.append("motion.complete")

    if operation == "load":
      loader.driver.load = cast(Any, motion)  # type: ignore[method-assign]
    else:
      loader.driver.unload = cast(Any, motion)  # type: ignore[method-assign]

    async def transfer() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          await (loader.load() if operation == "load" else loader.unload())
        except anyio.get_cancelled_exc_class():
          events.append("cancelled")
          raise

    async with vspin, loader:
      _mark_vspin_ready(vspin)
      vspin._at_bucket = vspin.bucket1
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(transfer)
        await motion_started.wait()
        assert vspin._command_lock.locked()
        scopes[0].cancel()
        release.set()
      assert events == ["cancelled"]
      assert plate.parent is source
      assert plate.parent is not destination
      assert loader.driver.state.recovery_required
      assert vspin.state.recovery_required
      assert vspin.is_active

  @pytest.mark.parametrize("operation", ["load", "unload"])
  async def test_pending_cancellation_does_not_start_motion_or_commit_model(
    self, operation: str
  ) -> None:
    loader, vspin, _, _ = make_parent()
    plate = Resource("plate", size_x=1, size_y=1, size_z=1)
    source = loader if operation == "load" else vspin.bucket1
    destination = vspin.bucket1 if operation == "load" else loader
    source.assign_child_resource(plate, location=Coordinate.zero())
    motion_called = False

    async def motion() -> None:
      nonlocal motion_called
      motion_called = True

    if operation == "load":
      loader.driver.load = cast(Any, motion)  # type: ignore[method-assign]
    else:
      loader.driver.unload = cast(Any, motion)  # type: ignore[method-assign]

    async with vspin, loader:
      _mark_vspin_ready(vspin)
      vspin._at_bucket = vspin.bucket1
      with anyio.CancelScope() as scope:
        scope.cancel()
        await (loader.load() if operation == "load" else loader.unload())

      assert not motion_called
      assert plate.parent is source
      assert plate.parent is not destination
      assert not vspin.state.recovery_required


class TestVSpinEvents(AnyioTestBase):
  async def test_spin_emits_loaded_bucket_resources_and_parameters(self) -> None:
    with patch("pylabrobot.agilent.vspin.vspin.FTDI"):
      vspin = VSpin(name="centrifuge", device_id="test")
    _mark_vspin_ready(vspin)
    plate = Resource("plate_1", size_x=1, size_y=1, size_z=1)
    vspin.bucket1.assign_child_resource(plate, location=Coordinate.zero())
    vspin.request_door_open = _return_false  # type: ignore[method-assign]
    vspin.request_door_locked = _return_true  # type: ignore[method-assign]
    vspin.request_bucket_locked = _return_false  # type: ignore[method-assign]
    vspin.request_tachometer = _return_large_speed  # type: ignore[method-assign]
    positions = deque([0, 10**9, 2 * 10**9])
    vspin.request_position = lambda: _pop_position(positions)  # type: ignore[method-assign]
    vspin.request_positions_and_tachometer = AsyncMock(  # type: ignore[method-assign]
      return_value=_nmc.ServoStatus(status=_nmc.STATUS_MOVE_DONE, velocity=0)
    )
    vspin.request_spinning = AsyncMock(return_value=False)  # type: ignore[method-assign]
    vspin._raise_for_spin_faults = AsyncMock()  # type: ignore[method-assign]
    vspin._send_nmc = AsyncMock(  # type: ignore[method-assign]
      return_value=_nmc.NMCResponse(status=0, data=b"")
    )
    events: list[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus):
      await vspin.spin(500, 1, 0.5, 0.6)

    assert [event.name for event in events] == [
      "centrifuge.spin.started",
      "centrifuge.spin.completed",
    ]


class TestUpstreamVSpinEvents(AnyioTestBase):
  async def test_setup_lifecycle_events(self):
    """Report one correlated lifecycle and preserve the original failure."""
    for error in (None, RuntimeError("setup failed")):
      with patch("pylabrobot.agilent.vspin.vspin.FTDI"):
        vspin = VSpin(name="centrifuge")
      io = RecordingFTDI()
      vspin.io = cast(FTDI, io)
      vspin._setup = AsyncMock(side_effect=error)  # type: ignore[method-assign]
      events: list[PLREvent] = []
      event_bus = EventBus()
      event_bus.subscribe(events.append)

      with use_event_bus(event_bus):
        if error is None:
          async with vspin:
            assert io.is_active
        else:
          with pytest.raises(RuntimeError) as raised:
            async with vspin:
              pytest.fail("failed setup reached body")
          assert raised.value is error

      assert not io.is_active
      setup_events = [event for event in events if event.name.startswith("centrifuge.setup.")]
      assert [event.name for event in setup_events] == [
        "centrifuge.setup.started",
        "centrifuge.setup." + ("completed" if error is None else "failed"),
      ]
      started, terminal = setup_events
      assert started.data == {"device": {"name": "centrifuge", "type": "VSpin"}}
      assert terminal.data["device"] == started.data["device"]
      assert started.context["operation"] == "centrifuge.setup"
      assert started.context["operation_id"] == terminal.context["operation_id"]
      assert "backend" not in terminal.data
      if error is not None:
        assert terminal.data["error_type"] == "RuntimeError"
        assert terminal.data["error_message"] == str(error)

  async def test_stop_lifecycle_events(self):
    """Report one correlated lifecycle and preserve the original failure."""
    for error in (None, RuntimeError("stop failed")):
      with patch("pylabrobot.agilent.vspin.vspin.FTDI"):
        vspin = VSpin(name="centrifuge")
      io = RecordingFTDI(exit_error=error)
      vspin.io = cast(FTDI, io)
      vspin._setup = AsyncMock()  # type: ignore[method-assign]
      events: list[PLREvent] = []
      event_bus = EventBus()
      event_bus.subscribe(events.append)

      with use_event_bus(event_bus):
        if error is None:
          async with vspin:
            assert io.is_active
        else:
          with pytest.raises(RuntimeError) as raised:
            async with vspin:
              assert io.is_active
          assert raised.value is error

      stop_events = [event for event in events if event.name.startswith("centrifuge.stop.")]
      assert [event.name for event in stop_events] == [
        "centrifuge.stop.started",
        "centrifuge.stop." + ("completed" if error is None else "failed"),
      ]
      started, terminal = stop_events
      assert started.data == {"device": {"name": "centrifuge", "type": "VSpin"}}
      assert terminal.data["device"] == started.data["device"]
      assert started.context["operation"] == "centrifuge.stop"
      assert started.context["operation_id"] == terminal.context["operation_id"]
      assert "backend" not in terminal.data
      if error is not None:
        assert terminal.data["error_type"] == "RuntimeError"
        assert terminal.data["error_message"] == str(error)

  async def test_spin_emits_loaded_bucket_resources_and_parameters(self):
    vspin = VSpin(name="centrifuge", device_id="test")
    _mark_vspin_ready(vspin)
    plate = Resource("plate_1", size_x=1, size_y=1, size_z=1)
    vspin.bucket1.assign_child_resource(plate, location=Coordinate.zero())
    vspin.request_door_open = AsyncMock(return_value=False)  # type: ignore[method-assign]
    vspin.request_door_locked = AsyncMock(return_value=True)  # type: ignore[method-assign]
    vspin.request_bucket_locked = AsyncMock(return_value=False)  # type: ignore[method-assign]
    vspin.request_tachometer = AsyncMock(  # type: ignore[method-assign]
      return_value=100000
    )
    vspin.request_position = AsyncMock(  # type: ignore[method-assign]
      side_effect=[0, 10000000, 20000000]
    )
    vspin.request_positions_and_tachometer = AsyncMock(  # type: ignore[method-assign]
      return_value=_nmc.ServoStatus(status=_nmc.STATUS_MOVE_DONE, velocity=0)
    )
    vspin.request_spinning = AsyncMock(return_value=False)  # type: ignore[method-assign]
    vspin._raise_for_spin_faults = AsyncMock()  # type: ignore[method-assign]
    vspin._send_nmc = AsyncMock(  # type: ignore[method-assign]
      return_value=_nmc.NMCResponse(status=0, data=b"")
    )
    events: list[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus):
      await vspin.spin(g=500, duration=1, acceleration=0.5, deceleration=0.6)

    assert [event.name for event in events] == [
      "centrifuge.spin.started",
      "centrifuge.spin.completed",
    ]
    started, completed = events
    assert started.context["operation_id"] == completed.context["operation_id"]
    assert started.data["device"]["name"] == "centrifuge"
    assert started.data["resources"][0]["name"] == "plate_1"
    assert started.data["bucket_resources"][0]["holder"]["name"] == "centrifuge_bucket1"
    assert started.data["relative_centrifugal_force"] == 500
    assert started.data["duration"] == 1
    assert started.data["acceleration_fraction"] == 0.5
    assert started.data["deceleration_fraction"] == 0.6
    assert "relative_centrifugal_force_g" not in started.data
    assert "duration_seconds" not in started.data

    rpm = VSpin.g_to_rpm(500)
    spin_target = _nmc.spin_target_distance(rpm, duration=1, acceleration=0.5)
    expected_spin_command = _nmc.build_load_trajectory(
      _nmc.PIC_SERVO_ADDRESS,
      0x97,
      position=spin_target,
      velocity=_nmc.rpm_to_nmc_velocity(rpm),
      acceleration=_nmc.acceleration_to_nmc(0.5),
    )
    expected_deceleration_command = _nmc.build_load_trajectory(
      _nmc.PIC_SERVO_ADDRESS,
      0xB6,
      velocity=0,
      acceleration=_nmc.acceleration_to_nmc(0.6),
    )
    commands = [call.args[0] for call in vspin._send_nmc.await_args_list]
    assert expected_spin_command in commands
    assert expected_deceleration_command in commands

  async def test_spin_failure_emits_requested_parameters(self):
    vspin = VSpin(name="centrifuge", device_id="test")
    _mark_vspin_ready(vspin)
    events: list[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus):
      with pytest.raises(ValueError, match="G-force"):
        await vspin.spin(g=0)

    assert [event.name for event in events] == ["centrifuge.spin.started", "centrifuge.spin.failed"]
    assert events[0].context["operation_id"] == events[1].context["operation_id"]
    assert events[1].data["error_type"] == "ValueError"

  async def test_spin_accepts_positional_parameters_with_event_bus(self):
    vspin = VSpin(name="centrifuge", device_id="test")
    _mark_vspin_ready(vspin)
    vspin.request_door_open = AsyncMock(return_value=False)  # type: ignore[method-assign]
    vspin.request_door_locked = AsyncMock(return_value=True)  # type: ignore[method-assign]
    vspin.request_bucket_locked = AsyncMock(return_value=False)  # type: ignore[method-assign]
    vspin.request_tachometer = AsyncMock(  # type: ignore[method-assign]
      return_value=100000
    )
    vspin.request_position = AsyncMock(  # type: ignore[method-assign]
      side_effect=[0, 10000000, 20000000]
    )
    vspin.request_positions_and_tachometer = AsyncMock(  # type: ignore[method-assign]
      return_value=_nmc.ServoStatus(status=_nmc.STATUS_MOVE_DONE, velocity=0)
    )
    vspin.request_spinning = AsyncMock(return_value=False)  # type: ignore[method-assign]
    vspin._raise_for_spin_faults = AsyncMock()  # type: ignore[method-assign]
    vspin._send_nmc = AsyncMock(  # type: ignore[method-assign]
      return_value=_nmc.NMCResponse(status=0, data=b"")
    )
    events: list[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus):
      await vspin.spin(500, 1, 0.5, 0.6)

    assert [event.name for event in events] == [
      "centrifuge.spin.started",
      "centrifuge.spin.completed",
    ]
    assert events[0].data["relative_centrifugal_force"] == 500
    assert events[0].data["deceleration_fraction"] == 0.6

  async def test_spin_failure_emits_requested_parameters_without_ready_state(self) -> None:
    with patch("pylabrobot.agilent.vspin.vspin.FTDI"):
      vspin = VSpin(name="centrifuge", device_id="test")
    events: list[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus), pytest.raises(ValueError, match="G-force"):
      await vspin.spin(g=0)

    assert [event.name for event in events] == ["centrifuge.spin.started", "centrifuge.spin.failed"]
    assert events[1].data["error_type"] == "ValueError"


class TestVSpinProtocol(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.ftdi_patch = patch("pylabrobot.agilent.vspin.vspin.FTDI", autospec=True)
    ftdi_class = self.ftdi_patch.start()
    self.addCleanup(self.ftdi_patch.stop)
    self.io = ftdi_class.return_value
    self.vspin = VSpin(name="centrifuge")
    _mark_vspin_ready(self.vspin)

  async def test_connection_does_not_imply_initialization_or_homing(self):
    vspin = VSpin(name="fresh centrifuge")
    vspin._state = dataclasses.replace(
      vspin.state,
      connection=ConnectionState.CONNECTED,
    )

    with self.assertRaisesRegex(RuntimeError, "not initialized"):
      await vspin.open_door()

  async def test_failure_before_actuation_restores_activity(self):
    before = self.vspin.state

    with self.assertRaisesRegex(RuntimeError, "precondition failed"):
      async with self.vspin._command_scope(
        "test transition",
        activity=VSpinActivity.POSITIONING,
      ):
        raise RuntimeError("precondition failed")

    self.assertEqual(self.vspin.state, before)

  async def test_setup_network_failure_does_not_send_motor_commands(self) -> None:
    """A failure before setup motion must not send commands to an uninitialized servo."""
    self.vspin._initialize_nmc_network = AsyncMock(  # type: ignore[method-assign]
      side_effect=TimeoutError("network initialization failed")
    )
    send_nmc = AsyncMock()
    self.vspin._send_nmc = send_nmc  # type: ignore[method-assign]

    with self.assertRaisesRegex(TimeoutError, "network initialization failed"):
      async with self.vspin:
        self.fail("failed initialization reached body")

    send_nmc.assert_not_awaited()
    self.assertTrue(self.vspin.state.recovery_required)
    self.assertFalse(self.vspin._command_lock.locked())

  async def test_actuated_position_failure_invalidates_presentation(self):
    self.vspin._at_bucket = self.vspin.bucket1

    with self.assertRaisesRegex(RuntimeError, "motion failed"):
      async with self.vspin._command_scope(
        "test transition",
        activity=VSpinActivity.POSITIONING,
      ) as transition:
        transition.mark_actuated(position_uncertain=True)
        raise RuntimeError("motion failed")

    self.assertTrue(self.vspin.state.recovery_required)
    self.assertEqual(self.vspin.state.activity, VSpinActivity.POSITIONING)
    self.assertIsNone(self.vspin.at_bucket)

  async def test_transfer_reservation_owns_vspin_command_state(self):
    self.vspin._at_bucket = self.vspin.bucket2
    self.vspin.request_door_open = AsyncMock(return_value=True)  # type: ignore[method-assign]
    self.vspin.request_bucket_locked = AsyncMock(return_value=True)  # type: ignore[method-assign]
    self.vspin._request_rotor_safe_to_access = AsyncMock(  # type: ignore[method-assign]
      return_value=True
    )

    async with self.vspin.reserve_transfer(self.vspin.bucket2):
      self.assertTrue(self.vspin._command_lock.locked())
      self.assertEqual(self.vspin.state.activity, VSpinActivity.TRANSFERRING)

    self.assertEqual(self.vspin.state.activity, VSpinActivity.IDLE)

  async def test_spin_reports_acceleration_cruise_and_deceleration_phases(self):
    observed: list[tuple[str, VSpinActivity]] = []
    self.vspin.request_door_open = AsyncMock(return_value=False)  # type: ignore[method-assign]
    self.vspin.request_door_locked = AsyncMock(return_value=True)  # type: ignore[method-assign]
    self.vspin.request_bucket_locked = AsyncMock(return_value=False)  # type: ignore[method-assign]
    self.vspin.request_position = AsyncMock(side_effect=[0, 100])  # type: ignore[method-assign]
    self.vspin._enable_amplifier_and_reset_servo_status = AsyncMock()  # type: ignore[method-assign]
    self.vspin._send_nmc = AsyncMock(  # type: ignore[method-assign]
      return_value=_nmc.NMCResponse(status=0, data=b"")
    )
    self.vspin._raise_for_spin_faults = AsyncMock()  # type: ignore[method-assign]

    async def wait_for_target_speed(rpm: float, acceleration: float) -> None:
      del rpm, acceleration
      observed.append(("acceleration", self.vspin.state.activity))

    async def wait_for_position(
      position: int,
      timeout: float,
      operation: str,
      *,
      cancel_on_spin_abort: bool = False,
    ) -> int:
      del timeout, operation, cancel_on_spin_abort
      observed.append(("cruise", self.vspin.state.activity))
      return position

    async def command_deceleration(deceleration: float) -> None:
      del deceleration
      observed.append(("deceleration command", self.vspin.state.activity))

    async def wait_until_stopped(initial_rpm: float, deceleration: float) -> None:
      del initial_rpm, deceleration
      observed.append(("stopped waiter", self.vspin.state.activity))

    self.vspin._wait_for_target_speed = AsyncMock(side_effect=wait_for_target_speed)  # type: ignore[method-assign]
    self.vspin._wait_for_position = AsyncMock(side_effect=wait_for_position)  # type: ignore[method-assign]
    self.vspin._command_deceleration = AsyncMock(side_effect=command_deceleration)  # type: ignore[method-assign]
    self.vspin._wait_until_stopped = AsyncMock(side_effect=wait_until_stopped)  # type: ignore[method-assign]

    await self.vspin.spin(g=500, duration=1, acceleration=0.5, deceleration=0.5)

    self.assertEqual(
      observed,
      [
        ("acceleration", VSpinActivity.ACCELERATING),
        ("cruise", VSpinActivity.AT_SPEED),
        ("deceleration command", VSpinActivity.DECELERATING),
        ("stopped waiter", VSpinActivity.DECELERATING),
      ],
    )
    self.assertEqual(self.vspin.state.activity, VSpinActivity.IDLE)

  async def test_stop_invalidates_session_state_and_bucket_presentation(self):
    io = RecordingFTDI()
    self.vspin.io = cast(FTDI, io)
    self.vspin._setup = AsyncMock()  # type: ignore[method-assign]
    async with self.vspin:
      self.vspin._at_bucket = self.vspin.bucket1
      self.assertTrue(io.is_active)

    self.assertFalse(io.is_active)
    self.assertEqual(self.vspin.state.connection, ConnectionState.DISCONNECTED)
    self.assertEqual(self.vspin.state.initialization, VSpinInitializationState.UNKNOWN)
    self.assertEqual(self.vspin.state.homing, VSpinHomingState.UNKNOWN)
    self.assertIsNone(self.vspin.at_bucket)

  async def test_ftdi_error_invalidates_lifecycle_and_bucket_presentation(self):
    self.vspin._at_bucket = self.vspin.bucket1
    self.io.write = AsyncMock(side_effect=RuntimeError("transport lost"))

    with (
      patch("pylabrobot.agilent.vspin.vspin.is_ftdi_transport_error", return_value=True),
      self.assertRaisesRegex(RuntimeError, "transport lost"),
    ):
      await self.vspin.open_door()

    self.assertEqual(self.vspin.state.connection, ConnectionState.DISCONNECTED)
    self.assertEqual(self.vspin.state.initialization, VSpinInitializationState.UNKNOWN)
    self.assertEqual(self.vspin.state.homing, VSpinHomingState.UNKNOWN)
    self.assertEqual(self.vspin.state.activity, VSpinActivity.IDLE)
    self.assertIsNone(self.vspin.at_bucket)

  async def test_position_status_uses_fixed_length_and_checksum(self):
    response = bytes.fromhex("11222500004f000018e0050000a4")
    self.io.write = AsyncMock(return_value=4)
    self.io.read = AsyncMock(side_effect=[response[:5], response[5:]])

    status = await self.vspin.request_positions_and_tachometer()

    self.assertEqual(status.status, 0x11)
    self.assertEqual(status.position, 0x2522)
    self.assertEqual(status.velocity, 0)
    self.assertEqual(status.home_position, 0x05E0)
    self.io.write.assert_awaited_once_with(_nmc.build_no_op(_nmc.PIC_SERVO_ADDRESS))
    self.assertEqual([call.args[0] for call in self.io.read.await_args_list], [14, 9])

  async def test_position_poll_stall_reports_last_position(self):
    async def position_then_stall() -> int:
      if not first_position.is_set():
        first_position.set()
        return 42
      await anyio.sleep_forever()
      raise AssertionError("unreachable")

    first_position = anyio.Event()
    self.vspin.request_position = position_then_stall  # type: ignore[method-assign]
    with anyio.fail_after(0.5):
      with self.assertRaisesRegex(TimeoutError, "last position was 42"):
        await self.vspin._wait_for_position(100, timeout=0.15, operation="test motion")

  async def test_position_status_rejects_bad_checksum(self):
    response = bytearray.fromhex("11222500004f000018e0050000a4")
    response[-1] ^= 0xFF
    self.io.write = AsyncMock(return_value=4)
    self.io.read = AsyncMock(return_value=bytes(response))

    with self.assertRaisesRegex(
      _nmc.NMCProtocolError,
      rf"command {_nmc.build_no_op(_nmc.PIC_SERVO_ADDRESS).hex()}.*"
      rf"response {bytes(response).hex()}.*checksum mismatch",
    ):
      await self.vspin.request_positions_and_tachometer()

  async def test_send_nmc_timeout_includes_command_and_partial_response(self):
    command = _nmc.build_no_op(_nmc.PIC_SERVO_ADDRESS)
    self.io.write = AsyncMock(return_value=len(command))

    async def read(length: int) -> bytes:
      if self.io.read.await_count == 1:
        return b"\x01"
      await anyio.sleep_forever()
      raise AssertionError("unreachable")

    self.io.read = AsyncMock(side_effect=read)

    with self.assertRaisesRegex(
      TimeoutError,
      rf"command {command.hex()} timed out.*1 of 2 expected.*01",
    ):
      await self.vspin._send_nmc(command, timeout=0.01)

  async def test_exact_response_times_out_with_partial_bytes(self):
    async def read(length: int) -> bytes:
      if self.io.read.await_count == 1:
        return b"\x01"
      await anyio.sleep_forever()
      raise AssertionError("unreachable")

    self.io.read = AsyncMock(side_effect=read)

    with self.assertRaisesRegex(TimeoutError, "1 of 2 expected"):
      await self.vspin._read_exact_response(length=2, timeout=0.01)

  async def test_nmc_input_drain_discards_bytes_until_quiet(self):
    async def read(length: int) -> bytes:
      if self.io.read.await_count == 1:
        return b"\x00\x89"
      await anyio.sleep_forever()
      raise AssertionError("unreachable")

    self.io.read = AsyncMock(side_effect=read)

    with patch("pylabrobot.agilent.vspin.vspin._NETWORK_INPUT_QUIET_TIME", 0.01):
      await self.vspin._drain_nmc_input()

    self.assertEqual(self.io.read.await_count, 2)

  async def test_nmc_input_drain_times_out_when_bytes_do_not_stop(self):
    self.io.read = AsyncMock(return_value=b"\x00\x89")

    with (
      patch("pylabrobot.agilent.vspin.vspin._NETWORK_INPUT_DRAIN_TIMEOUT", 0.01),
      self.assertRaisesRegex(TimeoutError, "discarded .* bytes:"),
    ):
      await self.vspin._drain_nmc_input()

  async def test_send_nmc_uses_active_status_mask_length(self):
    self.vspin._servo_status_mask = _nmc.SEND_POSITION | _nmc.SEND_VELOCITY
    response = bytes.fromhex("0101000000020004")
    command = _nmc.build_no_op(_nmc.PIC_SERVO_ADDRESS)
    self.io.write = AsyncMock(return_value=len(command))
    self.io.read = AsyncMock(side_effect=[response[:3], response[3:]])

    parsed = await self.vspin._send_nmc(command)

    self.assertEqual(parsed, _nmc.NMCResponse(status=1, data=bytes.fromhex("010000000200")))
    self.io.write.assert_awaited_once_with(command)
    self.assertEqual([call.args[0] for call in self.io.read.await_args_list], [8, 5])

  async def test_servo_enable_sequence_uses_vendor_transition_delays(self):
    self.vspin._send_nmc = AsyncMock()  # type: ignore[method-assign]

    with patch("pylabrobot.agilent.vspin.vspin.anyio.sleep", new=AsyncMock()) as sleep:
      await self.vspin._enable_amplifier_and_reset_servo_status()

    sleep.assert_has_awaits(
      [
        call(vspin_module._SERVO_TRANSITION_SETTLE_TIME),
        call(vspin_module._SERVO_TRANSITION_SETTLE_TIME),
      ]
    )
    self.vspin._send_nmc.assert_has_awaits(  # type: ignore[attr-defined]
      [
        call(_nmc.build_stop_motor(_nmc.PIC_SERVO_ADDRESS, _nmc.MOTOR_OFF)),
        call(_nmc.build_set_gain(_nmc.PIC_SERVO_ADDRESS, vspin_module._POSITION_GAINS)),
        call(_nmc.build_stop_motor(_nmc.PIC_SERVO_ADDRESS, _nmc.STOP_ABRUPT)),
        call(_nmc.build_stop_motor(_nmc.PIC_SERVO_ADDRESS, _nmc.AMPLIFIER_ENABLE)),
        call(_nmc.build_clear_bits(_nmc.PIC_SERVO_ADDRESS)),
      ]
    )

  async def test_servo_disable_sequence_uses_vendor_transition_delays(self):
    self.vspin._send_nmc = AsyncMock()  # type: ignore[method-assign]

    with patch("pylabrobot.agilent.vspin.vspin.anyio.sleep", new=AsyncMock()) as sleep:
      await self.vspin._disable_servo_after_motion()

    sleep.assert_has_awaits(
      [
        call(vspin_module._SERVO_TRANSITION_SETTLE_TIME),
        call(vspin_module._SERVO_TRANSITION_SETTLE_TIME),
      ]
    )
    self.vspin._send_nmc.assert_awaited_once_with(  # type: ignore[attr-defined]
      _nmc.build_stop_motor(_nmc.PIC_SERVO_ADDRESS, _nmc.MOTOR_OFF)
    )

  async def test_io_sensor_polarities_match_vspin_wiring(self):
    self.vspin._request_input_flags = AsyncMock(  # type: ignore[method-assign]
      return_value=(1 << _nmc.INPUT_DOOR_OPEN) | (1 << _nmc.INPUT_BUCKET_LOCKED)
    )

    self.assertTrue(await self.vspin.request_door_open())
    self.assertTrue(await self.vspin.request_door_locked())
    self.assertFalse(await self.vspin.request_bucket_locked())

  async def test_io_output_updates_preserve_other_output_bits(self):
    self.vspin._io_output_word = 1 << _nmc.OUTPUT_BUCKET_LOCK_CYLINDER
    self.vspin._send_nmc = AsyncMock(  # type: ignore[method-assign]
      return_value=_nmc.NMCResponse(status=0, data=b"")
    )

    await self.vspin._set_io_output_bit(_nmc.OUTPUT_DOOR_LOCK_CYLINDER, True)

    expected_word = (1 << _nmc.OUTPUT_BUCKET_LOCK_CYLINDER) | (1 << _nmc.OUTPUT_DOOR_LOCK_CYLINDER)
    self.vspin._send_nmc.assert_awaited_once_with(  # type: ignore[attr-defined]
      _nmc.build_set_output(_nmc.PIC_IO_ADDRESS, expected_word)
    )
    self.assertEqual(self.vspin._io_output_word, expected_word)

  async def test_position_wait_reports_last_position(self):
    self.vspin.request_position = AsyncMock(return_value=25)  # type: ignore[method-assign]

    with self.assertRaisesRegex(TimeoutError, "last position was 25"):
      await self.vspin._wait_for_position(100, timeout=0, operation="test motion")

  async def test_zero_timeout_position_wait_reads_checkpointing_first_status(self):
    async def read_position() -> int:
      await anyio.lowlevel.checkpoint()
      return 25

    self.vspin.request_position = read_position  # type: ignore[method-assign]
    with self.assertRaisesRegex(TimeoutError, "last position was 25"):
      await self.vspin._wait_for_position(100, timeout=0, operation="test motion")

  async def test_first_position_read_stall_is_bounded(self):
    async def read_position() -> int:
      await anyio.sleep_forever()
      raise AssertionError("unreachable")

    self.vspin.request_position = read_position  # type: ignore[method-assign]
    with anyio.fail_after(0.5):
      with self.assertRaisesRegex(TimeoutError, "last position was None"):
        await self.vspin._wait_for_position(100, timeout=0.02, operation="test motion")

  async def test_zero_timeout_motion_checks_first_servo_fault_and_stops_motor(self):
    async def read_status() -> _nmc.ServoStatus:
      await anyio.lowlevel.checkpoint()
      return _nmc.ServoStatus(status=_nmc.STATUS_OVERCURRENT, position=25)

    self.vspin._close_door = AsyncMock()  # type: ignore[method-assign]
    self.vspin._lock_door = AsyncMock()  # type: ignore[method-assign]
    self.vspin._unlock_bucket = AsyncMock()  # type: ignore[method-assign]
    self.vspin._enable_amplifier_and_reset_servo_status = AsyncMock()  # type: ignore[method-assign]
    self.vspin._send_nmc = AsyncMock(  # type: ignore[method-assign]
      return_value=_nmc.NMCResponse(status=0, data=b"")
    )
    self.vspin.request_positions_and_tachometer = read_status  # type: ignore[method-assign]
    self.vspin._wait_until_rotor_safe_to_access = AsyncMock()  # type: ignore[method-assign]

    with (
      patch("pylabrobot.agilent.vspin.vspin._MOTION_TIMEOUT", 0),
      self.assertRaisesRegex(RuntimeError, "servo overcurrent"),
    ):
      await self.vspin.go_to_position(100)

    self.vspin._send_nmc.assert_awaited_with(  # type: ignore[attr-defined]
      _nmc.build_stop_motor(_nmc.PIC_SERVO_ADDRESS, _nmc.MOTOR_OFF)
    )
    self.assertTrue(self.vspin.state.recovery_required)

  async def test_zero_timeout_speed_wait_reads_checkpointing_tachometer(self):
    async def read_tachometer() -> float:
      await anyio.lowlevel.checkpoint()
      return 25.0

    self.vspin._raise_for_spin_faults = AsyncMock()  # type: ignore[method-assign]
    self.vspin.request_tachometer = read_tachometer  # type: ignore[method-assign]
    with (
      patch("pylabrobot.agilent.vspin.vspin._nmc.predicted_ramp_time", return_value=0),
      patch("pylabrobot.agilent.vspin.vspin._SPIN_TIMEOUT_MARGIN", 0),
      self.assertRaisesRegex(TimeoutError, "reached only 25.0 RPM"),
    ):
      await self.vspin._wait_for_target_speed(rpm=100, acceleration=0.5)

  async def test_spin_faults_decode_ground_truth_io_bits(self):
    self.vspin._request_input_flags = AsyncMock(  # type: ignore[method-assign]
      return_value=1 << _nmc.INPUT_IMBALANCE
    )

    with self.assertRaisesRegex(RuntimeError, "imbalance"):
      await self.vspin._raise_for_spin_faults()

  async def test_spin_rejects_long_run_position_overflow_before_servo_motion(self):
    self.vspin.request_door_open = AsyncMock(return_value=False)  # type: ignore[method-assign]
    self.vspin.request_door_locked = AsyncMock(return_value=True)  # type: ignore[method-assign]
    self.vspin.request_bucket_locked = AsyncMock(return_value=False)  # type: ignore[method-assign]
    self.vspin.request_position = AsyncMock(return_value=2**31 - 1)  # type: ignore[method-assign]
    self.vspin._send_nmc = AsyncMock()  # type: ignore[method-assign]

    with self.assertRaisesRegex(NotImplementedError, "signed 32-bit position"):
      await self.vspin.spin(g=500, duration=1)

    self.vspin._send_nmc.assert_not_awaited()  # type: ignore[attr-defined]

  async def test_command_lock_rejects_state_changing_commands_before_hardware_access(self):
    self.vspin.request_door_open = AsyncMock()  # type: ignore[method-assign]
    operations = (
      ("set bucket 1 position", self.vspin.set_bucket_1_position_to_current),
      ("open door", self.vspin.open_door),
      ("close door", self.vspin.close_door),
      ("lock door", self.vspin.lock_door),
      ("unlock door", self.vspin.unlock_door),
      ("lock bucket", self.vspin.lock_bucket),
      ("unlock bucket", self.vspin.unlock_bucket),
      ("go to bucket 1", self.vspin.go_to_bucket1),
      ("go to bucket 2", self.vspin.go_to_bucket2),
      ("go to position", lambda: self.vspin.go_to_position(0)),
      ("spin", lambda: self.vspin.spin(g=500, duration=1)),
    )
    await self.vspin._command_lock.acquire()
    try:
      for name, operation in operations:
        with self.subTest(operation=name):
          with self.assertRaisesRegex(RuntimeError, "another VSpin command is active"):
            await operation()
    finally:
      self.vspin._command_lock.release()

    self.vspin.request_door_open.assert_not_awaited()  # type: ignore[attr-defined]

  async def test_command_lock_is_released_after_preparation_failure(self):
    self.vspin.request_door_open = AsyncMock(  # type: ignore[method-assign]
      side_effect=RuntimeError("door status failed")
    )

    with self.assertRaisesRegex(RuntimeError, "door status failed"):
      await self.vspin.spin(g=500, duration=1)

    self.assertFalse(self.vspin._command_lock.locked())

  async def test_status_queries_remain_available_during_a_command(self):
    self.vspin._request_input_flags = AsyncMock(return_value=0)  # type: ignore[method-assign]

    await self.vspin._command_lock.acquire()
    try:
      self.assertFalse(await self.vspin.request_door_open())
    finally:
      self.vspin._command_lock.release()

  async def _check_spin_reply_failure(self, *, cancel: bool) -> None:
    """Fail a reply after writing the spin trajectory and require controlled stopping."""
    self.vspin._at_bucket = self.vspin.bucket1
    self.vspin.request_door_open = AsyncMock(return_value=False)  # type: ignore[method-assign]
    self.vspin.request_door_locked = AsyncMock(return_value=True)  # type: ignore[method-assign]
    self.vspin.request_bucket_locked = AsyncMock(return_value=False)  # type: ignore[method-assign]
    self.vspin.request_position = AsyncMock(return_value=0)  # type: ignore[method-assign]
    self.vspin._enable_amplifier_and_reset_servo_status = AsyncMock()  # type: ignore[method-assign]
    self.vspin._raise_for_spin_faults = AsyncMock()  # type: ignore[method-assign]

    rpm = VSpin.g_to_rpm(500)
    spin_trajectory = _nmc.build_load_trajectory(
      _nmc.PIC_SERVO_ADDRESS,
      vspin_module._POSITION_TRAJECTORY_MODE,
      position=_nmc.spin_target_distance(rpm, duration=1, acceleration=0.5),
      velocity=_nmc.rpm_to_nmc_velocity(rpm),
      acceleration=_nmc.acceleration_to_nmc(0.5),
    )
    deceleration_trajectory = _nmc.build_load_trajectory(
      _nmc.PIC_SERVO_ADDRESS,
      vspin_module._VELOCITY_TRAJECTORY_MODE,
      velocity=0,
      acceleration=_nmc.acceleration_to_nmc(0.6),
    )
    reply_started = asyncio.Event()
    fail_reply = asyncio.Event()

    async def read(length: int) -> bytes:
      """Leave the trajectory reply pending until the test times out or cancels it."""
      self.assertEqual(length, 2)
      if self.io.write.call_args.args[0] == spin_trajectory:
        reply_started.set()
        await fail_reply.wait()
        raise TimeoutError("spin acknowledgement timed out")
      return _nmc_response(_nmc.STATUS_MOVE_DONE)

    async def confirm_stop(initial_rpm: float, deceleration: float) -> None:
      """Check that the spin owns its lock and completion event until stop confirmation."""
      self.assertEqual((initial_rpm, deceleration), (rpm, 0.6))
      self.assertTrue(self.vspin._command_lock.locked())
      self.assertFalse(self.vspin._spin_completion_event.is_set())

    self.io.write = AsyncMock(side_effect=len)
    self.io.read = AsyncMock(side_effect=read)
    wait_until_stopped = AsyncMock(side_effect=confirm_stop)
    self.vspin._wait_until_stopped = wait_until_stopped  # type: ignore[method-assign]

    spin_task = asyncio.ensure_future(self.vspin.spin(500, 1, 0.5, 0.6))
    await asyncio.wait_for(reply_started.wait(), timeout=1)
    if cancel:
      spin_task.cancel()
      with self.assertRaises(asyncio.CancelledError):
        await spin_task
    else:
      fail_reply.set()
      with self.assertRaisesRegex(TimeoutError, "spin acknowledgement timed out"):
        await spin_task

    gain_command = _nmc.build_set_gain(_nmc.PIC_SERVO_ADDRESS, vspin_module._VELOCITY_GAINS)
    self.assertEqual(
      self.io.write.await_args_list,
      [
        call(gain_command),
        call(spin_trajectory),
        call(gain_command),
        call(deceleration_trajectory),
      ],
    )
    wait_until_stopped.assert_awaited_once_with(rpm, 0.6)
    self.assertTrue(self.vspin.state.recovery_required)
    self.assertIsNone(self.vspin.at_bucket)
    self.assertTrue(self.vspin._spin_completion_event.is_set())
    self.assertFalse(self.vspin._command_lock.locked())

  async def test_spin_reply_timeout_decelerates_before_reraising(self) -> None:
    """A timeout after the motion write must still stop the rotor before returning."""
    await self._check_spin_reply_failure(cancel=False)

  async def test_cancelled_spin_reply_decelerates_before_reraising(self) -> None:
    """Cancelling a task awaiting the motion reply must still stop the rotor."""
    await self._check_spin_reply_failure(cancel=True)

  async def test_stop_spin_requests_owner_deceleration_and_waits_for_completion(self):
    owner_started = asyncio.Event()
    allow_completion = asyncio.Event()
    requested_decelerations: list[float | None] = []

    async def run_spin_cycle(
      g: float,
      duration: float,
      acceleration: float,
      deceleration: float,
      *,
      transition: TransitionToken,
    ) -> None:
      del g, duration, acceleration, deceleration
      transition.mark_actuated(position_uncertain=True)
      self.vspin._set_activity(VSpinActivity.AT_SPEED)
      owner_started.set()
      while not self.vspin._spin_cancel_requested:
        await asyncio.sleep(0)
      requested_decelerations.append(self.vspin._spin_stop_deceleration)
      self.vspin._set_activity(VSpinActivity.DECELERATING)
      await allow_completion.wait()
      transition.confirm_position()

    self.vspin._run_spin_cycle = AsyncMock(  # type: ignore[method-assign]
      side_effect=run_spin_cycle
    )
    spin_task: asyncio.Future[None] = asyncio.ensure_future(self.vspin.spin(g=500, duration=1))
    await owner_started.wait()

    stop_task = asyncio.create_task(self.vspin.stop_spin(deceleration=0.5))
    while not self.vspin._spin_cancel_requested:
      await asyncio.sleep(0)
    while not requested_decelerations:
      await asyncio.sleep(0)

    self.assertFalse(stop_task.done())
    self.assertEqual(requested_decelerations, [0.5])
    self.assertTrue(self.vspin._command_lock.locked())

    allow_completion.set()
    await asyncio.gather(spin_task, stop_task)

    self.assertEqual(self.vspin.state.activity, VSpinActivity.IDLE)
    self.assertFalse(self.vspin.state.recovery_required)
    self.assertTrue(self.vspin._spin_completion_event.is_set())

  async def test_stop_spin_during_preparation_prevents_servo_motion(self):
    position_request_started = asyncio.Event()
    allow_position_response = asyncio.Event()

    self.vspin.request_door_open = AsyncMock(return_value=False)  # type: ignore[method-assign]
    self.vspin.request_door_locked = AsyncMock(return_value=True)  # type: ignore[method-assign]
    self.vspin.request_bucket_locked = AsyncMock(return_value=False)  # type: ignore[method-assign]

    async def request_position() -> int:
      position_request_started.set()
      await allow_position_response.wait()
      return 0

    self.vspin.request_position = AsyncMock(side_effect=request_position)  # type: ignore[method-assign]
    self.vspin._enable_amplifier_and_reset_servo_status = AsyncMock()  # type: ignore[method-assign]
    self.vspin._send_nmc = AsyncMock()  # type: ignore[method-assign]

    spin_task: asyncio.Future[None] = asyncio.ensure_future(self.vspin.spin(g=500, duration=1))
    await position_request_started.wait()
    stop_task = asyncio.create_task(self.vspin.stop_spin(deceleration=0.5))
    while not self.vspin._spin_cancel_requested:
      await asyncio.sleep(0)

    self.assertEqual(self.vspin.state.activity, VSpinActivity.PREPARING_TO_SPIN)
    self.assertFalse(stop_task.done())

    allow_position_response.set()
    await asyncio.gather(spin_task, stop_task)

    self.vspin._enable_amplifier_and_reset_servo_status.assert_not_awaited()  # type: ignore[attr-defined]
    self.vspin._send_nmc.assert_not_awaited()  # type: ignore[attr-defined]
    self.assertEqual(self.vspin.state.activity, VSpinActivity.IDLE)
    self.assertTrue(self.vspin._spin_completion_event.is_set())

    cancellation_seen_by_next_spin: list[bool] = []

    async def next_spin_cycle(
      g: float,
      duration: float,
      acceleration: float,
      deceleration: float,
      *,
      transition: TransitionToken,
    ) -> None:
      del g, duration, acceleration, deceleration, transition
      cancellation_seen_by_next_spin.append(self.vspin._spin_cancel_requested)

    self.vspin._run_spin_cycle = AsyncMock(side_effect=next_spin_cycle)  # type: ignore[method-assign]

    await self.vspin.spin(g=500, duration=1)

    self.assertEqual(cancellation_seen_by_next_spin, [False])

  async def test_stop_spin_at_end_of_preparation_turns_off_the_servo(self):
    final_safety_check_started = asyncio.Event()
    allow_safety_check = asyncio.Event()

    self.vspin.request_door_open = AsyncMock(return_value=False)  # type: ignore[method-assign]
    self.vspin.request_door_locked = AsyncMock(return_value=True)  # type: ignore[method-assign]
    self.vspin.request_bucket_locked = AsyncMock(return_value=False)  # type: ignore[method-assign]
    self.vspin.request_position = AsyncMock(return_value=0)  # type: ignore[method-assign]
    self.vspin._enable_amplifier_and_reset_servo_status = AsyncMock()  # type: ignore[method-assign]
    self.vspin._disable_servo_after_motion = AsyncMock()  # type: ignore[method-assign]
    self.vspin._send_nmc = AsyncMock(  # type: ignore[method-assign]
      return_value=_nmc.NMCResponse(status=0, data=b"")
    )

    async def final_safety_check() -> None:
      final_safety_check_started.set()
      await allow_safety_check.wait()

    self.vspin._raise_for_spin_faults = AsyncMock(  # type: ignore[method-assign]
      side_effect=final_safety_check
    )

    spin_task: asyncio.Future[None] = asyncio.ensure_future(self.vspin.spin(g=500, duration=1))
    await final_safety_check_started.wait()
    stop_task = asyncio.create_task(self.vspin.stop_spin(deceleration=0.5))
    while not self.vspin._spin_cancel_requested:
      await asyncio.sleep(0)

    self.assertFalse(stop_task.done())

    allow_safety_check.set()
    await asyncio.gather(spin_task, stop_task)

    self.vspin._enable_amplifier_and_reset_servo_status.assert_awaited_once()  # type: ignore[attr-defined]
    self.vspin._disable_servo_after_motion.assert_awaited_once()  # type: ignore[attr-defined]
    self.vspin._send_nmc.assert_awaited_once_with(  # type: ignore[attr-defined]
      _nmc.build_set_gain(_nmc.PIC_SERVO_ADDRESS, vspin_module._VELOCITY_GAINS)
    )
    self.assertEqual(self.vspin.state.activity, VSpinActivity.IDLE)
    self.assertFalse(self.vspin.state.recovery_required)

  async def test_spin_preparation_failures_turn_motor_off(self) -> None:
    """Preparation errors and task cancellation disable the servo and require recovery."""
    motor_off = _nmc.build_stop_motor(_nmc.PIC_SERVO_ADDRESS, _nmc.MOTOR_OFF)
    boundaries = (
      _nmc.build_stop_motor(_nmc.PIC_SERVO_ADDRESS, _nmc.AMPLIFIER_ENABLE),
      _nmc.build_set_gain(_nmc.PIC_SERVO_ADDRESS, vspin_module._VELOCITY_GAINS),
      None,  # The final safety check after amplifier and gain configuration.
    )
    for boundary in boundaries:
      for cancel, cleanup_fails in ((False, False), (True, False), (False, True)):
        with self.subTest(boundary=boundary, cancel=cancel, cleanup_fails=cleanup_fails):
          vspin = VSpin(name="centrifuge")
          _mark_vspin_ready(vspin)
          vspin.request_door_open = AsyncMock(return_value=False)  # type: ignore[method-assign]
          vspin.request_door_locked = AsyncMock(return_value=True)  # type: ignore[method-assign]
          vspin.request_bucket_locked = AsyncMock(return_value=False)  # type: ignore[method-assign]
          vspin.request_position = AsyncMock(return_value=0)  # type: ignore[method-assign]
          boundary_reached = asyncio.Event()
          release_boundary = asyncio.Event()
          failure = RuntimeError("spin preparation failed")

          async def fail_preparation() -> None:
            """Pause preparation until the test fails or cancels the spin task."""
            boundary_reached.set()
            await release_boundary.wait()
            raise failure

          async def send_nmc(command: bytes) -> _nmc.NMCResponse:
            """Fail the selected preparation command or the motor-off cleanup."""
            if command == boundary:
              await fail_preparation()
            if command == motor_off and boundary_reached.is_set() and cleanup_fails:
              raise RuntimeError("motor-off reply failed")
            return _nmc.NMCResponse(status=_nmc.STATUS_MOVE_DONE, data=b"")

          send = AsyncMock(side_effect=send_nmc)
          vspin._send_nmc = send  # type: ignore[method-assign]
          vspin._raise_for_spin_faults = AsyncMock(  # type: ignore[method-assign]
            side_effect=fail_preparation if boundary is None else None
          )

          with (
            patch("pylabrobot.agilent.vspin.vspin._SERVO_TRANSITION_SETTLE_TIME", 0),
            patch.object(vspin_module.logger, "exception") as log_exception,
          ):
            spin_task: asyncio.Future[None] = asyncio.ensure_future(vspin.spin(g=500, duration=1))
            await asyncio.wait_for(boundary_reached.wait(), timeout=1)
            if cancel:
              spin_task.cancel()
              with self.assertRaises(asyncio.CancelledError):
                await spin_task
            else:
              release_boundary.set()
              with self.assertRaises(RuntimeError) as raised:
                await spin_task
              self.assertIs(raised.exception, failure)

          self.assertEqual(send.await_args_list[-1], call(motor_off))
          self.assertFalse(
            any(c.args[0][2] & 0x0F == _nmc.CMD_LOAD_TRAJECTORY for c in send.await_args_list)
          )
          self.assertTrue(vspin.state.recovery_required)
          self.assertEqual(vspin.state.activity, VSpinActivity.PREPARING_TO_SPIN)
          self.assertFalse(vspin._command_lock.locked())
          self.assertTrue(vspin._spin_completion_event.is_set())
          with self.assertRaisesRegex(RuntimeError, "requires recovery"):
            vspin._require_operational_state()
          if cleanup_fails:
            log_exception.assert_called_once()
          else:
            log_exception.assert_not_called()

  async def test_stop_spin_reports_owner_failure_after_recovery_is_recorded(self):
    owner_started = asyncio.Event()

    async def fail_spin_cycle(
      g: float,
      duration: float,
      acceleration: float,
      deceleration: float,
      *,
      transition: TransitionToken,
    ) -> None:
      del g, duration, acceleration, deceleration
      transition.mark_actuated(position_uncertain=True)
      self.vspin._set_activity(VSpinActivity.AT_SPEED)
      owner_started.set()
      while not self.vspin._spin_cancel_requested:
        await asyncio.sleep(0)
      self.vspin._set_activity(VSpinActivity.DECELERATING)
      raise RuntimeError("owner stop failed")

    self.vspin._run_spin_cycle = AsyncMock(  # type: ignore[method-assign]
      side_effect=fail_spin_cycle
    )
    spin_task: asyncio.Future[None] = asyncio.ensure_future(self.vspin.spin(g=500, duration=1))
    await owner_started.wait()
    stop_task = asyncio.create_task(self.vspin.stop_spin())

    with self.assertRaisesRegex(RuntimeError, "owner stop failed"):
      await spin_task
    with self.assertRaisesRegex(RuntimeError, "requires recovery"):
      await stop_task

    self.assertTrue(self.vspin.state.recovery_required)
    self.assertEqual(self.vspin.state.activity, VSpinActivity.DECELERATING)

  async def test_deceleration_timeout_reports_motion_status_and_velocity(self):
    self.vspin._raise_for_spin_faults = AsyncMock()  # type: ignore[method-assign]
    self.vspin.request_positions_and_tachometer = AsyncMock(  # type: ignore[method-assign]
      return_value=_nmc.ServoStatus(status=_nmc.STATUS_MOVE_DONE, velocity=-1)
    )

    with (
      patch("pylabrobot.agilent.vspin.vspin._SPIN_TIMEOUT_MARGIN", 0),
      self.assertRaisesRegex(
        TimeoutError,
        "last status was 0x01 at 14.7 RPM",
      ),
    ):
      await self.vspin._wait_until_stopped(initial_rpm=0, deceleration=0.5)

  async def test_zero_timeout_deceleration_reads_checkpointing_servo_status(self):
    async def read_status() -> _nmc.ServoStatus:
      await anyio.lowlevel.checkpoint()
      return _nmc.ServoStatus(status=_nmc.STATUS_MOVE_DONE, velocity=-1)

    self.vspin._raise_for_spin_faults = AsyncMock()  # type: ignore[method-assign]
    self.vspin.request_positions_and_tachometer = read_status  # type: ignore[method-assign]
    with (
      patch("pylabrobot.agilent.vspin.vspin._SPIN_TIMEOUT_MARGIN", 0),
      self.assertRaisesRegex(TimeoutError, "last status was 0x01 at 14.7 RPM"),
    ):
      await self.vspin._wait_until_stopped(initial_rpm=0, deceleration=0.5)

  async def test_deceleration_ignores_transient_stopped_status_before_motion_starts(self):
    self.vspin._raise_for_spin_faults = AsyncMock()  # type: ignore[method-assign]
    self.vspin.request_positions_and_tachometer = AsyncMock(  # type: ignore[method-assign]
      side_effect=[
        _nmc.ServoStatus(status=_nmc.STATUS_MOVE_DONE, velocity=0),
        _nmc.ServoStatus(status=0, velocity=-1),
        _nmc.ServoStatus(status=_nmc.STATUS_MOVE_DONE, velocity=0),
        _nmc.ServoStatus(status=_nmc.STATUS_MOVE_DONE, velocity=0),
        _nmc.ServoStatus(status=_nmc.STATUS_MOVE_DONE, velocity=0),
      ]
    )
    self.vspin._wait_until_rotor_safe_to_access = AsyncMock()  # type: ignore[method-assign]

    with patch("pylabrobot.agilent.vspin.vspin._STATUS_POLL_INTERVAL", 0):
      await self.vspin._wait_until_stopped(initial_rpm=100, deceleration=0.5)

    self.assertEqual(  # type: ignore[attr-defined]
      self.vspin.request_positions_and_tachometer.await_count,
      5,
    )
    self.vspin._wait_until_rotor_safe_to_access.assert_awaited_once()  # type: ignore[attr-defined]

  async def test_rotor_access_requires_servo_stop_and_cleared_spinning_input(self):
    self.vspin.request_positions_and_tachometer = AsyncMock(  # type: ignore[method-assign]
      side_effect=[
        _nmc.ServoStatus(status=_nmc.STATUS_MOVE_DONE, velocity=0),
        _nmc.ServoStatus(status=_nmc.STATUS_MOVE_DONE, velocity=0),
        _nmc.ServoStatus(status=0, velocity=0),
        _nmc.ServoStatus(status=_nmc.STATUS_MOVE_DONE, velocity=-1),
      ]
    )
    self.vspin.request_spinning = AsyncMock(  # type: ignore[method-assign]
      side_effect=[False, True]
    )

    self.assertTrue(await self.vspin._request_rotor_safe_to_access())
    self.assertFalse(await self.vspin._request_rotor_safe_to_access())
    self.assertFalse(await self.vspin._request_rotor_safe_to_access())
    self.assertFalse(await self.vspin._request_rotor_safe_to_access())
    self.assertEqual(self.vspin.request_spinning.await_count, 2)  # type: ignore[attr-defined]

  async def test_rotor_access_confirmation_has_a_bounded_wait(self):
    self.vspin.request_positions_and_tachometer = AsyncMock(  # type: ignore[method-assign]
      return_value=_nmc.ServoStatus(status=_nmc.STATUS_MOVE_DONE, velocity=0)
    )
    self.vspin.request_spinning = AsyncMock(return_value=True)  # type: ignore[method-assign]

    with (
      patch("pylabrobot.agilent.vspin.vspin._IO_TRANSITION_TIMEOUT", 0),
      self.assertRaisesRegex(TimeoutError, "did not become safe to access"),
    ):
      await self.vspin._wait_until_rotor_safe_to_access()

  async def test_zero_timeout_io_wait_reads_checkpointing_sensor(self):
    async def read_sensor(bit: int, *, active_low: bool = False) -> bool:
      del bit, active_low
      await anyio.lowlevel.checkpoint()
      return False

    self.vspin._request_io_bit = read_sensor  # type: ignore[method-assign]
    with (
      patch("pylabrobot.agilent.vspin.vspin._IO_TRANSITION_TIMEOUT", 0),
      self.assertRaisesRegex(TimeoutError, "last value was False"),
    ):
      await self.vspin._wait_for_io_bit(_nmc.INPUT_DOOR_OPEN, True, name="door-open sensor")

  async def test_zero_timeout_rotor_safety_reads_checkpointing_status(self):
    async def check_rotor() -> bool:
      await anyio.lowlevel.checkpoint()
      return True

    self.vspin._request_rotor_safe_to_access = check_rotor  # type: ignore[method-assign]
    with patch("pylabrobot.agilent.vspin.vspin._IO_TRANSITION_TIMEOUT", 0):
      await self.vspin._wait_until_rotor_safe_to_access()

  async def test_first_rotor_safety_read_stall_is_bounded(self):
    async def check_rotor() -> bool:
      await anyio.sleep_forever()
      raise AssertionError("unreachable")

    self.vspin._request_rotor_safe_to_access = check_rotor  # type: ignore[method-assign]
    with anyio.fail_after(0.5):
      with (
        patch("pylabrobot.agilent.vspin.vspin._IO_TRANSITION_TIMEOUT", 0.02),
        self.assertRaisesRegex(TimeoutError, "rotor did not become safe"),
      ):
        await self.vspin._wait_until_rotor_safe_to_access()

  async def test_rotor_access_actuators_wait_for_stop_confirmation(self):
    async def assert_guarded(operation: Callable[[], Awaitable[None]]) -> None:
      self.vspin._wait_until_rotor_safe_to_access = AsyncMock(  # type: ignore[method-assign]
        side_effect=TimeoutError("rotor not stopped")
      )
      self.vspin._set_io_output_bit = AsyncMock()  # type: ignore[method-assign]

      with self.assertRaisesRegex(TimeoutError, "rotor not stopped"):
        await operation()

      self.vspin._wait_until_rotor_safe_to_access.assert_awaited_once()  # type: ignore[attr-defined]
      self.vspin._set_io_output_bit.assert_not_awaited()  # type: ignore[attr-defined]
      self.assertEqual(self.vspin.state.activity, VSpinActivity.IDLE)
      self.assertFalse(self.vspin.state.recovery_required)

    self.vspin.request_door_open = AsyncMock(return_value=False)  # type: ignore[method-assign]
    await assert_guarded(self.vspin.open_door)

    self.vspin.request_door_locked = AsyncMock(return_value=True)  # type: ignore[method-assign]
    await assert_guarded(self.vspin.unlock_door)

    self.vspin.request_bucket_locked = AsyncMock(return_value=False)  # type: ignore[method-assign]
    await assert_guarded(self.vspin.lock_bucket)

  async def test_bucket_calibration_is_normalized_and_saved_consistently(self):
    self.vspin.request_position = AsyncMock(return_value=12_345)  # type: ignore[method-assign]
    self.vspin.request_home_position = AsyncMock(return_value=400)  # type: ignore[method-assign]
    self.io.request_serial = AsyncMock(return_value="vspin-serial")

    with patch("pylabrobot.agilent.vspin.vspin._save_vspin_calibrations") as save:
      await self.vspin.set_bucket_1_position_to_current()

    self.assertEqual(self.vspin.bucket_1_remainder, 4055)
    save.assert_called_once_with("vspin-serial", 4055)

  async def test_bucket_targets_use_shortest_path_independently(self):
    self.vspin._bucket_1_remainder = 100
    self.vspin.request_home_position = AsyncMock(return_value=500)  # type: ignore[method-assign]
    self.vspin.request_position = AsyncMock(return_value=7900)  # type: ignore[method-assign]

    self.assertEqual(await self.vspin.request_bucket_1_position(), 8400)
    self.assertEqual(await self.vspin.request_bucket_2_position(), 4400)

  async def test_bucket_target_uses_saved_home_position_after_spin(self):
    self.vspin._bucket_1_remainder = 100
    self.vspin._home_position = 500
    self.vspin.request_home_position = AsyncMock()  # type: ignore[method-assign]
    self.vspin.request_position = AsyncMock(return_value=7900)  # type: ignore[method-assign]

    self.assertEqual(await self.vspin.request_bucket_1_position(), 8400)
    self.vspin.request_home_position.assert_not_awaited()  # type: ignore[attr-defined]

  async def test_bucket_presentation_retries_alignment_one_revolution_later(self):
    self.vspin.request_bucket_1_position = AsyncMock(return_value=8400)  # type: ignore[method-assign]
    self.vspin._go_to_position = AsyncMock(  # type: ignore[method-assign]
      side_effect=[vspin_module._PositionAlignmentError("misaligned"), None]
    )

    await self.vspin.go_to_bucket1()

    self.vspin._go_to_position.assert_has_awaits(  # type: ignore[attr-defined]
      [call(8400, transition=ANY), call(16400, transition=ANY)]
    )
    self.assertIs(self.vspin.at_bucket, self.vspin.bucket1)


class TestVSpinScriptedFTDI(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.ftdi_patch = patch("pylabrobot.agilent.vspin.vspin.FTDI", autospec=True)
    self.ftdi_patch.start()
    self.addCleanup(self.ftdi_patch.stop)
    self.quiet_time_patch = patch("pylabrobot.agilent.vspin.vspin._NETWORK_INPUT_QUIET_TIME", 0)
    self.quiet_time_patch.start()
    self.addCleanup(self.quiet_time_patch.stop)

  def _make_vspin(self, steps: list[_VSpinScriptStep]) -> tuple[VSpin, _ScriptedVSpinFTDI]:
    vspin = VSpin(name="centrifuge")
    io = _ScriptedVSpinFTDI(steps)
    vspin.io = io  # type: ignore[assignment]
    vspin._servo_status_mask = _SERVO_STATUS_MASK
    vspin._io_status_mask = _IO_STATUS_MASK
    _mark_vspin_ready(vspin)
    return vspin, io

  @staticmethod
  def _bucket_presentation_steps(
    current_position: int, target_position: int
  ) -> list[_VSpinScriptStep]:
    io_status = _nmc.build_no_op(_nmc.PIC_IO_ADDRESS)
    servo_status = _nmc.build_no_op(_nmc.PIC_SERVO_ADDRESS)
    closed_locked_bucket_unlocked = 1 << _nmc.INPUT_BUCKET_LOCKED
    position_trajectory = _nmc.build_load_trajectory(
      _nmc.PIC_SERVO_ADDRESS,
      vspin_module._POSITION_TRAJECTORY_MODE,
      position=target_position,
      velocity=0x28F5C3,
      acceleration=0x1AD7,
    )
    return [
      _servo_step(servo_status, position=current_position),
      _io_step(io_status, inputs=closed_locked_bucket_unlocked),
      _io_step(io_status, inputs=closed_locked_bucket_unlocked),
      _io_step(io_status, inputs=closed_locked_bucket_unlocked),
      _io_step(io_status, inputs=closed_locked_bucket_unlocked),
      _servo_step(_nmc.build_stop_motor(_nmc.PIC_SERVO_ADDRESS, _nmc.MOTOR_OFF)),
      _servo_step(_nmc.build_set_gain(_nmc.PIC_SERVO_ADDRESS, vspin_module._POSITION_GAINS)),
      _servo_step(_nmc.build_stop_motor(_nmc.PIC_SERVO_ADDRESS, _nmc.STOP_ABRUPT)),
      _servo_step(_nmc.build_stop_motor(_nmc.PIC_SERVO_ADDRESS, _nmc.AMPLIFIER_ENABLE)),
      _servo_step(_nmc.build_clear_bits(_nmc.PIC_SERVO_ADDRESS)),
      _servo_step(_nmc.build_set_gain(_nmc.PIC_SERVO_ADDRESS, vspin_module._POSITION_GAINS)),
      _servo_step(position_trajectory, status=0, position=current_position),
      _servo_step(servo_status, position=target_position),
      _servo_step(_nmc.build_stop_motor(_nmc.PIC_SERVO_ADDRESS, _nmc.MOTOR_OFF)),
      _io_step(io_status, inputs=closed_locked_bucket_unlocked),
      _servo_step(servo_status, position=target_position),
      _io_step(io_status, inputs=closed_locked_bucket_unlocked),
      _io_step(_nmc.build_set_output(_nmc.PIC_IO_ADDRESS, 0x0100), inputs=0),
      _io_step(io_status, inputs=0),
      _io_step(io_status, inputs=0),
      _servo_step(servo_status, position=target_position),
      _io_step(io_status, inputs=0),
      _io_step(
        _nmc.build_set_output(_nmc.PIC_IO_ADDRESS, 0x0500),
        inputs=1 << _nmc.INPUT_DOOR_LOCKED,
      ),
      _io_step(io_status, inputs=1 << _nmc.INPUT_DOOR_LOCKED),
      _io_step(io_status, inputs=1 << _nmc.INPUT_DOOR_LOCKED),
      _servo_step(servo_status, position=target_position),
      _io_step(io_status, inputs=1 << _nmc.INPUT_DOOR_LOCKED),
      _io_step(
        _nmc.build_set_output(_nmc.PIC_IO_ADDRESS, 0x0700),
        inputs=(1 << _nmc.INPUT_DOOR_LOCKED) | (1 << _nmc.INPUT_DOOR_OPEN),
      ),
      _io_step(
        io_status,
        inputs=(1 << _nmc.INPUT_DOOR_LOCKED) | (1 << _nmc.INPUT_DOOR_OPEN),
      ),
    ]

  @staticmethod
  def _network_reset_steps(stale_response: bytes | None = None) -> list[_VSpinScriptStep]:
    steps = [_VSpinScriptStep(b"\x00" * 20, None)]
    steps.extend(
      _VSpinScriptStep(
        _nmc.build_no_op(address) + b"\x00" * 8,
        None,
      )
      for address in range(33)
    )
    steps.append(_VSpinScriptStep(_nmc.build_hard_reset(), stale_response))
    return steps

  @classmethod
  def _setup_steps(cls) -> list[_VSpinScriptStep]:
    steps = cls._network_reset_steps()
    steps.extend(cls._network_reset_steps())
    steps.extend(
      [
        _empty_step(_nmc.build_set_address(_nmc.PIC_SERVO_ADDRESS)),
        _VSpinScriptStep(
          _nmc.build_read_status(_nmc.PIC_SERVO_ADDRESS, _nmc.SEND_MODULE_ID),
          _nmc_response(
            _nmc.STATUS_MOVE_DONE,
            bytes([_nmc.PIC_SERVO_MODULE_TYPE, 1]),
          ),
        ),
        _empty_step(_nmc.build_set_address(_nmc.PIC_IO_ADDRESS)),
        _VSpinScriptStep(
          _nmc.build_read_status(_nmc.PIC_IO_ADDRESS, _nmc.SEND_MODULE_ID),
          _nmc_response(
            _nmc.STATUS_MOVE_DONE,
            bytes([_nmc.PIC_IO_MODULE_TYPE, 1]),
          ),
        ),
        _VSpinScriptStep(_nmc.build_set_address(3), None),
        _VSpinScriptStep(_nmc.build_set_baud(57600), None),
        _servo_step(_nmc.build_define_status(_nmc.PIC_SERVO_ADDRESS, _SERVO_STATUS_MASK)),
      ]
    )
    steps.extend(
      _empty_step(_nmc.build_set_io_direction(_nmc.PIC_IO_ADDRESS, 0x0FFF)) for _ in range(8)
    )
    steps.extend(
      _empty_step(_nmc.build_set_io_direction(_nmc.PIC_IO_ADDRESS, direction))
      for direction in (0x0FDF, 0x0EDF, 0x0CDF, 0x08DF)
    )
    steps.extend(_empty_step(_nmc.build_set_output(_nmc.PIC_IO_ADDRESS, 0)) for _ in range(4))
    safe_inputs = 1 << _nmc.INPUT_BUCKET_LOCKED
    steps.append(
      _io_step(
        _nmc.build_define_status(_nmc.PIC_IO_ADDRESS, _IO_STATUS_MASK),
        inputs=safe_inputs,
      )
    )
    for _ in range(5):
      steps.extend(
        [
          _io_step(
            _nmc.build_set_output(
              _nmc.PIC_IO_ADDRESS,
              1 << _nmc.OUTPUT_VERSION_TOGGLE,
            ),
            inputs=safe_inputs,
          ),
          _io_step(
            _nmc.build_set_output(_nmc.PIC_IO_ADDRESS, 0),
            inputs=safe_inputs,
          ),
        ]
      )
    io_status = _nmc.build_no_op(_nmc.PIC_IO_ADDRESS)
    steps.extend(
      [
        _io_step(io_status, inputs=safe_inputs),
        _io_step(io_status, inputs=safe_inputs),
        _io_step(
          _nmc.build_set_output(_nmc.PIC_IO_ADDRESS, 0),
          inputs=safe_inputs,
        ),
        _io_step(io_status, inputs=safe_inputs),
        _servo_step(_nmc.build_stop_motor(_nmc.PIC_SERVO_ADDRESS, _nmc.MOTOR_OFF)),
        _servo_step(_nmc.build_set_gain(_nmc.PIC_SERVO_ADDRESS, vspin_module._POSITION_GAINS)),
        _servo_step(_nmc.build_stop_motor(_nmc.PIC_SERVO_ADDRESS, _nmc.STOP_ABRUPT)),
        _servo_step(_nmc.build_stop_motor(_nmc.PIC_SERVO_ADDRESS, _nmc.AMPLIFIER_ENABLE)),
        _servo_step(_nmc.build_clear_bits(_nmc.PIC_SERVO_ADDRESS)),
        _servo_step(_nmc.build_reset_position(_nmc.PIC_SERVO_ADDRESS)),
        _servo_step(_nmc.build_set_gain(_nmc.PIC_SERVO_ADDRESS, vspin_module._HOMING_GAINS)),
        _servo_step(
          _nmc.build_load_trajectory(
            _nmc.PIC_SERVO_ADDRESS,
            vspin_module._VELOCITY_TRAJECTORY_MODE,
            velocity=0x8312,
            acceleration=0x0112,
          ),
          status=_nmc.STATUS_HOMING_IN_PROGRESS,
        ),
        _servo_step(
          _nmc.build_set_homing(_nmc.PIC_SERVO_ADDRESS, 0x28),
          status=_nmc.STATUS_HOMING_IN_PROGRESS,
        ),
        _servo_step(
          _nmc.build_no_op(_nmc.PIC_SERVO_ADDRESS),
          status=_nmc.STATUS_HOMING_IN_PROGRESS,
          position=100,
        ),
        _servo_step(
          _nmc.build_no_op(_nmc.PIC_SERVO_ADDRESS),
          position=200,
          home_position=200,
        ),
        _servo_step(_nmc.build_stop_motor(_nmc.PIC_SERVO_ADDRESS, _nmc.MOTOR_OFF)),
        _servo_step(_nmc.build_set_gain(_nmc.PIC_SERVO_ADDRESS, vspin_module._POSITION_GAINS)),
        _servo_step(_nmc.build_stop_motor(_nmc.PIC_SERVO_ADDRESS, _nmc.STOP_ABRUPT)),
        _servo_step(_nmc.build_stop_motor(_nmc.PIC_SERVO_ADDRESS, _nmc.AMPLIFIER_ENABLE)),
        _servo_step(_nmc.build_clear_bits(_nmc.PIC_SERVO_ADDRESS)),
        _servo_step(_nmc.build_set_gain(_nmc.PIC_SERVO_ADDRESS, vspin_module._POSITION_GAINS)),
        _servo_step(
          _nmc.build_load_trajectory(
            _nmc.PIC_SERVO_ADDRESS,
            vspin_module._POSITION_TRAJECTORY_MODE,
            position=0,
            velocity=0x28F5C3,
            acceleration=0x1AD7,
          ),
          status=0,
          position=200,
          home_position=200,
        ),
        _servo_step(
          _nmc.build_no_op(_nmc.PIC_SERVO_ADDRESS),
          status=0,
          position=50,
          home_position=200,
        ),
        _servo_step(
          _nmc.build_no_op(_nmc.PIC_SERVO_ADDRESS),
          position=0,
          home_position=200,
        ),
        _servo_step(_nmc.build_stop_motor(_nmc.PIC_SERVO_ADDRESS, _nmc.MOTOR_OFF)),
        _io_step(io_status, inputs=safe_inputs),
        _io_step(io_status, inputs=safe_inputs),
      ]
    )
    return steps

  async def test_complete_setup_and_homing_ftdi_transcript(self):
    vspin, io = self._make_vspin(self._setup_steps())

    with (
      patch("pylabrobot.agilent.vspin.vspin._NETWORK_INPUT_QUIET_TIME", 0),
      patch("pylabrobot.agilent.vspin.vspin._SERVO_TRANSITION_SETTLE_TIME", 0),
      patch("pylabrobot.agilent.vspin.vspin._NMC_RESET_SETTLE_TIME", 0),
      patch("pylabrobot.agilent.vspin.vspin._NMC_BAUD_SETTLE_TIME", 0),
      patch("pylabrobot.agilent.vspin.vspin._STATUS_POLL_INTERVAL", 0),
      patch("pylabrobot.agilent.vspin.vspin._NETWORK_PROBE_TIMEOUT", 0),
    ):
      async with vspin:
        io.assert_complete(self)
        self.assertTrue(io.is_active)
        self.assertEqual(vspin.state.connection, ConnectionState.CONNECTED)
        self.assertEqual(vspin.state.initialization, VSpinInitializationState.INITIALIZED)
        self.assertEqual(vspin.state.homing, VSpinHomingState.HOMED)

    io.assert_complete(self)
    self.assertTrue(io.setup_called)
    self.assertEqual(io.setup_call_count, 1)
    self.assertEqual(io.reconnect_calls, 3)
    self.assertEqual(io.stop_call_count, 1)
    self.assertEqual(io.latency_timers, [16, 16, 16, 16])
    self.assertEqual(io.line_properties, [(8, 1, 0)] * 4)
    self.assertEqual(io.flow_controls, [0, 0, 0, 0])
    self.assertEqual(io.baudrates, [19200, 19200, 19200, 19200, 57600])
    self.assertEqual(io.rx_purge_count, 3)
    self.assertEqual(io.rts_levels, [True])
    self.assertEqual(io.dtr_levels, [True])
    self.assertEqual(vspin._home_position, 200)
    self.assertEqual(vspin.state.connection, ConnectionState.DISCONNECTED)
    self.assertIsNone(vspin.at_bucket)

  async def test_setup_homing_timeout_turns_motor_off(self) -> None:
    """A homing timeout must turn off the motor before propagating the failure."""
    steps = self._setup_steps()
    home_command = _nmc.build_set_homing(_nmc.PIC_SERVO_ADDRESS, 0x28)
    home_index = next(i for i, step in enumerate(steps) if step.command == home_command)
    motor_off = _nmc.build_stop_motor(_nmc.PIC_SERVO_ADDRESS, _nmc.MOTOR_OFF)
    vspin, io = self._make_vspin(steps[: home_index + 2] + [_servo_step(motor_off)])

    with (
      patch("pylabrobot.agilent.vspin.vspin._NETWORK_INPUT_QUIET_TIME", 0),
      patch("pylabrobot.agilent.vspin.vspin._SERVO_TRANSITION_SETTLE_TIME", 0),
      patch("pylabrobot.agilent.vspin.vspin._NMC_RESET_SETTLE_TIME", 0),
      patch("pylabrobot.agilent.vspin.vspin._NMC_BAUD_SETTLE_TIME", 0),
      patch("pylabrobot.agilent.vspin.vspin._NETWORK_PROBE_TIMEOUT", 0),
      patch("pylabrobot.agilent.vspin.vspin._MOTION_TIMEOUT", 0),
      self.assertRaisesRegex(TimeoutError, "homing did not finish"),
    ):
      async with vspin:
        self.fail("timed out entry reached body")

    io.assert_complete(self)
    self.assertEqual(io.writes[-1], motor_off)
    self.assertTrue(vspin.state.recovery_required)
    self.assertEqual(vspin.state.homing, VSpinHomingState.UNKNOWN)
    self.assertEqual(vspin.state.connection, ConnectionState.DISCONNECTED)
    self.assertIsNone(vspin.at_bucket)
    self.assertFalse(vspin._command_lock.locked())

  async def test_setup_motor_off_failure_preserves_homing_timeout(self) -> None:
    """A failed motor-off acknowledgement must not hide the original homing error."""
    steps = self._setup_steps()
    home_command = _nmc.build_set_homing(_nmc.PIC_SERVO_ADDRESS, 0x28)
    home_index = next(i for i, step in enumerate(steps) if step.command == home_command)
    motor_off = _nmc.build_stop_motor(_nmc.PIC_SERVO_ADDRESS, _nmc.MOTOR_OFF)
    invalid_reply = _nmc_response(0, _servo_status_data())[:-1] + b"\x01"
    vspin, io = self._make_vspin(
      steps[: home_index + 2] + [_VSpinScriptStep(motor_off, invalid_reply)]
    )

    with (
      patch("pylabrobot.agilent.vspin.vspin._NETWORK_INPUT_QUIET_TIME", 0),
      patch("pylabrobot.agilent.vspin.vspin._SERVO_TRANSITION_SETTLE_TIME", 0),
      patch("pylabrobot.agilent.vspin.vspin._NMC_RESET_SETTLE_TIME", 0),
      patch("pylabrobot.agilent.vspin.vspin._NMC_BAUD_SETTLE_TIME", 0),
      patch("pylabrobot.agilent.vspin.vspin._NETWORK_PROBE_TIMEOUT", 0),
      patch("pylabrobot.agilent.vspin.vspin._MOTION_TIMEOUT", 0),
      self.assertLogs(vspin_module.logger, level="ERROR") as logs,
      self.assertRaisesRegex(TimeoutError, "homing did not finish"),
    ):
      async with vspin:
        self.fail("timed out entry reached body")

    io.assert_complete(self)
    self.assertIn("failed to turn off motor after setup error", logs.output[0])
    self.assertTrue(vspin.state.recovery_required)
    self.assertFalse(vspin._command_lock.locked())

  async def test_cancelled_setup_homing_turns_motor_off(self) -> None:
    """Task cancellation during homing must wait for motor-off cleanup."""
    steps = self._setup_steps()
    home_command = _nmc.build_set_homing(_nmc.PIC_SERVO_ADDRESS, 0x28)
    home_index = next(i for i, step in enumerate(steps) if step.command == home_command)
    motor_off = _nmc.build_stop_motor(_nmc.PIC_SERVO_ADDRESS, _nmc.MOTOR_OFF)
    vspin, io = self._make_vspin(steps[: home_index + 2] + [_servo_step(motor_off)])
    homing_started = asyncio.Event()
    request_status = vspin.request_positions_and_tachometer

    async def wait_for_homing_status() -> _nmc.ServoStatus:
      """Pause setup with homing active and the status reply fully consumed."""
      status = await request_status()
      homing_started.set()
      await anyio.sleep_forever()
      return status

    with (
      patch("pylabrobot.agilent.vspin.vspin._NETWORK_INPUT_QUIET_TIME", 0),
      patch("pylabrobot.agilent.vspin.vspin._SERVO_TRANSITION_SETTLE_TIME", 0),
      patch("pylabrobot.agilent.vspin.vspin._NMC_RESET_SETTLE_TIME", 0),
      patch("pylabrobot.agilent.vspin.vspin._NMC_BAUD_SETTLE_TIME", 0),
      patch("pylabrobot.agilent.vspin.vspin._NETWORK_PROBE_TIMEOUT", 0),
      patch.object(vspin, "request_positions_and_tachometer", side_effect=wait_for_homing_status),
    ):

      async def enter() -> None:
        async with vspin:
          self.fail("cancelled entry reached body")

      setup_task = asyncio.create_task(enter())
      await asyncio.wait_for(homing_started.wait(), timeout=1)
      setup_task.cancel()
      with self.assertRaises(asyncio.CancelledError):
        await setup_task

    io.assert_complete(self)
    self.assertEqual(io.writes[-1], motor_off)
    self.assertTrue(vspin.state.recovery_required)
    self.assertEqual(vspin.state.homing, VSpinHomingState.UNKNOWN)
    self.assertIsNone(vspin.at_bucket)
    self.assertFalse(vspin._command_lock.locked())

  async def test_network_initialization_probes_the_next_baudrate(self):
    steps = self._network_reset_steps()
    steps.extend(self._network_reset_steps())
    steps.append(_VSpinScriptStep(_nmc.build_set_address(_nmc.PIC_SERVO_ADDRESS), None))
    steps.extend(self._network_reset_steps())
    steps.extend(self._network_reset_steps())
    steps.extend(
      [
        _empty_step(_nmc.build_set_address(_nmc.PIC_SERVO_ADDRESS)),
        _VSpinScriptStep(
          _nmc.build_read_status(_nmc.PIC_SERVO_ADDRESS, _nmc.SEND_MODULE_ID),
          _nmc_response(1, bytes([_nmc.PIC_SERVO_MODULE_TYPE, 1])),
        ),
        _empty_step(_nmc.build_set_address(_nmc.PIC_IO_ADDRESS)),
        _VSpinScriptStep(
          _nmc.build_read_status(_nmc.PIC_IO_ADDRESS, _nmc.SEND_MODULE_ID),
          _nmc_response(1, bytes([_nmc.PIC_IO_MODULE_TYPE, 1])),
        ),
        _VSpinScriptStep(_nmc.build_set_address(3), None),
        _VSpinScriptStep(_nmc.build_set_baud(57600), None),
      ]
    )
    vspin, io = self._make_vspin(steps)

    with (
      patch("pylabrobot.agilent.vspin.vspin._NMC_RESET_SETTLE_TIME", 0),
      patch("pylabrobot.agilent.vspin.vspin._NMC_BAUD_SETTLE_TIME", 0),
      patch("pylabrobot.agilent.vspin.vspin._NETWORK_PROBE_TIMEOUT", 0),
    ):
      async with io.lifespan():
        await vspin._initialize_nmc_network()

    io.assert_complete(self)
    self.assertEqual(io.setup_call_count, 1)
    self.assertEqual(io.reconnect_calls, 5)
    self.assertEqual(io.stop_call_count, 1)
    self.assertEqual(io.baudrates, [19200, 19200, 19200, 115200, 19200, 19200, 57600])
    self.assertEqual(io.rx_purge_count, 5)

  async def test_network_reopens_to_discard_stale_reset_responses(self):
    steps = self._network_reset_steps(stale_response=b"\xfa")
    steps.extend(self._network_reset_steps(stale_response=b"\xfb"))
    steps.extend(
      [
        _empty_step(_nmc.build_set_address(_nmc.PIC_SERVO_ADDRESS)),
        _VSpinScriptStep(
          _nmc.build_read_status(_nmc.PIC_SERVO_ADDRESS, _nmc.SEND_MODULE_ID),
          _nmc_response(1, bytes([_nmc.PIC_SERVO_MODULE_TYPE, 1])),
        ),
        _empty_step(_nmc.build_set_address(_nmc.PIC_IO_ADDRESS)),
        _VSpinScriptStep(
          _nmc.build_read_status(_nmc.PIC_IO_ADDRESS, _nmc.SEND_MODULE_ID),
          _nmc_response(1, bytes([_nmc.PIC_IO_MODULE_TYPE, 1])),
        ),
        _VSpinScriptStep(_nmc.build_set_address(3), None),
        _VSpinScriptStep(_nmc.build_set_baud(57600), None),
      ]
    )
    vspin, io = self._make_vspin(steps)

    with (
      patch("pylabrobot.agilent.vspin.vspin._NMC_RESET_SETTLE_TIME", 0),
      patch("pylabrobot.agilent.vspin.vspin._NMC_BAUD_SETTLE_TIME", 0),
      patch("pylabrobot.agilent.vspin.vspin._NETWORK_PROBE_TIMEOUT", 0),
    ):
      async with io.lifespan():
        await vspin._initialize_nmc_network()

    io.assert_complete(self)
    self.assertEqual(io.setup_call_count, 1)
    self.assertEqual(io.reconnect_calls, 3)
    self.assertEqual(io.stop_call_count, 1)

  async def test_network_initialization_rejects_a_third_module(self):
    steps = self._network_reset_steps()
    steps.extend(self._network_reset_steps())
    steps.extend(
      [
        _empty_step(_nmc.build_set_address(_nmc.PIC_SERVO_ADDRESS)),
        _VSpinScriptStep(
          _nmc.build_read_status(_nmc.PIC_SERVO_ADDRESS, _nmc.SEND_MODULE_ID),
          _nmc_response(1, bytes([_nmc.PIC_SERVO_MODULE_TYPE, 1])),
        ),
        _empty_step(_nmc.build_set_address(_nmc.PIC_IO_ADDRESS)),
        _VSpinScriptStep(
          _nmc.build_read_status(_nmc.PIC_IO_ADDRESS, _nmc.SEND_MODULE_ID),
          _nmc_response(1, bytes([_nmc.PIC_IO_MODULE_TYPE, 1])),
        ),
        _empty_step(_nmc.build_set_address(3)),
        _VSpinScriptStep(
          _nmc.build_read_status(3, _nmc.SEND_MODULE_ID),
          _nmc_response(1, bytes([_nmc.PIC_SERVO_MODULE_TYPE, 2])),
        ),
      ]
    )
    vspin, io = self._make_vspin(steps)

    with self.assertRaisesRegex(RuntimeError, "unexpected third NMC module"):
      async with io.lifespan():
        await vspin._initialize_nmc_network()

    io.assert_complete(self)

  async def test_complete_spin_ftdi_transcript(self):
    g = 500
    duration = 1
    acceleration = 0.5
    deceleration = 0.6
    rpm = VSpin.g_to_rpm(g)
    spin_start_position = 0
    cruise_start_position = 100_000
    deceleration_position = int(
      cruise_start_position + rpm / 60 * _nmc.COUNTS_PER_REVOLUTION * duration
    )
    spin_target = spin_start_position + _nmc.spin_target_distance(
      rpm,
      duration,
      acceleration,
    )
    measured_velocity = -int(rpm / abs(vspin_module._TACHOMETER_TO_RPM))
    safe_inputs = 1 << _nmc.INPUT_BUCKET_LOCKED
    io_status = _nmc.build_no_op(_nmc.PIC_IO_ADDRESS)
    servo_status = _nmc.build_no_op(_nmc.PIC_SERVO_ADDRESS)
    spin_trajectory = _nmc.build_load_trajectory(
      _nmc.PIC_SERVO_ADDRESS,
      vspin_module._POSITION_TRAJECTORY_MODE,
      position=spin_target,
      velocity=_nmc.rpm_to_nmc_velocity(rpm),
      acceleration=_nmc.acceleration_to_nmc(acceleration),
    )
    deceleration_trajectory = _nmc.build_load_trajectory(
      _nmc.PIC_SERVO_ADDRESS,
      vspin_module._VELOCITY_TRAJECTORY_MODE,
      velocity=0,
      acceleration=_nmc.acceleration_to_nmc(deceleration),
    )
    steps = [
      _io_step(io_status, inputs=safe_inputs),
      _io_step(io_status, inputs=safe_inputs),
      _io_step(io_status, inputs=safe_inputs),
      _servo_step(servo_status, position=spin_start_position),
      _servo_step(_nmc.build_stop_motor(_nmc.PIC_SERVO_ADDRESS, _nmc.MOTOR_OFF)),
      _servo_step(_nmc.build_set_gain(_nmc.PIC_SERVO_ADDRESS, vspin_module._POSITION_GAINS)),
      _servo_step(_nmc.build_stop_motor(_nmc.PIC_SERVO_ADDRESS, _nmc.STOP_ABRUPT)),
      _servo_step(_nmc.build_stop_motor(_nmc.PIC_SERVO_ADDRESS, _nmc.AMPLIFIER_ENABLE)),
      _servo_step(_nmc.build_clear_bits(_nmc.PIC_SERVO_ADDRESS)),
      _servo_step(_nmc.build_set_gain(_nmc.PIC_SERVO_ADDRESS, vspin_module._VELOCITY_GAINS)),
      _io_step(io_status, inputs=safe_inputs),
      _servo_step(spin_trajectory, status=0, position=spin_start_position),
      _io_step(io_status, inputs=safe_inputs),
      _servo_step(
        servo_status,
        status=0,
        position=50_000,
        velocity=measured_velocity,
      ),
      _servo_step(
        servo_status,
        status=0,
        position=cruise_start_position,
        velocity=measured_velocity,
      ),
      _io_step(io_status, inputs=safe_inputs),
      _servo_step(
        servo_status,
        status=0,
        position=deceleration_position,
        velocity=measured_velocity,
      ),
      _servo_step(_nmc.build_set_gain(_nmc.PIC_SERVO_ADDRESS, vspin_module._VELOCITY_GAINS)),
      _servo_step(deceleration_trajectory, status=0, position=deceleration_position),
      _io_step(io_status, inputs=safe_inputs),
      _servo_step(
        servo_status,
        position=deceleration_position,
        velocity=0,
      ),
      _io_step(io_status, inputs=safe_inputs),
      _servo_step(
        servo_status,
        position=deceleration_position,
        velocity=0,
      ),
      _io_step(io_status, inputs=safe_inputs),
      _servo_step(
        servo_status,
        position=deceleration_position,
        velocity=0,
      ),
      _servo_step(
        servo_status,
        position=deceleration_position,
        velocity=0,
      ),
      _io_step(io_status, inputs=safe_inputs),
    ]
    vspin, io = self._make_vspin(steps)
    vspin._at_bucket = vspin.bucket1

    await vspin.spin(g, duration, acceleration, deceleration)

    io.assert_complete(self)
    self.assertIsNone(vspin.at_bucket)
    self.assertNotIn(_nmc.build_reset_position(_nmc.PIC_SERVO_ADDRESS), io.writes)
    self.assertNotIn(
      _nmc.build_set_homing(_nmc.PIC_SERVO_ADDRESS, 0x28),
      io.writes,
    )

  async def test_stop_spin_does_not_send_commands_in_parallel_with_spin_owner(self):
    vspin, io = self._make_vspin([])
    vspin._set_activity(VSpinActivity.AT_SPEED)
    vspin._spin_completion_event = anyio.Event()

    stop_task = asyncio.create_task(vspin.stop_spin(deceleration=0.5))
    while not vspin._spin_cancel_requested:
      await asyncio.sleep(0)

    self.assertFalse(stop_task.done())
    self.assertEqual(vspin._spin_stop_deceleration, 0.5)
    io.assert_complete(self)

    vspin._set_activity(VSpinActivity.IDLE)
    vspin._spin_completion_event.set()
    await stop_task

    io.assert_complete(self)

  async def test_complete_bucket_presentation_ftdi_transcripts(self):
    current_position = 7900
    cases = (
      ("go_to_bucket1", 8400, "bucket1"),
      ("go_to_bucket2", 4400, "bucket2"),
    )
    for method_name, target_position, bucket_name in cases:
      with self.subTest(bucket=bucket_name):
        steps = self._bucket_presentation_steps(current_position, target_position)
        vspin, io = self._make_vspin(steps)
        vspin._home_position = 500
        vspin._bucket_1_remainder = 100

        await getattr(vspin, method_name)()

        io.assert_complete(self)
        self.assertIs(vspin.at_bucket, getattr(vspin, bucket_name))
        self.assertEqual(vspin.state.activity, VSpinActivity.IDLE)

  async def test_bucket_motion_fault_prevents_lock_and_door_commands(self):
    position = 8400
    steps = self._bucket_presentation_steps(7900, position)[1:13]
    steps[-1] = _servo_step(
      _nmc.build_no_op(_nmc.PIC_SERVO_ADDRESS),
      status=_nmc.STATUS_POSITION_ERROR,
      position=7900,
    )
    steps.append(_servo_step(_nmc.build_stop_motor(_nmc.PIC_SERVO_ADDRESS, _nmc.MOTOR_OFF)))
    vspin, io = self._make_vspin(steps)

    with self.assertRaisesRegex(RuntimeError, "position error.*move to position 8400"):
      await vspin.go_to_position(position)

    io.assert_complete(self)
    self.assertTrue(vspin.state.recovery_required)
    self.assertIsNone(vspin.at_bucket)

  async def test_door_and_lock_operations_are_idempotent(self):
    io_status = _nmc.build_no_op(_nmc.PIC_IO_ADDRESS)
    steps = [
      _io_step(io_status, inputs=1 << _nmc.INPUT_DOOR_OPEN),
      _io_step(io_status, inputs=0),
      _io_step(io_status, inputs=0),
      _io_step(io_status, inputs=0),
      _io_step(io_status, inputs=1 << _nmc.INPUT_DOOR_LOCKED),
      _io_step(io_status, inputs=0),
      _io_step(io_status, inputs=0),
    ]
    vspin, io = self._make_vspin(steps)

    await vspin.open_door()
    await vspin.close_door()
    await vspin.lock_door()
    await vspin.unlock_door()
    await vspin.lock_bucket()
    await vspin.unlock_bucket()

    io.assert_complete(self)
    self.assertTrue(all(write == io_status for write in io.writes))

  async def test_stop_closes_ftdi_without_resetting_the_nmc_network(self):
    vspin, io = self._make_vspin([])
    vspin._setup = AsyncMock()  # type: ignore[method-assign]

    async with vspin:
      self.assertTrue(io.is_active)

    io.assert_complete(self)
    self.assertTrue(io.stopped)
    self.assertEqual(io.writes, [])


class TestAccess2Events(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.vspin_ftdi = patch("pylabrobot.agilent.vspin.vspin.FTDI", autospec=True)
    self.access2_ftdi = patch("pylabrobot.agilent.vspin.access2.FTDI", autospec=True)
    self.vspin_ftdi.start()
    self.access2_ftdi.start()
    self.addCleanup(self.access2_ftdi.stop)
    self.addCleanup(self.vspin_ftdi.stop)

  async def asyncSetUp(self):
    self.vspin = VSpin(name="centrifuge", device_id="test")
    self.vspin.io = cast(FTDI, RecordingFTDI())
    self.vspin._setup = AsyncMock()  # type: ignore[method-assign]
    self.vspin.request_door_open = AsyncMock(return_value=True)  # type: ignore[method-assign]
    self.vspin.request_bucket_locked = AsyncMock(return_value=True)  # type: ignore[method-assign]
    self.vspin._request_rotor_safe_to_access = AsyncMock(  # type: ignore[method-assign]
      return_value=True
    )
    self.loader = Access2(name="loader", device_id="test", vspin=self.vspin)
    self.loader.driver.io = cast(FTDI, RecordingFTDI())
    self.loader.driver._initialize = AsyncMock()  # type: ignore[method-assign]
    self.loader.driver.load = AsyncMock()  # type: ignore[method-assign]
    self.loader.driver.unload = AsyncMock()  # type: ignore[method-assign]
    self.scope = AsyncExitStackWithShielding()
    await self.scope.__aenter__()
    self.addAsyncCleanup(self.scope.aclose)
    await self.scope.enter_async_context(self.vspin.lifespan())
    _mark_vspin_ready(self.vspin)
    self.vspin._at_bucket = self.vspin.bucket1
    await self.scope.enter_async_context(self.loader.lifespan())

  async def test_setup_lifecycle_events(self):
    """Report one correlated lifecycle and preserve the original failure."""
    for error in (None, RuntimeError("setup failed")):
      with self.subTest(error=error):
        with patch("pylabrobot.agilent.vspin.access2.FTDI"):
          loader = Access2(name="event_loader", device_id="test", vspin=self.vspin)
        io = RecordingFTDI()
        loader.driver.io = cast(FTDI, io)
        loader.driver._initialize = AsyncMock(side_effect=error)  # type: ignore[method-assign]
        events: list[PLREvent] = []
        event_bus = EventBus()
        event_bus.subscribe(events.append)

        with use_event_bus(event_bus):
          if error is None:
            async with loader:
              self.assertTrue(io.is_active)
          else:
            with self.assertRaises(RuntimeError) as raised:
              async with loader:
                self.fail("failed entry reached body")
            self.assertIs(raised.exception, error)

        self.assertFalse(io.is_active)
        setup_events = [
          event for event in events if event.name.startswith("centrifuge_loader.setup.")
        ]
        self.assertEqual(
          [event.name for event in setup_events],
          [
            "centrifuge_loader.setup.started",
            "centrifuge_loader.setup." + ("completed" if error is None else "failed"),
          ],
        )
        started, terminal = setup_events
        self.assertEqual(started.data, {"device": resource_reference(loader)})
        self.assertEqual(terminal.data["device"], started.data["device"])
        self.assertEqual(started.context["operation"], "centrifuge_loader.setup")
        self.assertEqual(started.context["operation_id"], terminal.context["operation_id"])
        self.assertNotIn("backend", terminal.data)
        if error is not None:
          self.assertEqual(terminal.data["error_type"], "RuntimeError")
          self.assertEqual(terminal.data["error_message"], str(error))

  async def test_stop_lifecycle_events(self):
    """Report one correlated lifecycle and preserve the original failure."""
    for error in (None, RuntimeError("stop failed")):
      with self.subTest(error=error):
        with patch("pylabrobot.agilent.vspin.access2.FTDI"):
          loader = Access2(name="event_loader", device_id="test", vspin=self.vspin)
        io = RecordingFTDI(exit_error=error)
        loader.driver.io = cast(FTDI, io)
        loader.driver._initialize = AsyncMock()  # type: ignore[method-assign]
        events: list[PLREvent] = []
        event_bus = EventBus()
        event_bus.subscribe(events.append)

        with use_event_bus(event_bus):
          if error is None:
            async with loader:
              self.assertTrue(io.is_active)
          else:
            with self.assertRaises(RuntimeError) as raised:
              async with loader:
                self.assertTrue(io.is_active)
            self.assertIs(raised.exception, error)

        stop_events = [
          event for event in events if event.name.startswith("centrifuge_loader.stop.")
        ]
        self.assertEqual(
          [event.name for event in stop_events],
          [
            "centrifuge_loader.stop.started",
            "centrifuge_loader.stop." + ("completed" if error is None else "failed"),
          ],
        )
        started, terminal = stop_events
        self.assertEqual(started.data, {"device": resource_reference(loader)})
        self.assertEqual(terminal.data["device"], started.data["device"])
        self.assertEqual(started.context["operation"], "centrifuge_loader.stop")
        self.assertEqual(started.context["operation_id"], terminal.context["operation_id"])
        self.assertNotIn("backend", terminal.data)
        if error is not None:
          self.assertEqual(terminal.data["error_type"], "RuntimeError")
          self.assertEqual(terminal.data["error_message"], str(error))

  async def test_load_emits_loader_to_bucket_transfer(self):
    plate = Resource("plate_1", size_x=1, size_y=1, size_z=1)
    self.loader.assign_child_resource(plate, location=Coordinate.zero())
    events: list[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus):
      await self.loader.load()

    lifecycle_events = [
      event for event in events if event.name.startswith("centrifuge_loader.load.")
    ]
    self.assertEqual(
      [event.name for event in lifecycle_events],
      [
        "centrifuge_loader.load.started",
        "centrifuge_loader.load.completed",
      ],
    )
    started, completed = lifecycle_events
    self.assertEqual(started.context["operation_id"], completed.context["operation_id"])
    self.assertEqual(started.data["resources"][0]["name"], "plate_1")
    self.assertEqual(started.data["source"]["name"], "loader")
    self.assertEqual(started.data["destination"]["name"], "centrifuge_bucket1")
    self.assertIs(self.vspin.bucket1.resource, plate)
    self.loader.driver.load.assert_awaited_once_with(  # type: ignore[attr-defined]
      protocol.TEACHPOINT_BUCKET_1,
      plate_height=10,
      source_z_offset=3,
      destination_z_offset=3,
      park_z_offset=3,
      gripper_open_position=0,
      gripper_closed_position=5.68,
      gripper_close_threshold=1.5,
      source_speed="slow",
      destination_speed="slow",
      park_speed="slow",
      gripper_open_speed="fast",
      gripper_close_speed="slow",
      gripper_release_speed="slow",
    )

  async def test_load_maps_presented_bucket_2_to_its_teachpoint(self):
    plate = Resource("plate_1", size_x=1, size_y=1, size_z=1)
    self.loader.assign_child_resource(plate, location=Coordinate.zero())
    self.vspin._at_bucket = self.vspin.bucket2

    await self.loader.load()

    self.loader.driver.load.assert_awaited_once_with(  # type: ignore[attr-defined]
      protocol.TEACHPOINT_BUCKET_2,
      plate_height=10,
      source_z_offset=3,
      destination_z_offset=3,
      park_z_offset=3,
      gripper_open_position=0,
      gripper_closed_position=5.68,
      gripper_close_threshold=1.5,
      source_speed="slow",
      destination_speed="slow",
      park_speed="slow",
      gripper_open_speed="fast",
      gripper_close_speed="slow",
      gripper_release_speed="slow",
    )
    self.assertIs(self.vspin.bucket2.resource, plate)

  async def test_transfer_parameters_reach_driver_and_events(self):
    plate = Resource("plate_1", size_x=127, size_y=85, size_z=22)
    self.loader.assign_child_resource(plate, location=Coordinate.zero())
    parameters = {
      "plate_height": 22.0,
      "source_z_offset": 4.0,
      "destination_z_offset": 2.0,
      "park_z_offset": 1.0,
      "gripper_open_position": 0.25,
      "gripper_closed_position": 4.75,
      "gripper_close_threshold": 1.8,
      "source_speed": "medium",
      "destination_speed": "fast",
      "park_speed": "medium",
      "gripper_open_speed": "slow",
      "gripper_close_speed": "medium",
      "gripper_release_speed": "fast",
    }
    events: list[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus):
      await self.loader.load(**parameters)
      self.assertIs(self.vspin.bucket1.resource, plate)
      self.assertIsNone(self.loader.resource)
      await self.loader.unload(**parameters)

    self.loader.driver.load.assert_awaited_once_with(  # type: ignore[attr-defined]
      protocol.TEACHPOINT_BUCKET_1, **parameters
    )
    self.loader.driver.unload.assert_awaited_once_with(  # type: ignore[attr-defined]
      protocol.TEACHPOINT_BUCKET_1, **parameters
    )
    self.assertIs(self.loader.resource, plate)
    self.assertIsNone(self.vspin.bucket1.resource)
    started = [
      event
      for event in events
      if event.name in ("centrifuge_loader.load.started", "centrifuge_loader.unload.started")
    ]
    self.assertEqual(len(started), 2)
    for event in started:
      self.assertEqual(event.data["parameters"], parameters)

  async def test_invalid_transfer_settings_release_vspin_without_recovery(self):
    plate = Resource("plate_1", size_x=127, size_y=85, size_z=22)
    self.loader.driver.load = cast(Any, partial(Access2Driver.load, self.loader.driver))  # type: ignore[method-assign]
    self.loader.assign_child_resource(plate, location=Coordinate.zero())
    before = self.vspin.state
    loader_before = self.loader.driver.state

    with self.assertRaisesRegex(ValueError, "open position < close threshold"):
      await self.loader.load(gripper_open_position=2, gripper_close_threshold=1)

    self.assertEqual(self.vspin.state, before)
    self.assertEqual(self.loader.driver.state, loader_before)
    self.assertIs(self.loader.resource, plate)
    self.assertIsNone(self.vspin.bucket1.resource)

  async def test_actuated_loader_failure_blocks_vspin_motion(self):
    plate = Resource("plate_1", size_x=1, size_y=1, size_z=1)
    self.loader.assign_child_resource(plate, location=Coordinate.zero())

    async def fail_after_actuation(bucket_teachpoint: int, **parameters: float | str) -> None:
      self.assertEqual(bucket_teachpoint, protocol.TEACHPOINT_BUCKET_1)
      self.loader.driver._mark_recovery_required(position_uncertain=False)
      raise RuntimeError("loader motion failed")

    self.loader.driver.load = AsyncMock(side_effect=fail_after_actuation)  # type: ignore[method-assign]

    with self.assertRaisesRegex(RuntimeError, "loader motion failed"):
      await self.loader.load()

    self.assertTrue(self.vspin.state.recovery_required)
    self.assertEqual(self.vspin.state.activity, VSpinActivity.TRANSFERRING)
    self.assertIs(self.loader.resource, plate)
    self.assertIsNone(self.vspin.bucket1.resource)

  async def test_unload_failure_emits_bucket_to_loader_transfer(self) -> None:
    plate = Resource("plate_1", size_x=1, size_y=1, size_z=1)
    self.vspin.bucket1.assign_child_resource(plate, location=Coordinate.zero())
    self.loader.driver.unload = AsyncMock(side_effect=RuntimeError("loader fault"))  # type: ignore[method-assign]
    events: list[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus), pytest.raises(RuntimeError, match="loader fault"):
      await self.loader.unload()

    lifecycle_events = [
      event for event in events if event.name.startswith("centrifuge_loader.unload.")
    ]
    self.assertEqual(
      [event.name for event in lifecycle_events],
      [
        "centrifuge_loader.unload.started",
        "centrifuge_loader.unload.failed",
      ],
    )
    started, failed = lifecycle_events
    self.assertEqual(started.context["operation_id"], failed.context["operation_id"])
    self.assertEqual(started.data["source"]["name"], "centrifuge_bucket1")
    self.assertEqual(started.data["destination"]["name"], "loader")
    self.assertEqual(failed.data["error_type"], "RuntimeError")

  async def test_load_requires_physical_bucket_lock_before_driver_motion(self):
    plate = Resource("plate_1", size_x=1, size_y=1, size_z=1)
    self.loader.assign_child_resource(plate, location=Coordinate.zero())
    self.vspin.request_bucket_locked = AsyncMock(return_value=False)  # type: ignore[method-assign]

    with self.assertRaisesRegex(RuntimeError, "physically locked"):
      await self.loader.load()

    self.loader.driver.load.assert_not_awaited()  # type: ignore[attr-defined]
    self.assertIs(self.loader.resource, plate)
    self.assertIsNone(self.vspin.bucket1.resource)

  async def test_unload_requires_stopped_vspin_before_driver_motion(self):
    plate = Resource("plate_1", size_x=1, size_y=1, size_z=1)
    self.vspin.bucket1.assign_child_resource(plate, location=Coordinate.zero())
    self.vspin._request_rotor_safe_to_access = AsyncMock(  # type: ignore[method-assign]
      return_value=False
    )

    with self.assertRaisesRegex(RuntimeError, "must be stopped"):
      await self.loader.unload()

    self.loader.driver.unload.assert_not_awaited()  # type: ignore[attr-defined]
    self.assertIs(self.vspin.bucket1.resource, plate)
    self.assertIsNone(self.loader.resource)

  async def test_load_requires_physical_door_open_before_driver_motion(self):
    plate = Resource("plate_1", size_x=1, size_y=1, size_z=1)
    self.loader.assign_child_resource(plate, location=Coordinate.zero())
    self.vspin.request_door_open = AsyncMock(return_value=False)  # type: ignore[method-assign]

    with self.assertRaisesRegex(CentrifugeDoorError, "door-open sensor"):
      await self.loader.load()

    self.loader.driver.load.assert_not_awaited()  # type: ignore[attr-defined]
