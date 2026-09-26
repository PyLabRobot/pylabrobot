import contextlib
from typing import Any, Awaitable, Callable, Dict, Iterator, List, Optional, cast

import anyio
import pytest

from pylabrobot.azenta.fluidx import (
  CartridgeProfile,
  ExtendedStatus,
  FluidXError,
  FluidXIntelliXcap96,
  get_error_message,
  is_recoverable_error,
)
from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.io.serial import Serial
from pylabrobot.testing.concurrency import AnyioTestBase

ACK = "\x06"


WriteHook = Callable[[], Awaitable[None]]


class FakeXcapSerial(HasLifespan):
  """In-memory serial stand-in for the STX/ETX framed decapper protocol.

  ``script`` is a list of turns, one per ``write``. Each turn is the list of
  frame payloads the device emits in response to that write; every payload is
  queued wrapped in STX (0x02) .. ETX (0x03), exactly as the firmware frames it.
  """

  def __init__(
    self,
    script: List[List[str]],
    *,
    events: Optional[List[str]] = None,
    entry_error: Optional[BaseException] = None,
    exit_error: Optional[BaseException] = None,
    block_entry: Optional[anyio.Event] = None,
    after_write: Optional[Dict[str, WriteHook]] = None,
    reply_complete_hook: Optional[WriteHook] = None,
    reply_complete_hooks: Optional[List[WriteHook]] = None,
    delayed_script: Optional[List[List[str]]] = None,
    before_delayed_read: Optional[WriteHook] = None,
    empty_read_error: Optional[BaseException] = None,
  ) -> None:
    super().__init__()
    self.port = "FAKE"
    self.written: List[str] = []
    self._script = list(script)
    self._rx = bytearray()
    self.events = events if events is not None else []
    self.entry_error = entry_error
    self.exit_error = exit_error
    self.block_entry = block_entry
    self.after_write = after_write or {}
    self.reply_complete_hooks = list(reply_complete_hooks or [])
    if reply_complete_hook is not None:
      self.reply_complete_hooks.append(reply_complete_hook)
    self._delayed_script = list(delayed_script or [])
    self._delayed: List[str] = []
    self._initial_gap_returned = False
    self.before_delayed_read = before_delayed_read
    self.empty_read_error = empty_read_error
    self.timeout = 5.0

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    if kwargs:
      raise TypeError("FakeXcapSerial does not accept lifespan options")
    self.events.append("serial.enter")
    stack.push_shielded_async_callback(self._close)
    if self.entry_error is not None:
      raise self.entry_error
    if self.block_entry is not None:
      self.block_entry.set()
      await anyio.sleep_forever()

  async def _close(self) -> None:
    await anyio.lowlevel.checkpoint()
    self.events.append("serial.exit")
    if self.exit_error is not None:
      raise self.exit_error

  async def reset_input_buffer(self) -> None:
    await anyio.lowlevel.checkpoint()
    self.events.append("input.reset")
    self._rx.clear()

  def get_read_timeout(self) -> float:
    return self.timeout

  def set_read_timeout(self, timeout: float) -> None:
    self.timeout = timeout
    self.events.append(f"timeout:{timeout}")

  @contextlib.contextmanager
  def temporary_timeout(self, timeout: float) -> Iterator[None]:
    original = self.get_read_timeout()
    self.set_read_timeout(timeout)
    try:
      yield
    finally:
      self.set_read_timeout(original)

  async def write(self, data: bytes) -> None:
    await anyio.lowlevel.checkpoint()
    command = data.decode("ascii").rstrip("\x03")
    self.written.append(command)
    self.events.append(f"write:{command}")
    turn = self._script.pop(0) if self._script else []
    self._delayed = self._delayed_script.pop(0) if self._delayed_script else []
    self._initial_gap_returned = False
    for payload in turn:
      self._rx += b"\x02" + payload.encode("ascii") + b"\x03"
    hook = self.after_write.get(command)
    if hook is not None:
      await hook()

  async def read(self, num_bytes: int = 1) -> bytes:
    await anyio.lowlevel.checkpoint()
    self.events.append("read")
    if not self._rx:
      if self.empty_read_error is not None:
        error = self.empty_read_error
        self.empty_read_error = None
        raise error
      if self._delayed and self._initial_gap_returned:
        if self.before_delayed_read is not None:
          hook = self.before_delayed_read
          self.before_delayed_read = None
          await hook()
        for payload in self._delayed:
          self.events.append(f"delayed:{payload}")
          self._rx += b"\x02" + payload.encode("ascii") + b"\x03"
        self._delayed = []
      elif self._delayed:
        self._initial_gap_returned = True
      if self._rx:
        out = bytes(self._rx[:num_bytes])
        del self._rx[:num_bytes]
        return out
      if self.reply_complete_hooks:
        hook = self.reply_complete_hooks.pop(0)
        await hook()
      self.events.append("reply.complete")
      await anyio.sleep_forever()
      raise AssertionError("unreachable")
    out = bytes(self._rx[:num_bytes])
    del self._rx[:num_bytes]
    return out


