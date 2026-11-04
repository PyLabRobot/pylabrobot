from collections import defaultdict, deque
from typing import Any, Awaitable, Callable, Deque, Dict, List, Optional, Type, Union, cast

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.io.serial import Serial
from pylabrobot.kbiosystems import (
  KBiosystemsError,
  KBiosystemsUltrasealEPRO,
  KBiosystemsUltrasealPRO,
  KBiosystemsUltrasealXTPro,
  UltrasealEPROStatus,
  UltrasealPROStatus,
  UltrasealXTProStatus,
)
from pylabrobot.kbiosystems import sealer as sealer_module
from pylabrobot.kbiosystems.sealer import KBiosystemsSealer
from pylabrobot.testing.concurrency import AnyioTestBase

ResponseEffect = Union[str, BaseException, Callable[[], Awaitable[str]]]
WriteHook = Callable[[], Awaitable[None]]


@pytest.fixture(autouse=True)
def _skip_protocol_delays(monkeypatch: pytest.MonkeyPatch) -> None:
  """Keep software protocol tests deterministic and fast."""
  monkeypatch.setattr(sealer_module, "_COMMAND_DELAY_SECONDS", 0, raising=False)
  monkeypatch.setattr(sealer_module, "_SETPOINT_SETTLE_SECONDS", 0, raising=False)


class FakeSealerSerial(HasLifespan):
  """Replay sealer echo replies and record lifecycle and protocol events."""

  def __init__(
    self,
    responses: Optional[Dict[str, List[ResponseEffect]]] = None,
    events: Optional[List[str]] = None,
    entry_error: Optional[BaseException] = None,
    exit_error: Optional[BaseException] = None,
    block_entry: Optional[anyio.Event] = None,
    after_write: Optional[Dict[str, WriteHook]] = None,
  ) -> None:
    super().__init__()
    self.responses: Dict[str, Deque[ResponseEffect]] = defaultdict(deque)
    for command, effects in (responses or {}).items():
      self.responses[command].extend(effects)
    self.events = events if events is not None else []
    self.entry_error = entry_error
    self.exit_error = exit_error
    self.block_entry = block_entry
    self.after_write = after_write or {}
    self.port = "FAKE"
    self.written: List[str] = []
    self._rx = bytearray()
    self._pending_effects: Deque[tuple[str, ResponseEffect]] = deque()

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    if kwargs:
      raise TypeError("FakeSealerSerial does not accept lifespan options")
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
    self._rx.clear()
    self.events.append("serial.reset_input")

  async def write(self, data: bytes) -> None:
    await anyio.lowlevel.checkpoint()
    command = data.decode("ascii").removesuffix("\r")
    self.written.append(command)
    self.events.append(f"write:{command}")
    hook = self.after_write.get(command)
    if hook is not None:
      await hook()
    effects = self.responses[command]
    if effects:
      self._pending_effects.append((command, effects.popleft()))

  async def read(self, num_bytes: int = 1) -> bytes:
    await anyio.lowlevel.checkpoint()
    if not self._rx and self._pending_effects:
      command, effect = self._pending_effects.popleft()
      if isinstance(effect, BaseException):
        raise effect
      response = await effect() if callable(effect) else effect
      self._rx.extend((command + response + "\r").encode("ascii"))
      self.events.append(f"response.committed:{command}")
    chunk = bytes(self._rx[:num_bytes])
    del self._rx[:num_bytes]
    if chunk == b"\r":
      self.events.append("response.read")
    return chunk


DeviceType = Type[KBiosystemsSealer]


def make_device(
  device_type: DeviceType,
  responses: Optional[Dict[str, List[ResponseEffect]]] = None,
  events: Optional[List[str]] = None,
  entry_error: Optional[BaseException] = None,
  exit_error: Optional[BaseException] = None,
  block_entry: Optional[anyio.Event] = None,
  after_write: Optional[Dict[str, WriteHook]] = None,
  timeout: float = 0.02,
) -> tuple[KBiosystemsSealer, FakeSealerSerial]:
  """Build one sealer model backed by a hardware-free serial transport."""
  device = device_type(port="FAKE", timeout=timeout, settle_time=0)
  fake_io = FakeSealerSerial(responses, events, entry_error, exit_error, block_entry, after_write)
  device.io = cast(Serial, fake_io)
  return device, fake_io


