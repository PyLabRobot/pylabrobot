# mypy: disable-error-code="assignment,method-assign,override"

import unittest
from typing import Any, List, Optional, TypeVar, cast, overload

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.io.usb import USB
from pylabrobot.legacy.liquid_handling import LiquidHandler
from pylabrobot.legacy.liquid_handling.standard import Drop, Pickup
from pylabrobot.legacy.tip_tracker import TipTracker
from pylabrobot.resources import (
  PLT_CAR_L5AC_A00,
  TIP_CAR_480_A00,
  Coordinate,
  cor_96_wellplate_360uL_Fb,
  hamilton_96_tiprack_10uL,
  hamilton_96_tiprack_50uL,
  hamilton_96_tiprack_50uL_NTR,
  hamilton_96_tiprack_300uL,
  hamilton_96_tiprack_1000uL,
  set_tip_tracking,
)
from pylabrobot.resources.hamilton import VantageDeck
from pylabrobot.testing.concurrency import AnyioTestBase

from .hamilton_base_lifecycle_tests import (
  RecordingReplyRouter,
  RecordingUSB,
  exception_messages,
)
from .vantage_backend import (
  VantageBackend,
  VantageFirmwareError,
  parse_vantage_fw_string,
  vantage_response_string_to_error,
)

T = TypeVar("T")

PICKUP_TIP_FORMAT = {
  "xp": "[int]",
  "yp": "[int]",
  "tm": "[int]",
  "tt": "[int]",
  "tp": "[int]",
  "tz": "[int]",
  "th": "[int]",
  "ba": "[int]",
  "td": "[int]",
}

DROP_TIP_FORMAT = {
  "xp": "[int]",
  "yp": "[int]",
  "tm": "[int]",
  "tp": "[int]",
  "tz": "[int]",
  "th": "[int]",
  "te": "[int]",
  "ts": "[int]",
  "td": "[int]",
}

# "dj": "int" = side_touch_off_distance, only documented for dispense, but for some reason VoV also
# sends it for aspirate.
ASPIRATE_FORMAT = {
  "at": "[int]",
  "tm": "[int]",
  "xp": "[int]",
  "yp": "[int]",
  "th": "[int]",
  "te": "[int]",
  "lp": "[int]",
  "ch": "[int]",
  "zl": "[int]",
  "zx": "[int]",
  "ip": "[int]",
  "fp": "[int]",
  "av": "[int]",
  "as": "[int]",
  "ta": "[int]",
  "ba": "[int]",
  "oa": "[int]",
  "lm": "[int]",
  "ll": "[int]",
  "lv": "[int]",
  "de": "[int]",
  "wt": "[int]",
  "mv": "[int]",
  "mc": "[int]",
  "mp": "[int]",
  "ms": "[int]",
  "gi": "[int]",
  "gj": "[int]",
  "gk": "[int]",
  "zu": "[int]",
  "zr": "[int]",
  "mh": "[int]",
  "zo": "[int]",
  "po": "[int]",
  "la": "[int]",
  "lb": "[int]",
  "lc": "[int]",
  "id": "int",
}

DISPENSE_FORMAT = {
  "dm": "[int]",
  "tm": "[int]",
  "xp": "[int]",
  "yp": "[int]",
  "zx": "[int]",
  "lp": "[int]",
  "zl": "[int]",
  "ip": "[int]",
  "fp": "[int]",
  "th": "[int]",
  "te": "[int]",
  "dv": "[int]",
  "ds": "[int]",
  "ss": "[int]",
  "rv": "[int]",
  "ta": "[int]",
  "ba": "[int]",
  "lm": "[int]",
  "zo": "[int]",
  "ll": "[int]",
  "lv": "[int]",
  "de": "[int]",
  "mv": "[int]",
  "mc": "[int]",
  "mp": "[int]",
  "ms": "[int]",
  "wt": "[int]",
  "gi": "[int]",
  "gj": "[int]",
  "gk": "[int]",
  "zu": "[int]",
  "zr": "[int]",
  "dj": "[int]",
  "mh": "[int]",
  "po": "[int]",
  "la": "[int]",
}


