import sys
from typing import Any, Awaitable, Callable, Dict, List, Optional

import anyio
import pytest

if sys.version_info >= (3, 11):
  from builtins import BaseExceptionGroup
else:
  from exceptiongroup import BaseExceptionGroup

from pylabrobot.agrowpumps import AgrowPumpArray
from pylabrobot.agrowpumps import agrow_pump_array as agrow_module
from pylabrobot.concurrency import HasLifespan
from pylabrobot.testing.concurrency import AnyioTestBase

AsyncHook = Callable[[], Awaitable[None]]


class SimulatedRegisterResponse:
  """Register response returned by the software Modbus double."""

  def __init__(self, registers: List[int]) -> None:
    self.registers = registers


class SimulatedModbusClient:
  """Software-only stand-in for pymodbus' asynchronous serial client."""

  def __init__(
    self,
    *,
    num_channels: int = 6,
    connect_hook: Optional[AsyncHook] = None,
    discovery_hook: Optional[AsyncHook] = None,
    keep_alive_hook: Optional[AsyncHook] = None,
    write_hook: Optional[Callable[[int, int], Awaitable[None]]] = None,
    connect_succeeds: bool = True,
    close_error: Optional[BaseException] = None,
    events: Optional[List[str]] = None,
  ) -> None:
    self._connected = False
    self.num_channels = num_channels
    self.connect_hook = connect_hook
    self.discovery_hook = discovery_hook
    self.keep_alive_hook = keep_alive_hook
    self.write_hook = write_hook
    self.connect_succeeds = connect_succeeds
    self.close_error = close_error
    self.events = events if events is not None else []
    self.reads: List[tuple] = []
    self.writes: List[tuple] = []

  async def connect(self) -> None:
    self.events.append("connect.start")
    if self.connect_hook is not None:
      await self.connect_hook()
    self._connected = self.connect_succeeds
    self.events.append("connect.done")

  @property
  def connected(self) -> bool:
    return self._connected

  async def read_holding_registers(self, address: int, count: int, **kwargs: Any) -> Any:
    if "unit" not in kwargs:
      raise ValueError("unit must be specified")
    self.reads.append((address, count, kwargs))
    if address == 19:
      self.events.append("discover.start")
      if self.discovery_hook is not None:
        await self.discovery_hook()
      self.events.append("discover.done")
      return SimulatedRegisterResponse([16708, 12288 + self.num_channels * 256][:count])
    if address == 0:
      self.events.append("keep_alive.start")
      if self.keep_alive_hook is not None:
        await self.keep_alive_hook()
      self.events.append("keep_alive.done")
      return SimulatedRegisterResponse([0])
    raise AssertionError(f"unexpected register address {address}")

  async def write_register(self, address: int, value: int, **kwargs: Any) -> None:
    if "unit" not in kwargs:
      raise ValueError("unit must be specified")
    self.events.append(f"write.{address}.start")
    if self.write_hook is not None:
      await self.write_hook(address, value)
    self.writes.append((address, value, kwargs))
    self.events.append(f"write.{address}.done")

  def close(self, reconnect: bool = False) -> None:
    del reconnect
    self.events.append("close")
    self._connected = False
    if self.close_error is not None:
      raise self.close_error


def make_device(
  monkeypatch: pytest.MonkeyPatch, clients: List[SimulatedModbusClient]
) -> AgrowPumpArray:
  """Create a pump array whose client factory returns the supplied software doubles."""
  created_with: List[Dict[str, Any]] = []

  def client_factory(**kwargs: Any) -> SimulatedModbusClient:
    created_with.append(kwargs)
    return clients.pop(0)

  monkeypatch.setattr(agrow_module, "AsyncModbusSerialClient", client_factory)
  monkeypatch.setattr(agrow_module, "_MODBUS_IMPORT_ERROR", None)
  device = AgrowPumpArray(port="simulated", address=1)
  device._test_client_options = created_with  # type: ignore[attr-defined]
  return device


