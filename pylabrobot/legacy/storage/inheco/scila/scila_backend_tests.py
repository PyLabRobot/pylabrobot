"""Software-only lifecycle tests for the legacy SCILA backend."""

import xml.etree.ElementTree as ET
from typing import Any, Dict, Optional, Tuple

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.inheco.transport.sila import InhecoSiLAInterface
from pylabrobot.legacy.storage.inheco.scila import scila_backend as scila_module
from pylabrobot.legacy.storage.inheco.scila.scila_backend import SCILABackend
from pylabrobot.testing.concurrency import AnyioTestBase

Command = Tuple[str, Dict[str, Any]]


class RecordingSiLATransport(HasLifespan):
  """Typed software double for the Wave 8 SiLA transport boundary."""

  def __init__(
    self,
    *,
    entry_error: Optional[BaseException] = None,
    close_error: Optional[BaseException] = None,
    command_errors: Optional[Dict[str, BaseException]] = None,
  ) -> None:
    super().__init__()
    self.client_ip = "127.0.0.9"
    self.machine_ip = "192.0.2.10"
    self.bound_port = 43123
    self.entry_error = entry_error
    self.close_error = close_error
    self.command_errors = command_errors or {}
    self.command_results: Dict[str, Any] = {}
    self.commands: list[Command] = []
    self.events: list[str] = []
    self.entries = 0
    self.exits = 0
    self.status_stall = 0.0
    self.sensor_payload = (
      "<Sensors><Mount>3700</Mount><Lid>10500</Lid><Ambient>2345</Ambient></Sensors>"
    )
    self.reset_started = anyio.Event()
    self.release_reset = anyio.Event()
    self.block_reset = False

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    """Record one transport generation and register its software close."""
    if kwargs:
      raise TypeError("RecordingSiLATransport does not accept lifespan options")
    self.entries += 1
    self.events.append("transport.enter")
    if self.entry_error is not None:
      raise self.entry_error
    stack.push_shielded_async_callback(self._close)

  async def _close(self) -> None:
    """Record transport closure and apply an optional scripted failure."""
    self.exits += 1
    self.events.append("transport.exit")
    await anyio.lowlevel.checkpoint()
    if self.close_error is not None:
      raise self.close_error

  async def send_command(self, command: str, **kwargs: Any) -> Any:
    """Return complete protocol-shaped responses without network access."""
    self.commands.append((command, dict(kwargs)))
    self.events.append(f"command:{command}")
    if command == "Reset" and self.block_reset:
      self.reset_started.set()
      await self.release_reset.wait()
    error = self.command_errors.get(command)
    if error is not None:
      raise error
    if command in self.command_results:
      return self.command_results[command]
    if command == "GetStatus":
      if self.status_stall:
        await anyio.sleep(self.status_stall)
      return {"GetStatusResponse": {"state": "Busy"}}
    if command == "ReadActualTemperature":
      root = ET.Element("Response")
      ET.SubElement(root, "String").text = self.sensor_payload
      return root
    return {}


def make_scila_backend(
  monkeypatch: pytest.MonkeyPatch, transport: Optional[RecordingSiLATransport] = None
) -> tuple[SCILABackend, RecordingSiLATransport]:
  """Construct production SCILA with one typed software transport."""
  interface = transport or RecordingSiLATransport()

  def build_interface(machine_ip: str, client_ip: Optional[str] = None) -> InhecoSiLAInterface:
    interface.machine_ip = machine_ip
    interface.client_ip = client_ip or "127.0.0.9"
    return interface  # type: ignore[return-value]

  monkeypatch.setattr(scila_module, "InhecoSiLAInterface", build_interface)
  return SCILABackend(scila_ip="192.0.2.44", client_ip="127.0.0.8"), interface


