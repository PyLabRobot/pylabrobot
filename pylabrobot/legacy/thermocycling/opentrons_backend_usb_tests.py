"""Software-only tests for the direct-USB Opentrons thermocycler."""

import asyncio
import threading
from typing import Any, Dict, List, Optional, Tuple, cast

import anyio
import pytest
import sniffio

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.legacy.machines.backend import MachineBackend
from pylabrobot.legacy.thermocycling import opentrons_backend_usb as backend_module
from pylabrobot.legacy.thermocycling.opentrons_backend_usb import (
  OpentronsThermocyclerUSBBackend,
  wait_for_block_target,
)
from pylabrobot.legacy.thermocycling.standard import Protocol, Stage, Step
from pylabrobot.testing.concurrency import AnyioTestBase


class RecordingPort:
  """One serial-port discovery result."""

  def __init__(self, device: str, vid: int, pid: int) -> None:
    self.device = device
    self.vid = vid
    self.pid = pid


class RecordingListPorts:
  """Record and optionally block serial port discovery."""

  def __init__(self) -> None:
    self.ports: List[RecordingPort] = []
    self.calls = 0
    self.call_threads: List[int] = []
    self.block = False
    self.started = threading.Event()
    self.release = threading.Event()
    self.timed_out = False

  def comports(self) -> List[RecordingPort]:
    self.calls += 1
    self.call_threads.append(threading.get_ident())
    if self.block:
      self.started.set()
      if not self.release.wait(timeout=3):
        self.timed_out = True
    return list(self.ports)


class RecordingSerialTools:
  """Typed replacement for ``serial.tools``."""

  def __init__(self, list_ports: RecordingListPorts) -> None:
    self.list_ports = list_ports


class RecordingSerialNamespace:
  """Typed replacement for the imported ``serial`` package namespace."""

  def __init__(self, list_ports: RecordingListPorts) -> None:
    self.tools = RecordingSerialTools(list_ports)


class Temperature:
  """Complete temperature response consumed by the backend."""

  def __init__(self, current: float, target: Optional[float]) -> None:
    self.current = current
    self.target = target


class RecordingLidStatus:
  """Typed replacement for the OEM lid-status enum."""

  OPEN = object()


class RecordingAnyioNamespace:
  """Record polling suspension without waiting for wall-clock time."""

  def __init__(self) -> None:
    self.sleep_calls: List[float] = []

  async def sleep(self, delay: float) -> None:
    self.sleep_calls.append(delay)
    await anyio.lowlevel.checkpoint()


class DriverScenario:
  """Configure one recording OEM driver."""

  def __init__(self) -> None:
    self.failures: Dict[str, BaseException] = {}
    self.block_at: Optional[str] = None
    self.block_started: Optional[anyio.Event] = None
    self.block_release: Optional[anyio.Event] = None
    self.temperatures: List[Temperature] = [Temperature(25.0, None)]


class RecordingDriver:
  """Typed asynchronous stand-in for the Opentrons OEM driver."""

  def __init__(self, events: List[str], scenario: DriverScenario) -> None:
    self.events = events
    self.scenario = scenario
    self.calls: List[Tuple[str, Tuple[object, ...], Dict[str, object]]] = []

  async def _record(self, name: str, *args: object, **kwargs: object) -> Optional[BaseException]:
    self.calls.append((name, args, kwargs))
    self.events.append(name)
    if self.scenario.block_at == name:
      assert self.scenario.block_started is not None
      assert self.scenario.block_release is not None
      self.scenario.block_started.set()
      await self.scenario.block_release.wait()
    await anyio.lowlevel.checkpoint()
    failure = self.scenario.failures.get(name)
    if failure is not None:
      raise failure
    return None

  async def disconnect(self) -> None:
    await self._record("disconnect")

  async def deactivate_block(self) -> None:
    await self._record("deactivate_block")

  async def deactivate_lid(self) -> None:
    await self._record("deactivate_lid")

  async def open_lid(self) -> None:
    await self._record("open_lid")

  async def close_lid(self) -> None:
    await self._record("close_lid")

  async def lift_plate(self) -> None:
    await self._record("lift_plate")

  async def jog_lid(self, angle: float) -> None:
    await self._record("jog_lid", angle)

  async def set_plate_temperature(self, *args: object, **kwargs: object) -> None:
    await self._record("set_plate_temperature", *args, **kwargs)

  async def set_lid_temperature(self, temperature: float) -> None:
    await self._record("set_lid_temperature", temperature)

  async def set_ramp_rate(self, *args: object, **kwargs: object) -> None:
    await self._record("set_ramp_rate", *args, **kwargs)

  async def get_device_info(self) -> Dict[str, str]:
    await self._record("get_device_info")
    return {"serial": "fake-thermocycler"}

  async def get_plate_temperature(self) -> Temperature:
    await self._record("get_plate_temperature")
    if len(self.scenario.temperatures) > 1:
      return self.scenario.temperatures.pop(0)
    return self.scenario.temperatures[0]

  async def get_lid_temperature(self) -> Temperature:
    await self._record("get_lid_temperature")
    return Temperature(40.0, 40.0)

  async def get_lid_status(self) -> object:
    await self._record("get_lid_status")
    return RecordingLidStatus.OPEN


