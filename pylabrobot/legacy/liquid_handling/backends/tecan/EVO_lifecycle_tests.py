from typing import Any, Dict, List, Optional, Tuple, cast

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.legacy.liquid_handling import LiquidHandler
from pylabrobot.legacy.liquid_handling.backends.tecan.EVO_backend import EVOBackend
from pylabrobot.legacy.liquid_handling.backends.tecan.errors import TecanError
from pylabrobot.legacy.tip_tracker import TipTracker
from pylabrobot.resources import Deck, EVO150Deck
from pylabrobot.testing import AnyioTestBase
from pylabrobot.testing.mock_io import MockIO


def evo_response(module: str, *data: int) -> bytes:
  payload = b"" if not data else b"," + b",".join(str(value).encode() for value in data)
  return b"\x02" + module.encode() + bytes([0x80]) + payload + b"\x00"


EXPECTED_SETUP_WRITES = [
  b"\x02C5PIA\x00",
  b"\x02C5BMX2\x00",
  b"\x02W1PIB\x00",
  b"\x02W1PIA\x00",
  b"\x02C1PIA\x00",
  b"\x02C1BMX2\x00",
  b"\x02C1PIX\x00",
  b"\x02C1RPX0\x00",
  b"\x02C1SAA1,9000,2000,2464,1800,,1,0,0\x00",
  b"\x02C1AAC\x00",
  b"\x02C1RPX0\x00",
  b"\x02W1PIA\x00",
  b"\x02W1PAA,,2000\x00",
  b"\x02W1PAA6000,1000,\x00",
  b"\x02W1BMA0,0,0\x00",
  b"\x02C5PIX\x00",
  b"\x02C5RNT1\x00",
  b"\x02C5RPX5\x00",
  b"\x02C5RPY5\x00",
  b"\x02C5RPZ5\x00",
  b"\x02C5SHZ2000,2000,2000,2000,2000,2000,2000,2000\x00",
  b"\x02C5RPX0\x00",
  b"\x02C5PAA45,1031,90,1200,1200,1200,1200,1200,1200,1200,1200\x00",
  b"\x02C5PID255\x00",
  b"\x02C5PVL1,1,1,1,1,1,1,1\x00",
  b"\x02C5PPR100,100,100,100,100,100,100,100\x00",
  b"\x02C5PVL0,0,0,0,0,0,0,0\x00",
  b"\x02C5SEP1800,1800,1800,1800,1800,1800,1800,1800\x00",
  b"\x02C5PPR-100,-100,-100,-100,-100,-100,-100,-100\x00",
  b"\x02C5RPX0\x00",
  b"\x02C5PAA45,1031,90,2000,2000,2000,2000,2000,2000,2000,2000\x00",
]


def setup_response_for(write: bytes) -> bytes:
  module = write[1:3].decode()
  if write == b"\x02C5RNT1\x00":
    return evo_response(module, 8)
  if write == b"\x02C5RPX5\x00":
    return evo_response(module, 9000)
  if write == b"\x02C5RPY5\x00":
    return evo_response(module, 3000)
  if write == b"\x02C5RPZ5\x00":
    return evo_response(module, 2000)
  if write == b"\x02C1RPX0\x00":
    return evo_response(module, 9000)
  if write == b"\x02C5RPX0\x00":
    return evo_response(module, 0)
  return evo_response(module)


class RecordingEVOUSB(MockIO):
  def __init__(self, events: List[str]) -> None:
    super().__init__()
    self.events = events
    self.pending_writes: List[bytes] = []
    self.entry_error: Optional[BaseException] = None
    self.exit_error: Optional[BaseException] = None

  async def _enter_lifespan(
    self,
    stack: AsyncExitStackWithShielding,
    **kwargs: Any,
  ) -> None:
    assert kwargs == {}
    self.events.append("usb.enter")
    if self.entry_error is not None:
      raise self.entry_error
    stack.push_shielded_async_callback(self._record_exit)

  async def _record_exit(self) -> None:
    self.events.append("usb.exit")
    if self.exit_error is not None:
      raise self.exit_error


