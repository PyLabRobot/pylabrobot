"""Software-only tests for the direct-USB Opentrons temperature module."""

from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple, Union, cast

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.io.serial import Serial
from pylabrobot.legacy.machines.backend import MachineBackend
from pylabrobot.legacy.temperature_controlling import opentrons_backend_usb as backend_module
from pylabrobot.legacy.temperature_controlling.opentrons_backend_usb import (
  OpentronsTemperatureModuleUSBBackend,
)
from pylabrobot.testing.concurrency import AnyioTestBase
from pylabrobot.testing.mock_io import MockIO

Call = Tuple[Tuple[Any, ...], Dict[str, Any]]
ReadEffect = Union[
  bytes,
  BaseException,
  Callable[[], Awaitable[bytes]],
]


class SerialScenario:
  """Configure one recording serial lifespan."""

  def __init__(self) -> None:
    self.replies: List[ReadEffect] = [b"ok\r\n", b"ok\r\n"]
    self.entry_failure: Optional[BaseException] = None
    self.exit_failure: Optional[BaseException] = None
    self.exit_observer: Optional[Callable[[], None]] = None
    self.entry_started: Optional[anyio.Event] = None
    self.entry_release: Optional[anyio.Event] = None


class RecordingSerial(MockIO):
  """Record the Serial boundary without opening or enumerating hardware."""

  def __init__(
    self,
    events: List[str],
    scenario: SerialScenario,
    options: Dict[str, object],
  ) -> None:
    super().__init__()
    self.events = events
    self.scenario = scenario
    self.options = options
    self.readline_calls: List[Call] = []
    self.write.side_effect = self._record_write

  def _record_write(self, data: bytes) -> None:
    self.events.append(f"write:{data.decode('ascii').rstrip()}")

  async def _enter_lifespan(
    self,
    stack: AsyncExitStackWithShielding,
    **kwargs: Any,
  ) -> None:
    if kwargs:
      raise TypeError("RecordingSerial does not accept lifespan options")
    self.events.append("serial.enter")
    stack.push_shielded_async_callback(self._exit_serial)
    if self.scenario.entry_failure is not None:
      raise self.scenario.entry_failure
    if self.scenario.entry_started is not None:
      self.scenario.entry_started.set()
    if self.scenario.entry_release is not None:
      await self.scenario.entry_release.wait()

  async def _exit_serial(self) -> None:
    await anyio.lowlevel.checkpoint()
    if self.scenario.exit_observer is not None:
      self.scenario.exit_observer()
    self.events.append("serial.exit")
    if self.scenario.exit_failure is not None:
      raise self.scenario.exit_failure

  async def readline(self) -> bytes:
    """Record and resolve one line-oriented read."""
    self.readline_calls.append(((), {}))
    self.events.append("readline")
    effect = self.scenario.replies.pop(0)
    await anyio.lowlevel.checkpoint()
    if isinstance(effect, BaseException):
      raise effect
    if callable(effect):
      return await effect()
    return effect


class RecordingSerialFactory:
  """Create one explicitly configured serial double per backend entry."""

  def __init__(self, events: List[str], scenarios: List[SerialScenario]) -> None:
    self.events = events
    self.scenarios = scenarios
    self.instances: List[RecordingSerial] = []
    self.calls: List[Dict[str, object]] = []

  def __call__(
    self,
    human_readable_device_name: str,
    port: Optional[str] = None,
    baudrate: int = 9600,
    timeout: float = 1,
  ) -> Serial:
    options: Dict[str, object] = {
      "human_readable_device_name": human_readable_device_name,
      "port": port,
      "baudrate": baudrate,
      "timeout": timeout,
    }
    self.calls.append(options)
    self.events.append("serial.create")
    serial = RecordingSerial(self.events, self.scenarios.pop(0), options)
    self.instances.append(serial)
    return cast(Serial, serial)


def restore_serial(serial_class: object) -> None:
  """Restore the production Serial constructor after each test lifespan."""
  backend_module.Serial = cast(Any, serial_class)  # type: ignore[misc]


def exception_chain(error: BaseException) -> List[BaseException]:
  """Return an exception and each failure reachable through its context."""
  chain: List[BaseException] = []
  current: Optional[BaseException] = error
  while current is not None:
    chain.append(current)
    current = current.__context__
  return chain