def entry_responses(device_type: DeviceType, repeats: int = 1) -> Dict[str, List[ResponseEffect]]:
  """Return literal initialization and cleanup replies for one model."""
  offline_command = "A020" if device_type is KBiosystemsUltrasealEPRO else "A025"
  responses: Dict[str, List[ResponseEffect]] = {
    "?": ["00"] * repeats,
    "A100": ["ok"] * repeats,
    offline_command: ["ok"] * repeats,
  }
  if device_type is KBiosystemsUltrasealEPRO:
    responses.update({"I": ["ok"] * repeats, "V": ["1.2\rboardA"] * repeats})
  return responses


def initialization_commands(device_type: DeviceType) -> List[str]:
  """Return the exact model initialization command transcript."""
  if device_type is KBiosystemsUltrasealEPRO:
    return ["I", "?", "V", "A100"]
  return ["?", "A100"]


class KBiosystemsSealerBehavior(AnyioTestBase):
  """Shared lifecycle contract exercised by every KBiosystems model."""

  device_type: DeviceType
  offline_command: str

  def test_uses_managed_lifecycle_methods(self) -> None:
    assert self.device_type.__aenter__ is HasLifespan.__aenter__
    assert self.device_type.__aexit__ is HasLifespan.__aexit__
    assert self.device_type.setup is HasLifespan.setup
    assert self.device_type.stop is HasLifespan.stop

  async def test_initialization_and_cleanup_transcript(self) -> None:
    events: List[str] = []
    device, fake_io = make_device(self.device_type, entry_responses(self.device_type), events)

    async with device:
      events.append("body")

    expected = [*initialization_commands(self.device_type), self.offline_command]
    assert fake_io.written == expected
    assert events[0] == "serial.enter"
    assert events[-1] == "serial.exit"
    assert events.index(f"write:{self.offline_command}") < events.index("serial.exit")
    assert not device.is_active

  async def test_serial_entry_failure_rolls_back(self) -> None:
    events: List[str] = []
    failure = RuntimeError("serial entry failed")
    device, fake_io = make_device(self.device_type, events=events, entry_error=failure)

    with pytest.raises(RuntimeError) as error_info:
      async with device:
        pytest.fail("failed serial entry reached body")

    assert error_info.value is failure
    assert fake_io.written == []
    assert events == ["serial.enter", "serial.exit"]
    assert not device.is_active

  async def test_cancelled_serial_entry_rolls_back(self) -> None:
    events: List[str] = []
    entry_started = anyio.Event()
    device, fake_io = make_device(self.device_type, events=events, block_entry=entry_started)

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(_enter_and_wait, device)
      await entry_started.wait()
      task_group.cancel_scope.cancel()

    assert fake_io.written == []
    assert events == ["serial.enter", "serial.exit"]
    assert not device.is_active

  async def test_each_initialization_failure_sets_offline_and_closes_serial(self) -> None:
    for failed_command in initialization_commands(self.device_type):
      events: List[str] = []
      failure = RuntimeError(f"{failed_command} failed")
      responses = entry_responses(self.device_type)
      responses[failed_command] = [failure]
      device, fake_io = make_device(self.device_type, responses, events)

      with pytest.raises(RuntimeError) as error_info:
        async with device:
          pytest.fail("failed initialization reached body")

      assert error_info.value is failure
      assert fake_io.written[-1] == self.offline_command
      assert events[-1] == "serial.exit"
      assert not device.is_active

  async def test_cancellation_during_each_initialization_command_reconciles_reply(self) -> None:
    for blocked_command in initialization_commands(self.device_type):
      events: List[str] = []
      write_started = anyio.Event()
      release_write = anyio.Event()
      scopes: List[anyio.CancelScope] = []

      async def hold_write() -> None:
        write_started.set()
        await release_write.wait()

      device, fake_io = make_device(
        self.device_type,
        entry_responses(self.device_type),
        events,
        after_write={blocked_command: hold_write},
      )

      async def enter() -> None:
        with anyio.CancelScope() as scope:
          scopes.append(scope)
          try:
            async with device:
              pytest.fail("cancelled initialization reached body")
          except anyio.get_cancelled_exc_class():
            events.append("cancelled")
            raise

      async with anyio.create_task_group() as task_group:
        task_group.start_soon(enter)
        await write_started.wait()
        scopes[0].cancel()
        await anyio.sleep(0)
        assert "cancelled" not in events
        release_write.set()

      assert blocked_command in fake_io.written
      response_committed = events.index(f"response.committed:{blocked_command}")
      cleanup_write = events.index(f"write:{self.offline_command}")
      expected_reply_lines = 2 if blocked_command == "V" else 1
      assert events[response_committed:cleanup_write].count("response.read") == expected_reply_lines
      assert events.index(f"write:{self.offline_command}") < events.index("serial.exit")
      assert events.index("serial.exit") < events.index("cancelled")

  async def test_body_cancellation_completes_offline_reply_before_serial_close(self) -> None:
    events: List[str] = []
    body_started = anyio.Event()
    cleanup_started = anyio.Event()
    release_cleanup = anyio.Event()
    scopes: List[anyio.CancelScope] = []

    async def hold_cleanup_write() -> None:
      cleanup_started.set()
      await release_cleanup.wait()

    device, fake_io = make_device(
      self.device_type,
      entry_responses(self.device_type),
      events,
      after_write={self.offline_command: hold_cleanup_write},
    )

    async def use_device() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
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
      scopes[0].cancel()
      await cleanup_started.wait()
      assert "serial.exit" not in events
      release_cleanup.set()

    assert fake_io.written[-1] == self.offline_command
    cleanup_write = events.index(f"write:{self.offline_command}")
    assert events.index("response.read", cleanup_write) < events.index("serial.exit")
    assert events.index("serial.exit") < events.index("cancelled")
    assert not device.is_active

  async def test_sequential_reuse_initializes_and_cleans_up_twice(self) -> None:
    events: List[str] = []
    device, fake_io = make_device(
      self.device_type, entry_responses(self.device_type, repeats=2), events
    )

    async with device:
      pass
    async with device:
      pass

    expected_cycle = [*initialization_commands(self.device_type), self.offline_command]
    assert fake_io.written == expected_cycle * 2
    assert events.count("serial.enter") == 2
    assert events.count("serial.exit") == 2

  async def test_serial_close_failure_propagates_after_offline_cleanup(self) -> None:
    events: List[str] = []
    failure = RuntimeError("serial close failed")
    device, fake_io = make_device(
      self.device_type,
      entry_responses(self.device_type),
      events,
      exit_error=failure,
    )

    with pytest.raises(RuntimeError) as error_info:
      async with device:
        pass

    assert error_info.value is failure
    assert fake_io.written[-1] == self.offline_command
    assert events[-1] == "serial.exit"
    assert not device.is_active

  async def test_rejects_lifespan_options_before_serial_entry(self) -> None:
    events: List[str] = []
    device, _ = make_device(self.device_type, events=events)

    with pytest.raises(TypeError, match="unexpected keyword argument"):
      async with device.lifespan(unknown=True):
        pytest.fail("invalid entry reached body")

    assert events == []

  async def test_accepted_command_consumes_reply_before_cancellation(self) -> None:
    events: List[str] = []
    write_started = anyio.Event()
    release_write = anyio.Event()
    scopes: List[anyio.CancelScope] = []

    async def hold_write() -> None:
      write_started.set()
      await release_write.wait()

    device, fake_io = make_device(
      self.device_type,
      {"A160": ["ok"]},
      events,
      after_write={"A160": hold_write},
    )

    async def set_temperature() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          await device.set_temperature(160)
        except anyio.get_cancelled_exc_class():
          events.append("cancelled")
          raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(set_temperature)
      await write_started.wait()
      scopes[0].cancel()
      await anyio.sleep(0)
      assert "cancelled" not in events
      release_write.set()

    assert fake_io.written == ["A160"]
    assert events[-4:] == ["write:A160", "response.committed:A160", "response.read", "cancelled"]

  async def test_pending_cancellation_writes_no_command(self) -> None:
    device, fake_io = make_device(self.device_type, {"A160": ["ok"]})
    returned = False

    with anyio.CancelScope() as scope:
      scope.cancel()
      await device.set_temperature(160)
      returned = True

    assert not returned
    assert fake_io.written == []

  async def test_concurrent_commands_do_not_interleave_transactions(self) -> None:
    events: List[str] = []
    first_write_started = anyio.Event()
    release_first_write = anyio.Event()
    second_started = anyio.Event()

    async def hold_first_write() -> None:
      first_write_started.set()
      await release_first_write.wait()

    device, fake_io = make_device(
      self.device_type,
      {"C": ["160"], "D": ["25"]},
      events,
      after_write={"C": hold_first_write},
    )

    async def second_command() -> None:
      second_started.set()
      assert await device.request_sealing_time() == 2.5

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(device.request_sealing_temperature)
      await first_write_started.wait()
      task_group.start_soon(second_command)
      await second_started.wait()
      await anyio.sleep(0)
      assert fake_io.written == ["C"]
      release_first_write.set()

    assert fake_io.written == ["C", "D"]
    assert events.index("response.read") < events.index("write:D")

  async def test_busy_poll_propagates_transport_timeout_unchanged(self) -> None:
    device, _ = make_device(self.device_type, timeout=0)

    with pytest.raises(TimeoutError, match="Timeout while waiting for response from sealer"):
      await device._wait_for_busy_cleared(timeout=1)

  async def test_busy_poll_translates_its_own_deadline(self) -> None:
    device, _ = make_device(self.device_type, {"?": ["04"]})

    with pytest.raises(KBiosystemsError, match="Timeout while waiting for busy flag to clear"):
      await device._wait_for_busy_cleared(timeout=0)

  async def test_temperature_poll_propagates_transport_timeout_unchanged(self) -> None:
    device, _ = make_device(self.device_type, timeout=0)

    with pytest.raises(TimeoutError, match="Timeout while waiting for response from sealer"):
      await device.wait_for_sealing_temperature(timeout=1)

  async def test_temperature_poll_translates_its_own_deadline(self) -> None:
    device, _ = make_device(self.device_type, {"?": ["08"]})

    with pytest.raises(TimeoutError, match="Timeout while waiting for sealing temperature"):
      await device.wait_for_sealing_temperature(timeout=0)