class TestVantageResponseParsing(unittest.TestCase):
  """Test parsing of response from Hamilton."""

  def test_parse_response_params(self):
    parsed = parse_vantage_fw_string("A1PMDAid1111", None)
    self.assertEqual(parsed, {"id": 1111})

    parsed = parse_vantage_fw_string("A1PMDAid1111", {"id": "int"})
    self.assertEqual(parsed, {"id": 1111})

    parsed = parse_vantage_fw_string('A1PMDAid1112rw"abc"', {"rw": "str"})
    self.assertEqual(parsed, {"id": 1112, "rw": "abc"})

    parsed = parse_vantage_fw_string("A1PMDAid1112rw-21", {"rw": "int"})
    self.assertEqual(parsed, {"id": 1112, "rw": -21})

    parsed = parse_vantage_fw_string("A1PMDAid1113rwABC", {"rw": "hex"})
    self.assertEqual(parsed, {"id": 1113, "rw": int("ABC", base=16)})

    parsed = parse_vantage_fw_string("A1PMDAid1113rw1 -2 +3", {"rw": "[int]"})
    self.assertEqual(parsed, {"id": 1113, "rw": [1, -2, 3]})

    with self.assertRaises(ValueError):
      # should fail with auto-added id.
      parsed = parse_vantage_fw_string("A1PMDrwbc", None)
      self.assertEqual(parsed, "")

    with self.assertRaises(ValueError):
      parse_vantage_fw_string("A1PMDA", {"id": "int"})

  def test_parse_error_response(self):
    resp = 'I1AMRQid0000er4et"Slave not available"'
    error = vantage_response_string_to_error(resp)
    self.assertEqual(
      error,
      VantageFirmwareError(errors={"Cover": "Slave not available"}, raw_response=resp),
    )

    resp = 'I1AMLPid215er57et"S-Drive: Drive not initialized"'
    error = vantage_response_string_to_error(resp)
    self.assertEqual(
      error,
      VantageFirmwareError(
        errors={"Cover": "S-Drive: Drive not initialized"},
        raw_response=resp,
      ),
    )

    resp = 'A1HMDAid239er99es"H070"'
    error = vantage_response_string_to_error(resp)
    self.assertEqual(
      error,
      VantageFirmwareError(errors={"Core 96": "No liquid level found"}, raw_response=resp),
    )

    resp = 'A1PMDAid262er99es"P170 P270 P370 P470 P570 P670 P770 P870"'
    error = vantage_response_string_to_error(resp)
    self.assertEqual(
      error,
      VantageFirmwareError(
        errors={
          "Pipetting channel 1": "No liquid level found",
          "Pipetting channel 2": "No liquid level found",
          "Pipetting channel 3": "No liquid level found",
          "Pipetting channel 4": "No liquid level found",
          "Pipetting channel 5": "No liquid level found",
          "Pipetting channel 6": "No liquid level found",
          "Pipetting channel 7": "No liquid level found",
          "Pipetting channel 8": "No liquid level found",
        },
        raw_response=resp,
      ),
    )


class RecordingVantageBackend(VantageBackend):
  """Record the Vantage initialization protocol while using the real base lifespan."""

  def __init__(self, fail_at: Optional[str] = None, block_at: Optional[str] = None) -> None:
    super().__init__(read_timeout=1, packet_read_timeout=1)
    self.events: list[str] = []
    io = RecordingUSB(self.events)
    self.io = cast(USB, io)
    self._reply_router = RecordingReplyRouter(io, cast(Any, self), self.events)
    self.fail_at = fail_at
    self.block_at = block_at
    self.block_started = anyio.Event()
    self.set_deck(VantageDeck(size=1.3))
    self.set_heads({index: TipTracker(thing=f"channel {index}") for index in range(8)})

  @overload
  async def _record(self, event: str) -> None: ...

  @overload
  async def _record(self, event: str, result: T) -> T: ...

  async def _record(self, event: str, result: Any = None) -> Any:
    self.events.append(event)
    if event == self.fail_at:
      raise RuntimeError(f"{event} failed")
    if event == self.block_at:
      self.block_started.set()
      await anyio.sleep_forever()
    return result

  async def query_tip_presence(self) -> list[bool]:
    return await self._record("pip.tip_presence", [False] * 8)

  async def arm_request_instrument_initialization_status(self) -> bool:
    return await self._record("arm.status", False)

  async def arm_pre_initialize(self) -> None:
    await self._record("arm.initialize")

  async def pip_request_initialization_status(self) -> bool:
    return await self._record("pip.status", False)

  async def pip_initialize(self, **kwargs: Any) -> None:
    await self._record("pip.initialize")

  async def loading_cover_request_initialization_status(self) -> bool:
    return await self._record("cover.status", False)

  async def loading_cover_initialize(self) -> None:
    await self._record("cover.initialize")

  async def core96_request_initialization_status(self) -> bool:
    return await self._record("core96.status", False)

  async def core96_initialize(self, **kwargs: Any) -> None:
    await self._record("core96.initialize")

  async def ipg_request_initialization_status(self) -> bool:
    return await self._record("ipg.status", False)

  async def ipg_initialize(self) -> None:
    await self._record("ipg.initialize")

  async def ipg_get_parking_status(self) -> bool:
    return await self._record("ipg.parking_status", False)

  async def ipg_park(self) -> None:
    await self._record("ipg.park")


