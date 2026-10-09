"""Software-only lifecycle and timing tests for the Inheco ODTC backend."""

import xml.etree.ElementTree as ET
from typing import Any, Optional

import anyio
import pytest
from trio.testing import MockClock

from pylabrobot.concurrency import HasLifespan
from pylabrobot.inheco.transport import sila as sila_transport
from pylabrobot.inheco.transport.sila import InhecoSiLAInterface, SiLAError
from pylabrobot.legacy.storage.inheco.scila.scila_backend_tests import RecordingSiLATransport
from pylabrobot.legacy.thermocycling.inheco import odtc_backend as odtc_module
from pylabrobot.legacy.thermocycling.inheco.odtc_backend import ExperimentalODTCBackend
from pylabrobot.legacy.thermocycling.standard import Protocol, Stage, Step
from pylabrobot.testing.concurrency import AnyioTestBase


def make_odtc_backend(
  monkeypatch: pytest.MonkeyPatch, transport: Optional[RecordingSiLATransport] = None
) -> tuple[ExperimentalODTCBackend, RecordingSiLATransport]:
  """Construct production ODTC with one typed software transport."""
  interface = transport or RecordingSiLATransport()

  def build_interface(machine_ip: str, client_ip: Optional[str] = None) -> InhecoSiLAInterface:
    interface.machine_ip = machine_ip
    interface.client_ip = client_ip or "127.0.0.9"
    return interface  # type: ignore[return-value]

  monkeypatch.setattr(odtc_module, "InhecoSiLAInterface", build_interface)
  return ExperimentalODTCBackend(ip="192.0.2.45", client_ip="127.0.0.7"), interface


