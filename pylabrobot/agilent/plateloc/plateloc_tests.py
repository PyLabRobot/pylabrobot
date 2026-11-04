import contextlib
import logging
from collections import deque
from dataclasses import replace
from typing import Any, Awaitable, Callable, Deque, List, Optional, Type, Union, cast
from unittest.mock import MagicMock, patch

import anyio
import pytest

import pylabrobot.agilent.plateloc.plateloc as plateloc_module
from pylabrobot.agilent.plateloc import (
  PlateLoc,
  PlateLocError,
  PlateLocSerialProfile,
  PlateLocStatus,
)
from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.io.serial import Serial
from pylabrobot.testing.concurrency import AnyioTestBase

ResponseEffect = Union[bytes, BaseException, Callable[[], Awaitable[bytes]]]
WriteEffect = Callable[[], Awaitable[None]]


class FakeSerialIO(HasLifespan):
  """Replay PlateLoc responses and record lifecycle and wire events without hardware."""

  def __init__(
    self,
    responses: Optional[List[ResponseEffect]] = None,
    events: Optional[List[str]] = None,
    entry_error: Optional[BaseException] = None,
    exit_error: Optional[BaseException] = None,
    block_entry: Optional[anyio.Event] = None,
    after_entry: Optional[Callable[[], None]] = None,
    before_write: Optional[dict[bytes, WriteEffect]] = None,
    after_write: Optional[dict[bytes, WriteEffect]] = None,
  ) -> None:
    super().__init__()
    self.responses: Deque[ResponseEffect] = deque(responses or [])
    self.events = events if events is not None else []
    self.entry_error = entry_error
    self.exit_error = exit_error
    self.block_entry = block_entry
    self.after_entry = after_entry
    self.before_write = before_write or {}
    self.after_write = after_write or {}
    self.writes: List[bytes] = []
    self.reset_count = 0
    self.port = "COM6"
    self._rx = bytearray()

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    if kwargs:
      raise TypeError("FakeSerialIO does not accept lifespan options")
    self.events.append("serial.enter")
    stack.push_shielded_async_callback(self._close)
    if self.entry_error is not None:
      raise self.entry_error
    if self.block_entry is not None:
      self.block_entry.set()
      await anyio.sleep_forever()
    if self.after_entry is not None:
      self.after_entry()

  async def _close(self) -> None:
    await anyio.lowlevel.checkpoint()
    self.events.append("serial.exit")
    if self.exit_error is not None:
      raise self.exit_error

  async def reset_input_buffer(self) -> None:
    await anyio.lowlevel.checkpoint()
    self.reset_count += 1
    self._rx.clear()
    self.events.append("serial.reset_input")

  async def write(self, data: bytes) -> None:
    await anyio.lowlevel.checkpoint()
    effect = self.before_write.get(data)
    if effect is not None:
      await effect()
    self.writes.append(data)
    self.events.append(f"write:{data!r}")
    effect = self.after_write.get(data)
    if effect is not None:
      await effect()

  async def read(self, num_bytes: int = 1) -> bytes:
    await anyio.lowlevel.checkpoint()
    if not self._rx and self.responses:
      effect = self.responses.popleft()
      if isinstance(effect, BaseException):
        raise effect
      response = await effect() if callable(effect) else effect
      self._rx.extend(response)
    chunk = bytes(self._rx[:num_bytes])
    del self._rx[:num_bytes]
    return chunk

  def temporary_timeout(self, _timeout: float) -> contextlib.AbstractContextManager[None]:
    return contextlib.nullcontext()


def make_device(
  responses: Optional[List[ResponseEffect]] = None,
  events: Optional[List[str]] = None,
  entry_error: Optional[BaseException] = None,
  exit_error: Optional[BaseException] = None,
  block_entry: Optional[anyio.Event] = None,
  after_entry: Optional[Callable[[], None]] = None,
  before_write: Optional[dict[bytes, WriteEffect]] = None,
  after_write: Optional[dict[bytes, WriteEffect]] = None,
  ack_timeout: float = 0.01,
  timeout: float = 30,
  device_type: Type[PlateLoc] = PlateLoc,
) -> tuple[PlateLoc, FakeSerialIO]:
  """Build a PlateLoc backed by a hardware-free serial transport."""
  profile = PlateLocSerialProfile(
    response_timeout=0.01,
    ack_timeout=ack_timeout,
    read_delay=0,
    stage_move_delay=0,
    cycle_poll_interval=0,
  )
  with patch.object(plateloc_module, "HAS_SERIAL", True):
    device = device_type(port="COM6", profile=profile, timeout=timeout)
  fake_io = FakeSerialIO(
    responses,
    events,
    entry_error,
    exit_error,
    block_entry,
    after_entry,
    before_write,
    after_write,
  )
  device.io = cast(Serial, fake_io)
  return device, fake_io