class TestVantageTiming(AnyioTestBase):
  async def test_disco_mode_sleeps_after_each_of_69_led_updates(self, monkeypatch) -> None:
    backend = VantageBackend()
    events: list[tuple[str, float]] = []

    async def set_led_color(
      mode: str,
      intensity: int,
      white: int,
      red: int,
      green: int,
      blue: int,
      uv: int,
    ) -> None:
      assert mode == "on"
      assert (intensity, white, uv) == (100, 0, 0)
      assert all(30 <= value <= 100 for value in (red, green, blue))
      events.append(("led", float(red + green + blue)))

    async def sleep(duration: float) -> None:
      events.append(("sleep", duration))

    backend.set_led_color = set_led_color
    monkeypatch.setattr(anyio, "sleep", sleep)
    await backend.disco_mode()

    assert len(events) == 138
    assert events[::2] == [event for event in events if event[0] == "led"]
    assert events[1::2] == [("sleep", 0.1)] * 69

  async def test_russian_roulette_waits_five_seconds_before_reset(self, monkeypatch) -> None:
    backend = VantageBackend()
    events: list[tuple[str, tuple[Any, ...]]] = []

    async def set_led_color(*args: Any, **kwargs: Any) -> None:
      events.append(("led", (args, kwargs)))

    async def sleep(duration: float) -> None:
      events.append(("sleep", (duration,)))

    backend.set_led_color = set_led_color
    monkeypatch.setattr("builtins.input", lambda prompt: "yes")
    monkeypatch.setattr("random.randint", lambda start, end: 6)
    monkeypatch.setattr(anyio, "sleep", sleep)
    await backend.russian_roulette()

    assert events == [
      (
        "led",
        (
          ("on",),
          {"intensity": 100, "white": 100, "red": 100, "green": 0, "blue": 0, "uv": 100},
        ),
      ),
      ("sleep", (5,)),
      (
        "led",
        (
          ("on",),
          {
            "intensity": 100,
            "white": 100,
            "red": 100,
            "green": 100,
            "blue": 100,
            "uv": 0,
          },
        ),
      ),
    ]


