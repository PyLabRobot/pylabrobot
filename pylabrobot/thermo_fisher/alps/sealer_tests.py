from collections import defaultdict, deque
from typing import Any, Awaitable, Callable, Deque, Dict, List, Optional, Type, Union, cast

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.io.serial import Serial
from pylabrobot.testing.concurrency import AnyioTestBase
from pylabrobot.thermo_fisher.alps import alps5000 as alps5000_module
from pylabrobot.thermo_fisher.alps import sealer as sealer_module
from pylabrobot.thermo_fisher.alps.alps300 import ALPS300Status, ThermoScientificALPS300
from pylabrobot.thermo_fisher.alps.alps3000 import ALPS3000Status, ThermoScientificALPS3000
from pylabrobot.thermo_fisher.alps.alps5000 import ALPS5000Status, ThermoScientificALPS5000
from pylabrobot.thermo_fisher.alps.sealer import (
  ThermoScientificALPSError,
  ThermoScientificALPSSealer,
)

ResponseEffect = Union[str, BaseException, Callable[[], Awaitable[str]]]


@pytest.fixture(autouse=True)
def _skip_protocol_settles(monkeypatch: pytest.MonkeyPatch) -> None:
  """Keep software protocol tests fast without changing production timings."""
  monkeypatch.setattr(sealer_module, "_COMMAND_DELAY_SECONDS", 0)
  monkeypatch.setattr(alps5000_module, "_INIT_SECONDS", 0)


class FakeALPSSerial(HasLifespan):
  """Replay complete ALPS replies and record lifecycle and protocol events."""

  def __init__(
    self,
    responses: Optional[Dict[str, List[ResponseEffect]]] = None,
    events: Optional[List[str]] = None,
    entry_error: Optional[BaseException] = None,
    exit_error: Optional[BaseException] = None,
    block_entry: Optional[anyio.Event] = None,
    before_write: Optional[Dict[str, Callable[[], Awaitable[None]]]] = None,
    after_write: Optional[Dict[str, Callable[[], Awaitable[None]]]] = None,
  ) -> None:
    super().__init__()
    self.responses: Dict[str, Deque[ResponseEffect]] = defaultdict(deque)
    for command, effects in (responses or {}).items():
      self.responses[command].extend(effects)
    self.events = events if events is not None else []
    self.entry_error = entry_error
    self.exit_error = exit_error
    self.block_entry = block_entry
    self.before_write = before_write or {}
    self.after_write = after_write or {}
    self.written: List[str] = []
    self.port = "FAKE"
    self._rx = bytearray()

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    if kwargs:
      raise TypeError("FakeALPSSerial does not accept lifespan options")
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
    before = self.before_write.get(command)
    if before is not None:
      await before()
    self.written.append(command)
    self.events.append(f"write:{command}")
    effects = self.responses[command]
    if effects:
      effect = effects.popleft()
      if isinstance(effect, BaseException):
        raise effect
      reply = await effect() if callable(effect) else effect
      self._rx.extend((command + reply + "\r").encode("ascii"))
    after = self.after_write.get(command)
    if after is not None:
      await after()

  async def read(self, num_bytes: int = 1) -> bytes:
    await anyio.lowlevel.checkpoint()
    chunk = bytes(self._rx[:num_bytes])
    del self._rx[:num_bytes]
    if chunk == b"\r":
      self.events.append("response.read")
    return chunk


DeviceType = Type[ThermoScientificALPSSealer]


def make_device(
  device_type: DeviceType,
  responses: Optional[Dict[str, List[ResponseEffect]]] = None,
  events: Optional[List[str]] = None,
  entry_error: Optional[BaseException] = None,
  exit_error: Optional[BaseException] = None,
  block_entry: Optional[anyio.Event] = None,
  before_write: Optional[Dict[str, Callable[[], Awaitable[None]]]] = None,
  after_write: Optional[Dict[str, Callable[[], Awaitable[None]]]] = None,
  timeout: float = 0.02,
  settle_time: float = 0,
) -> tuple[ThermoScientificALPSSealer, FakeALPSSerial]:
  """Build one ALPS model backed by a hardware-free serial transport."""
  device = device_type(port="FAKE", timeout=timeout, settle_time=settle_time)
  fake_io = FakeALPSSerial(
    responses, events, entry_error, exit_error, block_entry, before_write, after_write
  )
  device.io = cast(Serial, fake_io)
  return device, fake_io


