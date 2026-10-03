"""Software-only lifecycle tests for the Tecan Spark reader."""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, cast

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.io.usb import USB
from pylabrobot.legacy.plate_reading.tecan.spark20m import spark_reader_async as reader_module
from pylabrobot.legacy.plate_reading.tecan.spark20m.enums import (
  DEVICE_ENDPOINTS,
  SparkDevice,
  SparkEndpoint,
)
from pylabrobot.legacy.plate_reading.tecan.spark20m.spark_reader_async import SparkReaderAsync
from pylabrobot.testing.concurrency import AnyioTestBase


class RecordingEndpoint:
  """One complete USB endpoint descriptor."""

  def __init__(self, address: int, attributes: int) -> None:
    self.bEndpointAddress = address
    self.bmAttributes = attributes


class RecordingConfiguration:
  """USB configuration exposing interface zero."""

  def __init__(self) -> None:
    self.interface = [
      RecordingEndpoint(SparkEndpoint.BULK_IN.value, 2),
      RecordingEndpoint(SparkEndpoint.BULK_OUT.value, 2),
      RecordingEndpoint(SparkEndpoint.INTERRUPT_IN.value, 3),
    ]

  def __getitem__(self, key: tuple[int, int]) -> List[RecordingEndpoint]:
    assert key == (0, 0)
    return self.interface


class RecordingDevice:
  """USB device descriptor used by one recording transport."""

  def __init__(self, discovery_error: Optional[BaseException] = None) -> None:
    self.discovery_error = discovery_error
    self.configurations: List[int] = []

  def is_kernel_driver_active(self, interface: int) -> bool:
    assert interface == 0
    return False

  def set_configuration(self, configuration: int) -> None:
    self.configurations.append(configuration)

  def get_active_configuration(self) -> RecordingConfiguration:
    if self.discovery_error is not None:
      raise self.discovery_error
    return RecordingConfiguration()


@dataclass
class USBScenario:
  """Entry and exit behavior for one recording USB instance."""

  entry_error: Optional[BaseException] = None
  exit_error: Optional[BaseException] = None
  discovery_error: Optional[BaseException] = None
  block_entry: Optional[anyio.Event] = None


class RecordingUSB(HasLifespan):
  """Native in-memory USB resource with observable ownership."""

  def __init__(
    self,
    device_type: SparkDevice,
    scenario: USBScenario,
    events: List[str],
    owner: SparkReaderAsync,
    configuration_callback: Any,
  ) -> None:
    super().__init__()
    self.device_type = device_type
    self.scenario = scenario
    self.events = events
    self.owner = owner
    self.configuration_callback = configuration_callback
    self.dev = RecordingDevice(scenario.discovery_error)
    self.empty_buffer_values: List[bool] = []
    self.maps_at_exit: List[tuple[Dict[SparkDevice, USB], Dict[SparkDevice, Any]]] = []

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    assert set(kwargs) <= {"empty_buffer"}
    empty_buffer = cast(bool, kwargs.get("empty_buffer", True))
    self.empty_buffer_values.append(empty_buffer)
    self.events.append(f"{self.device_type.name.lower()}.enter")
    stack.push_shielded_async_callback(self._close)
    self.configuration_callback(self.dev)
    if self.scenario.entry_error is not None:
      raise self.scenario.entry_error
    if self.scenario.block_entry is not None:
      self.scenario.block_entry.set()
      await anyio.sleep_forever()

  async def _close(self) -> None:
    await anyio.lowlevel.checkpoint()
    self.maps_at_exit.append((dict(self.owner.devices), dict(self.owner.device_endpoints)))
    self.events.append(f"{self.device_type.name.lower()}.exit")
    if self.scenario.exit_error is not None:
      raise self.scenario.exit_error


class RecordingUSBFactory:
  """Construct one explicitly scripted native USB double per attempt."""

  def __init__(self, owner: SparkReaderAsync, events: List[str]) -> None:
    self.owner = owner
    self.events = events
    self.scenarios: Dict[SparkDevice, List[USBScenario]] = {}
    self.instances: List[RecordingUSB] = []
    self.calls: List[Dict[str, Any]] = []

  def add(self, device_type: SparkDevice, *scenarios: USBScenario) -> None:
    self.scenarios[device_type] = list(scenarios)

  def __call__(self, **kwargs: Any) -> RecordingUSB:
    self.calls.append(dict(kwargs))
    product = kwargs["id_product"]
    device_type = next(device for device in SparkDevice if device.value == product)
    scenarios = self.scenarios.get(device_type)
    if not scenarios:
      raise RuntimeError("Device not found")
    instance = RecordingUSB(
      device_type,
      scenarios.pop(0),
      self.events,
      self.owner,
      kwargs["configuration_callback"],
    )
    self.instances.append(instance)
    return instance


def restore_usb(original_usb: Any) -> None:
  """Restore the production USB constructor after one test."""

  reader_module.USB = original_usb  # type: ignore[misc]