class RecordingFactory:
  """Create explicitly configured recording OEM drivers."""

  def __init__(self, events: List[str]) -> None:
    self.events = events
    self.calls: List[Tuple[str, asyncio.AbstractEventLoop]] = []
    self.scenarios: List[DriverScenario] = [DriverScenario()]
    self.drivers: List[RecordingDriver] = []
    self.failure: Optional[BaseException] = None
    self.create_started: Optional[anyio.Event] = None
    self.create_release: Optional[anyio.Event] = None

  async def create(self, port: str, loop: asyncio.AbstractEventLoop) -> RecordingDriver:
    self.calls.append((port, loop))
    self.events.append("factory.create")
    if self.create_started is not None:
      self.create_started.set()
    if self.create_release is not None:
      await self.create_release.wait()
    if self.failure is not None:
      raise self.failure
    driver = RecordingDriver(self.events, self.scenarios.pop(0))
    self.drivers.append(driver)
    return driver


def restore_dependencies(
  use_driver: bool,
  serial_namespace: object,
  factory_present: bool,
  driver_factory: Optional[object],
  driver_type_present: bool,
  driver_type: Optional[object],
  lid_status_present: bool,
  lid_status: Optional[object],
) -> None:
  """Restore every patched optional dependency boundary."""
  backend_module.USE_OPENTRONS_DRIVER = use_driver
  backend_module.serial = cast(Any, serial_namespace)  # type: ignore[misc]
  if factory_present:
    backend_module.ThermocyclerDriverFactory = cast(Any, driver_factory)  # type: ignore[misc]
  else:
    del backend_module.ThermocyclerDriverFactory
  if driver_type_present:
    backend_module.AbstractThermocyclerDriver = cast(Any, driver_type)  # type: ignore[misc]
  else:
    del backend_module.AbstractThermocyclerDriver
  if lid_status_present:
    backend_module.ThermocyclerLidStatus = cast(Any, lid_status)  # type: ignore[misc]
  else:
    del backend_module.ThermocyclerLidStatus


class PatchedUSBBackendTests(AnyioTestBase):
  """Patch every hardware boundary before backend construction."""

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    self.events: List[str] = []
    self.list_ports = RecordingListPorts()
    self.factory = RecordingFactory(self.events)
    original_use_driver = backend_module.USE_OPENTRONS_DRIVER
    original_serial = backend_module.serial
    try:
      original_factory: Optional[object] = backend_module.ThermocyclerDriverFactory
      factory_present = True
    except AttributeError:
      original_factory = None
      factory_present = False
    try:
      original_driver_type: Optional[object] = backend_module.AbstractThermocyclerDriver
      driver_type_present = True
    except AttributeError:
      original_driver_type = None
      driver_type_present = False
    try:
      original_lid_status: Optional[object] = backend_module.ThermocyclerLidStatus
      lid_status_present = True
    except AttributeError:
      original_lid_status = None
      lid_status_present = False
    backend_module.USE_OPENTRONS_DRIVER = True
    backend_module.ThermocyclerDriverFactory = cast(Any, self.factory)  # type: ignore[misc]
    backend_module.AbstractThermocyclerDriver = cast(Any, RecordingDriver)  # type: ignore[misc]
    backend_module.ThermocyclerLidStatus = cast(Any, RecordingLidStatus)  # type: ignore[misc]
    backend_module.serial = cast(Any, RecordingSerialNamespace(self.list_ports))  # type: ignore[misc]
    stack.callback(
      restore_dependencies,
      original_use_driver,
      original_serial,
      factory_present,
      original_factory,
      driver_type_present,
      original_driver_type,
      lid_status_present,
      original_lid_status,
    )
    self.backend = OpentronsThermocyclerUSBBackend()


