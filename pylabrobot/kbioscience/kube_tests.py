from collections import defaultdict, deque
from typing import Any, Awaitable, Callable, Deque, Dict, List, Optional, Union, cast

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.io.serial import Serial
from pylabrobot.kbioscience import kube as kube_module
from pylabrobot.kbioscience.kube import KBioscienceError, KBioscienceKUBE, KUBEStatus, SealPreset
from pylabrobot.testing.concurrency import AnyioTestBase

ResponseEffect = Union[str, BaseException, Callable[[], Awaitable[str]]]
WriteHook = Callable[[], Awaitable[None]]


@pytest.fixture(autouse=True)
def _skip_protocol_delays(monkeypatch: pytest.MonkeyPatch) -> None:
  """Keep software protocol tests deterministic and fast."""
  monkeypatch.setattr(kube_module, "_COMMAND_DELAY_SECONDS", 0, raising=False)
  monkeypatch.setattr(kube_module, "_SETPOINT_SETTLE_SECONDS", 0, raising=False)


class FakeKUBESerial(HasLifespan):
  """Replay KUBE echo replies and record lifecycle and protocol events."""

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
      raise TypeError("FakeKUBESerial does not accept lifespan options")
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


def make_device(
  responses: Optional[Dict[str, List[ResponseEffect]]] = None,
  events: Optional[List[str]] = None,
  entry_error: Optional[BaseException] = None,
  exit_error: Optional[BaseException] = None,
  block_entry: Optional[anyio.Event] = None,
  after_write: Optional[Dict[str, WriteHook]] = None,
  timeout: float = 0.02,
  portrait: bool = False,
) -> tuple[KBioscienceKUBE, FakeKUBESerial]:
  """Build a KUBE backed by a hardware-free Serial resource."""
  device = KBioscienceKUBE(port="FAKE", timeout=timeout, settle_time=0, portrait=portrait)
  fake_io = FakeKUBESerial(responses, events, entry_error, exit_error, block_entry, after_write)
  device.io = cast(Serial, fake_io)
  return device, fake_io


def entry_responses(repeats: int = 1, portrait: bool = False) -> Dict[str, List[ResponseEffect]]:
  """Return literal startup and cleanup replies for KUBE."""
  return {
    "": [""] * repeats,
    "?": ["28"] * repeats,
    "V": ["1.2.3"] * repeats,
    "@": ["KUBE"] * repeats,
    "X1" if portrait else "X2": ["ok"] * repeats,
    "A100": ["ok"] * repeats,
    "A020": ["ok"] * repeats,
  }


def initialization_commands(portrait: bool = False) -> List[str]:
  """Return the exact KUBE initialization command transcript."""
  return ["", "?", "V", "@", "X1" if portrait else "X2", "A100"]


