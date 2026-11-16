"""CERTUS command sequencing and preconditions, without hardware."""

import logging
from typing import Any, TypedDict
from unittest.mock import patch

import anyio
import pytest
from typing_extensions import NotRequired

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.testing.concurrency import AnyioTestBase

from ._inco import Argument, Inco, IncoError
from ._inco_udp import IncoUDP
from .device import CertusFlex, CertusFlexError, Head, ValveCalibration
from .inco_tests import numeric
from .inco_tests import reply as tcp_reply
from .inco_udp_tests import reply as udp_reply
from .testing_utils import FakeInco, capture_logs

ROOT = "Machine.Entity.Dispenser"
POSITION = ROOT + ".Module.Entity.PosSystem"
PRESSURE = ROOT + ".Module.Entity.PressureRegulator"
HEAD = ROOT + ".Module.Entity.Head"
VALVE = ROOT + ".Module.Entity.Valve1"


class CleanCycleParameters(TypedDict):
  """Typed keyword arguments shared by clean-cycle cases."""

  prime_duration: float
  purge_duration: float
  purge_pulse_duration: float
  cycles: int
  purge_interval: NotRequired[float]


class TestCertusFlex(AnyioTestBase):
  """Check recovered device commands, completion barriers, and failures."""

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    self.device = CertusFlex(
      "example.invalid", target="CERTUS", head="flex5", timeout=0.03, poll_interval=0.001
    )
    self.inco = FakeInco(strings=["CF123", "1.2.3"])
    self.device._inco = self.inco  # type: ignore[assignment]
    await stack.enter_async_context(self.device.lifespan())
    self.inco.string_calls.clear()

  def test_inherits_managed_lifespan_methods(self) -> None:
    assert issubclass(CertusFlex, HasLifespan)
    assert issubclass(Inco, HasLifespan)
    assert issubclass(IncoUDP, HasLifespan)
    assert CertusFlex.setup is HasLifespan.setup
    assert CertusFlex.stop is HasLifespan.stop
    assert Inco.setup is HasLifespan.setup
    assert Inco.stop is HasLifespan.stop
    assert IncoUDP.setup is HasLifespan.setup
    assert IncoUDP.stop is HasLifespan.stop

  def test_connection_selection(self) -> None:
    """Default to direct UDP while preserving named-target TCP connections."""
    direct = CertusFlex("example.invalid", head="flex8_angled", source_ip="192.0.2.2")
    assert isinstance(direct._inco, IncoUDP)
    assert direct._inco.io.serialize()["port"] == 1964
    assert direct._inco.io.serialize()["source_ip"] == "192.0.2.2"
    server = CertusFlex("example.invalid", head="flex5", target="CERTUS")
    assert isinstance(server._inco, Inco)
    assert server._inco.io.serialize()["port"] == 7979
    for target in (None, "CERTUS"):
      custom = CertusFlex("example.invalid", head="flex5", target=target, port=12345)
      assert custom._inco.io.serialize()["port"] == 12345
    with pytest.raises(ValueError):
      CertusFlex("example.invalid", head="flex5", target="CERTUS", source_ip="192.0.2.2")

  def numbers(self, values: dict[str, list[float]]) -> None:
    """Return a sequence per path, then repeat the final reading."""

    async def read(path: str) -> float:
      sequence = values[path]
      return sequence.pop(0) if len(sequence) > 1 else sequence[0]

    self.inco.number_handler = read

  async def test_lifespan_only_reads_identity(self) -> None:
    device = CertusFlex("example.invalid", target="CERTUS", head="flex5", timeout=0.03)
    inco = FakeInco(strings=["CF123", "1.2.3"])
    device._inco = inco  # type: ignore[assignment]
    with capture_logs("pylabrobot.gyger", logging.INFO) as records:
      async with device:
        assert device.serial_number == "CF123"
        assert device.firmware_version == "1.2.3"
        assert inco.string_calls == [ROOT + ".Act.Serialnumber", "Software.Revision.Dispenser"]
        assert inco.proc_calls == []
        assert inco.put_calls == []
    assert any("Connected to CERTUS FLEX CF123, firmware 1.2.3" in r.getMessage() for r in records)
    assert inco.close_count == 1
    assert inco.proc_calls == []

  async def test_failed_lifespan_entry_disconnects(self) -> None:
    device = CertusFlex("example.invalid", target="CERTUS", head="flex5", timeout=0.03)
    inco = FakeInco(strings=[ConnectionError("failed")])
    device._inco = inco  # type: ignore[assignment]
    with pytest.raises(ConnectionError):
      async with device:
        pass
    assert inco.close_count == 1
    assert not device.is_active

  async def test_operations_require_active_lifespan(self) -> None:
    inactive = CertusFlex("example.invalid", target="CERTUS", head="flex5")
    with pytest.raises(RuntimeError, match="enter its lifespan"):
      await inactive.get_pressure()
    with pytest.raises(RuntimeError, match="enter its lifespan"):
      await inactive.valves[0].close()

  async def test_head_outputs(self) -> None:
    heads: dict[Head, range] = {
      "flex5": range(1, 6),
      "flex8": range(8),
      "flex4_angled": range(4),
      "flex8_angled": range(8),
    }
    for head, outputs in heads.items():
      device = CertusFlex("example.invalid", target="CERTUS", head=head)
      assert [valve.output_id for valve in device.valves] == list(outputs)

  async def test_initialize_from_off_advances_two_states(self) -> None:
    self.numbers(
      {
        ROOT + ".Prop.State": [1, 3, 3, 4, 4],
        ROOT + ".Coord.Prop.State": [5, 4, 5, 4],
        POSITION + ".Prop.State": [3, 5, 4],
      }
    )
    await self.device.initialize()
    assert self.inco.proc_calls == [
      (ROOT + ".Cmd.Start", ()),
      (ROOT + ".Cmd.Start", ()),
      (POSITION + ".Cmd.Init", ("",)),
    ]

  async def test_initialize_ready_is_idempotent(self) -> None:
    self.inco.default_number = 4.0
    await self.device.initialize()
    assert self.inco.proc_calls == []

  async def test_initialize_fault_is_not_acknowledged(self) -> None:
    self.inco.default_number = 6.0
    with pytest.raises(CertusFlexError):
      await self.device.initialize()
    assert self.inco.proc_calls == []

  async def test_move_waits_until_ready(self) -> None:
    self.numbers({POSITION + ".Prop.State": [4, 4, 5, 5, 4]})
    await self.device.move_to(1, 2, 3)
    assert self.inco.proc_calls == [(POSITION + ".Cmd.MoveXYZ", (1, 2, 3, "Slow"))]
    assert len(self.inco.number_calls) == 5

  async def test_short_move_acceptance_grace(self) -> None:
    self.inco.default_number = 4.0
    times = iter([0.0, 0.0, 5.0])
    with patch("pylabrobot.gyger.certus_flex.device.anyio.current_time", side_effect=times):
      await self.device.move_to(1, 2, 3)
    assert len(self.inco.number_calls) == 3

  async def test_move_fault_and_timeout_do_not_repeat(self) -> None:
    self.numbers({POSITION + ".Prop.State": [4, 5, 6]})
    with pytest.raises(CertusFlexError):
      await self.device.move_to(1, 2, 3)
    self.numbers({POSITION + ".Prop.State": [4, 5]})
    with pytest.raises(TimeoutError):
      await self.device.move_to(1, 2, 3)
    assert len(self.inco.proc_calls) == 2

  async def test_prime_position_moves_z_before_xy(self) -> None:
    self.numbers({POSITION + ".Prop.State": [4, 5, 4, 4, 5, 4, 4, 5, 4]})
    await self.device.move_to_prime()
    assert self.inco.proc_calls == [
      (POSITION + ".Cmd.MoveZToPark", ("Slow",)),
      (POSITION + ".Cmd.MoveXYToPrime", ("Slow",)),
      (POSITION + ".Cmd.MoveZToPrime", ("Slow",)),
    ]

  async def test_pressure_init_waits_for_transition_and_actual_value(self) -> None:
    self.numbers(
      {
        PRESSURE + ".Prop.State": [3, 3, 5, 5, 4, 4],
        PRESSURE + ".Act.ChargeState": [8, 3, 3],
        PRESSURE + ".Act.Pressure": [0.1, 0.2, 0.3],
      }
    )
    await self.device.set_pressure(0.3)
    assert self.inco.put_calls == [(PRESSURE + ".Prop.SetPressure", 0.3)]
    assert self.inco.proc_calls == [(PRESSURE + ".Cmd.Init", ())]

  async def test_pressure_failure_is_reported(self) -> None:
    self.numbers({PRESSURE + ".Prop.State": [4], PRESSURE + ".Act.ChargeState": [2]})
    with pytest.raises(CertusFlexError):
      await self.device.set_pressure(0.3)

  async def test_discharge(self) -> None:
    self.numbers({PRESSURE + ".Prop.State": [4, 5, 3], PRESSURE + ".Act.Pressure": [0.1, 0.0]})
    await self.device.set_pressure(0)
    assert self.inco.proc_calls == [(PRESSURE + ".Cmd.Discharge", ())]
    assert self.inco.put_calls == [(PRESSURE + ".Prop.SetPressure", 0)]

  async def test_timed_pulse_uses_microseconds_and_completion_barrier(self) -> None:
    self.numbers(
      {path + ".Prop.State": [4.0] for path in (ROOT, POSITION, PRESSURE, HEAD, VALVE)}
      | {HEAD + ".Prop.SyncValves": [0.0, 2.0]}
    )
    await self.device.valves[0].dispense_for(0.001)
    assert self.inco.proc_calls == [
      (VALVE + ".Cmd.Dispens", (1000,)),
      (HEAD + ".Cmd.SyncValves", ()),
    ]
    assert self.inco.put_calls == [(HEAD + ".Prop.SyncValves", 0)]

  async def test_valve_fault_prevents_dispensing(self) -> None:
    self.numbers({ROOT + ".Prop.State": [4], VALVE + ".Prop.State": [6]})
    with pytest.raises(CertusFlexError):
      await self.device.valves[0].dispense_for(0.001)
    assert self.inco.proc_calls == []

  async def test_valve_fault_after_pulse_is_reported(self) -> None:
    self.numbers(
      {path + ".Prop.State": [4.0] for path in (ROOT, POSITION, PRESSURE, HEAD)}
      | {VALVE + ".Prop.State": [4.0, 6.0], HEAD + ".Prop.SyncValves": [1.0]}
    )
    with pytest.raises(CertusFlexError):
      await self.device.valves[0].dispense_for(0.001)
    assert len(self.inco.proc_calls) == 2

  async def test_barrier_timeout_does_not_repeat_dose(self) -> None:
    self.numbers(
      {path + ".Prop.State": [4.0] for path in (ROOT, POSITION, PRESSURE, HEAD, VALVE)}
      | {HEAD + ".Prop.SyncValves": [0.0]}
    )
    with pytest.raises(TimeoutError, match="do not retry"):
      await self.device.valves[0].dispense_for(0.001)
    assert len(self.inco.proc_calls) == 2

  async def test_volume_requires_calibration_and_matching_pressure(self) -> None:
    valve = self.device.valves[0]
    with pytest.raises(ValueError):
      await valve.dispense(1)
    valve.calibration = ValveCalibration(((1, 0.001), (2, 0.002)), pressure=0.3)
    self.numbers({PRESSURE + ".Act.Pressure": [0.1], PRESSURE + ".Prop.SetPressure": [0.3]})
    with pytest.raises(CertusFlexError):
      await valve.dispense(1)
    assert self.inco.proc_calls == []

  async def test_corrected_volume_sends_vendor_open_time(self) -> None:
    """A volume correction precedes interpolation and sends microseconds to Dispens."""
    valve = self.device.valves[0]
    valve.calibration = ValveCalibration(((0.5, 0.0005), (1.5, 0.00125)), pressure=0.2)
    self.numbers(
      {path + ".Prop.State": [4.0] for path in (ROOT, POSITION, PRESSURE, HEAD, VALVE)}
      | {
        PRESSURE + ".Act.Pressure": [0.2],
        PRESSURE + ".Prop.SetPressure": [0.2],
        HEAD + ".Prop.SyncValves": [1.0],
      }
    )
    await valve.dispense(1.0, correction_factor=1.25)
    assert self.inco.proc_calls == [
      (VALVE + ".Cmd.Dispens", (1062.5,)),
      (HEAD + ".Cmd.SyncValves", ()),
    ]
    self.inco.proc_calls.clear()
    with pytest.raises(ValueError):
      await valve.dispense(1.5, correction_factor=1.25)
    assert self.inco.proc_calls == []

  async def test_pressure_check_and_pulse_share_operation_lock(self) -> None:
    valve = self.device.valves[0]
    valve.calibration = ValveCalibration(((1, 0.001), (2, 0.002)), pressure=0.3)
    waiting, release = anyio.Event(), anyio.Event()

    async def read(path: str) -> float:
      if path == PRESSURE + ".Act.Pressure":
        waiting.set()
        await release.wait()
        return 0.3
      if path == PRESSURE + ".Prop.SetPressure":
        return 0.3
      return 4.0

    self.inco.number_handler = read
    async with anyio.create_task_group() as tg:
      tg.start_soon(valve.dispense, 1.5)
      await waiting.wait()
      tg.start_soon(valve.close)
      await anyio.sleep(0.005)
      assert self.inco.proc_calls == []
      release.set()
    assert self.inco.proc_calls == [
      (VALVE + ".Cmd.Dispens", (1500,)),
      (HEAD + ".Cmd.SyncValves", ()),
      (VALVE + ".Cmd.CloseValve", ()),
      (HEAD + ".Cmd.SyncValves", ()),
    ]

  async def test_invalid_parameters_do_not_communicate(self) -> None:
    for pressure in (-1, 0.05, 1.1, float("nan")):
      with pytest.raises(ValueError):
        await self.device.set_pressure(pressure)
    for duration in (0, -1, 0.00001, float("inf"), 3000):
      with pytest.raises(ValueError):
        await self.device.valves[0].dispense_for(duration)
    with pytest.raises(ValueError):
      await self.device.move_to(float("nan"), 1, 2)
    assert self.inco.number_calls == []
    assert self.inco.proc_calls == []

  async def test_invalid_state(self) -> None:
    for state in (-1, 7, 1.5, float("nan")):
      self.inco.default_number = state
      with pytest.raises(CertusFlexError):
        await self.device.valves[0].get_state()