class TestVantageLifecycle(AnyioTestBase):
  async def test_lifespan_preserves_setup_transcript_and_publishes_channels(self) -> None:
    backend = RecordingVantageBackend()

    async with backend:
      assert backend.num_channels == 8
      assert [
        event for event in backend.events if "." in event and not event.startswith("reader")
      ] == [
        "transport.enter",
        "pip.tip_presence",
        "arm.status",
        "arm.initialize",
        "pip.status",
        "pip.initialize",
        "cover.status",
        "cover.initialize",
        "core96.status",
        "core96.initialize",
        "ipg.status",
        "ipg.initialize",
        "ipg.parking_status",
        "ipg.park",
      ]

    assert (
      backend.events.index("reader.stop")
      < backend.events.index("reader.exit")
      < backend.events.index("transport.exit")
    )

  async def test_setup_options_are_forwarded_with_false_defaults(self) -> None:
    backend = RecordingVantageBackend()
    async with backend.lifespan(skip_loading_cover=True, skip_core96=True, skip_ipg=True):
      pass

    assert "cover.initialize" not in backend.events
    assert "core96.initialize" not in backend.events
    assert not any(event.startswith("ipg.") for event in backend.events)

  @pytest.mark.parametrize(
    "kwargs",
    [
      {"unknown": False},
      {"skip_loading_cover": 1},
      {"skip_core96": None},
      {"skip_ipg": "false"},
    ],
  )
  async def test_invalid_options_fail_before_transport_entry(self, kwargs: dict[str, Any]) -> None:
    backend = RecordingVantageBackend()
    with pytest.raises(TypeError):
      async with backend.lifespan(**kwargs):
        pytest.fail("invalid entry reached the body")
    assert "transport.enter" not in backend.events

  @pytest.mark.parametrize("phase", ["pip.tip_presence", "pip.initialize", "ipg.park"])
  async def test_firmware_failure_at_each_initialization_phase_cleans_up(self, phase: str) -> None:
    backend = RecordingVantageBackend(fail_at=phase)
    with pytest.raises(BaseException) as error_info:
      async with backend:
        pytest.fail("failed entry reached the body")

    assert f"{phase} failed" in exception_messages(error_info.value)
    assert (
      backend.events.index("reader.stop")
      < backend.events.index("reader.exit")
      < backend.events.index("transport.exit")
    )
    with pytest.raises(RuntimeError, match="num_channels is not set"):
      _ = backend.num_channels

  async def test_cancellation_during_entry_cleans_up_transport(self) -> None:
    backend = RecordingVantageBackend(block_at="pip.initialize")

    async def enter() -> None:
      async with backend:
        pytest.fail("cancelled entry reached the body")

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(enter)
      await backend.block_started.wait()
      task_group.cancel_scope.cancel()

    assert (
      backend.events.index("reader.stop")
      < backend.events.index("reader.exit")
      < backend.events.index("transport.exit")
    )
    assert not backend.is_active

  async def test_sequential_reuse_repeats_setup_and_resets_channels(self) -> None:
    backend = RecordingVantageBackend()
    for _ in range(2):
      async with backend:
        assert backend.num_channels == 8
      with pytest.raises(RuntimeError, match="num_channels is not set"):
        _ = backend.num_channels

    assert backend.events.count("pip.tip_presence") == 2
    assert backend.events.count("transport.enter") == 2
    assert backend.events.count("transport.exit") == 2


class VantageCommandCatcher(VantageBackend):
  """Mock backend for Vantage that catches commands and saves them instead of sending them to the
  machine."""

  def __init__(self):
    super().__init__()
    self.commands = []

  async def _enter_lifespan(
    self, stack: AsyncExitStackWithShielding, **backend_kwargs: Any
  ) -> None:
    if backend_kwargs:
      raise TypeError("VantageCommandCatcher does not accept lifespan options")
    self._validate_lifespan_context()
    self.setup_finished = True
    self._num_channels = 8
    self.iswap_installed = True
    self._num_arms = 1
    self._head96_installed = True
    stack.callback(self._reset_setup_state)

  def _reset_setup_state(self) -> None:
    self.setup_finished = False
    self._num_channels = None

  async def send_command(
    self,
    module: str,
    command: str,
    auto_id: bool = True,
    tip_pattern: Optional[List[bool]] = None,
    write_timeout: Optional[int] = None,
    read_timeout: Optional[int] = None,
    wait=True,
    fmt: Optional[Any] = None,
    **kwargs,
  ):
    cmd, _ = self._assemble_command(
      module=module, command=command, auto_id=auto_id, tip_pattern=tip_pattern, **kwargs
    )
    self.commands.append(cmd)


class TestVantageCommandCatcherLifecycle(AnyioTestBase):
  async def test_command_catcher_is_resource_free_and_reusable(self) -> None:
    backend = VantageCommandCatcher()
    backend.set_deck(VantageDeck(size=1.3))
    backend.set_heads({index: TipTracker(thing=f"channel {index}") for index in range(8)})
    events: list[str] = []
    io = RecordingUSB(events)
    backend.io = cast(USB, io)
    router = RecordingReplyRouter(io, cast(Any, backend), events)
    backend._reply_router = router

    for _ in range(2):
      async with backend:
        assert backend.setup_finished
        assert backend.num_channels == 8
      assert not backend.setup_finished
      assert events == []
      assert router.run_count == 0