class TestSCILABackend(AnyioTestBase):
  """Exercise the backend lifecycle on both AnyIO backends."""

  async def test_managed_methods_are_inherited(self) -> None:
    """Kills retaining either custom setup or stop after native ownership is added."""
    assert SCILABackend.setup is HasLifespan.setup
    assert SCILABackend.stop is HasLifespan.stop
    assert SCILABackend.__aenter__ is HasLifespan.__aenter__
    assert SCILABackend.__aexit__ is HasLifespan.__aexit__

  async def test_transport_entry_failure_does_not_exit(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    """Kills registering transport cleanup before successful transport entry."""
    failure = RuntimeError("transport entry failed")
    backend, transport = make_scila_backend(
      monkeypatch, RecordingSiLATransport(entry_error=failure)
    )

    with pytest.raises(RuntimeError, match="transport entry failed"):
      async with backend:
        pytest.fail("failed entry reached the body")

    assert transport.entries == 1
    assert transport.exits == 0

  async def test_reset_failure_closes_transport(self, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kills entering reset before the Wave 8 lifespan owns cleanup."""
    backend, transport = make_scila_backend(
      monkeypatch,
      RecordingSiLATransport(command_errors={"Reset": RuntimeError("reset failed")}),
    )

    with pytest.raises(RuntimeError, match="reset failed"):
      async with backend:
        pytest.fail("failed entry reached the body")

    assert transport.exits == 1
    assert transport.commands == [
      (
        "Reset",
        {
          "deviceId": "MyController",
          "eventReceiverURI": "http://127.0.0.8:43123/",
          "simulationMode": False,
        },
      )
    ]

  async def test_initialize_failure_closes_transport(self, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kills swallowing SCILA initialization failures or leaking their transport."""
    backend, transport = make_scila_backend(
      monkeypatch,
      RecordingSiLATransport(command_errors={"Initialize": RuntimeError("initialize failed")}),
    )

    with pytest.raises(RuntimeError, match="initialize failed"):
      async with backend:
        pytest.fail("failed entry reached the body")

    assert [command for command, _ in transport.commands] == ["Reset", "Initialize"]
    assert transport.exits == 1

  async def test_body_cancellation_closes_transport(self, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kills unscoped Wave 8 ownership that leaks when the backend body is cancelled."""
    backend, transport = make_scila_backend(monkeypatch)
    entered = anyio.Event()

    async def use_backend() -> None:
      async with backend:
        entered.set()
        await anyio.sleep_forever()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(use_backend)
      await entered.wait()
      task_group.cancel_scope.cancel()

    assert transport.exits == 1
    assert transport.events[-1] == "transport.exit"

  async def test_close_failure_propagates(self, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kills suppressing the sole transport owner's close failure."""
    backend, transport = make_scila_backend(
      monkeypatch, RecordingSiLATransport(close_error=RuntimeError("close failed"))
    )

    with pytest.raises(RuntimeError, match="close failed"):
      async with backend:
        pass

    assert transport.exits == 1

  async def test_sequential_reuse(self, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kills retaining one-shot transport state across backend lifespans."""
    backend, transport = make_scila_backend(monkeypatch)

    async with backend:
      assert backend.is_active
    async with backend:
      assert backend.is_active

    assert transport.entries == 2
    assert transport.exits == 2
    assert [command for command, _ in transport.commands] == [
      "Reset",
      "Initialize",
      "Reset",
      "Initialize",
    ]

  async def test_wave8_params_preserve_scalar_results(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    """Kills replacing Wave 8 get_params with untyped XML text extraction."""
    backend, transport = make_scila_backend(monkeypatch)
    response = ET.fromstring(
      "<Response>"
      "<Parameter name='CurrentTemperature'><Float64>25.5</Float64></Parameter>"
      "<Parameter name='TargetTemperature'><Int32>37</Int32></Parameter>"
      "<Parameter name='TemperatureControl'><Boolean>true</Boolean></Parameter>"
      "</Response>"
    )

    transport.command_results["GetTemperature"] = response
    async with backend:
      result = await backend.request_temperature_information()

    assert result == {
      "CurrentTemperature": 25.5,
      "TargetTemperature": 37,
      "TemperatureControl": True,
    }

  async def test_public_commands_and_return_values_are_preserved(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    """Kills changing legacy command names, arguments, status defaults, or serialization."""
    backend, transport = make_scila_backend(monkeypatch)
    transport.command_results.update(
      {
        "GetStatus": {"GetStatusResponse": {"state": "standBy"}},
        "GetLiquidLevel": ET.fromstring(
          "<Response><Parameter name='LiquidLevel'><String>High</String></Parameter></Response>"
        ),
        "GetDoorStatus": ET.fromstring(
          "<Response>"
          "<Parameter name='Drawer1'><String>Opened</String></Parameter>"
          "<Parameter name='Drawer2'><String>Closed</String></Parameter>"
          "<Parameter name='Drawer3'><String>Opened</String></Parameter>"
          "<Parameter name='Drawer4'><String>Closed</String></Parameter>"
          "</Response>"
        ),
        "GetCO2FlowStatus": ET.fromstring(
          "<Response><Parameter name='CO2FlowStatus'><String>OK</String></Parameter></Response>"
        ),
        "GetValveStatus": ET.fromstring(
          "<Response>"
          "<Parameter name='H2O'><String>Opened</String></Parameter>"
          "<Parameter name='CO2 Normal'><String>Opened</String></Parameter>"
          "<Parameter name='CO2 Boost'><String>Closed</String></Parameter>"
          "</Response>"
        ),
      }
    )

    async with backend:
      assert await backend.request_status() == "standBy"
      assert await backend.request_liquid_level() == "High"
      assert await backend.request_drawer_statuses() == {
        1: "Opened",
        2: "Closed",
        3: "Opened",
        4: "Closed",
      }
      assert await backend.request_drawer_status(3) == "Opened"
      assert await backend.request_co2_flow_status() == "OK"
      assert await backend.request_valve_status() == {
        "H2O": "Opened",
        "CO2 Normal": "Opened",
        "CO2 Boost": "Closed",
      }
      await backend.open(2)
      await backend.close(4)
      await backend.maintenance()
      await backend.start_temperature_control(30.0)
      await backend.stop_temperature_control()

    assert transport.commands[2:] == [
      ("GetStatus", {}),
      ("GetLiquidLevel", {}),
      ("GetDoorStatus", {}),
      ("GetDoorStatus", {}),
      ("GetCO2FlowStatus", {}),
      ("GetValveStatus", {}),
      ("PrepareForInput", {"position": 2}),
      ("OpenDoor", {}),
      ("PrepareForOutput", {"position": 4}),
      ("CloseDoor", {}),
      ("Maintenance", {}),
      ("SetTemperature", {"targetTemperature": 30.0, "temperatureControl": True}),
      ("SetTemperature", {"temperatureControl": False}),
    ]
    assert backend.serialize() == {
      "type": "SCILABackend",
      "scila_ip": "192.0.2.44",
      "client_ip": "127.0.0.8",
    }
    for invalid_drawer in (0, 5):
      with pytest.raises(ValueError, match="Invalid drawer ID"):
        await backend.open(invalid_drawer)
      with pytest.raises(ValueError, match="Invalid drawer ID"):
        await backend.close(invalid_drawer)
      with pytest.raises(ValueError, match="Invalid drawer ID"):
        await backend.request_drawer_status(invalid_drawer)