class RecordingPlateLoc(PlateLoc):
  """Record disconnect and optionally fail connected-state publication."""

  port_error: Optional[BaseException] = None
  lifecycle_events: Optional[List[str]] = None

  def _mark_disconnected(self) -> None:
    if self.lifecycle_events is not None:
      self.lifecycle_events.append("disconnected")
    super()._mark_disconnected()

  @property
  def port(self) -> str:
    if self.port_error is not None:
      error = self.port_error
      self.port_error = None
      raise error
    return super().port


class TestPlateLocProtocol(AnyioTestBase):
  async def test_read_response_waits_for_terminator_across_idle_poll(self) -> None:
    """An internal empty serial poll is not a completed partial reply."""
    device, _ = make_device([b"A", b"", b"B\r"])
    assert await device._read_response(timeout=0.1) == "AB"

  def test_constructor_uses_plr_serial_wrapper_settings(self) -> None:
    io = MagicMock(spec=Serial)
    io.port = "COM6"
    with (
      patch.object(plateloc_module, "HAS_SERIAL", True),
      patch.object(plateloc_module, "Serial", return_value=io) as serial_constructor,
    ):
      PlateLoc(port="COM6")

    serial_constructor.assert_called_once_with(
      human_readable_device_name="Agilent PlateLoc Sealer",
      port="COM6",
      vid=None,
      pid=None,
      baudrate=19200,
      bytesize=8,
      parity="N",
      stopbits=1,
      write_timeout=1,
      rtscts=False,
      dsrdtr=False,
      xonxoff=False,
    )

  async def test_sends_literal_serial_frame(self) -> None:
    device, fake_io = make_device([b"STAK\r"])

    assert await device._send_command("ST 0.030", timeout=0.01) == "STAK"
    assert fake_io.writes == [b"ST 0.030\r"]
    assert fake_io.reset_count == 1

  def test_serialize_contains_only_connection_configuration(self) -> None:
    device, _ = make_device(timeout=42)

    serialized = device.serialize()

    assert set(serialized) == {"port", "profile", "timeout"}
    assert serialized["port"] == "COM6"
    assert serialized["timeout"] == 42
    assert serialized["profile"] == device.profile.serialize()

  async def test_temperature_write_is_scaled_and_validated(self) -> None:
    device, fake_io = make_device([b"STAK\r"])

    await device.set_sealing_temperature(30)

    assert fake_io.writes == [b"ST 0.030\r"]
    with pytest.raises(ValueError):
      await device.set_sealing_temperature(19)

  async def test_negative_acknowledgement_raises_protocol_error(self) -> None:
    device, fake_io = make_device([b"STNK(Desired Temperature is Out of Range)\r\r"])

    with pytest.raises(PlateLocError, match="Desired Temperature is Out of Range"):
      await device.set_sealing_temperature(30)

    assert fake_io.writes == [b"ST 0.030\r"]

  async def test_missing_acknowledgement_raises_timeout(self) -> None:
    device, fake_io = make_device()

    with pytest.raises(TimeoutError, match="Timeout"):
      await device.set_sealing_temperature(30)

    assert fake_io.writes == [b"ST 0.030\r"]

  async def test_malformed_acknowledgement_raises_protocol_error(self) -> None:
    device, fake_io = make_device([b"unexpected\r"])

    with pytest.raises(PlateLocError, match="invalid response"):
      await device.set_sealing_temperature(30)

    assert fake_io.writes == [b"ST 0.030\r"]

  async def test_required_response_reads_until_plate_loc_ack(self) -> None:
    device, fake_io = make_device([b"CCAK\r"])

    assert await device.request_cycle_complete()
    assert fake_io.writes == [b"CC 00\r"]

  async def test_cycle_not_complete_returns_false(self) -> None:
    device, fake_io = make_device([b"CCNK\r"])

    assert not await device.request_cycle_complete()
    assert fake_io.writes == [b"CC 00\r"]

  async def test_invalid_cycle_complete_response_raises_protocol_error(self) -> None:
    device, fake_io = make_device([b"unexpected\r"])

    with pytest.raises(PlateLocError, match="invalid response"):
      await device.request_cycle_complete()

    assert fake_io.writes == [b"CC 00\r"]

  async def test_status_snapshot_tracks_setpoints_and_live_cycle_complete(self) -> None:
    device, fake_io = make_device(
      [b"STAK\r", b"SSAK\r", b"GOAK\r", b"CCAK\r", b"SOAK\r", b"CCAK\r"]
    )

    async with device:
      await device.seal(30, 0.5)
      await device.open()
      status = await device.request_status()

    assert isinstance(status, PlateLocStatus)
    assert status.port == "COM6"
    assert status.connected
    assert status.target_temperature == 30
    assert status.stage_position == "open"
    assert status.cycle_complete
    assert fake_io.writes == [
      b"ST 0.030\r",
      b"SS 0.05\r",
      b"GO 00\r",
      b"CC 00\r",
      b"SO 00\r",
      b"CC 00\r",
    ]

  async def test_seal_waits_for_cycle_completion(self) -> None:
    device, fake_io = make_device(
      [b"STAK\r", b"SSAK\r", b"GOAK\r", b"CCNK\r", b"CCAK\r"], timeout=1
    )

    await device.seal(120, 1.2)

    assert fake_io.writes == [
      b"ST 0.120\r",
      b"SS 0.12\r",
      b"GO 00\r",
      b"CC 00\r",
      b"CC 00\r",
    ]
    assert device.status_snapshot().target_temperature == 120

  async def test_seal_validates_duration_before_sending_commands(self) -> None:
    device, fake_io = make_device()

    with pytest.raises(ValueError):
      await device.seal(120, 0.4)

    assert fake_io.writes == []

  async def test_device_exposes_plain_sealer_api(self) -> None:
    device, fake_io = make_device(
      [
        b"STAK\r",
        b"STAK\r",
        b"SSAK\r",
        b"GOAK\r",
        b"CCAK\r",
        b"SOAK\r",
        b"SIAK\r",
        b"CCAK\r",
      ]
    )

    await device.set_sealing_temperature(100)
    await device.seal(120, 1.2)
    await device.open()
    await device.close()
    status = await device.request_status()

    assert fake_io.writes == [
      b"ST 0.100\r",
      b"ST 0.120\r",
      b"SS 0.12\r",
      b"GO 00\r",
      b"CC 00\r",
      b"SO 00\r",
      b"SI 00\r",
      b"CC 00\r",
    ]
    assert status.target_temperature == 120
    assert status.stage_position == "closed"
    assert status.cycle_complete