class TestVantageLiquidHandlerCommands(unittest.IsolatedAsyncioTestCase):
  """Test Vantage backend for liquid handling."""

  async def asyncSetUp(self):
    self.mockVantage = VantageCommandCatcher()
    self.deck = VantageDeck(size=1.3)
    self.lh = LiquidHandler(self.mockVantage, deck=self.deck)

    self.tip_car = TIP_CAR_480_A00(name="tip carrier")
    self.tip_car[0] = self.tip_rack = hamilton_96_tiprack_1000uL(name="tip_rack_01")
    self.tip_car[1] = self.small_tip_rack = hamilton_96_tiprack_10uL(name="tip_rack_02")
    self.deck.assign_child_resource(self.tip_car, track=18)

    self.plt_car = PLT_CAR_L5AC_A00(name="plate carrier")
    self.plt_car[0] = self.plate = cor_96_wellplate_360uL_Fb(name="plate_01")
    self.plt_car[1] = self.other_plate = cor_96_wellplate_360uL_Fb(name="plate_02")
    self.deck.assign_child_resource(self.plt_car, track=24)

    self.maxDiff = None

    await self.lh.setup()

    set_tip_tracking(enabled=False)

  async def asyncTearDown(self):
    await self.lh.stop()

  def _assert_command_in_command_buffer(self, cmd: str, should_be: bool, fmt: dict):
    """Assert that the given command was sent to the backend. The ordering of the parameters is not
    taken into account, but the values and formatting should match. The id parameter of the command
    is ignored.

    If a command is found, it is removed from the command buffer.

    Args:
      cmd: the command to look for
      should_be: whether the command should be found or not
      fmt: the format of the command
    """

    found = False
    # Command that fits the format, but is not the same as the command we are looking for.
    similar = None

    parsed_cmd = parse_vantage_fw_string(cmd, fmt)
    parsed_cmd.pop("id")

    for sent_cmd in self.mockVantage.commands:
      # When the module and command do not match, there is no point in comparing the parameters.
      if sent_cmd[0:6] != cmd[0:6]:
        continue

      try:
        parsed_sent_cmd = parse_vantage_fw_string(sent_cmd, fmt)
        parsed_sent_cmd.pop("id")

        if parsed_cmd == parsed_sent_cmd:
          self.mockVantage.commands.remove(sent_cmd)
          found = True
          break
        else:
          similar = parsed_sent_cmd
      except ValueError as e:
        # The command could not be parsed.
        print("Could not parse command", e)
        continue

    if should_be and not found:
      if similar is not None:
        # similar != parsed_cmd, but assertEqual gives a better error message than `fail`.
        self.assertEqual(similar, parsed_cmd)
      else:
        self.fail(f"Command {cmd} not found in sent commands: {self.mockVantage.commands}")
    elif not should_be and found:
      self.fail(f"Command {cmd} was found in sent commands: {self.mockVantage.commands}")

  def test_ops_to_fw_positions(self):
    """Convert channel positions to firmware positions."""
    tip_a1 = self.tip_rack.get_item("A1")
    tip_f1 = self.tip_rack.get_item("F1")
    tip = self.tip_rack.get_tip("A1")

    op1 = Pickup(resource=tip_a1, tip=tip, offset=Coordinate.zero())
    op2 = Pickup(resource=tip_f1, tip=tip, offset=Coordinate.zero())
    self.assertEqual(
      self.mockVantage._ops_to_fw_positions((op1,), use_channels=[0]),
      ([4329, 0], [1458, 0], [True, False]),
    )

    self.assertEqual(
      self.mockVantage._ops_to_fw_positions((op1, op2), use_channels=[0, 1]),
      ([4329, 4329, 0], [1458, 1008, 0], [True, True, False]),
    )

    self.assertEqual(
      self.mockVantage._ops_to_fw_positions((op1, op2), use_channels=[1, 2]),
      (
        [0, 4329, 4329, 0],
        [0, 1458, 1008, 0],
        [False, True, True, False],
      ),
    )

  def _assert_command_sent_once(self, cmd: str, fmt: dict):
    """Assert that the given command was sent to the backend exactly once."""
    self._assert_command_in_command_buffer(cmd, True, fmt)
    self._assert_command_in_command_buffer(cmd, False, fmt)

  def test_tip_definition(self):
    pass

  async def test_tip_pickup_01(self):
    await self.lh.pick_up_tips(self.tip_rack["A1", "B1"])
    self._assert_command_sent_once(
      "A1PMTPid0012xp4329 4329 0&yp1458 1368 0&tm1 1 0&tt1 1&tp2264 2264&tz2164 2164&th2450 2450&"
      "te2450 2450&ba0 0&td1 1&",
      PICKUP_TIP_FORMAT,
    )

  async def test_tip_drop_01(self):
    await self.lh.pick_up_tips(self.tip_rack["A1", "B1"])
    await self.lh.drop_tips(self.tip_rack["A1", "B1"])
    self._assert_command_sent_once(
      "A1PMTRid013xp04329 04329 0&yp1458 1368 0&tm1 1 0&tp1414 1414&tz1314 1314&th2450 2450&"
      "te2450 2450&ts0td0 0&",
      DROP_TIP_FORMAT,
    )

  async def test_small_tip_pickup(self):
    await self.lh.pick_up_tips(self.small_tip_rack["A1"])
    self._assert_command_sent_once(
      "A1PMTPid0010xp4329 0&yp2418 0&tm1 0&tt1&tp2224&tz2164&th2450&te2450&ba0&td1&",
      PICKUP_TIP_FORMAT,
    )

  async def test_small_tip_drop(self):
    await self.lh.pick_up_tips(self.small_tip_rack["A1"])
    await self.lh.drop_tips(self.small_tip_rack["A1"])
    self._assert_command_sent_once(
      "A1PMTRid0012xp4329 0&yp2418 0&tp2025&tz1925&th2450&te2450&tm1 0&ts0td0&",
      DROP_TIP_FORMAT,
    )

  async def test_discard_tips_to_trash(self):
    """Trash coordinates specify the drop height without a tip-length correction."""
    await self.lh.pick_up_tips(self.tip_rack["A1"])
    await self.lh.discard_tips()
    command = next(cmd for cmd in self.mockVantage.commands if cmd.startswith("A1PMTR"))
    parsed = parse_vantage_fw_string(command, DROP_TIP_FORMAT)
    self.assertEqual(parsed["tp"], [1471])
    self.assertEqual(parsed["tz"], [1371])

  async def test_nested_tip_rack_drop(self):
    """Nested rack spots, like embedded ones, are where the collar rests; discard uses the tip end."""
    rack = hamilton_96_tiprack_50uL_NTR("nested_tips")
    self.deck.assign_child_resource(rack, location=Coordinate(100, 200, 150))
    spot = rack.get_item("A1")
    await self.mockVantage.drop_tips(
      [Drop(resource=spot, tip=spot.get_tip(), offset=Coordinate.zero())], use_channels=[0]
    )
    command = next(cmd for cmd in self.mockVantage.commands if cmd.startswith("A1PMTR"))
    parsed = parse_vantage_fw_string(command, DROP_TIP_FORMAT)
    # spot 150 + 55, less the 50.4 mm tip below its 8 mm collar
    self.assertEqual(parsed["tp"], [1726])
    self.assertEqual(parsed["tz"], [1626])

  async def test_aspirate(self):
    await self.lh.pick_up_tips(self.tip_rack["A1"])  # pick up tips first
    await self.lh.aspirate(self.plate["A1"], vols=[100])

    self._assert_command_sent_once(
      "A1PMDAid0248at0&tm1 0&xp05683 0&yp1457 0 &th2450&te2450&lp1990&"
      "ch000&zl1866&zx1866&ip0000&fp0000&av010830&as2500&ta000&ba00000&oa000&lm0&ll4&lv4&de0020&"
      "wt10&mv00000&mc00&mp000&ms2500&gi000&gj0gk0zu0000&zr00000&mh0000&zo005&po0109&dj0la0&lb0&"
      "lc0&",
      ASPIRATE_FORMAT,
    )

  async def test_dispense(self):
    await self.lh.pick_up_tips(self.tip_rack["A1"])  # pick up tips first
    await self.lh.aspirate(self.plate["A1"], vols=[100])
    await self.lh.dispense(
      self.plate["A2"],
      vols=[100],
      liquid_height=[5],
      jet=[False],
      blow_out=[True],
    )

    self._assert_command_sent_once(
      "A1PMDDid0253dm3&tm1 0&xp05773 0&yp1457 0&zx1866&lp1990&zl1916&"
      "ip0000&fp0021&th2450&te2450&dv010830&ds1200&ss2500&rv000&ta050&ba00000&lm0&zo005&ll1&lv1&"
      "de0010&mv00000&mc00&mp000&ms0010&wt00&gi000&gj0gk0zu0000&dj00zr00000&mh0000&po0050&la0&",
      DISPENSE_FORMAT,
    )

  async def test_zero_volume_liquid_handling(self):
    # just test that this does not throw an error
    await self.lh.pick_up_tips(self.tip_rack["A1"])  # pick up tips first
    await self.lh.aspirate(self.plate["A1"], vols=[0])
    await self.lh.dispense(self.plate["A2"], vols=[0])

  async def test_tip_pickup96(self):
    await self.lh.pick_up_tips96(self.tip_rack)
    self._assert_command_sent_once(
      "A1HMTPid0237xp04329yp1458tt01td0tz2164th2450te2450",
      {
        "xp": "int",
        "yp": "int",
        "tt": "int",
        "td": "int",
        "tz": "int",
        "th": "int",
        "te": "int",
      },
    )

  async def test_tip_drop96(self):
    await self.lh.pick_up_tips96(self.tip_rack)
    await self.lh.drop_tips96(self.tip_rack)
    self._assert_command_sent_once(
      "A1HMTRid0284xp04329yp1458tz2164th2450te2450",
      {
        "xp": "int",
        "yp": "int",
        "tz": "int",
        "th": "int",
        "te": "int",
      },
    )

  async def test_aspirate96(self):
    await self.lh.pick_up_tips96(self.tip_rack)
    await self.lh.aspirate96(self.plate, volume=100, jet=True, blow_out=True)
    self._assert_command_sent_once(
      "A1HMDAid0236at0xp05683yp1457th2450te2450lp1990zl1866zx1866ip000fp000av010720as2500ta050"
      "ba004000oa00000lm0ll4de0020wt10mv00000mc00mp000ms2500zu0000zr00000mh000gj0gk0gi000"
      "cwFFFFFFFFFFFFFFFFFFFFFFFFpo0050",
      {
        "xp": "int",
        "yp": "int",
        "th": "int",
        "te": "int",
        "lp": "int",
        "zl": "int",
        "zx": "int",
        "ip": "int",
        "fp": "int",
        "av": "int",
        "as": "int",
        "ta": "int",
        "ba": "int",
        "oa": "int",
        "lm": "int",
        "ll": "int",
        "de": "int",
        "wt": "int",
        "mv": "int",
        "mc": "int",
        "mp": "int",
        "zu": "int",
        "zr": "int",
        "mh": "int",
        "gj": "int",
        "gk": "int",
        "gi": "int",
        "cw": "hex",
        "po": "int",
      },
    )

  async def test_dispense96(self):
    await self.lh.pick_up_tips96(self.tip_rack)
    await self.lh.aspirate96(self.plate, volume=100, jet=True, blow_out=True)
    await self.lh.dispense96(self.plate, volume=100, jet=True, blow_out=True)
    self._assert_command_sent_once(
      "A1HMDDid0238dm1xp05683yp1457th2450te2450lp1990zl1966zx1866ip000fp029dv010720ds4000ta050"
      "ba004000lm0ll4de0010wt00mv00000mc00mp000ms0010ss2500rv000zu0000dj00zr00000mh000gj0gk0gi000"
      "cwFFFFFFFFFFFFFFFFFFFFFFFFpo0050",
      {
        "xp": "int",
        "yp": "int",
        "th": "int",
        "te": "int",
        "lp": "int",
        "zl": "int",
        "zx": "int",
        "ip": "int",
        "fp": "int",
        "dv": "int",
        "ds": "int",
        "ta": "int",
        "ba": "int",
        "lm": "int",
        "ll": "int",
        "de": "int",
        "wt": "int",
        "mv": "int",
        "mc": "int",
        "mp": "int",
        "ms": "int",
        "ss": "int",
        "rv": "int",
        "zu": "int",
        "zr": "int",
        "dj": "int",
        "mh": "int",
        "gj": "int",
        "gk": "int",
        "gi": "int",
        "cw": "hex",
        "po": "int",
      },
    )

  async def test_zero_volume_liquid_handling96(self):
    # just test that this does not throw an error
    await self.lh.pick_up_tips96(self.tip_rack)
    await self.lh.aspirate96(self.plate, volume=0)
    await self.lh.dispense96(self.plate, volume=0)

  async def test_move_plate(self):
    self.plt_car[1].resource.unassign()
    await self.lh.move_plate(self.plate, self.plt_car[1], pickup_distance_from_top=5.2 - 3.33)

    # pickup
    self._assert_command_sent_once(
      "A1RMDGid0240xp6179yp1142zp1954yw81yo1310yg1245pt20zc0hd0te2840",
      {
        "xp": "int",
        "yp": "int",
        "zp": "int",
        "yw": "int",
        "yo": "int",
        "yg": "int",
        "pt": "int",
        "zc": "int",
        "hd": "int",
        "te": "int",
      },
    )

    # release
    self._assert_command_sent_once(
      "A1RMDRid0242xp6179yp2102zp1954yo1310zc0hd0te2840",
      {
        "xp": "int",
        "yp": "int",
        "zp": "int",
        "yo": "int",
        "zc": "int",
        "hd": "int",
        "te": "int",
      },
    )