class TestDeviceMaintenance(AnyioTestBase):
  """Check native valve maintenance, cycle sequencing, and failure containment."""

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    """Supply ready modules, regulated pressure, and a ten-second prime limit."""
    self.device = CertusFlex("example.invalid", head="flex8_angled", timeout=0.1)
    self.inco = FakeInco(strings=["CF123", "1.2.3"])
    self.device._inco = self.inco  # type: ignore[assignment]

    async def read(path: str) -> float:
      if path.endswith(".PrimeTimeout"):
        return 10e6
      if path in (PRESSURE + ".Act.Pressure", PRESSURE + ".Prop.SetPressure"):
        return 0.2
      if path == HEAD + ".Prop.SyncValves":
        return 1.0
      return 4.0

    self.inco.number_handler = read

    async def noop_wait_state(*args: Any, **kw: Any) -> None:
      return

    stack.enter_context(patch.object(self.device, "_wait_state", new=noop_wait_state))
    await stack.enter_async_context(self.device.lifespan())
    self.inco.string_calls.clear()
    self.parameters: CleanCycleParameters = {
      "prime_duration": 0.1,
      "purge_duration": 0.02,
      "purge_pulse_duration": 0.001,
      "cycles": 1,
    }

  async def test_prime_limit_and_completion(self) -> None:
    """Prime uses microseconds and a barrier; its firmware limit is checked first."""
    await self.device.valves[1].prime(0.1)
    assert self.inco.proc_calls == [
      (VALVE + ".Cmd.Prime", (100000,)),
      (HEAD + ".Cmd.SyncValves", ()),
    ]
    self.inco.proc_calls.clear()
    with pytest.raises(ValueError):
      await self.device.valves[1].prime(11)
    assert self.inco.proc_calls == []

  async def test_purge_timing_variants(self) -> None:
    """Keep pulse timing in firmware and support its explicit-interval form."""
    await self.device.valves[1].purge(0.02, pulse_duration=0.001)
    await self.device.valves[1].purge(0.02, pulse_duration=0.001, interval=0.002)
    assert self.inco.proc_calls == [
      (VALVE + ".Cmd.Purge", (20000, 1000)),
      (HEAD + ".Cmd.SyncValves", ()),
      (VALVE + ".Cmd.Purge", (20000, 1000, 2000)),
      (HEAD + ".Cmd.SyncValves", ()),
    ]

  async def test_unsupported_purge_is_not_resubmitted(self) -> None:
    """An unsupported explicit interval cannot trigger a second pulse train."""

    async def fail_purge(path: str, *args: Argument) -> None:
      raise IncoError(0x50403, VALVE + ".Cmd.Purge")

    self.inco.call_handler = fail_purge
    with pytest.raises(IncoError):
      await self.device.valves[1].purge(0.02, pulse_duration=0.001, interval=0.002)
    assert self.inco.proc_calls == [(VALVE + ".Cmd.Purge", (20000, 1000, 2000))]

  async def test_abort_failure_preserves_cycle_error(self) -> None:
    """Failed cleanup is logged without hiding the operation that failed."""
    failure = TimeoutError("lost purge reply")

    async def command(path: str, *arguments: Argument) -> None:
      if path.endswith(".Cmd.Purge"):
        raise failure
      if path.endswith(".Cmd.StopDispens"):
        raise ConnectionError("socket closed")

    self.inco.call_handler = command
    with capture_logs("pylabrobot.gyger", logging.ERROR) as records:
      with pytest.raises(TimeoutError) as caught:
        await self.device.clean_cycle([1, 2], **self.parameters)
    assert caught.value is failure
    assert len([r for r in records if r.levelno == logging.ERROR]) == 2

  async def test_clean_sequence_and_pressure_unchanged(self) -> None:
    """Purge all selected channels, pause, prime them, repeat, then park."""
    parameters = self.parameters.copy()
    parameters["cycles"] = 2
    sleeps: list[float] = []

    async def record_sleep(delay: float) -> None:
      sleeps.append(delay)

    with patch("pylabrobot.gyger.certus_flex.device.anyio.sleep", new=record_sleep):
      await self.device.clean_cycle([1, 2], **parameters)
    cycle = [
      (VALVE + ".Cmd.Purge", (20000, 1000)),
      (HEAD + ".Cmd.SyncValves", ()),
      (VALVE.replace("Valve1", "Valve2") + ".Cmd.Purge", (20000, 1000)),
      (HEAD + ".Cmd.SyncValves", ()),
      (VALVE + ".Cmd.Prime", (100000,)),
      (HEAD + ".Cmd.SyncValves", ()),
      (VALVE.replace("Valve1", "Valve2") + ".Cmd.Prime", (100000,)),
      (HEAD + ".Cmd.SyncValves", ()),
    ]
    assert self.inco.proc_calls == [
      (POSITION + ".Cmd.MoveZToPark", ("Slow",)),
      (POSITION + ".Cmd.MoveXYToPurge", ("Slow",)),
      (POSITION + ".Cmd.MoveZToPurge", ("Slow",)),
      *cycle,
      *cycle,
      (POSITION + ".Cmd.MoveZToPark", ("Slow",)),
      (POSITION + ".Cmd.MoveXYToPark", ("Slow",)),
    ]
    assert sleeps == [0.25, 0.25]
    assert all(item == (HEAD + ".Prop.SyncValves", 0) for item in self.inco.put_calls)

  async def test_invalid_cycle_inputs_never_move_or_dispense(self) -> None:
    """Reject invalid channels, cycles, pulse widths, and intervals up front."""
    for channels in ([], [True], [-1], [8], [1, 1]):
      with pytest.raises(ValueError):
        await self.device.clean_cycle(channels, **self.parameters)  # type: ignore[arg-type]
    invalid_parameters: tuple[CleanCycleParameters, ...] = (
      {**self.parameters, "cycles": 0},
      {**self.parameters, "cycles": True},
      {**self.parameters, "prime_duration": float("nan")},
      {**self.parameters, "prime_duration": 11},
      {**self.parameters, "purge_pulse_duration": 0.03},
      {**self.parameters, "purge_interval": -1},
    )
    for parameters in invalid_parameters:
      with pytest.raises(ValueError):
        await self.device.clean_cycle([1], **parameters)
    assert self.inco.proc_calls == []
    assert self.inco.put_calls == []

  async def test_unregulated_pressure_prevents_motion(self) -> None:
    """Ready flags alone do not authorize a clean cycle with zero pressure."""
    parameters = self.parameters.copy()
    parameters["prime_duration"] = 0.00005
    seq = iter([1e6, 4.0, 4.0, 4.0, 4.0, 4.0, 0.0, 0.0])

    async def seq_read(path: str) -> float:
      return next(seq, 4.0)

    self.inco.number_handler = seq_read
    with pytest.raises(CertusFlexError):
      await self.device.clean_cycle([1], **parameters)
    assert self.inco.proc_calls == []

  async def test_failed_purge_aborts_without_retry_or_recovery_move(self) -> None:
    """An uncertain pulse result stops selected valves and preserves the original error."""
    failure = TimeoutError("lost purge reply")

    async def command(path: str, *arguments: Argument) -> None:
      if path.endswith(".Cmd.Purge"):
        raise failure

    self.inco.call_handler = command
    with pytest.raises(TimeoutError) as caught:
      await self.device.clean_cycle([1, 2], **self.parameters)
    assert caught.value is failure
    assert self.inco.proc_calls[-3:] == [
      (VALVE + ".Cmd.Purge", (20000, 1000)),
      (VALVE + ".Cmd.StopDispens", ()),
      (VALVE.replace("Valve1", "Valve2") + ".Cmd.StopDispens", ()),
    ]
    assert len(self.inco.proc_calls) == 6

  async def test_cancellation_aborts_and_releases_operation_lock(self) -> None:
    """Level-triggered cancellation sends shielded StopDispens and releases the device lock."""
    started = anyio.Event()

    async def command(path: str, *arguments: Argument) -> None:
      if path.endswith(".Cmd.Purge"):
        started.set()
        await anyio.Event().wait()

    self.inco.call_handler = command
    scope = anyio.CancelScope()

    async def run_clean() -> None:
      with scope:
        await self.device.clean_cycle([1, 2], **self.parameters)

    async with anyio.create_task_group() as tg:
      tg.start_soon(run_clean)
      await started.wait()
      scope.cancel()
    assert scope.cancelled_caught
    assert self.inco.proc_calls[-2:] == [
      (VALVE + ".Cmd.StopDispens", ()),
      (VALVE.replace("Valve1", "Valve2") + ".Cmd.StopDispens", ()),
    ]
    assert len(self.inco.proc_calls) == 6
    with anyio.fail_after(0.1):
      async with self.device._operation_lock:
        pass

  async def test_clean_cycle_excludes_other_motion(self) -> None:
    """A second operation cannot interleave motion into an active cleaning sequence."""
    started, release = anyio.Event(), anyio.Event()

    async def command(path: str, *arguments: Argument) -> None:
      if path.endswith(".Cmd.Purge"):
        started.set()
        await release.wait()

    self.inco.call_handler = command
    async with anyio.create_task_group() as tg:
      tg.start_soon(
        lambda: self.device.clean_cycle([1], **self.parameters)  # type: ignore[arg-type,return-value]
      )
      await started.wait()
      tg.start_soon(self.device.move_to, 1, 2, 3)
      await anyio.sleep(0.005)
      assert (POSITION + ".Cmd.MoveXYZ", (1, 2, 3, "Slow")) not in self.inco.proc_calls
      release.set()
    assert self.inco.proc_calls[-1] == (POSITION + ".Cmd.MoveXYZ", (1, 2, 3, "Slow"))

  async def test_cancelled_dispense_reconnects_tcp_and_sends_stop_dispens(self) -> None:
    """Cancelling an in-flight TCP Dispens reopens INCO and delivers StopDispens."""
    string_payloads = [
      bytes.fromhex("0202000000060000") + b"CF123\0",
      bytes.fromhex("0202000000060000") + b"1.2.3\0",
    ]
    dispens_received = anyio.Event()
    stop_received = anyio.Event()
    received_frames: list[bytes] = []
    connection_count = 0

    async def handle_client(client: anyio.abc.ByteStream) -> None:
      nonlocal connection_count
      connection_count += 1
      conn_idx = connection_count
      seq = 0
      async with client:
        while True:
          try:
            frame = await client.receive(4096)
          except anyio.EndOfStream:
            return
          if not frame:
            return
          received_frames.append(frame)
          seq += 1
          kind, subtype = frame[18], frame[19]
          if conn_idx == 1 and b".Cmd.Dispens\x00" in frame:
            dispens_received.set()
            await anyio.sleep_forever()
          if b".Cmd.StopDispens\x00" in frame:
            stop_received.set()
          payload = string_payloads.pop(0) if (kind, subtype) == (2, 5) else numeric(4.0)
          header, body = tcp_reply(payload, kind=kind, subtype=subtype, sequence=seq)
          await client.send(header + body)

    listener = await anyio.create_tcp_listener(local_host="127.0.0.1", local_port=0)
    async with listener:
      port = int(listener.extra(anyio.abc.SocketAttribute.local_port))
      async with anyio.create_task_group() as server_tg:
        server_tg.start_soon(listener.serve, handle_client)
        device = CertusFlex(
          "127.0.0.1", target="CERTUS", port=port, head="flex5", timeout=2.0, poll_interval=0.005
        )
        async with device:
          scope = anyio.CancelScope()

          async def run_dispense() -> None:
            with scope:
              try:
                await device.valves[0].dispense_for(0.001)
              except BaseException:
                await device._stop_outputs([0])
                raise

          with anyio.fail_after(5.0):
            async with anyio.create_task_group() as tg:
              tg.start_soon(run_dispense)
              await dispens_received.wait()
              scope.cancel()
              await stop_received.wait()
          assert scope.cancelled_caught
          assert connection_count == 2
          assert sum(b".Cmd.Dispens\x00" in f for f in received_frames) == 1
          assert sum(b".Cmd.StopDispens\x00" in f for f in received_frames) == 1
        server_tg.cancel_scope.cancel()

  async def test_cancelled_purge_reconnects_udp_and_sends_stop_dispens(self) -> None:
    """Cancelling an in-flight UDP Purge rebinds a fresh socket and delivers StopDispens."""
    purge_received = anyio.Event()
    stop_received = anyio.Event()
    received_packets: list[bytes] = []
    source_ports: list[int] = []
    strings = [b"CF123\0", b"1.2.3\0"]

    server = await anyio.create_udp_socket(local_host="127.0.0.1", local_port=0)
    async with server:
      port = int(server.extra(anyio.abc.SocketAttribute.local_port))

      async def serve_udp() -> None:
        while True:
          packet, peer = await server.receive()
          received_packets.append(packet)
          source_ports.append(int(peer[1]))
          unescaped = packet[2:-3].replace(b"\x10\x10", b"\x10")
          seq = unescaped[0]
          if b".Cmd.Purge\x00" in unescaped:
            purge_received.set()
            continue
          if b".Cmd.StopDispens\x00" in unescaped:
            stop_received.set()
          if strings:
            payload = strings.pop(0)
          elif b"Pressure\x00" in unescaped:
            payload = bytes.fromhex("000000004ccccccc0d")
          else:
            payload = bytes.fromhex("00000004000000000d")
          await server.sendto(udp_reply(seq, payload), peer[0], peer[1])

      async with anyio.create_task_group() as server_tg:
        server_tg.start_soon(serve_udp)
        device = CertusFlex("127.0.0.1", port=port, head="flex5", timeout=2.0, poll_interval=0.005)
        device.valves[0].calibration = ValveCalibration(((1, 0.001), (5, 0.005)), pressure=0.3)
        async with device:
          scope = anyio.CancelScope()

          async def run_purge() -> None:
            with scope:
              await device.valves[0].purge_volume(2.0, pulse_volume=2.0)

          with anyio.fail_after(5.0):
            async with anyio.create_task_group() as tg:
              tg.start_soon(run_purge)
              await purge_received.wait()
              scope.cancel()
              await stop_received.wait()
          assert scope.cancelled_caught
          assert len(set(source_ports)) == 2
          assert sum(b".Cmd.Purge\x00" in p for p in received_packets) == 1
          assert sum(b".Cmd.StopDispens\x00" in p for p in received_packets) == 1
        server_tg.cancel_scope.cancel()