class TestODTCBackend(AnyioTestBase):
  """Exercise lifecycle and command behavior on the supported asyncio backend."""

  _anyio_backends = ["asyncio"]

  async def test_managed_methods_are_inherited(self) -> None:
    """Kills retaining ODTC custom setup or stop beside native ownership."""
    assert ExperimentalODTCBackend.setup is HasLifespan.setup
    assert ExperimentalODTCBackend.stop is HasLifespan.stop
    assert ExperimentalODTCBackend.__aenter__ is HasLifespan.__aenter__
    assert ExperimentalODTCBackend.__aexit__ is HasLifespan.__aexit__

  async def test_reset_warning_does_not_hide_cancellation(
    self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
  ) -> None:
    """Kills broadening the ordinary initialization warning branch over cancellation."""
    transport = RecordingSiLATransport()
    transport.block_reset = True
    backend, _ = make_odtc_backend(monkeypatch, transport)
    scopes: list[anyio.CancelScope] = []
    cancelled = anyio.Event()

    async def enter_backend() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          async with backend:
            pytest.fail("cancelled reset reached the body")
        finally:
          cancelled.set()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(enter_backend)
      await transport.reset_started.wait()
      scopes[0].cancel()
      await cancelled.wait()

    assert transport.exits == 1
    assert "Warning during ODTC initialization" not in capsys.readouterr().out

  async def test_initialization_uses_wave8_transport(
    self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
  ) -> None:
    """Kills duplicate ownership, wrong ODTC reset arguments, or lost ordinary warnings."""
    failure = RuntimeError("device warming")
    backend, transport = make_odtc_backend(
      monkeypatch, RecordingSiLATransport(command_errors={"Initialize": failure})
    )

    async with backend:
      assert backend.is_active

    assert transport.entries == 1
    assert transport.exits == 1
    assert transport.commands == [
      (
        "Reset",
        {
          "deviceId": "ODTC",
          "eventReceiverURI": "http://127.0.0.7:43123/",
          "simulationMode": False,
        },
      ),
      ("Initialize", {}),
    ]
    assert capsys.readouterr().out == "Warning during ODTC initialization: device warming\n"

  async def test_body_cancellation_closes_transport(self, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kills leaving the Wave 8 transport outside the ODTC lifespan."""
    backend, transport = make_odtc_backend(monkeypatch)
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

  async def test_execute_method_delegates_reconciliation_to_wave8_transport(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    """Kills bypassing the canonical Wave 8 send path for ExecuteMethod."""
    backend, transport = make_odtc_backend(monkeypatch)
    protocol = Protocol(stages=[Stage(steps=[Step([37.0], 5.0)], repeats=1)])

    async with backend:
      await backend.run_protocol(protocol, method_name="PLR_Test")

    assert transport.commands[-1] == ("ExecuteMethod", {"methodName": "PLR_Test"})

  async def test_method_xml_semantics_are_preserved(self, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kills changing ODTC temperatures, looping, PID defaults, or XML parameter wrapping."""
    backend, transport = make_odtc_backend(monkeypatch)
    protocol = Protocol(stages=[Stage(steps=[Step([95.0], 30.0), Step([60.0], 45.0)], repeats=3)])

    async with backend:
      await backend.run_protocol(
        protocol,
        block_max_volume=50.0,
        start_block_temperature=25.0,
        start_lid_temperature=105.0,
        post_heating=False,
        method_name="PLR_Exact",
      )

    set_parameters = next(
      kwargs for command, kwargs in transport.commands if command == "SetParameters"
    )
    wrapper = ET.fromstring(set_parameters["paramsXML"])
    method_xml = wrapper.find("./Parameter/String")
    assert method_xml is not None and method_xml.text is not None
    method = ET.fromstring(method_xml.text).find("Method")
    assert method is not None
    assert method.attrib["methodName"] == "PLR_Exact"
    assert method.findtext("FluidQuantity") == "1"
    assert method.findtext("PostHeating") == "false"
    assert method.findtext("StartBlockTemperature") == "25"
    assert method.findtext("StartLidTemperature") == "105"
    steps = method.findall("Step")
    assert [
      (step.findtext("PlateauTemperature"), step.findtext("PlateauTime")) for step in steps
    ] == [
      ("95", "30"),
      ("60", "45"),
    ]
    assert steps[-1].findtext("GotoNumber") == "1"
    assert steps[-1].findtext("LoopNumber") == "2"
    assert method.findtext("PIDSet/PID/PHeating") == "60"

  async def test_warning_classification_uses_sila_error_attributes(
    self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
  ) -> None:
    """Kills classifying code-12 warnings through Wave 8 exception text instead of attributes."""
    backend, transport = make_odtc_backend(monkeypatch)
    transport.command_errors["ExecuteMethod"] = SiLAError(
      code=12,
      message="method accepted with warning",
      command="ExecuteMethod",
      details={"severity": "warning"},
    )

    async with backend:
      await backend.run_protocol(Protocol(stages=[]), method_name="PLR_Warning")

    output = capsys.readouterr().out
    assert output.endswith("[ODTC Warning] method accepted with warning\n")


class TestODTCTiming(AnyioTestBase):
  """Observe AnyIO deadlines and cache time on a zero-cost backend clock."""

  _anyio_backends = {"trio": {"clock": MockClock(autojump_threshold=0)}}

  async def test_idle_wait_uses_anyio_deadline(self, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kills wall-clock polling or restarting the timeout around each await."""
    backend, transport = make_odtc_backend(monkeypatch)
    transport.status_stall = 2.0

    start = anyio.current_time()
    with anyio.fail_after(5.0) as guard:
      with pytest.raises(RuntimeError, match="Timeout waiting for ODTC idle state"):
        await backend._wait_for_idle(timeout=2.5)
    elapsed = anyio.current_time() - start

    assert not guard.cancelled_caught
    assert elapsed == pytest.approx(2.5)
    assert [command for command, _ in transport.commands] == ["GetStatus"]

  async def test_sensor_cache_uses_anyio_clock(self, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kills retaining wall time for the two-second sensor cache lifetime."""
    backend, transport = make_odtc_backend(monkeypatch)

    first = await backend.get_sensor_data()
    await anyio.sleep(1.9)
    cached = await backend.get_sensor_data()
    await anyio.sleep(0.2)
    refreshed = await backend.get_sensor_data()

    assert first == {"Mount": 37.0, "Lid": 105.0, "Ambient": 23.45}
    assert cached == first
    assert refreshed == first
    assert [command for command, _ in transport.commands] == [
      "ReadActualTemperature",
      "ReadActualTemperature",
    ]


class TestODTCTrioGuard(AnyioTestBase):
  """Exercise the real Wave 8 backend guard without opening a socket."""

  _anyio_backends = ["trio"]

  async def test_trio_rejects_before_network_bind(self, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kills moving Wave 8's asyncio guard after callback-server construction."""
    monkeypatch.setattr(odtc_module, "InhecoSiLAInterface", InhecoSiLAInterface)

    def unexpected_bind(*args: Any, **kwargs: Any) -> None:
      raise AssertionError("callback server bind attempted")

    monkeypatch.setattr(sila_transport, "_CallbackHTTPServer", unexpected_bind)
    backend = ExperimentalODTCBackend(ip="192.0.2.45", client_ip="127.0.0.1")

    with pytest.raises(RuntimeError, match="requires the asyncio backend"):
      async with backend:
        pytest.fail("unsupported backend reached the body")