class TestUltrasealEPRO(KBiosystemsSealerBehavior):
  device_type = KBiosystemsUltrasealEPRO
  offline_command = "A020"

  async def test_seal_distance_mode_wire_bytes(self) -> None:
    device, fake_io = make_device(
      self.device_type,
      {
        "ECO_OFF": ["ok"],
        "?": ["00", "00", "00"],
        "B25": ["ok"],
        "A170": ["ok"],
        "A100": ["ok"],
        "L=120": ["ok"],
        "FS=0": ["ok"],
        "DO=25": ["ok"],
        "S": ["ok"],
      },
    )

    await device.seal(temperature=170, duration=2.5)

    assert fake_io.written == [
      "ECO_OFF",
      "?",
      "B25",
      "A170",
      "L=120",
      "FS=0",
      "DO=25",
      "?",
      "S",
      "?",
      "A100",
    ]

  async def test_seal_force_mode_wire_bytes(self) -> None:
    device, fake_io = make_device(
      self.device_type,
      {
        "ECO_OFF": ["ok"],
        "?": ["00", "00", "00"],
        "B25": ["ok"],
        "A170": ["ok"],
        "A100": ["ok"],
        "L=120": ["ok"],
        "FS=1": ["ok"],
        "S": ["ok"],
      },
    )

    await cast(KBiosystemsUltrasealEPRO, device).seal(170, 2.5, force_mode=True, sealing_force=30)

    assert fake_io.written == [
      "ECO_OFF",
      "?",
      "B25",
      "A170",
      "L=120",
      "FS=1",
      "PS=30",
      "?",
      "S",
      "?",
      "A100",
    ]

  async def test_status_decode_and_temperature_floor(self) -> None:
    device, _ = make_device(self.device_type, {"?": ["a4"], "A005": ["ok"]})

    assert await device.request_status() == (
      UltrasealEPROStatus.ParkMode | UltrasealEPROStatus.NotInitialised | UltrasealEPROStatus.Busy
    )
    await device.set_temperature(5)
    with pytest.raises(ValueError):
      await device.set_temperature(4)