class TestOpentronsThermocyclerUSBBackendGuard(PatchedUSBBackendTests):
  """Exercise the asyncio dependency guard under both AnyIO backends."""

  async def test_rejects_non_asyncio_before_discovery_or_factory_creation(self) -> None:
    self.list_ports.ports = [RecordingPort("fake-discovered-port", 0x04D8, 0xED8C)]

    if sniffio.current_async_library() == "asyncio":
      async with self.backend:
        pass
      assert self.list_ports.calls == 1
      assert len(self.factory.calls) == 1
      return

    with pytest.raises(RuntimeError, match="Opentrons.*driver.*asyncio"):
      async with self.backend:
        pytest.fail("Trio entry reached the body")

    assert self.list_ports.calls == 0
    assert self.factory.calls == []
    assert self.events == []

  async def test_temperature_polling_is_loop_neutral(self) -> None:
    scenario = DriverScenario()
    scenario.temperatures = [Temperature(20.0, 30.0), Temperature(29.5, 30.0)]
    driver = RecordingDriver([], scenario)
    recording_anyio = RecordingAnyioNamespace()
    original_anyio = backend_module.anyio
    backend_module.anyio = cast(Any, recording_anyio)  # type: ignore[misc]

    try:
      await wait_for_block_target(driver)
    finally:
      backend_module.anyio = original_anyio

    assert [name for name, _, _ in driver.calls] == [
      "get_plate_temperature",
      "get_plate_temperature",
    ]
    assert recording_anyio.sleep_calls == [1.0]