class TestKBioscienceKUBE(AnyioTestBase):
  def test_managed_methods_are_inherited(self) -> None:
    assert KBioscienceKUBE.__aenter__ is HasLifespan.__aenter__
    assert KBioscienceKUBE.__aexit__ is HasLifespan.__aexit__
    assert KBioscienceKUBE.setup is HasLifespan.setup
    assert KBioscienceKUBE.stop is HasLifespan.stop

  async def test_startup_probe_queries_orientation_preheat_and_offline_transcript(self) -> None:
    events: List[str] = []
    device, fake_io = make_device(entry_responses(), events)

    async with device:
      events.append("body")
      assert device.firmware_version == "1.2.3"
      assert device.product_name == "KUBE"

    assert fake_io.written == [*initialization_commands(), "A020"]
    assert events[0] == "serial.enter"
    assert events.index("write:A020") < events.index("serial.exit")
    assert not device.is_active

  async def test_portrait_startup_uses_portrait_orientation_command(self) -> None:
    device, fake_io = make_device(entry_responses(portrait=True), portrait=True)

    async with device:
      pass

    assert fake_io.written == [*initialization_commands(portrait=True), "A020"]

  async def test_alps_probe_rejection_sets_offline_and_closes_serial(self) -> None:
    events: List[str] = []
    device, fake_io = make_device({"": ["ALPS300"], "A020": ["ok"]}, events)

    with pytest.raises(KBioscienceError, match="Incompatible communication protocol"):
      async with device:
        pytest.fail("ALPS protocol reached body")

    assert fake_io.written == ["", "A020"]
    assert events.index("response.read") < events.index("write:A020")
    assert events.index("write:A020") < events.index("serial.exit")

  async def test_idle_polling_waits_for_sealing_to_clear(self) -> None:
    device, fake_io = make_device({"?": ["04", "04", "00"]})

    assert await device.wait_for_idle() == KUBEStatus.Ready

    assert fake_io.written == ["?", "?", "?"]

  async def test_firmware_product_and_parameter_queries(self) -> None:
    device, fake_io = make_device(
      {"V": ["2.4"], "@": ["KUBE 2"], "C": ["160"], "D": ["25"], "F": ["099"]}
    )

    assert await device.request_firmware_version() == "2.4"
    assert await device.request_product_name() == "KUBE 2"
    assert await device.request_sealing_temperature() == 160
    assert await device.request_sealing_time() == 2.5
    assert await device.request_temperature() == 99
    assert fake_io.written == ["V", "@", "C", "D", "F"]

  async def test_orientation_preset_and_clear_error_commands(self) -> None:
    device, fake_io = make_device({"X1": ["ok"], "P3": ["ok"], "H": ["ignored"]})

    await device.set_plate_orientation(True)
    await device.set_seal_preset(SealPreset.THREE)
    await device.clear_error_window()

    assert fake_io.written == ["X1", "P3", "H"]

  async def test_user_defined_seal_transcript(self) -> None:
    device, fake_io = make_device(
      {
        "?": ["00", "00", "00"],
        "B25": ["ok"],
        "A160": ["ok"],
        "S": ["ok"],
        "A100": ["ok"],
      }
    )

    await device.seal()

    assert fake_io.written == ["?", "B25", "A160", "?", "S", "?", "A100"]

  async def test_stored_preset_seal_transcript(self) -> None:
    device, fake_io = make_device(
      {"?": ["00", "00", "00"], "P2": ["ok"], "S": ["ok"], "A100": ["ok"]}
    )

    await device.seal(preset=SealPreset.TWO)

    assert fake_io.written == ["?", "P2", "?", "S", "?", "A100"]

  async def test_serial_entry_failure_rolls_back(self) -> None:
    events: List[str] = []
    failure = RuntimeError("serial entry failed")
    device, fake_io = make_device(events=events, entry_error=failure)

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
    device, fake_io = make_device(events=events, block_entry=entry_started)

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(_enter_and_wait, device)
      await entry_started.wait()
      task_group.cancel_scope.cancel()

    assert fake_io.written == []
    assert events == ["serial.enter", "serial.exit"]
    assert not device.is_active

  async def test_each_initialization_failure_sets_offline_and_closes_serial(self) -> None:
    for failed_command in initialization_commands():
      events: List[str] = []
      failure = RuntimeError(f"{failed_command!r} failed")
      responses = entry_responses()
      responses[failed_command] = [failure]
      device, fake_io = make_device(responses, events)

      with pytest.raises(RuntimeError) as error_info:
        async with device:
          pytest.fail("failed initialization reached body")

      assert error_info.value is failure
      assert fake_io.written[-1] == "A020"
      assert events[-1] == "serial.exit"
      assert not device.is_active

  async def test_cancellation_during_each_initialization_command_reconciles_reply(self) -> None:
    for blocked_command in initialization_commands():
      events: List[str] = []
      write_started = anyio.Event()
      release_write = anyio.Event()
      scopes: List[anyio.CancelScope] = []

      async def hold_write() -> None:
        write_started.set()
        await release_write.wait()

      device, fake_io = make_device(
        entry_responses(), events, after_write={blocked_command: hold_write}
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

      committed = events.index(f"response.committed:{blocked_command}")
      cleanup_write = events.index("write:A020")
      assert events.index("response.read", committed) < cleanup_write
      assert cleanup_write < events.index("serial.exit") < events.index("cancelled")
      assert not device.is_active

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
      entry_responses(), events, after_write={"A020": hold_cleanup_write}
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

    cleanup_write = events.index("write:A020")
    assert events.index("response.read", cleanup_write) < events.index("serial.exit")
    assert events.index("serial.exit") < events.index("cancelled")
    assert not device.is_active

  async def test_sequential_reuse_initializes_and_cleans_up_twice(self) -> None:
    events: List[str] = []
    device, fake_io = make_device(entry_responses(repeats=2), events)

    async with device:
      pass
    async with device:
      pass

    expected_cycle = [*initialization_commands(), "A020"]
    assert fake_io.written == expected_cycle * 2
    assert events.count("serial.enter") == 2
    assert events.count("serial.exit") == 2

  async def test_serial_close_failure_propagates_after_offline_cleanup(self) -> None:
    events: List[str] = []
    failure = RuntimeError("serial close failed")
    device, fake_io = make_device(entry_responses(), events, exit_error=failure)

    with pytest.raises(RuntimeError) as error_info:
      async with device:
        pass

    assert error_info.value is failure
    assert fake_io.written[-1] == "A020"
    assert events[-1] == "serial.exit"
    assert not device.is_active

  async def test_rejects_lifespan_options_before_serial_entry(self) -> None:
    events: List[str] = []
    device, _ = make_device(events=events)

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

    device, fake_io = make_device({"A160": ["ok"]}, events, after_write={"A160": hold_write})

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
    device, fake_io = make_device({"A160": ["ok"]})
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
      {"C": ["160"], "D": ["25"]}, events, after_write={"C": hold_first_write}
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

  async def test_seal_write_consumes_reply_before_cancellation(self) -> None:
    events: List[str] = []
    write_started = anyio.Event()
    release_write = anyio.Event()
    scopes: List[anyio.CancelScope] = []

    async def hold_write() -> None:
      write_started.set()
      await release_write.wait()

    device, fake_io = make_device({"S": ["ok"]}, events, after_write={"S": hold_write})

    async def send_seal() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          await device.send_command("S")
        except anyio.get_cancelled_exc_class():
          events.append("cancelled")
          raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(send_seal)
      await write_started.wait()
      scopes[0].cancel()
      await anyio.sleep(0)
      assert "cancelled" not in events
      release_write.set()

    assert fake_io.written == ["S"]
    assert events[-4:] == ["write:S", "response.committed:S", "response.read", "cancelled"]

  async def test_read_line_translates_its_own_deadline(self) -> None:
    device, _ = make_device(timeout=0)

    with pytest.raises(TimeoutError, match="Timeout while waiting for response from sealer"):
      await device._read_line()

  async def test_seal_poll_propagates_transport_timeout_unchanged(self) -> None:
    failure = TimeoutError("serial read failed")
    device, _ = make_device({"?": [failure]})

    with pytest.raises(TimeoutError) as error_info:
      await device._wait_for_seal_cleared(timeout=1)

    assert error_info.value is failure

  async def test_seal_poll_translates_its_own_deadline(self) -> None:
    device, _ = make_device({"?": ["04"]})

    with pytest.raises(KBioscienceError, match="Timeout while waiting for the seal to complete"):
      await device._wait_for_seal_cleared(timeout=0)

  async def test_temperature_poll_propagates_transport_timeout_unchanged(self) -> None:
    failure = TimeoutError("serial read failed")
    device, _ = make_device({"?": [failure]})

    with pytest.raises(TimeoutError) as error_info:
      await device.wait_for_sealing_temperature(timeout=1)

    assert error_info.value is failure

  async def test_temperature_poll_translates_its_own_deadline(self) -> None:
    device, _ = make_device({"?": ["08"]})

    with pytest.raises(TimeoutError, match="Timeout while waiting for sealing temperature"):
      await device.wait_for_sealing_temperature(timeout=0)


async def _enter_and_wait(device: KBioscienceKUBE) -> None:
  async with device:
    pytest.fail("cancelled entry reached body")
