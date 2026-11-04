from typing import Any, Dict, List, cast
from unittest.mock import patch

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.io.serial import Serial
from pylabrobot.legacy.plate_reading.molecular_devices.backend import MolecularDevicesError
from pylabrobot.legacy.plate_reading.molecular_devices.backend_tests import (
  LEGACY_SPECTRAMAX_TYPES,
  FakeLegacySpectraMaxSerial,
)
from pylabrobot.testing.concurrency import AnyioTestBase


class TestLegacySpectraMaxLifecycle(AnyioTestBase):
  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    assert kwargs == {}

  def _make(
    self,
    backend_type: Any,
    script: List[List[bytes]],
    **serial_kwargs: Any,
  ) -> Any:
    with patch("pylabrobot.legacy.plate_reading.molecular_devices.backend.Serial"):
      backend = backend_type(port="FAKE")
    backend.io = cast(Serial, FakeLegacySpectraMaxSerial(script, **serial_kwargs))
    return backend

  @pytest.mark.parametrize("backend_type", LEGACY_SPECTRAMAX_TYPES)
  async def test_models_inherit_managed_methods(self, backend_type: Any) -> None:
    assert backend_type.setup is HasLifespan.setup
    assert backend_type.stop is HasLifespan.stop
    assert backend_type.__aenter__ is HasLifespan.__aenter__
    assert backend_type.__aexit__ is HasLifespan.__aexit__

  @pytest.mark.parametrize("backend_type", LEGACY_SPECTRAMAX_TYPES)
  async def test_serial_entry_failure_rolls_back(self, backend_type: Any) -> None:
    events: List[str] = []
    failure = RuntimeError("serial entry failed")
    backend = self._make(backend_type, [], events=events, entry_error=failure)

    with pytest.raises(RuntimeError) as error_info:
      async with backend:
        pytest.fail("backend entered after serial failure")

    assert error_info.value is failure
    assert events == ["serial.enter", "serial.exit"]
    assert not backend.is_active

  @pytest.mark.parametrize("backend_type", LEGACY_SPECTRAMAX_TYPES)
  async def test_probe_failure_closes_serial(self, backend_type: Any) -> None:
    events: List[str] = []
    backend = self._make(backend_type, [[b"FAIL\t105>\r\n"]], events=events)
    serial = cast(FakeLegacySpectraMaxSerial, backend.io)

    with pytest.raises(MolecularDevicesError):
      async with backend:
        pytest.fail("backend entered after probe failure")

    assert serial.written == ["!"]
    assert events[-1] == "serial.exit"
    assert not backend.is_active

  @pytest.mark.parametrize("backend_type", LEGACY_SPECTRAMAX_TYPES)
  async def test_cancelled_probe_reconciles_before_serial_close(self, backend_type: Any) -> None:
    events: List[str] = []
    read_started = anyio.Event()
    release_response = anyio.Event()
    scopes: List[anyio.CancelScope] = []

    async def block_probe_response() -> None:
      read_started.set()
      await release_response.wait()
      events.append("reply:!")

    backend = self._make(
      backend_type,
      [[b"OK>\r\n"]],
      events=events,
      before_read={"!": block_probe_response},
    )

    async def enter_backend() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        await backend.__aenter__()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(enter_backend)
      await read_started.wait()
      scopes[0].cancel()
      await anyio.sleep(0)
      assert "serial.exit" not in events
      release_response.set()

    assert events.index("reply:!") < events.index("serial.exit")
    assert not backend.is_active

  @pytest.mark.parametrize("backend_type", LEGACY_SPECTRAMAX_TYPES)
  async def test_normal_exit_closes_serial(self, backend_type: Any) -> None:
    events: List[str] = []

    async def record_open_reply() -> None:
      events.append("reply:!OPEN")

    backend = self._make(
      backend_type,
      [[b"OK>\r\n"], [b"OK>\r\n"], [b"OK>\r\n"]],
      events=events,
      before_read={"!OPEN": record_open_reply},
    )
    serial = cast(FakeLegacySpectraMaxSerial, backend.io)

    async with backend:
      assert backend.is_active
      await backend.open()
      await backend.close()

    assert serial.written == ["!", "!OPEN", "!CLOSE"]
    assert events.index("reply:!OPEN") < events.index("serial.exit")
    assert not backend.is_active

  @pytest.mark.parametrize("backend_type", LEGACY_SPECTRAMAX_TYPES)
  async def test_lifespan_options_are_rejected_before_serial_entry(self, backend_type: Any) -> None:
    events: List[str] = []
    backend = self._make(backend_type, [], events=events)

    with pytest.raises(TypeError, match="unexpected keyword argument"):
      async with backend.lifespan(unsupported=True):
        pytest.fail("backend accepted lifespan options")

    assert events == []

  @pytest.mark.parametrize("backend_type", LEGACY_SPECTRAMAX_TYPES)
  async def test_parent_cleanup_stops_shaking_before_serial_close(self, backend_type: Any) -> None:
    events: List[str] = []
    backend = self._make(
      backend_type,
      [[b"OK>\r\n"], [b"OK>\r\n"], [b"OK>\r\n"]],
      events=events,
    )
    serial = cast(FakeLegacySpectraMaxSerial, backend.io)

    async with backend:
      await backend.start_shake()

    assert serial.written == ["!", "!SHAKE NOW", "!SHAKE STOP"]
    assert events[-2:] == ["write:!SHAKE STOP", "serial.exit"]

  @pytest.mark.parametrize("backend_type", LEGACY_SPECTRAMAX_TYPES)
  async def test_stop_failure_still_closes_serial(self, backend_type: Any) -> None:
    events: List[str] = []
    backend = self._make(
      backend_type,
      [[b"OK>\r\n"], [b"OK>\r\n"], [b"FAIL\t105>\r\n"]],
      events=events,
    )

    with pytest.raises(MolecularDevicesError):
      async with backend:
        await backend.start_shake()

    assert events[-1] == "serial.exit"
    assert backend._shaking_active
    assert backend._shaking_state_unknown
    assert backend._shaking_scope_token is None

  @pytest.mark.parametrize("backend_type", LEGACY_SPECTRAMAX_TYPES)
  async def test_concurrent_commands_do_not_cross_responses(self, backend_type: Any) -> None:
    first_read_started = anyio.Event()
    release_first = anyio.Event()
    responses: Dict[str, List[str]] = {}

    async def block_first_read() -> None:
      first_read_started.set()
      await release_first.wait()

    backend = self._make(
      backend_type,
      [[b"OK OPEN>\r\n"], [b"OK CLOSE>\r\n"]],
      before_read={"!OPEN": block_first_read},
    )
    serial = cast(FakeLegacySpectraMaxSerial, backend.io)

    async def send(command: str) -> None:
      responses[command] = await backend.send_command(command)

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(send, "!OPEN")
      await first_read_started.wait()
      task_group.start_soon(send, "!CLOSE")
      await anyio.sleep(0)
      assert serial.written == ["!OPEN"]
      release_first.set()

    assert responses == {"!OPEN": ["OK OPEN"], "!CLOSE": ["OK CLOSE"]}
    assert serial.written == ["!OPEN", "!CLOSE"]

  @pytest.mark.parametrize("backend_type", LEGACY_SPECTRAMAX_TYPES)
  async def test_cancel_before_write_writes_nothing(self, backend_type: Any) -> None:
    backend = self._make(backend_type, [])
    serial = cast(FakeLegacySpectraMaxSerial, backend.io)
    started = anyio.Event()
    scopes: List[anyio.CancelScope] = []

    async def send() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        started.set()
        await backend.send_command("!OPEN")

    await backend._transaction_lock.acquire()
    async with anyio.create_task_group() as task_group:
      task_group.start_soon(send)
      await started.wait()
      await anyio.wait_all_tasks_blocked()
      scopes[0].cancel()
      backend._transaction_lock.release()

    assert serial.written == []

  @pytest.mark.parametrize("backend_type", LEGACY_SPECTRAMAX_TYPES)
  async def test_cancel_after_write_consumes_complete_response(self, backend_type: Any) -> None:
    write_finished = anyio.Event()
    release_response = anyio.Event()
    finished = anyio.Event()
    scopes: List[anyio.CancelScope] = []
    returned: List[bool] = []
    cancelled: List[bool] = []

    async def signal_write() -> None:
      write_finished.set()

    async def block_response() -> None:
      await release_response.wait()
      serial.events.append("reply:!OPEN")

    backend = self._make(
      backend_type,
      [[b"OK>\r\n"]],
      after_write={"!OPEN": signal_write},
      before_read={"!OPEN": block_response},
    )
    serial = cast(FakeLegacySpectraMaxSerial, backend.io)

    async def send() -> None:
      try:
        with anyio.CancelScope() as scope:
          scopes.append(scope)
          try:
            await backend.send_command("!OPEN")
            returned.append(True)
          except anyio.get_cancelled_exc_class():
            cancelled.append(True)
            raise
      finally:
        finished.set()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(send)
      await write_finished.wait()
      scopes[0].cancel()
      await anyio.sleep(0)
      assert not finished.is_set()
      release_response.set()

    assert "reply:!OPEN" in serial.events
    assert serial.written == ["!OPEN"]
    assert returned == []
    assert cancelled == [True]

  @pytest.mark.parametrize("backend_type", LEGACY_SPECTRAMAX_TYPES)
  async def test_sequential_reuse_runs_probe_each_time(self, backend_type: Any) -> None:
    events: List[str] = []
    backend = self._make(
      backend_type,
      [[b"OK>\r\n"], [b"OK>\r\n"]],
      events=events,
    )
    serial = cast(FakeLegacySpectraMaxSerial, backend.io)

    async with backend:
      pass
    async with backend:
      pass

    assert serial.written == ["!", "!"]
    assert events.count("serial.enter") == 2
    assert events.count("serial.exit") == 2

  async def test_wait_for_idle_times_out_during_stalled_status(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend = self._make(LEGACY_SPECTRAMAX_TYPES[0], [])

    async def stalled_status() -> List[str]:
      await anyio.sleep_forever()
      raise AssertionError("unreachable")

    monkeypatch.setattr(backend, "get_status", stalled_status)
    with anyio.fail_after(1):
      with pytest.raises(TimeoutError, match="Timeout waiting for plate reader to become idle"):
        await backend._wait_for_idle(timeout=0.01)

  async def test_wait_for_idle_zero_timeout_checks_first_status(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend = self._make(LEGACY_SPECTRAMAX_TYPES[0], [])

    async def idle_status() -> List[str]:
      return ["OK", "IDLE"]

    monkeypatch.setattr(backend, "get_status", idle_status)
    await backend._wait_for_idle(timeout=0)