class TestVantageTipPickupDropAllSizes(unittest.IsolatedAsyncioTestCase):
  """Test Vantage tip pickup and drop Z position calculations for all tip sizes."""

  async def asyncSetUp(self):
    self.backend = VantageCommandCatcher()
    self.deck = VantageDeck(size=1.3)
    self.lh = LiquidHandler(self.backend, deck=self.deck)

    self.tip_car = TIP_CAR_480_A00(name="tip_carrier")
    self.deck.assign_child_resource(self.tip_car, track=18)

    await self.lh.setup()
    set_tip_tracking(enabled=False)

  async def asyncTearDown(self):
    await self.lh.stop()

  def _get_tp_tz_from_commands(self, cmd_prefix: str, fmt: dict):
    """Extract tp and tz values from commands matching the prefix."""
    for cmd in self.backend.commands:
      if cmd.startswith(cmd_prefix):
        parsed = parse_vantage_fw_string(cmd, fmt)
        return parsed.get("tp"), parsed.get("tz")
    return None, None

  async def test_10uL_tips(self):
    tip_rack = hamilton_96_tiprack_10uL("tips")
    self.tip_car[1] = tip_rack

    # Pickup
    await self.lh.pick_up_tips(tip_rack["A1"])
    tp, tz = self._get_tp_tz_from_commands("A1PMTP", PICKUP_TIP_FORMAT)
    self.assertEqual(tp, [2224])
    self.assertEqual(tz, [2164])

    # Drop
    self.backend.commands = []
    await self.lh.drop_tips(tip_rack["A1"])
    tp, tz = self._get_tp_tz_from_commands("A1PMTR", DROP_TIP_FORMAT)
    self.assertEqual(tp, [2025])
    self.assertEqual(tz, [1925])

    tip_rack.unassign()

  async def test_50uL_tips(self):
    tip_rack = hamilton_96_tiprack_50uL("tips")
    self.tip_car[1] = tip_rack

    # Pickup
    await self.lh.pick_up_tips(tip_rack["A1"])
    tp, tz = self._get_tp_tz_from_commands("A1PMTP", PICKUP_TIP_FORMAT)
    self.assertEqual(tp, [2244])
    self.assertEqual(tz, [2164])

    # Drop
    self.backend.commands = []
    await self.lh.drop_tips(tip_rack["A1"])
    tp, tz = self._get_tp_tz_from_commands("A1PMTR", DROP_TIP_FORMAT)
    self.assertEqual(tp, [1840])
    self.assertEqual(tz, [1740])

    tip_rack.unassign()

  async def test_300uL_tips(self):
    tip_rack = hamilton_96_tiprack_300uL("tips")
    self.tip_car[1] = tip_rack

    # Pickup
    await self.lh.pick_up_tips(tip_rack["A1"])
    tp, tz = self._get_tp_tz_from_commands("A1PMTP", PICKUP_TIP_FORMAT)
    self.assertEqual(tp, [2244])
    self.assertEqual(tz, [2164])

    # Drop
    self.backend.commands = []
    await self.lh.drop_tips(tip_rack["A1"])
    tp, tz = self._get_tp_tz_from_commands("A1PMTR", DROP_TIP_FORMAT)
    self.assertEqual(tp, [1745])
    self.assertEqual(tz, [1645])

    tip_rack.unassign()

  async def test_1000uL_tips(self):
    tip_rack = hamilton_96_tiprack_1000uL("tips")
    self.tip_car[1] = tip_rack

    # Pickup
    await self.lh.pick_up_tips(tip_rack["A1"])
    tp, tz = self._get_tp_tz_from_commands("A1PMTP", PICKUP_TIP_FORMAT)
    self.assertEqual(tp, [2264])
    self.assertEqual(tz, [2164])

    # Drop
    self.backend.commands = []
    await self.lh.drop_tips(tip_rack["A1"])
    tp, tz = self._get_tp_tz_from_commands("A1PMTR", DROP_TIP_FORMAT)
    self.assertEqual(tp, [1414])
    self.assertEqual(tz, [1314])

    tip_rack.unassign()