def status(word: str, echo: str = "a") -> List[str]:
  """A full status reply: ACK, command echo, and the status word."""
  return [ACK, f"{echo}OK", word]


def query(command: str, payload: str) -> List[str]:
  """A full query reply: ACK, command echo, and the data frame."""
  return [ACK, f"{command}OK", payload]


def extended(
  caps_on_pins: bool = False,
  cartridge_installed: bool = False,
  estop_active: bool = False,
) -> List[str]:
  """An extended status reply built from the flags a test cares about."""
  bits = ["0"] * 12
  bits[0] = "1" if caps_on_pins else "0"
  bits[6] = "1" if cartridge_installed else "0"
  bits[10] = "1" if estop_active else "0"
  return query("e", "".join(bits))


class TestIntelliXcap96(AnyioTestBase):
  def _make(
    self,
    script: List[List[str]],
    auto_recover: bool = True,
    **fake_kwargs: Any,
  ) -> FluidXIntelliXcap96:
    device = FluidXIntelliXcap96(
      port="FAKE",
      auto_recover=auto_recover,
      command_delay=0,
      poll_interval=0,
      frame_gap=0.01,
    )
    device.io = cast(Serial, FakeXcapSerial(script, **fake_kwargs))
    return device

  def _written(self, device: FluidXIntelliXcap96) -> List[str]:
    return cast(FakeXcapSerial, device.io).written

  # === Connection ===

  async def test_setup_ok(self):
    device = self._make([status("StatusOK")])
    with anyio.fail_after(1):
      async with device:
        pass
    assert self._written(device) == ["a"]

  async def test_setup_busy_reports_engaged_estop(self):
    device = self._make([status("StatusBUSY"), extended(estop_active=True)])
    with pytest.raises(FluidXError) as ctx:
      async with device:
        pytest.fail("failed setup reached body")
    assert "E-STOP is engaged" in str(ctx.value)

  async def test_setup_busy_still_warns_when_estop_unreadable(self):
    device = self._make([status("StatusBUSY")])
    with pytest.raises(FluidXError) as ctx:
      async with device:
        pytest.fail("failed setup reached body")
    assert "E-STOP" in str(ctx.value)

  async def test_setup_error_reports_error_code(self):
    device = self._make([status("StatusError"), query("8", "137")])
    with pytest.raises(FluidXError) as ctx:
      async with device:
        pytest.fail("failed setup reached body")
    assert ctx.value.error_code == 137
    assert "Maximum recap attempts exceeded" in str(ctx.value)

  async def test_setup_error_raises_without_error_code(self):
    device = self._make([status("StatusError")])
    with pytest.raises(FluidXError) as ctx:
      async with device:
        pytest.fail("failed setup reached body")
    assert ctx.value.error_code is None

  async def test_setup_manual_recovery_raises(self):
    device = self._make([status("StatusMANUAL")])
    with pytest.raises(FluidXError) as ctx:
      async with device:
        pytest.fail("failed setup reached body")
    assert "manual recovery" in str(ctx.value)

  async def test_request_status_returns_status_word(self):
    device = self._make([status("StatusOK")])
    assert await device.request_status() == "StatusOK"

  def test_managed_methods_are_inherited(self) -> None:
    assert FluidXIntelliXcap96.__aenter__ is HasLifespan.__aenter__
    assert FluidXIntelliXcap96.__aexit__ is HasLifespan.__aexit__
    assert FluidXIntelliXcap96.setup is HasLifespan.setup
    assert FluidXIntelliXcap96.stop is HasLifespan.stop

  async def test_serial_entry_failure_rolls_back(self) -> None:
    events: List[str] = []
    failure = RuntimeError("serial entry failed")
    device = self._make([], events=events, entry_error=failure)

    with pytest.raises(RuntimeError) as error_info:
      async with device:
        pytest.fail("failed serial entry reached body")

    assert error_info.value is failure
    assert events == ["serial.enter", "serial.exit"]
    assert not device.is_active

  async def test_cancelled_serial_entry_rolls_back(self) -> None:
    events: List[str] = []
    entry_started = anyio.Event()
    device = self._make([], events=events, block_entry=entry_started)

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(device.__aenter__)
      await entry_started.wait()
      task_group.cancel_scope.cancel()

    assert events == ["serial.enter", "serial.exit"]
    assert not device.is_active

  async def test_body_cancellation_closes_serial_before_propagating(self) -> None:
    events: List[str] = []
    body_started = anyio.Event()
    device = self._make([status("StatusOK")], events=events)

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

    assert events[-2:] == ["serial.exit", "cancelled"]
    assert not device.is_active

  async def test_sequential_reuse_enters_and_checks_status_twice(self) -> None:
    events: List[str] = []
    device = self._make([status("StatusOK"), status("StatusOK")], events=events)

    async with device:
      pass
    async with device:
      pass

    assert self._written(device) == ["a", "a"]
    assert events.count("serial.enter") == 2
    assert events.count("serial.exit") == 2

  async def test_serial_close_failure_propagates(self) -> None:
    events: List[str] = []
    failure = RuntimeError("serial close failed")
    device = self._make([status("StatusOK")], events=events, exit_error=failure)

    with pytest.raises(RuntimeError) as error_info:
      async with device:
        pass

    assert error_info.value is failure
    assert events[-1] == "serial.exit"
    assert not device.is_active

  async def test_rejects_lifespan_options_before_serial_entry(self) -> None:
    events: List[str] = []
    device = self._make([], events=events)

    with pytest.raises(TypeError, match="unexpected keyword argument"):
      async with device.lifespan(unknown=True):
        pytest.fail("invalid entry reached body")

    assert events == []

  # === Error codes ===

  def test_get_error_message(self):
    assert get_error_message(145) == "Light curtain calibration max retries exceeded."
    assert get_error_message(999) == "Unknown IntelliXcap error code."

  def test_fluidx_error_from_error_code(self):
    error = FluidXError.from_error_code(145)
    assert error.error_code == 145
    assert str(error) == "IntelliXcap error 145: Light curtain calibration max retries exceeded."

  def test_recoverable_errors_are_the_ones_homing_clears(self):
    assert is_recoverable_error(113)
    assert is_recoverable_error(144)
    assert not is_recoverable_error(137)
    assert FluidXError.from_error_code(117).recoverable
    assert not FluidXError.from_error_code(137).recoverable
    assert not FluidXError(title="no code").recoverable

  async def test_request_error_code_returns_latched_code(self):
    device = self._make([query("8", "142")])
    assert await device.request_error_code() == 142
    assert self._written(device) == ["8"]

  async def test_request_error_code_is_none_when_clear(self):
    device = self._make([query("8", "000")])
    assert await device.request_error_code() is None

  async def test_request_error_code_raises_on_garbage(self):
    device = self._make([query("8", "nope")])
    with pytest.raises(FluidXError):
      await device.request_error_code()

  # === Queries ===

  async def test_request_extended_status(self):
    device = self._make([extended(caps_on_pins=True, cartridge_installed=True)])
    result = await device.request_extended_status()
    assert result.caps_on_pins
    assert result.cartridge_installed
    assert not result.estop_active
    assert not result.time_for_service

  async def test_caps_on_pins(self):
    device = self._make([extended(caps_on_pins=True)])
    assert await device.caps_on_pins()

  def test_extended_status_rejects_an_unexpected_width(self):
    # The command list names 12 flags but shows an 11-character answer. Padding
    # either end would guess at CAPS_ON_PINS, so the mismatch is surfaced.
    with pytest.raises(FluidXError) as ctx:
      ExtendedStatus.from_raw("00000100000")
    assert "11 bits, expected 12" in str(ctx.value)

  def test_extended_status_rejects_a_non_bitmask(self):
    with pytest.raises(FluidXError):
      ExtendedStatus.from_raw("10201")

  async def test_request_firmware_versions(self):
    device = self._make([query("V", "0044,0014,0003")])
    versions = await device.request_firmware_versions()
    assert versions.unit == "0044"
    assert versions.touchscreen == "0014"
    assert versions.light_curtain == "0003"

  async def test_request_cartridge_info(self):
    device = self._make([query("N", "016,000123,00000000")])
    info = await device.request_cartridge_info()
    assert info.profile == 16
    assert info.cycle_count == 123
    assert info.serial == "00000000"

  async def test_request_profile(self):
    device = self._make([query("E", "16032")])
    profile = await device.request_profile()
    assert profile.number == 16
    assert profile.communication_protocol == 0
    assert profile.decap_max_retry == 3
    assert profile.recap_max_retry == 2

  def test_profile_rejects_a_malformed_reply(self):
    with pytest.raises(FluidXError):
      CartridgeProfile.from_raw("160")

  # === Tray ===

  async def test_open_tray_moves_then_idle(self):
    device = self._make(
      [
        status("StatusOK"),  # _ensure_ready: not in error
        [ACK, "fOK"],  # f accepted
        status("StatusBUSY"),  # still moving
        status("StatusOK"),  # settled
      ]
    )
    await device.open_tray()
    assert self._written(device) == ["a", "f", "a", "a"]

  async def test_open_tray_finishes_on_its_own_done_frame(self):
    device = self._make([status("StatusOK"), [ACK, "fOK"], [ACK, "aOK", "OpenDONE"]])
    await device.open_tray()
    assert self._written(device) == ["a", "f", "a"]

  async def test_open_tray_already_open_is_noop(self):
    # CommandIgnore = tray already open = success; no status wait afterwards.
    device = self._make([status("StatusOK"), [ACK, "fOK", "CommandIgnore"]])
    await device.open_tray()
    assert self._written(device) == ["a", "f"]

  async def test_close_tray_moves_then_idle(self):
    device = self._make(
      [status("StatusOK"), [ACK, "gOK"], status("StatusBUSY"), status("StatusOK")]
    )
    await device.close_tray()
    assert self._written(device) == ["a", "g", "a", "a"]

  async def test_close_tray_preserves_caps_held_state(self):
    device = self._make(
      [status("StatusRECAP"), [ACK, "gOK"], status("StatusBUSY"), status("StatusRECAP")]
    )
    await device.close_tray()
    assert self._written(device) == ["a", "g", "a", "a"]

  async def test_step_tray_out_and_in(self):
    device = self._make(
      [
        status("StatusOK"),
        [ACK, "sOK", "sOK"],
        status("StatusOK"),
        [ACK, "SOK", "SOK"],
      ]
    )
    await device.step_tray_out()
    await device.step_tray_in()
    assert self._written(device) == ["a", "s", "a", "S"]

  async def test_tray_move_out_of_range_raises(self):
    device = self._make([status("StatusOK"), [ACK, "sOK", "sERROR"], query("8", "148")])
    with pytest.raises(FluidXError) as ctx:
      await device.step_tray_out()
    assert ctx.value.error_code == 148

  async def test_tray_move_times_out_without_a_second_echo(self):
    device = self._make([status("StatusOK"), [ACK, "sOK"]])
    with pytest.raises(FluidXError) as ctx:
      await device.step_tray_out(timeout=0.01)
    assert "timed out" in str(ctx.value)

  # === Homing and recovery ===

  async def test_home_moves_then_idle(self):
    device = self._make([[ACK, "ZOK"], status("StatusBUSY"), status("StatusOK")])
    await device.home()
    assert self._written(device) == ["Z", "a", "a"]

  async def test_reset_error_recovers_manual_state_by_homing(self):
    device = self._make(
      [
        status("StatusMANUAL"),
        [ACK, "ZOK"],
        status("StatusBUSY"),
        status("StatusOK"),
      ]
    )
    await device.reset_error()
    assert self._written(device) == ["a", "Z", "a", "a"]

  async def test_reset_error_recovers_error_state_by_homing(self):
    device = self._make(
      [
        status("StatusError"),
        [ACK, "ZOK"],
        status("StatusBUSY"),
        status("StatusOK"),
      ]
    )
    await device.reset_error()
    assert self._written(device) == ["a", "Z", "a", "a"]

  async def test_reset_error_is_noop_when_healthy(self):
    device = self._make([status("StatusOK")])
    await device.reset_error()
    assert self._written(device) == ["a"]

  async def test_initialize_keeping_caps_on_pins(self):
    device = self._make([status("StatusMANUAL"), [ACK, "zOK", "zDONE"]])
    await device.initialize_keeping_caps_on_pins()
    assert self._written(device) == ["a", "z"]

  async def test_initialize_keeping_caps_on_pins_reports_a_home_failure(self):
    device = self._make([status("StatusMANUAL"), [ACK, "zOK", "HomeERROR"], query("8", "156")])
    with pytest.raises(FluidXError) as ctx:
      await device.initialize_keeping_caps_on_pins()
    assert ctx.value.error_code == 156

  async def test_initialize_keeping_caps_on_pins_requires_manual_mode(self):
    device = self._make([status("StatusOK")])
    with pytest.raises(FluidXError) as ctx:
      await device.initialize_keeping_caps_on_pins()
    assert "manual recovery mode" in str(ctx.value)
    assert self._written(device) == ["a"]

  async def test_operation_auto_recovers_from_latched_error(self):
    device = self._make(
      [
        status("StatusError"),  # _ensure_ready: latched in error
        [ACK, "ZOK"],  # recovery home accepted
        status("StatusBUSY"),  # homing
        status("StatusOK"),  # homed
        status("StatusOK"),  # _ensure_ready re-check: clear
        [ACK, "fOK"],  # open accepted
        status("StatusBUSY"),
        status("StatusOK"),
      ]
    )
    await device.open_tray()
    assert self._written(device) == ["a", "Z", "a", "a", "a", "f", "a", "a"]

  async def test_latched_error_raises_when_auto_recover_disabled(self):
    device = self._make([status("StatusError"), query("8", "113")], auto_recover=False)
    with pytest.raises(FluidXError) as ctx:
      await device.open_tray()
    assert ctx.value.error_code == 113
    assert self._written(device) == ["a", "8"]

  async def test_operation_blocked_during_manual_recovery(self):
    device = self._make([status("StatusMANUAL")])
    with pytest.raises(FluidXError) as ctx:
      await device.open_tray()
    assert "manual recovery" in str(ctx.value)
    assert self._written(device) == ["a", "8"]

  # === Decap / recap ===

  async def test_decap_success(self):
    device = self._make(
      [
        status("StatusOK"),  # precheck: not recapped, no error
        [ACK, "hOK"],  # h accepted
        status("StatusBUSY"),
        status("StatusRECAP"),  # hardware terminal state: caps held
      ]
    )
    await device.decap()
    assert self._written(device) == ["a", "h", "a", "a"]

  async def test_decap_blocked_when_already_decapped(self):
    device = self._make([status("StatusRecap")])
    with pytest.raises(FluidXError):
      await device.decap()

  async def test_decap_reports_error_during_motion(self):
    device = self._make([status("StatusOK"), [ACK, "hOK"], [ACK, "aOK", "DecapERROR"]])
    with pytest.raises(FluidXError):
      await device.decap()

  async def test_decap_decodes_error_code_when_firmware_includes_it(self):
    device = self._make([status("StatusOK"), [ACK, "hOK"], [ACK, "aOK", "DecapERROR 145"]])
    with pytest.raises(FluidXError) as ctx:
      await device.decap()
    assert ctx.value.error_code == 145
    assert "Light curtain calibration max retries exceeded" in str(ctx.value)

  async def test_decap_queries_the_error_code_when_the_reply_omits_it(self):
    device = self._make(
      [
        status("StatusOK"),
        [ACK, "hOK"],
        [ACK, "aOK", "DecapERROR"],
        query("8", "114"),
      ]
    )
    with pytest.raises(FluidXError) as ctx:
      await device.decap()
    assert ctx.value.error_code == 114
    assert ctx.value.recoverable
    assert "Invalid tube height" in str(ctx.value)
    assert self._written(device) == ["a", "h", "a", "8"]

  async def test_manual_halt_during_motion_fails_immediately(self):
    device = self._make(
      [status("StatusOK"), [ACK, "hOK"], status("StatusMANUAL"), query("8", "167")]
    )
    with pytest.raises(FluidXError) as ctx:
      await device.decap()
    assert ctx.value.error_code == 167
    assert not ctx.value.recoverable

  async def test_retry_decap_finishes_with_caps_held(self):
    device = self._make(
      [
        status("StatusRECAP"),
        [ACK, "hOK"],
        status("StatusBUSY"),
        status("StatusRECAP"),
      ]
    )
    await device.retry_decap()
    assert self._written(device) == ["a", "Q", "a", "a"]

  async def test_recap_blocked_when_not_decapped(self):
    device = self._make([status("StatusDecap")])
    with pytest.raises(FluidXError):
      await device.recap()

  async def test_recap_success(self):
    device = self._make(
      [
        status("StatusRECAP"),  # precheck: caps held
        [ACK, "iOK"],  # i accepted
        status("StatusBUSY"),
        status("StatusOK"),
      ]
    )
    await device.recap()
    assert self._written(device) == ["a", "i", "a", "a"]

  async def test_waste_blocked_when_no_caps_are_held(self):
    device = self._make([status("StatusOK")])
    with pytest.raises(FluidXError):
      await device.waste()
    assert self._written(device) == ["a"]

  async def test_waste_success(self):
    device = self._make(
      [
        status("StatusRECAP"),  # precheck: caps held
        [ACK, "bOK"],  # b accepted
        status("StatusBUSY"),
        status("StatusOK"),
      ]
    )
    await device.waste()
    assert self._written(device) == ["a", "b", "a", "a"]

  # === Standby ===

  async def test_standby_reaches_sleep(self):
    device = self._make([status("StatusOK"), [ACK, "jOK"], status("StatusSLEEP")])
    await device.standby()
    assert self._written(device) == ["a", "j", "a"]

  async def test_standby_is_noop_when_asleep(self):
    device = self._make([status("StatusSLEEP")])
    await device.standby()
    assert self._written(device) == ["a"]

  async def test_ready_noop_when_awake(self):
    device = self._make([status("StatusOK")])
    await device.ready()
    assert self._written(device) == ["a"]

  async def test_ready_wakes_from_sleep(self):
    device = self._make(
      [
        status("StatusSLEEP"),  # request_status: asleep
        [ACK, "kOK"],  # k accepted
        status("StatusBUSY"),  # waking
        status("StatusOK"),  # awake
      ]
    )
    await device.ready()
    assert self._written(device) == ["a", "k", "a", "a"]

  # === Cartridge ===

  async def test_eject_cartridge(self):
    device = self._make(
      [
        extended(cartridge_installed=True),
        status("StatusOK"),
        [ACK, "cOK"],
        status("StatusCAREJECT"),
      ]
    )
    await device.eject_cartridge()
    assert self._written(device) == ["e", "a", "c", "a"]

  async def test_eject_cartridge_is_noop_without_a_cartridge(self):
    device = self._make([extended(cartridge_installed=False)])
    await device.eject_cartridge()
    assert self._written(device) == ["e"]

  async def test_eject_cartridge_blocked_while_caps_are_held(self):
    device = self._make([extended(cartridge_installed=True, caps_on_pins=True)])
    with pytest.raises(FluidXError):
      await device.eject_cartridge()
    assert self._written(device) == ["e"]

  async def test_load_cartridge_returns_the_profile(self):
    device = self._make(
      [extended(cartridge_installed=False), [ACK, "COK", "o16OK"], status("StatusOK")]
    )
    assert await device.load_cartridge() == 16
    assert self._written(device) == ["e", "C", "a"]

  async def test_load_cartridge_is_noop_when_already_loaded(self):
    device = self._make([extended(cartridge_installed=True)])
    assert await device.load_cartridge() is None
    assert self._written(device) == ["e"]

  async def test_reset_cartridge_counter(self):
    device = self._make([[ACK, "XOK", "CarResetDONE"]])
    await device.reset_cartridge_counter()
    assert self._written(device) == ["X"]

  async def test_reset_cartridge_counter_requires_its_answer(self):
    device = self._make([[ACK, "XOK"]])
    with pytest.raises(FluidXError):
      await device.reset_cartridge_counter()

  async def test_reset_cartridge_counter_is_idempotent(self):
    device = self._make([[ACK, "XOK", "CommandIgnore"]])
    await device.reset_cartridge_counter()
    assert self._written(device) == ["X"]

  async def test_load_cartridge_reads_the_profile_from_the_end_of_the_motion(self):
    # onnOK is documented as an end-of-motion answer, not part of the ack.
    device = self._make(
      [
        extended(cartridge_installed=False),
        [ACK, "COK"],
        [ACK, "aOK", "o24OK", "StatusOK"],
      ]
    )
    assert await device.load_cartridge() == 24

  async def test_load_cartridge_reports_a_missing_profile(self):
    device = self._make(
      [extended(cartridge_installed=False), [ACK, "COK", "ProfileLoadERROR"], query("8", "143")]
    )
    with pytest.raises(FluidXError) as ctx:
      await device.load_cartridge()
    assert ctx.value.error_code == 143

  # === Settings ===

  async def test_set_error_detection_off_is_not_read_as_a_fault(self):
    device = self._make([[ACK, "lOK", "ErrorDetectOFF"]])
    await device.set_error_detection_enabled(False)
    assert self._written(device) == ["l"]

  async def test_set_error_detection_on(self):
    device = self._make([[ACK, "LOK", "ErrorDetectON"]])
    await device.set_error_detection_enabled(True)
    assert self._written(device) == ["L"]

  async def test_set_error_detection_is_idempotent(self):
    device = self._make([[ACK, "LOK", "CommandIgnore"]])
    await device.set_error_detection_enabled(True)
    assert self._written(device) == ["L"]

  async def test_set_dry_run_enabled(self):
    device = self._make([[ACK, "dOK", "DryRunON"]])
    await device.set_dry_run_enabled(True)
    assert self._written(device) == ["d"]

  async def test_set_dry_run_disabled(self):
    device = self._make([[ACK, "DOK", "DryRunOFF"]])
    await device.set_dry_run_enabled(False)
    assert self._written(device) == ["D"]

  async def test_set_dry_run_is_idempotent(self):
    device = self._make([[ACK, "DOK", "CommandIgnore"]])
    await device.set_dry_run_enabled(False)
    assert self._written(device) == ["D"]

  async def test_setting_raises_when_the_device_does_not_confirm(self):
    device = self._make([[ACK, "dOK"]])
    with pytest.raises(FluidXError):
      await device.set_dry_run_enabled(True)

  async def test_set_safety_door_enabled(self):
    device = self._make([[ACK, "+OK", "DoorONDONE"]])
    await device.set_safety_door_enabled(True)
    assert self._written(device) == ["+"]

  async def test_set_safety_door_disabled(self):
    device = self._make([[ACK, "-OK", "DoorOFFDONE"]])
    await device.set_safety_door_enabled(False)
    assert self._written(device) == ["-"]

  async def test_set_safety_door_is_idempotent(self):
    device = self._make([[ACK, "+OK", "CommandIgnore"]])
    await device.set_safety_door_enabled(True)
    assert self._written(device) == ["+"]

  # === Manual recovery commands ===

  async def test_eject_caps_requires_manual_mode(self):
    device = self._make([status("StatusOK")])
    with pytest.raises(FluidXError):
      await device.eject_caps()
    assert self._written(device) == ["a"]

  async def test_eject_caps(self):
    # Manual recovery pins the status word to StatusMANUAL, so the answer frame
    # is the only completion signal: no status polling here.
    device = self._make([status("StatusMANUAL"), [ACK, "5OK", "EjectDONE"]])
    await device.eject_caps()
    assert self._written(device) == ["a", "5"]

  async def test_eject_caps_reports_a_failure(self):
    device = self._make([status("StatusMANUAL"), [ACK, "5OK", "EjectERROR"], query("8", "133")])
    with pytest.raises(FluidXError) as ctx:
      await device.eject_caps()
    assert ctx.value.error_code == 133

  async def test_head_up(self):
    device = self._make([status("StatusMANUAL"), [ACK, "6OK", "HeadDONE"]])
    await device.head_up()
    assert self._written(device) == ["a", "6"]

  async def test_open_safety_door(self):
    device = self._make([status("StatusMANUAL"), [ACK, "7OK", "DoorDONE"]])
    await device.open_safety_door()
    assert self._written(device) == ["a", "7"]

  async def test_manual_command_times_out_without_an_answer(self):
    device = self._make([status("StatusMANUAL"), [ACK, "6OK"]])
    with pytest.raises(FluidXError) as ctx:
      await device.head_up(timeout=0.01)
    assert "timed out" in str(ctx.value)

  # === Structured cancellation and transactions ===

  async def test_cancelled_setup_consumes_status_before_serial_close(self) -> None:
    events: List[str] = []
    write_started = anyio.Event()
    release_write = anyio.Event()
    scopes: List[anyio.CancelScope] = []

    async def hold_write() -> None:
      write_started.set()
      await release_write.wait()

    device = self._make([status("StatusOK")], events=events, after_write={"a": hold_write})

    async def enter_device() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          async with device:
            pytest.fail("cancelled setup reached body")
        except anyio.get_cancelled_exc_class():
          events.append("cancelled")
          raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(enter_device)
      await write_started.wait()
      scopes[0].cancel()
      await anyio.sleep(0)
      assert "cancelled" not in events
      release_write.set()

    assert events.index("reply.complete") < events.index("serial.exit")
    assert events[-1] == "cancelled"

  async def test_complete_frame_transactions_do_not_interleave(self) -> None:
    events: List[str] = []
    first_reply_collected = anyio.Event()
    release_first = anyio.Event()
    second_started = anyio.Event()

    async def hold_first_completion() -> None:
      first_reply_collected.set()
      await release_first.wait()

    device = self._make(
      [status("StatusOK"), extended()],
      events=events,
      reply_complete_hook=hold_first_completion,
    )

    async def second_query() -> None:
      second_started.set()
      await device.request_extended_status()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(device.request_status)
      await first_reply_collected.wait()
      task_group.start_soon(second_query)
      await second_started.wait()
      await anyio.sleep(0)
      try:
        assert self._written(device) == ["a"]
        assert device.io.get_read_timeout() == 5.0
      finally:
        release_first.set()

    assert self._written(device) == ["a", "e"]
    assert device.io.get_read_timeout() == 5.0
    first_reset = events.index("input.reset")
    first_write = events.index("write:a")
    first_reply_complete = events.index("reply.complete")
    second_reset = events.index("input.reset", events.index("input.reset") + 1)
    assert first_reset < first_write < first_reply_complete < second_reset

  async def test_transaction_restores_timeout_after_nested_transport_error(self) -> None:
    failure = TimeoutError("transport read timed out")
    device = self._make(
      [status("StatusOK")],
      empty_read_error=failure,
    )

    with pytest.raises(TimeoutError) as error_info:
      await device.request_status()

    assert error_info.value is failure
    assert device.io.get_read_timeout() == 5.0

  async def _assert_cancelled_queued_mutation_is_not_written(
    self, status_word: str, mutation_command: str
  ) -> None:
    events: List[str] = []
    precheck_complete = anyio.Event()
    blocker_started = anyio.Event()
    blocker_holds_lock = anyio.Event()
    release_blocker = anyio.Event()
    mutation_scopes: List[anyio.CancelScope] = []

    async def queue_blocker_before_precheck_releases() -> None:
      precheck_complete.set()
      await blocker_started.wait()
      await anyio.sleep(0)

    async def hold_blocker_reply() -> None:
      blocker_holds_lock.set()
      await release_blocker.wait()

    device = self._make(
      [status(status_word), extended(), [ACK, f"{mutation_command}OK"], status("StatusOK")],
      events=events,
      reply_complete_hooks=[queue_blocker_before_precheck_releases, hold_blocker_reply],
    )

    async def block_transaction() -> None:
      await precheck_complete.wait()
      blocker_started.set()
      await device.request_extended_status()

    async def run_mutation() -> None:
      with anyio.CancelScope() as scope:
        mutation_scopes.append(scope)
        if mutation_command == "b":
          await device.waste(timeout=0.05)
        else:
          await device.open_tray(timeout=0.05)

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(block_transaction)
      task_group.start_soon(run_mutation)
      await blocker_holds_lock.wait()
      mutation_scopes[0].cancel()
      await anyio.sleep(0)
      release_blocker.set()

    assert mutation_command not in self._written(device)

  async def test_cancelled_tray_command_waiting_for_transaction_lock_is_not_written(self) -> None:
    await self._assert_cancelled_queued_mutation_is_not_written("StatusOK", "f")

  async def test_cancelled_waste_waiting_for_transaction_lock_is_not_written(self) -> None:
    await self._assert_cancelled_queued_mutation_is_not_written("StatusRECAP", "b")

  @pytest.mark.parametrize(
    ("operation", "command", "initial_status", "ack", "terminal"),
    [
      ("tray", "s", "StatusOK", [ACK, "sOK"], "sOK"),
      ("manual recovery", "6", "StatusMANUAL", [ACK, "6OK"], "HeadDONE"),
    ],
  )
  async def test_delayed_terminal_answer_holds_complete_transaction_lock(
    self,
    operation: str,
    command: str,
    initial_status: str,
    ack: List[str],
    terminal: str,
  ) -> None:
    events: List[str] = []
    delayed_read_started = anyio.Event()
    release_terminal = anyio.Event()
    query_started = anyio.Event()

    async def hold_delayed_read() -> None:
      delayed_read_started.set()
      await release_terminal.wait()

    device = self._make(
      [status(initial_status), ack, query("V", "0044,0014,0003")],
      events=events,
      delayed_script=[[], [terminal], []],
      before_delayed_read=hold_delayed_read,
    )

    async def run_operation() -> None:
      if operation == "tray":
        await device.step_tray_out(timeout=0.05)
      else:
        await device.head_up(timeout=0.05)

    async def concurrent_query() -> None:
      query_started.set()
      await device.request_firmware_versions()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(run_operation)
      await delayed_read_started.wait()
      protected_events = list(events)
      task_group.start_soon(concurrent_query)
      await query_started.wait()
      await anyio.sleep(0)
      try:
        assert events == protected_events
        assert self._written(device) == ["a", command]
        assert device.io.get_read_timeout() == 5.0
      finally:
        release_terminal.set()

    assert self._written(device) == ["a", command, "V"]
    assert device.io.get_read_timeout() == 5.0
    delayed_terminal = events.index(f"delayed:{terminal}")
    first_reset = events.index("input.reset")
    second_reset = events.index("input.reset", first_reset + 1)
    concurrent_reset = events.index("input.reset", second_reset + 1)
    assert delayed_terminal < concurrent_reset

  @pytest.mark.parametrize(
    ("operation", "command", "script", "expected_writes"),
    [
      ("open_tray", "f", [status("StatusOK"), [ACK, "fOK"], status("StatusOK")], ["a", "f", "a"]),
      ("step_tray_out", "s", [status("StatusOK"), [ACK, "sOK", "sOK"]], ["a", "s"]),
      ("home", "Z", [[ACK, "ZOK"], status("StatusOK")], ["Z", "a"]),
      (
        "recovery",
        "Z",
        [status("StatusError"), [ACK, "ZOK"], status("StatusOK"), status("StatusOK")],
        ["a", "Z", "a"],
      ),
      (
        "initialize_keeping_caps_on_pins",
        "z",
        [status("StatusMANUAL"), [ACK, "zOK", "zDONE"]],
        ["a", "z"],
      ),
      ("decap", "h", [status("StatusOK"), [ACK, "hOK"], status("StatusRECAP")], ["a", "h", "a"]),
      (
        "retry_decap",
        "Q",
        [status("StatusRECAP"), [ACK, "hOK"], status("StatusRECAP")],
        ["a", "Q", "a"],
      ),
      ("recap", "i", [status("StatusRECAP"), [ACK, "iOK"], status("StatusOK")], ["a", "i", "a"]),
      ("waste", "b", [status("StatusRECAP"), [ACK, "bOK"], status("StatusOK")], ["a", "b", "a"]),
      ("standby", "j", [status("StatusOK"), [ACK, "jOK"], status("StatusSLEEP")], ["a", "j", "a"]),
      ("ready", "k", [status("StatusSLEEP"), [ACK, "kOK"], status("StatusOK")], ["a", "k", "a"]),
      (
        "eject_cartridge",
        "c",
        [
          extended(cartridge_installed=True),
          status("StatusOK"),
          [ACK, "cOK"],
          status("StatusCAREJECT"),
        ],
        ["e", "a", "c", "a"],
      ),
      (
        "load_cartridge",
        "C",
        [extended(), [ACK, "COK", "o16OK"], status("StatusOK")],
        ["e", "C", "a"],
      ),
      ("reset_cartridge_counter", "X", [[ACK, "XOK", "CarResetDONE"]], ["X"]),
      ("set_error_detection_enabled", "l", [[ACK, "lOK", "ErrorDetectOFF"]], ["l"]),
      ("set_dry_run_enabled", "d", [[ACK, "dOK", "DryRunON"]], ["d"]),
      ("set_safety_door_enabled", "+", [[ACK, "+OK", "DoorONDONE"]], ["+"]),
    ],
  )
  async def test_committed_operation_reaches_existing_terminal_path_before_cancellation(
    self,
    operation: str,
    command: str,
    script: List[List[str]],
    expected_writes: List[str],
  ) -> None:
    events: List[str] = []
    write_started = anyio.Event()
    release_write = anyio.Event()
    scopes: List[anyio.CancelScope] = []

    async def hold_write() -> None:
      write_started.set()
      await release_write.wait()

    device = self._make(script, events=events, after_write={command: hold_write})

    async def run_operation() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          if operation == "recovery":
            await device.open_tray()
          elif operation == "set_error_detection_enabled":
            await device.set_error_detection_enabled(False)
          elif operation == "set_dry_run_enabled":
            await device.set_dry_run_enabled(True)
          elif operation == "set_safety_door_enabled":
            await device.set_safety_door_enabled(True)
          else:
            methods: Dict[str, Callable[[], Awaitable[Any]]] = {
              "open_tray": device.open_tray,
              "step_tray_out": device.step_tray_out,
              "home": device.home,
              "initialize_keeping_caps_on_pins": device.initialize_keeping_caps_on_pins,
              "decap": device.decap,
              "retry_decap": device.retry_decap,
              "recap": device.recap,
              "waste": device.waste,
              "standby": device.standby,
              "ready": device.ready,
              "eject_cartridge": device.eject_cartridge,
              "load_cartridge": device.load_cartridge,
              "reset_cartridge_counter": device.reset_cartridge_counter,
            }
            await methods[operation]()
        except anyio.get_cancelled_exc_class():
          events.append("cancelled")
          raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(run_operation)
      await write_started.wait()
      scopes[0].cancel()
      await anyio.sleep(0)
      assert "cancelled" not in events
      release_write.set()

    assert self._written(device) == expected_writes
    assert events[-1] == "cancelled"
    assert any(
      event == "reply.complete" for event in events[events.index(f"write:{command}") + 1 :]
    )