class TestOpentronsThermocyclerUSBBackend(PatchedUSBBackendTests):
  """Exercise asyncio-only OEM driver acquisition and ownership."""

  _anyio_backends = ["asyncio"]

  async def test_constructor_and_native_lifecycle_preserve_public_behavior(self) -> None:
    assert not self.backend.is_active
    assert self.backend in MachineBackend.get_all_instances()
    assert self.backend.serialize() == {"type": "OpentronsThermocyclerUSBBackend"}
    assert OpentronsThermocyclerUSBBackend.setup is HasLifespan.setup
    assert OpentronsThermocyclerUSBBackend.stop is HasLifespan.stop

  async def test_explicit_port_skips_discovery_and_passes_running_loop(self) -> None:
    running_loop = asyncio.get_running_loop()

    async with self.backend.lifespan(port="fake-explicit-port"):
      assert self.backend._driver is self.factory.drivers[0]

    assert self.list_ports.calls == 0
    assert self.factory.calls == [("fake-explicit-port", running_loop)]

  @pytest.mark.parametrize(
    ("ports", "message"),
    [
      ([], "No Opentrons Thermocycler found"),
      (
        [
          RecordingPort("fake-one", 0x04D8, 0xED8C),
          RecordingPort("fake-two", 0x0483, 0xED8D),
        ],
        "Multiple Opentrons Thermocyclers found: \\['fake-one', 'fake-two'\\]",
      ),
    ],
  )
  async def test_automatic_discovery_rejects_zero_or_multiple_matches(
    self, ports: List[RecordingPort], message: str
  ) -> None:
    self.list_ports.ports = ports

    with pytest.raises(RuntimeError, match=message):
      async with self.backend:
        pytest.fail("invalid discovery reached the body")

    assert self.list_ports.calls == 1
    assert self.factory.calls == []
    assert self.backend._driver is None
    assert not self.backend.is_active

  async def test_automatic_discovery_filters_ports_and_offloads_comports(self) -> None:
    self.list_ports.ports = [
      RecordingPort("unrelated", 1, 2),
      RecordingPort("fake-discovered", 0x04D8, 0xED8C),
    ]
    self.list_ports.block = True
    async_thread = threading.get_ident()
    progressed = anyio.Event()
    finished = anyio.Event()

    async def enter_backend() -> None:
      async with self.backend:
        finished.set()

    async def progress() -> None:
      await anyio.lowlevel.checkpoint()
      progressed.set()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(enter_backend)
      assert await anyio.to_thread.run_sync(self.list_ports.started.wait, 3)
      task_group.start_soon(progress)
      await progressed.wait()
      assert not finished.is_set()
      self.list_ports.release.set()
      await finished.wait()

    assert self.factory.calls[0][0] == "fake-discovered"
    assert self.list_ports.call_threads[0] != async_thread
    assert not self.list_ports.timed_out

  async def test_factory_failure_clears_state_and_preserves_exception(self) -> None:
    failure = RuntimeError("factory failed")
    self.factory.failure = failure

    with pytest.raises(RuntimeError) as error_info:
      async with self.backend.lifespan(port="fake-port"):
        pytest.fail("failed factory reached the body")

    assert error_info.value is failure
    assert self.backend._driver is None
    assert self.backend._current_protocol is None
    assert not self.backend.is_active

  async def test_factory_cancellation_never_publishes_driver(self) -> None:
    self.factory.create_started = anyio.Event()
    self.factory.create_release = anyio.Event()
    scopes: List[anyio.CancelScope] = []
    cancelled: List[BaseException] = []

    async def enter_backend() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          async with self.backend.lifespan(port="fake-port"):
            pytest.fail("cancelled factory reached the body")
        except anyio.get_cancelled_exc_class() as error:
          cancelled.append(error)
          raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(enter_backend)
      await self.factory.create_started.wait()
      scopes[0].cancel()

    assert len(cancelled) == 1
    assert self.factory.drivers == []
    assert self.backend._driver is None
    assert self.events == ["factory.create"]
    assert not self.backend.is_active

  async def test_normal_exit_runs_block_lid_disconnect_then_clears_state(self) -> None:
    protocol = Protocol(stages=[])

    async with self.backend.lifespan(port="fake-port"):
      driver = self.factory.drivers[0]
      await self.backend.run_protocol(protocol, 25.0)
      assert self.backend._current_protocol is protocol

    assert self.events == [
      "factory.create",
      "deactivate_block",
      "deactivate_lid",
      "disconnect",
    ]
    assert self.backend._driver is None
    assert self.backend._current_protocol is None
    assert await self.backend.get_total_cycle_count() == 0
    assert await self.backend.get_current_cycle_index() == 0
    assert await self.backend.get_total_step_count() == 0
    assert await self.backend.get_current_step_index() == 0
    assert driver.calls[-3:] == [
      ("deactivate_block", (), {}),
      ("deactivate_lid", (), {}),
      ("disconnect", (), {}),
    ]

  async def test_body_cancellation_waits_for_each_shielded_cleanup(self) -> None:
    scenario = self.factory.scenarios[0]
    scenario.block_at = "deactivate_block"
    scenario.block_started = anyio.Event()
    scenario.block_release = anyio.Event()
    body_started = anyio.Event()
    finished = anyio.Event()
    scopes: List[anyio.CancelScope] = []

    async def run_body() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        async with self.backend.lifespan(port="fake-port"):
          body_started.set()
          await anyio.sleep_forever()
      finished.set()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(run_body)
      await body_started.wait()
      scopes[0].cancel()
      await scenario.block_started.wait()
      assert not finished.is_set()
      scenario.block_release.set()
      await finished.wait()

    assert self.events[-3:] == ["deactivate_block", "deactivate_lid", "disconnect"]
    assert self.backend._driver is None
    assert not self.backend.is_active

  @pytest.mark.parametrize(
    "failed_action",
    ["deactivate_block", "deactivate_lid", "disconnect"],
  )
  async def test_each_cleanup_failure_still_attempts_every_action(self, failed_action: str) -> None:
    failure = RuntimeError(f"{failed_action} failed")
    self.factory.scenarios[0].failures[failed_action] = failure

    with pytest.raises(RuntimeError) as error_info:
      async with self.backend.lifespan(port="fake-port"):
        await self.backend.run_protocol(Protocol(stages=[]), 25.0)

    assert error_info.value is failure
    assert self.events[-3:] == ["deactivate_block", "deactivate_lid", "disconnect"]
    assert self.backend._driver is None
    assert self.backend._current_protocol is None
    assert await self.backend.get_total_cycle_count() == 0
    assert await self.backend.get_current_cycle_index() == 0
    assert await self.backend.get_total_step_count() == 0
    assert await self.backend.get_current_step_index() == 0

  async def test_all_cleanup_failures_remain_reachable(self) -> None:
    block_failure = RuntimeError("block failed")
    lid_failure = RuntimeError("lid failed")
    disconnect_failure = RuntimeError("disconnect failed")
    self.factory.scenarios[0].failures = {
      "deactivate_block": block_failure,
      "deactivate_lid": lid_failure,
      "disconnect": disconnect_failure,
    }

    with pytest.raises(RuntimeError) as error_info:
      async with self.backend.lifespan(port="fake-port"):
        await self.backend.run_protocol(Protocol(stages=[]), 25.0)

    assert error_info.value is disconnect_failure
    assert disconnect_failure.__context__ is lid_failure
    assert lid_failure.__context__ is block_failure
    assert self.events[-3:] == ["deactivate_block", "deactivate_lid", "disconnect"]
    assert self.backend._driver is None
    assert self.backend._current_protocol is None
    assert await self.backend.get_total_cycle_count() == 0
    assert await self.backend.get_current_cycle_index() == 0
    assert await self.backend.get_total_step_count() == 0
    assert await self.backend.get_current_step_index() == 0

  async def test_sequential_reuse_owns_each_captured_driver(self) -> None:
    self.factory.scenarios.append(DriverScenario())

    async with self.backend.lifespan(port="fake-first"):
      first = self.factory.drivers[0]
    async with self.backend.lifespan(port="fake-second"):
      second = self.factory.drivers[1]

    assert first is not second
    assert [name for name, _, _ in first.calls] == [
      "deactivate_block",
      "deactivate_lid",
      "disconnect",
    ]
    assert [name for name, _, _ in second.calls] == [
      "deactivate_block",
      "deactivate_lid",
      "disconnect",
    ]
    assert [port for port, _ in self.factory.calls] == ["fake-first", "fake-second"]
    assert self.backend._driver is None

  async def test_driver_commands_and_protocol_tracking_are_preserved(self) -> None:
    protocol = Protocol(stages=[Stage(steps=[Step([95.0], 0, rate=2.0)], repeats=1)])

    async with self.backend.lifespan(port="fake-port"):
      driver = self.factory.drivers[0]
      driver.scenario.temperatures = [Temperature(95.0, 95.0)]
      await self.backend.open_lid()
      await self.backend.close_lid()
      await self.backend.lift_plate()
      await self.backend.jog_lid(12.5)
      await self.backend.set_block_temperature([37.0])
      await self.backend.set_lid_temperature([105.0])
      await self.backend.set_ramp_rate(1.5)
      assert await self.backend.get_device_info() == {"serial": "fake-thermocycler"}
      await self.backend.run_protocol(protocol, 50.0)
      assert self.backend._current_protocol is protocol

    assert driver.calls[:8] == [
      ("open_lid", (), {}),
      ("close_lid", (), {}),
      ("lift_plate", (), {}),
      ("jog_lid", (12.5,), {}),
      ("set_plate_temperature", (37.0,), {}),
      ("set_lid_temperature", (105.0,), {}),
      ("set_ramp_rate", (1.5,), {}),
      ("get_device_info", (), {}),
    ]
    assert ("set_ramp_rate", (), {"ramp_rate": 2.0}) in driver.calls
    assert (
      "set_plate_temperature",
      (),
      {"temp": 95.0, "hold_time": 0, "volume": 50.0},
    ) in driver.calls