class TestPlateLocLifecycle(AnyioTestBase):
  def test_uses_managed_lifecycle_methods(self) -> None:
    assert PlateLoc.__aenter__ is HasLifespan.__aenter__
    assert PlateLoc.__aexit__ is HasLifespan.__aexit__
    assert PlateLoc.setup is HasLifespan.setup
    assert PlateLoc.stop is HasLifespan.stop

  async def test_normal_exit_closes_serial_and_clears_connected_state(self) -> None:
    events: List[str] = []
    device, _ = make_device(events=events)

    async with device:
      assert device.connected
      events.append("body")

    assert events == ["serial.enter", "body", "serial.exit"]
    assert not device.connected
    assert not device.is_active

  async def test_serial_entry_failure_rolls_back_without_publishing_connected(self) -> None:
    events: List[str] = []
    failure = RuntimeError("serial entry failed")
    device, _ = make_device(events=events, entry_error=failure)

    with pytest.raises(RuntimeError) as error_info:
      async with device:
        pytest.fail("failed serial entry reached the body")

    assert error_info.value is failure
    assert events == ["serial.enter", "serial.exit"]
    assert not device.connected
    assert not device.is_active

  async def test_cancelled_serial_entry_rolls_back_without_publishing_connected(self) -> None:
    events: List[str] = []
    entry_started = anyio.Event()
    device, _ = make_device(events=events, block_entry=entry_started)

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(_enter_and_wait, device)
      await entry_started.wait()
      task_group.cancel_scope.cancel()

    assert events == ["serial.enter", "serial.exit"]
    assert not device.connected
    assert not device.is_active

  async def test_pending_cancellation_after_serial_entry_unwinds_before_body(
    self, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    monkeypatch.setattr(logging.getLogger("pylabrobot"), "propagate", True)
    events: List[str] = []
    device, fake_io = make_device(events=events, device_type=RecordingPlateLoc)
    assert isinstance(device, RecordingPlateLoc)
    device.lifecycle_events = events
    body_reached = False

    with caplog.at_level("INFO", logger=plateloc_module.logger.name), anyio.CancelScope() as scope:
      fake_io.after_entry = scope.cancel
      async with device:
        body_reached = True

    assert not body_reached
    assert events == ["serial.enter", "disconnected", "serial.exit"]
    assert "disconnected" in caplog.text
    assert not device.connected
    assert not device.is_active

  async def test_connected_publication_failure_runs_disconnected_callback_before_serial_close(
    self,
  ) -> None:
    events: List[str] = []
    failure = RuntimeError("connected publication failed")
    device, _ = make_device(events=events, device_type=RecordingPlateLoc)
    assert isinstance(device, RecordingPlateLoc)
    device.lifecycle_events = events
    device.port_error = failure

    with pytest.raises(RuntimeError) as error_info:
      async with device:
        pytest.fail("failed connected publication reached the body")

    assert error_info.value is failure
    assert events == ["serial.enter", "disconnected", "serial.exit"]
    assert not device.connected
    assert not device.is_active

  async def test_body_cancellation_closes_serial_before_propagating(self) -> None:
    events: List[str] = []
    body_started = anyio.Event()
    device, _ = make_device(events=events)

    async def use_device() -> None:
      try:
        async with device:
          body_started.set()
          await anyio.sleep_forever()
      except anyio.get_cancelled_exc_class():
        events.append("cancelled")
        raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(use_device)
      await body_started.wait()
      task_group.cancel_scope.cancel()

    assert events == ["serial.enter", "serial.exit", "cancelled"]
    assert not device.connected
    assert not device.is_active

  async def test_sequential_reuse_enters_and_closes_serial_twice(self) -> None:
    events: List[str] = []
    device, _ = make_device(events=events)

    async with device:
      assert device.connected
    async with device:
      assert device.connected

    assert events == ["serial.enter", "serial.exit", "serial.enter", "serial.exit"]
    assert not device.connected

  async def test_serial_close_failure_propagates_and_clears_connected_state(self) -> None:
    events: List[str] = []
    failure = RuntimeError("serial close failed")
    device, _ = make_device(events=events, exit_error=failure)

    with pytest.raises(RuntimeError) as error_info:
      async with device:
        assert device.connected

    assert error_info.value is failure
    assert events == ["serial.enter", "serial.exit"]
    assert not device.connected
    assert not device.is_active

  async def test_rejects_unknown_lifespan_options_before_serial_entry(self) -> None:
    events: List[str] = []
    device, _ = make_device(events=events)

    with pytest.raises(TypeError, match="unexpected keyword argument"):
      async with device.lifespan(unknown=True):
        pytest.fail("invalid entry reached the body")

    assert events == []


class TestPlateLocCycleCancellation(AnyioTestBase):
  async def test_zero_cycle_timeout_checks_ready_status_before_stopping(self) -> None:
    device, io = make_device([b"STAK\r", b"SSAK\r", b"GOAK\r", b"CCAK\r"], timeout=0)

    with anyio.fail_after(0.3):
      assert await device.seal(120, 1.2) == "GOAK"

    assert io.writes == [b"ST 0.120\r", b"SS 0.12\r", b"GO 00\r", b"CC 00\r"]
    assert not device._cycle_active

  async def test_cycle_timeout_interrupts_stalled_status_poll_and_stops_cycle(self) -> None:
    async def stalled_poll() -> bytes:
      await anyio.sleep_forever()
      raise AssertionError("unreachable")

    device, io = make_device(
      [b"STAK\r", b"SSAK\r", b"GOAK\r", stalled_poll, b"ACAK\r"], timeout=0.02
    )
    device.profile = replace(device.profile, response_timeout=1)

    with anyio.fail_after(0.3):
      with pytest.raises(TimeoutError, match="PlateLoc cycle"):
        await device.seal(120, 1.2)

    assert io.writes == [b"ST 0.120\r", b"SS 0.12\r", b"GO 00\r", b"CC 00\r", b"AC 00\r"]
    assert not device._cycle_active

  async def test_cancellation_before_cycle_start_write_does_not_stop_cycle(self) -> None:
    events: List[str] = []
    scopes: List[anyio.CancelScope] = []

    async def cancel_before_go_write() -> None:
      scopes[0].cancel()

    device, fake_io = make_device(
      [b"STAK\r", b"SSAK\r"],
      events,
      after_write={b"SS 0.12\r": cancel_before_go_write},
    )

    with anyio.CancelScope() as scope:
      scopes.append(scope)
      await device.seal(120, 1.2)

    assert fake_io.writes == [b"ST 0.120\r", b"SS 0.12\r"]
    assert b"AC 00\r" not in fake_io.writes

  async def test_cancellation_after_cycle_start_write_stops_before_propagating(self) -> None:
    events: List[str] = []
    go_write_recorded = anyio.Event()

    async def record_go_write() -> None:
      go_write_recorded.set()

    async def complete_stop() -> None:
      events.append("stop.started")
      await anyio.lowlevel.checkpoint()
      events.append("stop.completed")

    device, fake_io = make_device(
      [b"STAK\r", b"SSAK\r", b"GOAK\r", b"ACAK\r"],
      events,
      after_write={b"GO 00\r": record_go_write, b"AC 00\r": complete_stop},
    )

    async def seal() -> None:
      try:
        async with device:
          await device.seal(120, 1.2)
      except anyio.get_cancelled_exc_class():
        events.append("cancelled")
        raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(seal)
      await go_write_recorded.wait()
      task_group.cancel_scope.cancel()

    assert fake_io.writes == [b"ST 0.120\r", b"SS 0.12\r", b"GO 00\r", b"AC 00\r"]
    assert events.index("stop.completed") < events.index("serial.exit")
    assert events.index("serial.exit") < events.index("cancelled")

  async def test_cancellation_after_cycle_start_stops_before_propagating(self) -> None:
    events: List[str] = []
    cycle_poll_started = anyio.Event()

    async def block_cycle_poll() -> bytes:
      cycle_poll_started.set()
      await anyio.sleep_forever()
      raise AssertionError("unreachable")

    async def complete_stop() -> None:
      events.append("stop.started")
      await anyio.lowlevel.checkpoint()
      events.append("stop.completed")

    device, fake_io = make_device(
      [b"STAK\r", b"SSAK\r", b"GOAK\r", block_cycle_poll, b"ACAK\r"],
      events,
      after_write={b"AC 00\r": complete_stop},
    )

    async def seal() -> None:
      try:
        await device.seal(120, 1.2)
      except anyio.get_cancelled_exc_class():
        events.append("cancelled")
        raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(seal)
      await cycle_poll_started.wait()
      task_group.cancel_scope.cancel()

    assert fake_io.writes == [
      b"ST 0.120\r",
      b"SS 0.12\r",
      b"GO 00\r",
      b"CC 00\r",
      b"AC 00\r",
    ]
    assert events.index("stop.completed") < events.index("cancelled")

  async def test_normal_cycle_completion_does_not_send_stop(self) -> None:
    device, fake_io = make_device([b"STAK\r", b"SSAK\r", b"GOAK\r", b"CCAK\r"])

    assert await device.seal(120, 1.2) == "GOAK"

    assert fake_io.writes == [b"ST 0.120\r", b"SS 0.12\r", b"GO 00\r", b"CC 00\r"]


async def _enter_and_wait(device: PlateLoc) -> None:
  async with device:
    pytest.fail("cancelled entry reached the body")