def _entry_responses(device_type: DeviceType, repeats: int = 1) -> Dict[str, List[ResponseEffect]]:
  if device_type is ThermoScientificALPS5000:
    return {
      "?": [response for _ in range(repeats) for response in ("00", "00")],
      "I": ["ok"] * repeats,
      "A100": ["ok"] * repeats,
    }
  return {}


class ALPSBehavior(AnyioTestBase):
  """Shared protocol and lifecycle contract exercised by every ALPS model."""

  device_type: DeviceType
  status_type: Type[Any]
  status_members: Dict[str, int]
  error_code: int
  error_message: str

  def test_status_flags_match_model_protocol(self) -> None:
    assert {name: int(member) for name, member in self.status_type.__members__.items()} == (
      self.status_members
    )

  def test_uses_managed_lifecycle_methods(self) -> None:
    assert self.device_type.__aenter__ is HasLifespan.__aenter__
    assert self.device_type.__aexit__ is HasLifespan.__aexit__
    assert self.device_type.setup is HasLifespan.setup
    assert self.device_type.stop is HasLifespan.stop

  async def test_initialization_transcript(self) -> None:
    events: List[str] = []
    device, fake_io = make_device(self.device_type, events=events)

    async with device:
      events.append("body")

    assert fake_io.written == []
    assert events == ["serial.enter", "serial.reset_input", "body", "serial.exit"]

  async def test_status_temperature_and_duration_conversion(self) -> None:
    device, fake_io = make_device(
      self.device_type,
      {"?": ["08"], "C": ["175"], "D": ["27"], "F": ["143"], "A005": ["ok"], "B99": ["ok"]},
    )

    assert int(await device.request_status()) == 0x08
    assert await device.request_sealing_temperature() == 175
    assert await device.request_sealing_time() == 2.7
    assert await device.request_temperature() == 143
    await device.set_temperature(5)
    await device.set_sealing_time(9.9)

    assert fake_io.written == ["?", "C", "D", "F", "A005", "B99"]

  async def test_default_seal_command_sequence(self) -> None:
    responses: Dict[str, List[ResponseEffect]] = {
      "?": ["00", "00", "00"],
      "B25": ["ok"],
      "A160": ["ok"],
      "S": ["ok"],
    }
    if self.device_type is ThermoScientificALPS5000:
      responses["FS=0"] = ["ok"]
    device, fake_io = make_device(self.device_type, responses)

    await device.seal(160, 2.5)

    expected = ["?", "B25", "A160"]
    if self.device_type is ThermoScientificALPS5000:
      expected.append("FS=0")
    assert fake_io.written == [*expected, "?", "S", "?"]

  async def test_busy_waiting_polls_until_clear(self) -> None:
    device, fake_io = make_device(self.device_type, {"?": ["04", "04", "00"]})

    assert int(await device.wait_for_idle()) == 0

    assert fake_io.written == ["?", "?", "?"]

  async def test_error_status_reports_model_error(self) -> None:
    device, fake_io = make_device(self.device_type, {"?": ["02"], "E": [str(self.error_code)]})

    with pytest.raises(ThermoScientificALPSError) as error_info:
      await device.wait_for_idle()

    assert error_info.value.error_code == self.error_code
    assert error_info.value.message == self.error_message
    assert fake_io.written == ["?", "E"]

  async def test_read_timeout_uses_configured_deadline(self) -> None:
    device, fake_io = make_device(self.device_type, timeout=0.001)

    with pytest.raises(TimeoutError, match="waiting for response"):
      await device.request_status()

    assert fake_io.written == ["?"]

  async def test_busy_timeout_uses_configured_deadline(self) -> None:
    device, _ = make_device(self.device_type, {"?": ["04", "04", "04"]})

    with pytest.raises(ThermoScientificALPSError, match="seal to complete"):
      await device._wait_for_busy_cleared(timeout=0.001)

  async def test_heating_timeout_uses_configured_deadline(self) -> None:
    device, _ = make_device(self.device_type, {"?": ["08", "08"]})

    with pytest.raises(TimeoutError, match="sealing temperature"):
      await device.wait_for_sealing_temperature(timeout=0.001)

  async def test_normal_exit_closes_serial(self) -> None:
    events: List[str] = []
    device, _ = make_device(self.device_type, _entry_responses(self.device_type), events)

    async with device:
      events.append("body")

    assert events[0] == "serial.enter"
    assert events[-2:] == ["body", "serial.exit"]
    assert not device.is_active

  async def test_serial_entry_failure_rolls_back(self) -> None:
    events: List[str] = []
    failure = RuntimeError("serial entry failed")
    device, _ = make_device(self.device_type, events=events, entry_error=failure)

    with pytest.raises(RuntimeError) as error_info:
      async with device:
        pytest.fail("failed entry reached body")

    assert error_info.value is failure
    assert events == ["serial.enter", "serial.exit"]
    assert not device.is_active

  async def test_cancelled_serial_entry_rolls_back(self) -> None:
    events: List[str] = []
    entry_started = anyio.Event()
    device, _ = make_device(self.device_type, events=events, block_entry=entry_started)

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(_enter_and_wait, device)
      await entry_started.wait()
      task_group.cancel_scope.cancel()

    assert events == ["serial.enter", "serial.exit"]
    assert not device.is_active

  async def test_body_cancellation_closes_before_propagating(self) -> None:
    events: List[str] = []
    body_started = anyio.Event()
    device, _ = make_device(self.device_type, _entry_responses(self.device_type), events)

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

    assert events[-2:] == ["serial.exit", "cancelled"]
    assert not device.is_active

  async def test_sequential_reuse_enters_and_exits_twice(self) -> None:
    events: List[str] = []
    device, _ = make_device(self.device_type, _entry_responses(self.device_type, 2), events)

    async with device:
      pass
    async with device:
      pass

    assert events.count("serial.enter") == 2
    assert events.count("serial.exit") == 2
    assert events.index("serial.exit") < len(events) - 1 - events[::-1].index("serial.enter")

  async def test_serial_close_failure_propagates(self) -> None:
    events: List[str] = []
    failure = RuntimeError("serial close failed")
    device, _ = make_device(
      self.device_type, _entry_responses(self.device_type), events, exit_error=failure
    )

    with pytest.raises(RuntimeError) as error_info:
      async with device:
        pass

    assert error_info.value is failure
    assert events[-1] == "serial.exit"
    assert not device.is_active

  async def test_rejects_unknown_lifespan_options_before_serial_entry(self) -> None:
    events: List[str] = []
    device, _ = make_device(self.device_type, events=events)

    with pytest.raises(TypeError, match="unexpected keyword argument"):
      async with device.lifespan(unknown=True):
        pytest.fail("invalid entry reached body")

    assert events == []