class RecordingEVOBackend(EVOBackend):
  def __init__(self) -> None:
    super().__init__(diti_count=8)
    self.recorded_delays: List[float] = []

  async def _sleep_between_mca_commands(self, delay: float) -> None:
    self.recorded_delays.append(delay)


def setup_ready_evo(
  *,
  set_context: bool = True,
  write_failure: Optional[Tuple[int, BaseException]] = None,
) -> Tuple[RecordingEVOBackend, RecordingEVOUSB]:
  events: List[str] = []
  io = RecordingEVOUSB(events)
  backend = RecordingEVOBackend()
  backend.io = cast(Any, io)

  async def record_write(data: bytes, timeout: Optional[float] = None) -> None:
    del timeout
    write_index = len(io.write.calls) - 1
    if write_failure is not None and write_index == write_failure[0]:
      raise write_failure[1]
    io.pending_writes.append(data)

  async def reply(timeout: Optional[float] = None) -> bytes:
    del timeout
    return setup_response_for(io.pending_writes.pop(0))

  io.write.side_effect = record_write
  io.read.side_effect = reply
  if set_context:
    backend.set_deck(Deck(size_x=1, size_y=1, size_z=1))
    backend.set_heads({channel: TipTracker(thing=f"Channel {channel}") for channel in range(8)})
  return backend, io


class TestSplitEVOReply(AnyioTestBase):
  async def test_split_reply_is_parsed_only_after_terminator(self) -> None:
    """One firmware reply may span several USB reads."""
    backend, io = setup_ready_evo()
    reply = evo_response("C5", 123)
    io.read.side_effect = [reply[:3], reply[3:]]
    assert await backend.send_command("C5", "RPX", [5]) == {"module": "C5", "data": [123]}


def assert_evo_run_state_reset(backend: EVOBackend) -> None:
  errors: Dict[str, str] = {
    "num_channels": "has not loaded num_channels, forgot to call `setup`?",
    "liha_connected": "liha_connected not set, forgot to call `setup`?",
    "mca_connected": "mca_connected not set, forgot to call `setup`?",
    "roma_connected": "roma_connected not set, forgot to call `setup`?",
  }
  for name, message in errors.items():
    with pytest.raises(RuntimeError) as error_info:
      if name == "num_channels":
        backend.num_channels
      elif name == "liha_connected":
        backend.liha_connected
      elif name == "mca_connected":
        backend.mca_connected
      else:
        backend.roma_connected
    assert str(error_info.value) == message
  assert backend._cache == {}
  assert backend._arm_position_cache == {}
  assert backend._x_range is None
  assert backend._y_range is None
  assert backend._z_range is None
  assert backend.liha is None
  assert backend.roma is None
  assert backend.mca is None


def configured_evo(io: RecordingEVOUSB) -> EVOBackend:
  backend = EVOBackend(diti_count=8)
  backend.io = cast(Any, io)
  backend.set_deck(Deck(size_x=1, size_y=1, size_z=1))
  backend.set_heads({channel: TipTracker(thing=f"Channel {channel}") for channel in range(8)})
  return backend