class TestUltrasealXTPro(KBiosystemsSealerBehavior):
  device_type = KBiosystemsUltrasealXTPro
  offline_command = "A025"

  async def test_seal_and_shuttle_wire_bytes(self) -> None:
    device, fake_io = make_device(
      self.device_type,
      {
        "?": ["00", "00", "00"],
        "B30": ["ok"],
        "A180": ["ok"],
        "A100": ["ok"],
        "S": ["ok"],
        "P": ["ok"],
        "U": ["ok"],
        "R": ["ok"],
      },
    )

    await device.seal(temperature=180, duration=3.0)
    assert await cast(KBiosystemsUltrasealXTPro, device).park()
    assert await cast(KBiosystemsUltrasealXTPro, device).unpark()
    assert await cast(KBiosystemsUltrasealXTPro, device).reset()

    assert fake_io.written == ["?", "B30", "A180", "?", "S", "?", "A100", "P", "U", "R"]

  async def test_status_error_and_temperature_policy(self) -> None:
    device, _ = make_device(self.device_type, {"?": ["24", "02"], "E": ["12"], "A025": ["ok"]})

    assert await device.request_status() == UltrasealXTProStatus.LowAir | UltrasealXTProStatus.Busy
    with pytest.raises(KBiosystemsError) as error_info:
      await device.wait_for_idle()
    assert error_info.value.error_code == 12
    assert error_info.value.message == "Low air pressure."
    await device.set_temperature(25)
    with pytest.raises(ValueError):
      await device.set_temperature(20)