class TestValveCalibration:
  """Calibrations interpolate only within their measured range."""

  def test_interpolation_and_boundaries(self) -> None:
    calibration = ValveCalibration(((1, 0.001), (3, 0.005)), pressure=0.3)
    assert calibration.duration_for_volume(1) == 0.001
    assert calibration.duration_for_volume(2) == 0.003
    assert calibration.duration_for_volume(3) == 0.005
    for volume in (0, 4, float("nan")):
      with pytest.raises(ValueError):
        calibration.duration_for_volume(volume)

  def test_bad_calibrations(self) -> None:
    for points in (
      (),
      ((1, 1),),
      ((1, 1), (1, 2)),
      ((1, 2), (2, 1)),
      ((-1, 1), (2, 2)),
      ((1, 1), (float("inf"), 2)),
    ):
      with pytest.raises(ValueError):
        ValveCalibration(points, pressure=0.3)  # type: ignore[arg-type]

  def test_vendor_volume_conversion(self) -> None:
    """Reference values from CERTUS CONTROL's volume correction and linear interpolation."""
    calibration = ValveCalibration(((0.5, 0.0005), (1.5, 0.00125), (4.0, 0.003)), pressure=0.2)
    for volume, factor, expected_us in (
      (0.5, 1.0, 500),
      (1.0, 1.0, 875),
      (1.5, 1.0, 1250),
      (2.0, 1.0, 1600),
      (4.0, 1.0, 3000),
      (1.0, 0.5, 500),
      (1.0, 1.25, 1062.5),
      (1.0, 1.5, 1250),
      (2.0, 1.25, 1950),
    ):
      assert calibration.duration_for_volume(
        volume, correction_factor=factor
      ) * 1e6 == pytest.approx(expected_us)
    for factor in (0, 0.4999, 1.5001, True, float("nan"), float("inf")):
      with pytest.raises(ValueError):
        calibration.duration_for_volume(1.0, correction_factor=factor)
    for volume, factor in ((0.5, 0.5), (4.0, 1.5)):
      with pytest.raises(ValueError):
        calibration.duration_for_volume(volume, correction_factor=factor)