class TestEVOLifespan(AnyioTestBase):
  async def test_native_entry_preserves_complete_setup_transcript(self) -> None:
    backend, io = setup_ready_evo()
    async with backend:
      assert backend.num_channels == 8
      assert backend.liha_connected
      assert backend.mca_connected
      assert backend.roma_connected
      assert [call[0][0] for call in io.write.calls] == EXPECTED_SETUP_WRITES
      assert io.pending_writes == []
      assert backend.recorded_delays == [0.5, 1, 1, 0.5]
    assert io.events == ["usb.enter", "usb.exit"]
    assert_evo_run_state_reset(backend)

  async def test_normal_exit_closes_usb_without_automatic_arm_parking(self) -> None:
    backend, io = setup_ready_evo()
    async with backend:
      writes_at_body_exit = list(io.write.calls)
    assert io.write.calls == writes_at_body_exit
    assert [call[0][0] for call in io.write.calls] == EXPECTED_SETUP_WRITES
    assert io.events[-1] == "usb.exit"
    assert_evo_run_state_reset(backend)

  async def test_sequential_reuse_replays_full_setup_and_resets_caches(self) -> None:
    backend, io = setup_ready_evo()
    for _ in range(2):
      async with backend:
        assert backend.num_channels == 8
      assert_evo_run_state_reset(backend)
    assert [call[0][0] for call in io.write.calls] == EXPECTED_SETUP_WRITES * 2
    assert io.events == ["usb.enter", "usb.exit", "usb.enter", "usb.exit"]
    assert backend._cache == {}
    assert backend._arm_position_cache == {}

  @pytest.mark.parametrize(
    ("failure_index", "case"),
    [
      (0, "liha-discovery"),
      (7, "roma-position-read"),
      (12, "mca-park"),
      (17, "range-discovery"),
      (22, "liha-position"),
      (28, "plunger-finalization"),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
  )
  async def test_setup_failure_closes_usb_and_resets_all_run_state(
    self, failure_index: int, case: str
  ) -> None:
    failure = RuntimeError(f"{case} failed")
    backend, io = setup_ready_evo(write_failure=(failure_index, failure))
    with pytest.raises(RuntimeError) as error_info:
      async with backend:
        pytest.fail("failed setup must not reach the body")
    assert error_info.value is failure
    assert io.events == ["usb.enter", "usb.exit"]
    assert_evo_run_state_reset(backend)

  async def test_cancelled_setup_reconciles_committed_command_then_closes_usb(self) -> None:
    backend, io = setup_ready_evo()
    blocked_write = b"\x02C1SAA1,9000,2000,2464,1800,,1,0,0\x00"
    reply_started = anyio.Event()
    release_reply = anyio.Event()
    scopes: List[anyio.CancelScope] = []
    cancelled: List[bool] = []

    async def blocked_reply(timeout: Optional[float] = None) -> bytes:
      del timeout
      write = io.pending_writes.pop(0)
      if write == blocked_write:
        reply_started.set()
        await release_reply.wait()
      return setup_response_for(write)

    async def enter_backend() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        async with backend:
          pytest.fail("cancelled setup must not reach the body")
      cancelled.append(scope.cancelled_caught)

    io.read.side_effect = blocked_reply
    async with anyio.create_task_group() as task_group:
      task_group.start_soon(enter_backend)
      await reply_started.wait()
      scopes[0].cancel()
      await anyio.lowlevel.checkpoint()
      assert "usb.exit" not in io.events
      release_reply.set()

    assert cancelled == [True]
    assert io.pending_writes == []
    assert io.events == ["usb.enter", "usb.exit"]
    assert [call[0][0] for call in io.write.calls] == EXPECTED_SETUP_WRITES[:9]
    assert_evo_run_state_reset(backend)

  async def test_cancelled_body_closes_usb_without_arm_commands(self) -> None:
    backend, io = setup_ready_evo()
    body_started = anyio.Event()

    async def use_backend() -> None:
      async with backend:
        body_started.set()
        await anyio.sleep_forever()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(use_backend)
      await body_started.wait()
      task_group.cancel_scope.cancel()

    assert [call[0][0] for call in io.write.calls] == EXPECTED_SETUP_WRITES
    assert io.events == ["usb.enter", "usb.exit"]
    assert_evo_run_state_reset(backend)

  async def test_usb_entry_failure_publishes_no_run_state(self) -> None:
    failure = RuntimeError("usb entry failed")
    backend, io = setup_ready_evo()
    io.entry_error = failure

    with pytest.raises(RuntimeError) as error_info:
      async with backend:
        pytest.fail("failed USB entry must not reach the body")

    assert error_info.value is failure
    assert io.events == ["usb.enter"]
    assert io.write.calls == []
    assert_evo_run_state_reset(backend)

  async def test_usb_exit_failure_still_resets_run_state(self) -> None:
    failure = RuntimeError("usb exit failed")
    backend, io = setup_ready_evo()
    io.exit_error = failure

    with pytest.raises(RuntimeError) as error_info:
      async with backend:
        writes_at_body_exit = list(io.write.calls)

    assert error_info.value is failure
    assert io.write.calls == writes_at_body_exit
    assert io.events == ["usb.enter", "usb.exit"]
    assert_evo_run_state_reset(backend)

  async def test_inactive_liha_operation_preserves_connected_state_error(self) -> None:
    backend, _ = setup_ready_evo()

    with pytest.raises(RuntimeError) as error_info:
      await backend._park_liha()

    assert str(error_info.value) == "liha_connected not set, forgot to call `setup`?"

  async def test_inactive_roma_operation_preserves_connected_state_error(self) -> None:
    backend, _ = setup_ready_evo()

    with pytest.raises(RuntimeError) as error_info:
      await backend._park_roma()

    assert str(error_info.value) == "roma_connected not set, forgot to call `setup`?"

  async def test_liquid_handler_frontend_owns_evo_and_publishes_discovered_head(self) -> None:
    backend, io = setup_ready_evo(set_context=False)
    liquid_handler = LiquidHandler(backend=backend, deck=EVO150Deck())
    assert liquid_handler.head == {}

    async with liquid_handler:
      assert set(liquid_handler.head) == set(range(8))
      assert backend.head is liquid_handler.head
      assert backend.num_channels == 8

    assert set(liquid_handler.head) == set(range(8))
    assert [call[0][0] for call in io.write.calls] == EXPECTED_SETUP_WRITES
    assert io.events == ["usb.enter", "usb.exit"]

  def test_serialization_shape_is_unchanged(self) -> None:
    assert EVOBackend(diti_count=8).serialize() == {
      "type": "EVOBackend",
      "human_readable_device_name": "Tecan EVO",
      "id_vendor": 0x0C47,
      "id_product": 0x4000,
      "device_address": None,
      "serial_number": None,
      "packet_read_timeout": 12,
      "read_timeout": 60,
      "write_timeout": 60,
    }


class TestEVOCommands(AnyioTestBase):
  async def test_partial_timed_out_reply_prevents_successor_dispatch(self) -> None:
    io = RecordingEVOUSB([])
    backend = configured_evo(io)
    old_reply = evo_response("C5", 8)
    io.read.side_effect = [old_reply[:4], lambda timeout=None: anyio.sleep_forever()]

    with pytest.raises(TimeoutError):
      await backend.send_command("C5", "RNT", [1], read_timeout=0.01)

    # Even if the rest of the old frame arrives later, it cannot be attributed
    # to a new command on the same unresolved connection.
    io.read.side_effect = [old_reply[4:], evo_response("C5", 9000)]
    with pytest.raises(RuntimeError, match="reply boundary unknown"):
      await backend.send_command("C5", "RPX", [5])
    assert [call[0][0] for call in io.write.calls] == [b"\x02C5RNT1\x00"]

  async def test_concurrent_commands_hold_one_complete_write_read_parse_transaction(self) -> None:
    io = RecordingEVOUSB([])
    backend = configured_evo(io)
    first_read_started = anyio.Event()
    release_first_reply = anyio.Event()

    async def first_reply(timeout: Optional[float] = None) -> bytes:
      del timeout
      first_read_started.set()
      await release_first_reply.wait()
      return evo_response("C5", 8)

    async def send_rnt() -> None:
      await backend.send_command("C5", "RNT", [1])

    async def send_rpx() -> None:
      await backend.send_command("C5", "RPX", [5])

    io.read.side_effect = [first_reply, evo_response("C5", 9000)]
    async with anyio.create_task_group() as task_group:
      task_group.start_soon(send_rnt)
      await first_read_started.wait()
      task_group.start_soon(send_rpx)
      await anyio.lowlevel.checkpoint()
      assert [call[0][0] for call in io.write.calls] == [b"\x02C5RNT1\x00"]
      release_first_reply.set()

    assert [call[0][0] for call in io.write.calls] == [
      b"\x02C5RNT1\x00",
      b"\x02C5RPX5\x00",
    ]
    assert len(io.read.calls) == 2

  async def test_cancelled_command_waiting_for_transaction_lock_writes_nothing(self) -> None:
    io = RecordingEVOUSB([])
    backend = configured_evo(io)
    first_read_started = anyio.Event()
    release_first_reply = anyio.Event()
    second_started = anyio.Event()
    second_scopes: List[anyio.CancelScope] = []
    cancelled: List[bool] = []

    async def first_reply(timeout: Optional[float] = None) -> bytes:
      del timeout
      first_read_started.set()
      await release_first_reply.wait()
      return evo_response("C5", 8)

    async def run_second() -> None:
      with anyio.CancelScope() as scope:
        second_scopes.append(scope)
        second_started.set()
        await backend.send_command("C5", "RPX", [5])
      cancelled.append(scope.cancelled_caught)

    async def send_first() -> None:
      await backend.send_command("C5", "RNT", [1])

    io.read.side_effect = first_reply
    async with anyio.create_task_group() as task_group:
      task_group.start_soon(send_first)
      await first_read_started.wait()
      task_group.start_soon(run_second)
      await second_started.wait()
      await anyio.lowlevel.checkpoint()
      second_scopes[0].cancel()
      await anyio.lowlevel.checkpoint()
      release_first_reply.set()

    assert cancelled == [True]
    assert [call[0][0] for call in io.write.calls] == [b"\x02C5RNT1\x00"]
    assert len(io.read.calls) == 1

  async def test_cancellation_after_write_consumes_and_parses_reply_before_propagating(
    self,
  ) -> None:
    io = RecordingEVOUSB([])
    backend = configured_evo(io)
    read_started = anyio.Event()
    release_reply = anyio.Event()
    command_scopes: List[anyio.CancelScope] = []
    cancelled: List[bool] = []
    returned: List[object] = []
    pending_replies = [evo_response("C5", 8), evo_response("C5", 9000)]

    async def delayed_reply(timeout: Optional[float] = None) -> bytes:
      del timeout
      if not release_reply.is_set():
        read_started.set()
        await release_reply.wait()
      return pending_replies.pop(0)

    async def run_command() -> None:
      with anyio.CancelScope() as scope:
        command_scopes.append(scope)
        returned.append(await backend.send_command("C5", "RNT", [1]))
      cancelled.append(scope.cancelled_caught)

    io.read.side_effect = delayed_reply
    async with anyio.create_task_group() as task_group:
      task_group.start_soon(run_command)
      await read_started.wait()
      command_scopes[0].cancel()
      await anyio.lowlevel.checkpoint()
      release_reply.set()

    assert cancelled == [True]
    assert returned == []
    assert len(io.read.calls) == 1
    response = await backend.send_command("C5", "RPX", [5])
    assert response == {"module": "C5", "data": [9000]}
    assert len(io.read.calls) == 2

  async def test_wait_false_holds_lock_through_write_without_reading(self) -> None:
    io = RecordingEVOUSB([])
    backend = configured_evo(io)
    first_write_started = anyio.Event()
    release_first_write = anyio.Event()
    response_data: List[object] = []

    async def blocked_write(data: bytes, timeout: Optional[float] = None) -> None:
      del data, timeout
      first_write_started.set()
      await release_first_write.wait()

    async def send_without_reply() -> None:
      await backend.send_command("C5", "SHZ", [2000], wait=False)

    async def send_with_reply() -> None:
      response_data.append(await backend.send_command("C5", "RNT", [1]))

    io.write.side_effect = [blocked_write, None]
    io.read.side_effect = evo_response("C5", 8)
    async with anyio.create_task_group() as task_group:
      task_group.start_soon(send_without_reply)
      await first_write_started.wait()
      task_group.start_soon(send_with_reply)
      await anyio.lowlevel.checkpoint()
      assert [call[0][0] for call in io.write.calls] == [b"\x02C5SHZ2000\x00"]
      assert len(io.read.calls) == 0
      release_first_write.set()

    assert [call[0][0] for call in io.write.calls] == [
      b"\x02C5SHZ2000\x00",
      b"\x02C5RNT1\x00",
    ]
    assert len(io.read.calls) == 1
    assert response_data == [{"module": "C5", "data": [8]}]

  async def test_failed_set_write_does_not_commit_cache(self) -> None:
    io = RecordingEVOUSB([])
    backend = configured_evo(io)
    io.write.side_effect = [RuntimeError("write failed"), None]
    io.read.side_effect = evo_response("C5")
    params: List[Optional[int]] = [2000] * 8

    with pytest.raises(RuntimeError, match="write failed"):
      await backend.send_command("C5", "SHZ", params)
    await backend.send_command("C5", "SHZ", params)

    assert [call[0][0] for call in io.write.calls] == [
      b"\x02C5SHZ2000,2000,2000,2000,2000,2000,2000,2000\x00",
      b"\x02C5SHZ2000,2000,2000,2000,2000,2000,2000,2000\x00",
    ]
    assert backend._cache == {"C5SHZ": [2000] * 8}

  async def test_failed_set_read_or_parse_does_not_commit_cache(self) -> None:
    io = RecordingEVOUSB([])
    backend = configured_evo(io)
    io.read.side_effect = [b"\x02C5\x81\x00", evo_response("C5")]
    params: List[Optional[int]] = [2000] * 8

    with pytest.raises(TecanError):
      await backend.send_command("C5", "SHZ", params)
    await backend.send_command("C5", "SHZ", params)

    assert len(io.write.calls) == 2
    assert len(io.read.calls) == 2
    assert backend._cache == {"C5SHZ": [2000] * 8}

  async def test_successful_set_commits_a_copy_and_skips_only_equal_values(self) -> None:
    io = RecordingEVOUSB([])
    backend = configured_evo(io)
    io.read.side_effect = [evo_response("C5"), evo_response("C5")]
    params: List[Optional[int]] = [2000] * 8

    await backend.send_command("C5", "SHZ", params)
    params[0] = 1900
    await backend.send_command("C5", "SHZ", [2000] * 8)
    await backend.send_command("C5", "SHZ", params)

    assert len(io.write.calls) == 2
    assert len(io.read.calls) == 2
    assert backend._cache["C5SHZ"] == [1900] + [2000] * 7

  async def test_wait_false_set_commits_only_after_successful_write(self) -> None:
    io = RecordingEVOUSB([])
    backend = configured_evo(io)
    io.write.side_effect = [RuntimeError("write failed"), None]
    params: List[Optional[int]] = [2000] * 8

    with pytest.raises(RuntimeError, match="write failed"):
      await backend.send_command("C5", "SHZ", params, wait=False)
    await backend.send_command("C5", "SHZ", params, wait=False)
    await backend.send_command("C5", "SHZ", params, wait=False)
    params[0] = 1900

    assert len(io.write.calls) == 2
    assert len(io.read.calls) == 0
    assert backend._cache == {"C5SHZ": [2000] * 8}

  async def test_get_command_never_reads_or_mutates_set_cache_before_its_turn(self) -> None:
    io = RecordingEVOUSB([])
    backend = configured_evo(io)
    set_read_started = anyio.Event()
    release_set_reply = anyio.Event()

    async def set_reply(timeout: Optional[float] = None) -> bytes:
      del timeout
      set_read_started.set()
      await release_set_reply.wait()
      return evo_response("C5")

    async def send_set() -> None:
      await backend.send_command("C5", "SHZ", [2000] * 8)

    async def send_get() -> None:
      await backend.send_command("C5", "RNT", [1])

    io.read.side_effect = [set_reply, evo_response("C5", 8)]
    async with anyio.create_task_group() as task_group:
      task_group.start_soon(send_set)
      await set_read_started.wait()
      task_group.start_soon(send_get)
      await anyio.lowlevel.checkpoint()
      assert [call[0][0] for call in io.write.calls] == [
        b"\x02C5SHZ2000,2000,2000,2000,2000,2000,2000,2000\x00"
      ]
      assert backend._cache == {}
      release_set_reply.set()

    assert [call[0][0] for call in io.write.calls] == [
      b"\x02C5SHZ2000,2000,2000,2000,2000,2000,2000,2000\x00",
      b"\x02C5RNT1\x00",
    ]
    assert backend._cache == {"C5SHZ": [2000] * 8}