class TestAgrowPumpArrayBackendGuard(AnyioTestBase):
  """The pymodbus integration rejects Trio before constructing a client."""

  _anyio_backends = ["trio"]

  async def test_trio_backend_is_rejected_before_client_construction(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    constructed = False

    def client_factory(**kwargs: Any) -> SimulatedModbusClient:
      nonlocal constructed
      constructed = True
      return SimulatedModbusClient()

    monkeypatch.setattr(agrow_module, "AsyncModbusSerialClient", client_factory)
    monkeypatch.setattr(agrow_module, "_MODBUS_IMPORT_ERROR", None)
    device = AgrowPumpArray(port="simulated", address=1)

    with pytest.raises(RuntimeError, match="requires the asyncio backend"):
      async with device:
        pytest.fail("unsupported backend reached the lifespan body")

    assert not constructed


class TestAgrowPumpArray(AnyioTestBase):
  """Protocol and lifecycle tests for the asyncio-only pymodbus integration."""

  _anyio_backends = ["asyncio"]

  def test_inherits_all_managed_lifecycle_methods(self) -> None:
    assert AgrowPumpArray.setup is HasLifespan.setup
    assert AgrowPumpArray.stop is HasLifespan.stop
    assert AgrowPumpArray.__aenter__ is HasLifespan.__aenter__
    assert AgrowPumpArray.__aexit__ is HasLifespan.__aexit__

  async def test_unknown_lifespan_options_are_rejected_before_client_construction(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    client = SimulatedModbusClient()
    device = make_device(monkeypatch, [client])

    with pytest.raises(TypeError, match="unexpected keyword argument"):
      async with device.lifespan(unknown=True):
        pytest.fail("unknown options reached the lifespan body")

    assert device._test_client_options == []  # type: ignore[attr-defined]

  async def test_setup_discovers_channels_and_preserves_client_options(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    client = SimulatedModbusClient()
    device = make_device(monkeypatch, [client])

    async with device:
      assert device.port == "simulated"
      assert device.address == 1
      assert len(device.channels) == 6
      assert device.pump_index_to_address == {pump: pump + 100 for pump in range(6)}
      assert device._test_client_options == [  # type: ignore[attr-defined]
        {
          "port": "simulated",
          "baudrate": 115200,
          "timeout": 1,
          "stopbits": 1,
          "bytesize": 8,
          "parity": "E",
          "retry_on_empty": True,
        }
      ]
      assert client.reads[0] == (19, 2, {"unit": 1})

  async def test_pump_commands_bounds_and_serialization(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    client = SimulatedModbusClient()
    device = make_device(monkeypatch, [client])

    async with device:
      await device.channels[0].run_continuously(speed=1)
      await device.channels[2].halt()
      assert client.writes[:2] == [(100, 1, {"unit": 1}), (102, 0, {"unit": 1})]
      with pytest.raises(ValueError, match="between 0 and 100"):
        await device.channels[0].run_continuously(speed=101)
      with pytest.raises(ValueError, match="between 0 and 100"):
        await device.channels[0].run_continuously(speed=-1)
      with pytest.raises(NotImplementedError):
        await device.channels[0].run_revolutions(num_revolutions=1.0)
      assert device.channels[2].serialize() == {
        "port": "simulated",
        "address": 1,
        "channel": 2,
      }

  async def test_run_continuously_rejects_out_of_range_speed_before_writing(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    """Reject invalid speeds without sending a command to the pump."""
    client = SimulatedModbusClient()
    device = make_device(monkeypatch, [client])
    async with device:
      for speed in (-0.1, -0.9, 100.1, 100.9, float("nan"), float("inf"), -float("inf")):
        client.writes.clear()
        with pytest.raises(ValueError):
          await device.channels[0].run_continuously(speed=speed)
        assert client.writes == []

  async def test_run_continuously_valid_speed_conversion(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    """Preserve integer conversion for valid fractional speeds and boundaries."""
    client = SimulatedModbusClient()
    device = make_device(monkeypatch, [client])
    async with device:
      for speed, expected in ((0.0, 0), (0.9, 0), (50.9, 50), (100.0, 100)):
        client.writes.clear()
        await device.channels[0].run_continuously(speed=speed)
        assert client.writes == [(100, expected, {"unit": 1})]

  async def test_connection_failure_closes_client_and_clears_state(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    client = SimulatedModbusClient(connect_succeeds=False)
    device = make_device(monkeypatch, [client])

    with pytest.raises(ConnectionError, match="Modbus connection failed"):
      async with device:
        pytest.fail("failed connection reached body")

    assert client.events[-1] == "close"
    assert device._modbus is None
    assert device._pump_index_to_address is None
    assert device._num_channels is None
    assert device.channels == []

  async def test_cancellation_during_connect_closes_client_before_propagating(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    connect_started = anyio.Event()
    events: List[str] = []

    async def block_connect() -> None:
      connect_started.set()
      await anyio.sleep_forever()

    client = SimulatedModbusClient(connect_hook=block_connect, events=events)
    device = make_device(monkeypatch, [client])

    async def enter() -> None:
      try:
        async with device:
          pytest.fail("cancelled connection reached body")
      except anyio.get_cancelled_exc_class():
        events.append("cancelled")
        raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(enter)
      await connect_started.wait()
      task_group.cancel_scope.cancel()

    assert events[-2:] == ["close", "cancelled"]

  async def test_discovery_failure_closes_without_zero_writes(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    async def fail_discovery() -> None:
      raise RuntimeError("discovery failed")

    client = SimulatedModbusClient(discovery_hook=fail_discovery)
    device = make_device(monkeypatch, [client])

    with pytest.raises(RuntimeError, match="discovery failed"):
      async with device:
        pytest.fail("failed discovery reached body")

    assert client.writes == []
    assert client.events[-1] == "close"

  async def test_cancellation_during_discovery_closes_without_zero_writes(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    discovery_started = anyio.Event()
    events: List[str] = []

    async def block_discovery() -> None:
      discovery_started.set()
      await anyio.sleep_forever()

    client = SimulatedModbusClient(discovery_hook=block_discovery, events=events)
    device = make_device(monkeypatch, [client])

    async def enter() -> None:
      try:
        async with device:
          pytest.fail("cancelled discovery reached body")
      except anyio.get_cancelled_exc_class():
        events.append("cancelled")
        raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(enter)
      await discovery_started.wait()
      task_group.cancel_scope.cancel()

    assert client.writes == []
    assert events[-2:] == ["close", "cancelled"]

  async def test_normal_exit_zeros_every_pump_then_closes_and_clears_state(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    client = SimulatedModbusClient(num_channels=3)
    device = make_device(monkeypatch, [client])

    async with device:
      assert device.is_active

    assert client.writes == [
      (100, 0, {"unit": 1}),
      (101, 0, {"unit": 1}),
      (102, 0, {"unit": 1}),
    ]
    assert client.events[-1] == "close"
    assert device._modbus is None
    assert device._pump_index_to_address is None
    assert device._num_channels is None
    assert device.channels == []
    assert not device.is_active

  async def test_body_cancellation_finishes_cleanup_before_propagating(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    body_started = anyio.Event()
    events: List[str] = []
    client = SimulatedModbusClient(num_channels=2, events=events)
    device = make_device(monkeypatch, [client])

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

    assert events[-4:] == ["write.101.start", "write.101.done", "close", "cancelled"]

  async def test_sequential_reuse_constructs_and_discovers_fresh_clients(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    first = SimulatedModbusClient(num_channels=2)
    second = SimulatedModbusClient(num_channels=4)
    device = make_device(monkeypatch, [first, second])

    async with device:
      assert len(device.channels) == 2
    async with device:
      assert len(device.channels) == 4

    assert len(device._test_client_options) == 2  # type: ignore[attr-defined]
    assert first.events[-1] == "close"
    assert second.events[-1] == "close"
    assert device.channels == []

  async def test_close_failure_propagates_after_zeroing_and_state_clear(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    failure = RuntimeError("close failed")
    client = SimulatedModbusClient(num_channels=2, close_error=failure)
    device = make_device(monkeypatch, [client])

    with pytest.raises(RuntimeError) as error_info:
      async with device:
        pass

    assert error_info.value is failure
    assert len(client.writes) == 2
    assert device._modbus is None
    assert device.channels == []

  async def test_keep_alive_runs_as_child_of_lifespan_owner(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    request_seen = anyio.Event()
    worker_parent_ids: List[Optional[int]] = []

    async def record_worker() -> None:
      worker_parent_ids.append(anyio.get_current_task().parent_id)
      request_seen.set()

    client = SimulatedModbusClient(keep_alive_hook=record_worker)
    device = make_device(monkeypatch, [client])
    device._keep_alive_interval = 0.001

    async with device:
      owner_id = anyio.get_current_task().id
      await request_seen.wait()
      assert worker_parent_ids == [owner_id]

  async def test_keep_alive_failure_fails_lifespan_caller(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    async def fail_keep_alive() -> None:
      raise RuntimeError("keep alive failed")

    client = SimulatedModbusClient(keep_alive_hook=fail_keep_alive)
    device = make_device(monkeypatch, [client])
    device._keep_alive_interval = 0

    with pytest.raises(BaseExceptionGroup) as error_info:
      async with device:
        await anyio.sleep_forever()

    assert isinstance(error_info.value.exceptions[0], RuntimeError)
    assert str(error_info.value.exceptions[0]) == "keep alive failed"
    assert client.events[-1] == "close"

  async def test_no_keep_alive_request_occurs_after_exit(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    request_seen = anyio.Event()

    async def mark_request() -> None:
      request_seen.set()

    client = SimulatedModbusClient(keep_alive_hook=mark_request)
    device = make_device(monkeypatch, [client])
    device._keep_alive_interval = 0.001

    async with device:
      await request_seen.wait()
    reads_after_exit = len(client.reads)
    await anyio.sleep(0.01)

    assert len(client.reads) == reads_after_exit

  async def test_keep_alive_is_cancelled_and_joined_before_pumps_are_zeroed(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    keep_alive_started = anyio.Event()
    events: List[str] = []

    async def block_keep_alive() -> None:
      keep_alive_started.set()
      try:
        await anyio.sleep_forever()
      finally:
        events.append("keep_alive.joined")

    client = SimulatedModbusClient(num_channels=2, keep_alive_hook=block_keep_alive, events=events)
    device = make_device(monkeypatch, [client])
    device._keep_alive_interval = 0

    async with device:
      await keep_alive_started.wait()

    assert events.index("keep_alive.joined") < events.index("write.100.start")
    assert events.index("write.101.done") < events.index("close")

  async def test_close_waits_until_all_zero_writes_finish(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    events: List[str] = []

    async def checkpoint_write(address: int, value: int) -> None:
      del address, value
      await anyio.lowlevel.checkpoint()

    client = SimulatedModbusClient(num_channels=3, write_hook=checkpoint_write, events=events)
    device = make_device(monkeypatch, [client])

    async with device:
      pass

    assert events.index("write.100.done") < events.index("write.101.start")
    assert events.index("write.101.done") < events.index("write.102.start")
    assert events.index("write.102.done") < events.index("close")


class TestAgrowPymodbusIntegration(AnyioTestBase):
  """Construction tests that require the installed pymodbus implementation."""

  _anyio_backends = ["asyncio"]

  async def test_missing_pymodbus_fails_after_backend_validation(self) -> None:
    if agrow_module._MODBUS_IMPORT_ERROR is None:
      pytest.skip("pymodbus is installed")
    device = AgrowPumpArray(port="simulated", address=1)

    with pytest.raises(RuntimeError, match="pymodbus is not installed"):
      async with device:
        pytest.fail("missing dependency reached body")