class TestOpentronsTemperatureModuleUSBBackend(AnyioTestBase):
  """Exercise native serial ownership and the unchanged wire protocol."""

  async def _enter_lifespan(
    self,
    stack: AsyncExitStackWithShielding,
    **kwargs: Any,
  ) -> None:
    self.events: List[str] = []
    self.scenario = SerialScenario()
    self.factory = RecordingSerialFactory(self.events, [self.scenario])
    original_serial = backend_module.Serial
    backend_module.Serial = cast(Any, self.factory)  # type: ignore[misc]
    stack.callback(restore_serial, original_serial)
    self.backend = OpentronsTemperatureModuleUSBBackend(port="loop://temperature")

  async def test_constructor_initializes_superclass_without_creating_serial(self) -> None:
    assert not self.backend.is_active
    assert self.backend in MachineBackend.get_all_instances()
    assert self.factory.calls == []
    with pytest.raises(RuntimeError, match="Serial device not initialized"):
      self.backend.serial
    assert self.backend.serialize() == {
      "type": "OpentronsTemperatureModuleUSBBackend",
      "port": "loop://temperature",
    }

  async def test_uses_native_lifecycle_and_managed_legacy_bindings(self) -> None:
    assert OpentronsTemperatureModuleUSBBackend.setup is HasLifespan.setup
    assert OpentronsTemperatureModuleUSBBackend.stop is HasLifespan.stop

  async def test_entry_uses_exact_serial_options_and_normal_exit_order(self) -> None:
    def observe_unpublication() -> None:
      with pytest.raises(RuntimeError, match="Serial device not initialized"):
        self.backend.serial
      self.events.append("serial.unpublished")

    self.scenario.exit_observer = observe_unpublication
    async with self.backend:
      assert self.backend.serial is self.factory.instances[0]
      self.events.append("body")

    assert self.factory.calls == [
      {
        "human_readable_device_name": "Opentrons Temperature Module",
        "port": "loop://temperature",
        "baudrate": 115200,
        "timeout": 3,
      }
    ]
    assert self.events == [
      "serial.create",
      "serial.enter",
      "body",
      "write:M18",
      "readline",
      "readline",
      "serial.unpublished",
      "serial.exit",
    ]
    with pytest.raises(RuntimeError, match="Serial device not initialized"):
      self.backend.serial

  async def test_serial_entry_failure_rolls_back_without_publication(self) -> None:
    failure = RuntimeError("serial entry failed")
    self.scenario.entry_failure = failure

    with pytest.raises(RuntimeError) as error_info:
      async with self.backend:
        pytest.fail("failed entry reached the body")

    assert error_info.value is failure
    assert self.events == ["serial.create", "serial.enter", "serial.exit"]
    with pytest.raises(RuntimeError, match="Serial device not initialized"):
      self.backend.serial
    assert not self.backend.is_active

  async def test_cancelled_serial_entry_rolls_back_without_publication(self) -> None:
    self.scenario.entry_started = anyio.Event()
    self.scenario.entry_release = anyio.Event()
    cancelled: List[BaseException] = []

    async def enter_backend() -> None:
      try:
        async with self.backend:
          pytest.fail("cancelled entry reached the body")
      except anyio.get_cancelled_exc_class() as error:
        cancelled.append(error)
        raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(enter_backend)
      await self.scenario.entry_started.wait()
      task_group.cancel_scope.cancel()

    assert len(cancelled) == 1
    assert self.events == ["serial.create", "serial.enter", "serial.exit"]
    assert not self.backend.is_active
    with pytest.raises(RuntimeError, match="Serial device not initialized"):
      self.backend.serial

  async def test_cancelled_body_completes_shielded_deactivation_before_exit(self) -> None:
    read_started = anyio.Event()
    read_release = anyio.Event()
    body_started = anyio.Event()
    finished = anyio.Event()
    scopes: List[anyio.CancelScope] = []

    async def blocked_reply() -> bytes:
      read_started.set()
      await read_release.wait()
      return b"ok\r\n"

    self.scenario.replies = [blocked_reply, b"ok\r\n"]

    async def run_body() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        async with self.backend:
          body_started.set()
          await anyio.sleep_forever()
      finished.set()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(run_body)
      await body_started.wait()
      scopes[0].cancel()
      await read_started.wait()
      assert not finished.is_set()
      read_release.set()
      await finished.wait()

    assert self.events[-4:] == ["write:M18", "readline", "readline", "serial.exit"]
    assert not self.backend.is_active

  async def test_deactivate_failure_still_unpublishes_and_exits_serial(self) -> None:
    failure = RuntimeError("deactivation failed")
    self.factory.scenarios[0].replies = [failure]

    with pytest.raises(RuntimeError) as error_info:
      async with self.backend:
        pass

    assert error_info.value is failure
    assert self.events[-3:] == ["write:M18", "readline", "serial.exit"]
    with pytest.raises(RuntimeError, match="Serial device not initialized"):
      self.backend.serial

  async def test_serial_exit_failure_propagates_after_deactivation_and_unpublication(self) -> None:
    failure = RuntimeError("serial exit failed")
    self.scenario.exit_failure = failure

    with pytest.raises(RuntimeError) as error_info:
      async with self.backend:
        pass

    assert error_info.value is failure
    assert self.events[-4:] == ["write:M18", "readline", "readline", "serial.exit"]
    with pytest.raises(RuntimeError, match="Serial device not initialized"):
      self.backend.serial

  async def test_both_cleanup_failures_are_ordered_and_chained(self) -> None:
    deactivate_failure = RuntimeError("deactivation failed")
    exit_failure = RuntimeError("serial exit failed")
    self.scenario.replies = [deactivate_failure]
    self.scenario.exit_failure = exit_failure

    with pytest.raises(RuntimeError) as error_info:
      async with self.backend:
        pass

    assert error_info.value is exit_failure
    assert exception_chain(error_info.value) == [exit_failure, deactivate_failure]
    assert self.events[-3:] == ["write:M18", "readline", "serial.exit"]
    with pytest.raises(RuntimeError, match="Serial device not initialized"):
      self.backend.serial

  async def test_supports_sequential_reuse_with_fresh_serial_instances(self) -> None:
    second_scenario = SerialScenario()
    self.factory.scenarios.append(second_scenario)

    async with self.backend:
      first = self.backend.serial
    async with self.backend:
      second = self.backend.serial

    assert first is not second
    assert self.events == [
      "serial.create",
      "serial.enter",
      "write:M18",
      "readline",
      "readline",
      "serial.exit",
      "serial.create",
      "serial.enter",
      "write:M18",
      "readline",
      "readline",
      "serial.exit",
    ]
    assert not self.backend.is_active

  async def test_lifespan_rejects_options_before_creating_serial(self) -> None:
    with pytest.raises(TypeError, match="unexpected keyword argument"):
      async with self.backend.lifespan(unknown=True):
        pytest.fail("invalid entry reached the body")

    assert self.factory.calls == []

  async def test_set_temperature_preserves_command_and_two_reply_consumption(self) -> None:
    self.scenario.replies = [b"prefix ok suffix", b"ok\r\n", b"trailing", b"ok", b"ok"]
    async with self.backend:
      serial = self.factory.instances[0]
      await self.backend.set_temperature(37.25)
      assert serial.write.calls == [((b"M104 S37.25\r\n",), {})]
      assert serial.readline_calls == [((), {}), ((), {})]
      assert self.scenario.replies == [b"trailing", b"ok", b"ok"]
      self.scenario.replies = [b"ok", b"ok"]

  async def test_set_temperature_malformed_first_reply_still_consumes_second(self) -> None:
    self.scenario.replies = [b"bad", b"ok", b"ok", b"ok"]
    async with self.backend:
      serial = self.factory.instances[0]
      with pytest.raises(RuntimeError, match="Unexpected response from device"):
        await self.backend.set_temperature(20)
      assert serial.readline_calls == [((), {}), ((), {})]
      assert self.scenario.replies == [b"ok", b"ok"]

  async def test_deactivate_preserves_command_and_two_reply_consumption(self) -> None:
    self.scenario.replies = [b"ok", b"ok", b"trailing", b"ok", b"ok"]
    async with self.backend:
      serial = self.factory.instances[0]
      await self.backend.deactivate()
      assert serial.write.calls == [((b"M18\r\n",), {})]
      assert serial.readline_calls == [((), {}), ((), {})]
      assert self.scenario.replies == [b"trailing", b"ok", b"ok"]
      self.scenario.replies = [b"ok", b"ok"]

  async def test_deactivate_malformed_first_reply_still_consumes_second(self) -> None:
    self.scenario.replies = [b"bad", b"ok", b"ok", b"ok"]
    async with self.backend:
      serial = self.factory.instances[0]
      with pytest.raises(RuntimeError, match="Unexpected response from device"):
        await self.backend.deactivate()
      assert serial.readline_calls == [((), {}), ((), {})]
      assert self.scenario.replies == [b"ok", b"ok"]

  async def test_current_temperature_preserves_command_parsing_and_three_replies(self) -> None:
    self.scenario.replies = [b"T:19.000 C:21.125\r\n", b"ok", b"ok", b"ok", b"ok"]
    async with self.backend:
      serial = self.factory.instances[0]
      assert await self.backend.get_current_temperature() == 21.125
      assert serial.write.calls == [((b"M105\r\n",), {})]
      assert serial.readline_calls == [((), {}), ((), {}), ((), {})]
      assert self.scenario.replies == [b"ok", b"ok"]

  async def test_current_temperature_malformed_first_reply_leaves_trailing_replies(self) -> None:
    self.scenario.replies = [b"malformed", b"trailing one", b"trailing two", b"ok", b"ok"]
    async with self.backend:
      serial = self.factory.instances[0]
      with pytest.raises(ValueError, match="Unexpected response from device"):
        await self.backend.get_current_temperature()
      assert serial.readline_calls == [((), {})]
      assert self.scenario.replies == [b"trailing one", b"trailing two", b"ok", b"ok"]
      self.scenario.replies = [b"ok", b"ok"]