class TestSparkReaderLifecycle(AnyioTestBase):
  """Exercise Spark USB ownership under asyncio and Trio."""

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    assert kwargs == {}
    self.events: List[str] = []
    self.reader = SparkReaderAsync()
    self.factory = RecordingUSBFactory(self.reader, self.events)
    original_usb = reader_module.USB
    reader_module.USB = cast(Any, self.factory)  # type: ignore[misc]
    stack.callback(restore_usb, original_usb)

  async def test_reader_inherits_managed_methods(self) -> None:
    assert SparkReaderAsync.setup is HasLifespan.setup
    assert SparkReaderAsync.stop is HasLifespan.stop
    assert SparkReaderAsync.__aenter__ is HasLifespan.__aenter__
    assert SparkReaderAsync.__aexit__ is HasLifespan.__aexit__

  async def test_enters_each_discovered_usb(self) -> None:
    self.factory.add(SparkDevice.ABSORPTION, USBScenario())
    self.factory.add(SparkDevice.PLATE_TRANSPORT, USBScenario())

    async with self.reader:
      assert set(self.reader.devices) == {
        SparkDevice.ABSORPTION,
        SparkDevice.PLATE_TRANSPORT,
      }
      assert all(usb.empty_buffer_values == [False] for usb in self.factory.instances)
      assert all(usb.dev.configurations == [0, 0, 1] for usb in self.factory.instances)
      assert all("max_workers" not in call for call in self.factory.calls)

    assert self.events == [
      "absorption.enter",
      "plate_transport.enter",
      "plate_transport.exit",
      "absorption.exit",
    ]
    assert self.reader.devices == {}
    assert self.reader.device_endpoints == {}
    assert all(state == ({}, {}) for usb in self.factory.instances for state in usb.maps_at_exit)

  async def test_later_device_failure_keeps_successful_device_available(self) -> None:
    self.factory.add(SparkDevice.FLUORESCENCE, USBScenario())
    self.factory.add(
      SparkDevice.ABSORPTION,
      USBScenario(entry_error=RuntimeError("absorption unavailable")),
    )

    async with self.reader:
      assert set(self.reader.devices) == {SparkDevice.FLUORESCENCE}
      assert set(self.reader.device_endpoints) == {SparkDevice.FLUORESCENCE}

    assert self.events == [
      "fluorescence.enter",
      "absorption.enter",
      "absorption.exit",
      "fluorescence.exit",
    ]

  async def test_no_devices_raises_after_all_attempts(self) -> None:
    with pytest.raises(ValueError, match="Failed to connect to any known Spark devices"):
      async with self.reader:
        pytest.fail("missing devices reached the body")

    assert len(self.factory.calls) == len(SparkDevice)
    assert self.reader.devices == {}
    assert self.reader.device_endpoints == {}

  async def test_endpoint_discovery_failure_uses_fallback(self) -> None:
    self.factory.add(
      SparkDevice.ABSORPTION,
      USBScenario(discovery_error=RuntimeError("descriptor unavailable")),
    )

    async with self.reader:
      assert (
        self.reader.get_endpoints(SparkDevice.ABSORPTION)
        == DEVICE_ENDPOINTS[SparkDevice.ABSORPTION]
      )

  async def test_cancelled_entry_closes_entered_usb(self) -> None:
    blocked = anyio.Event()
    self.factory.add(SparkDevice.FLUORESCENCE, USBScenario())
    self.factory.add(SparkDevice.ABSORPTION, USBScenario(block_entry=blocked))
    scopes: List[anyio.CancelScope] = []

    async def enter_reader() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        async with self.reader:
          pytest.fail("cancelled entry reached the body")

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(enter_reader)
      await blocked.wait()
      scopes[0].cancel()

    assert self.events[-2:] == ["absorption.exit", "fluorescence.exit"]
    assert self.reader.devices == {}
    assert self.reader.device_endpoints == {}

  async def test_body_cancellation_closes_all_usb(self) -> None:
    self.factory.add(SparkDevice.ABSORPTION, USBScenario())
    self.factory.add(SparkDevice.PLATE_TRANSPORT, USBScenario())
    body_started = anyio.Event()
    scopes: List[anyio.CancelScope] = []

    async def use_reader() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        async with self.reader:
          body_started.set()
          await anyio.sleep_forever()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(use_reader)
      await body_started.wait()
      scopes[0].cancel()

    assert self.events[-2:] == ["plate_transport.exit", "absorption.exit"]
    assert self.reader.devices == {}
    assert self.reader.device_endpoints == {}

  async def test_cleanup_failure_does_not_skip_other_usb(self) -> None:
    failure = RuntimeError("absorption close failed")
    self.factory.add(SparkDevice.ABSORPTION, USBScenario(exit_error=failure))
    self.factory.add(SparkDevice.PLATE_TRANSPORT, USBScenario())

    with pytest.raises(RuntimeError) as error_info:
      async with self.reader:
        pass

    assert error_info.value is failure
    assert self.events[-2:] == ["plate_transport.exit", "absorption.exit"]
    assert self.reader.devices == {}
    assert self.reader.device_endpoints == {}

  async def test_sequential_reuse_rebuilds_maps(self) -> None:
    self.factory.add(SparkDevice.ABSORPTION, USBScenario(), USBScenario())
    connected: List[USB] = []

    async with self.reader:
      connected.append(self.reader.devices[SparkDevice.ABSORPTION])
    assert self.reader.devices == {}
    assert self.reader.device_endpoints == {}

    async with self.reader:
      connected.append(self.reader.devices[SparkDevice.ABSORPTION])

    assert connected[0] is not connected[1]
    assert self.events == [
      "absorption.enter",
      "absorption.exit",
      "absorption.enter",
      "absorption.exit",
    ]
    assert self.reader.devices == {}
    assert self.reader.device_endpoints == {}

  async def test_rejects_options_before_usb_construction(self) -> None:
    with pytest.raises(TypeError, match="unexpected keyword argument"):
      async with self.reader.lifespan(unknown=True):
        pytest.fail("invalid options reached the body")

    assert self.factory.calls == []