class TestUltrasealPRO(KBiosystemsSealerBehavior):
  device_type = KBiosystemsUltrasealPRO
  offline_command = "A025"

  async def test_seal_wire_bytes(self) -> None:
    device, fake_io = make_device(
      self.device_type,
      {
        "?": ["00", "00", "00"],
        "B30": ["ok"],
        "A180": ["ok"],
        "A100": ["ok"],
        "S": ["ok"],
      },
    )

    await device.seal(temperature=180, duration=3.0)

    assert fake_io.written == ["?", "B30", "A180", "?", "S", "?", "A100"]

  async def test_status_bits_error_and_temperature_policy(self) -> None:
    device, _ = make_device(
      self.device_type,
      {"?": ["80", "40", "24", "02"], "E": ["09"], "A025": ["ok"]},
    )

    assert await device.request_status() == UltrasealPROStatus.ParkMode
    assert await device.request_status() == UltrasealPROStatus.Spare
    assert await device.request_status() == UltrasealPROStatus.LowAir | UltrasealPROStatus.Busy
    with pytest.raises(KBiosystemsError) as error_info:
      await device.wait_for_idle()
    assert error_info.value.error_code == 9
    assert error_info.value.message == "The sealer is overheating."
    await device.set_temperature(25)
    with pytest.raises(ValueError):
      await device.set_temperature(20)


async def _enter_and_wait(device: KBiosystemsSealer) -> None:
  async with device:
    pytest.fail("cancelled entry reached body")