class TestALPS300(ALPSBehavior):
  device_type = ThermoScientificALPS300
  status_type = ALPS300Status
  status_members = {
    "Ready": 0x00,
    "Busy": 0x01,
    "Error": 0x02,
    "Sealing": 0x04,
    "NotAtSealTemperature": 0x08,
    "NotAssigned": 0x10,
    "FoilLow": 0x20,
    "DoorOpen": 0x40,
    "ProceedOverridden": 0x80,
  }
  error_code = 4
  error_message = "No foil or low foil error. Check foil and foil sensor."


class TestALPS3000(ALPSBehavior):
  device_type = ThermoScientificALPS3000
  status_type = ALPS3000Status
  status_members = {
    "Ready": 0x00,
    "NoFoil": 0x01,
    "Error": 0x02,
    "Busy": 0x04,
    "WaitingForSealTemperature": 0x08,
    "PlateNotPresent": 0x10,
    "LowAir": 0x20,
    "FoilLoadModeAfterPark": 0x40,
    "ParkMode": 0x80,
  }
  error_code = 13
  error_message = "Mains Air error"


class TestALPS5000(ALPSBehavior):
  device_type = ThermoScientificALPS5000
  status_type = ALPS5000Status
  status_members = {
    "Ready": 0x00,
    "NoFoil": 0x01,
    "Error": 0x02,
    "Busy": 0x04,
    "WaitingForSealTemperature": 0x08,
    "PlateNotPresent": 0x10,
    "NotInitialised": 0x20,
    "ForceSensorActivated": 0x40,
    "PlatePresent": 0x80,
  }
  error_code = 9
  error_message = "Force sensor error"

  async def test_initialization_transcript(self) -> None:
    device, fake_io = make_device(self.device_type, _entry_responses(self.device_type))

    async with device:
      pass

    assert fake_io.written == ["?", "I", "?", "A100"]

  @pytest.mark.parametrize(
    ("responses", "message"),
    [
      ({"?": ["invalid"]}, "Unexpected status reply"),
      ({"?": ["00"], "I": ["err"]}, "Initialisation failed"),
      ({"?": ["00", "invalid"], "I": ["ok"]}, "Unexpected status reply"),
      ({"?": ["00", "00"], "I": ["ok"], "A100": ["err"]}, "temperature failed"),
    ],
  )
  async def test_each_initialization_command_failure_closes_serial(
    self, responses: Dict[str, List[ResponseEffect]], message: str
  ) -> None:
    events: List[str] = []
    device, _ = make_device(self.device_type, responses, events)

    with pytest.raises(ThermoScientificALPSError, match=message):
      async with device:
        pytest.fail("failed initialization reached body")

    assert events[-1] == "serial.exit"
    assert not device.is_active

  @pytest.mark.parametrize(
    ("blocked_command", "occurrence"), [("?", 1), ("I", 1), ("?", 2), ("A100", 1)]
  )
  async def test_cancellation_during_each_initialization_command_reconciles_response(
    self, blocked_command: str, occurrence: int
  ) -> None:
    events: List[str] = []
    write_started = anyio.Event()
    release_write = anyio.Event()
    scopes: List[anyio.CancelScope] = []
    seen = 0

    async def block_once() -> None:
      nonlocal seen
      seen += 1
      if seen != occurrence:
        return
      write_started.set()
      await release_write.wait()
      events.append("response.committed")

    device, fake_io = make_device(
      self.device_type,
      _entry_responses(self.device_type),
      events,
      after_write={blocked_command: block_once},
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
    assert events.index("response.committed") < events.index("serial.exit")
    assert events.index("response.read", events.index("response.committed")) < events.index(
      "serial.exit"
    )
    assert events.index("serial.exit") < events.index("cancelled")

  async def test_cancellation_during_initialization_settle_closes_serial(self) -> None:
    events: List[str] = []
    settle_started = anyio.Event()

    async def mark_initialised() -> None:
      settle_started.set()

    device, fake_io = make_device(
      self.device_type,
      _entry_responses(self.device_type),
      events,
      after_write={"I": mark_initialised},
    )
    alps5000_module._INIT_SECONDS = 1e9

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(_enter_and_wait, device)
      await settle_started.wait()
      task_group.cancel_scope.cancel()

    assert fake_io.written == ["?", "I"]
    assert events[-1] == "serial.exit"
    assert not device.is_active

  async def test_extra_commands_preserve_model_protocol(self) -> None:
    responses: Dict[str, List[ResponseEffect]] = {
      "PS=05": ["ok"],
      "FS=1": ["ok"],
      "L=128": ["ok"],
      "DO=10": ["ok"],
      "SI": ["ok"],
      "SO": ["ok"],
    }
    device, fake_io = make_device(self.device_type, responses)
    alps5000 = cast(ThermoScientificALPS5000, device)

    await alps5000.set_force(5)
    await alps5000.enable_force_sensor(True)
    await alps5000.set_foil_length(128)
    await alps5000.set_sealing_distance(10)
    await alps5000.shuttle_in()
    await alps5000.shuttle_out()

    assert fake_io.written == ["PS=05", "FS=1", "L=128", "DO=10", "SI", "SO"]


class TestALPSMutationCancellation(AnyioTestBase):
  async def test_pending_cancellation_issues_no_mutation(self) -> None:
    device, fake_io = make_device(ThermoScientificALPS300, {"A160": ["ok"]})
    returned = False

    with anyio.CancelScope() as scope:
      scope.cancel()
      await device.set_temperature(160)
      returned = True

    assert not returned
    assert fake_io.written == []

  async def test_cancellation_after_mutation_write_reconciles_response(self) -> None:
    events: List[str] = []
    write_started = anyio.Event()
    release_write = anyio.Event()
    scopes: List[anyio.CancelScope] = []

    async def complete_write() -> None:
      write_started.set()
      await release_write.wait()
      events.append("response.committed")

    device, fake_io = make_device(
      ThermoScientificALPS300,
      {"A160": ["ok"]},
      events,
      after_write={"A160": complete_write},
    )

    async def mutate() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          await device.set_temperature(160)
        except anyio.get_cancelled_exc_class():
          events.append("cancelled")
          raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(mutate)
      await write_started.wait()
      scopes[0].cancel()
      await anyio.sleep(0)
      assert "cancelled" not in events
      release_write.set()

    assert fake_io.written == ["A160"]
    assert events[-4:] == ["write:A160", "response.committed", "response.read", "cancelled"]


async def _enter_and_wait(device: ThermoScientificALPSSealer) -> None:
  async with device:
    pytest.fail("cancelled entry reached body")
