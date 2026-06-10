import contextlib
import functools

import anyio
import pytest
import trio
import trio.testing

from pylabrobot.liquid_handling import LiquidHandler
from pylabrobot.liquid_handling.backends.hamilton.STAR_backend import STARBackend
from pylabrobot.liquid_handling.standard import Mix
from pylabrobot.resources import PLT_CAR_L5AC_A00, TIP_CAR_480_A00, Cor_96_wellplate_360ul_Fb
from pylabrobot.resources.hamilton import STARLetDeck, hamilton_96_tiprack_300uL_filter

from .STAR_simulator import STARSimulatorBackend


def channels_test(func):
  @pytest.mark.parametrize("channels", [[0], list(range(8)), [0, 2, 5]])
  @functools.wraps(func)
  def wrapper(*args, **kwargs):
    async def run_test():
      return await func(*args, **kwargs)

    return trio.run(run_test, clock=trio.testing.MockClock(autojump_threshold=0.01))

  return wrapper


def simulator_test(func):
  @functools.wraps(func)
  def wrapper(*args, **kwargs):
    async def run_test():
      return await func(*args, **kwargs)

    return trio.run(run_test, clock=trio.testing.MockClock(autojump_threshold=0.01))

  return wrapper


class TestSTARSimulator:
  @contextlib.asynccontextmanager
  async def _setup_lh(self):
    self.backend = STARSimulatorBackend()
    self.deck = STARLetDeck()
    self.lh = LiquidHandler(self.backend, deck=self.deck)

    self.tip_car = TIP_CAR_480_A00(name="tip carrier")
    self.tip_car[1] = self.tip_rack = hamilton_96_tiprack_300uL_filter(name="tip_rack_01")
    self.deck.assign_child_resource(self.tip_car, rails=1)

    self.plt_car = PLT_CAR_L5AC_A00(name="plate carrier")
    self.plt_car[0] = self.plate = Cor_96_wellplate_360ul_Fb(name="plate_01")
    self.deck.assign_child_resource(self.plt_car, rails=9)

    async with self.lh:
      yield

  def _get_resources(self, channels: list[int], resource_grid):
    rows = "ABCDEFGH"
    return [resource_grid.get_item(f"{rows[ch]}1") for ch in channels]

  @channels_test
  async def test_timing_pick_up_tips(self, channels):
    async with self._setup_lh():
      tips = self._get_resources(channels, self.tip_rack)

      start_time = anyio.current_time()
      with self.lh.use_channels(channels):
        await self.lh.pick_up_tips(tips)
      end_time = anyio.current_time()

      duration = end_time - start_time
      print(f"Pick up tips took {duration} seconds with channels {channels}")
      assert duration > 0.5

      for ch in range(8):
        if ch in channels:
          assert self.backend.channels[ch].has_tip is True
        else:
          assert self.backend.channels[ch].has_tip is False

  @channels_test
  async def test_timing_aspirate(self, channels):
    async with self._setup_lh():
      tips = self._get_resources(channels, self.tip_rack)
      wells = self._get_resources(channels, self.plate)

      with self.lh.use_channels(channels):
        await self.lh.pick_up_tips(tips)

      for ch in range(8):
        if ch in channels:
          assert self.backend.channels[ch].has_tip is True
        else:
          assert self.backend.channels[ch].has_tip is False

      for well in wells:
        well.tracker.set_volume(100)

      start_time = anyio.current_time()
      with self.lh.use_channels(channels):
        await self.lh.aspirate(wells, vols=[50] * len(wells))
        for ch in channels:
          assert self.backend.channels[ch].dispensing_drive_position == pytest.approx(55.1)
        await self.lh.dispense(wells, vols=[50] * len(wells))
      end_time = anyio.current_time()

      for ch in channels:
        assert self.backend.channels[ch].dispensing_drive_position == 0.0

      duration = end_time - start_time
      print(f"Aspirate took {duration} seconds with channels {channels}")
      assert duration > 0.5

  @channels_test
  async def test_timing_aspirate_with_mix(self, channels):
    async with self._setup_lh():
      tips = self._get_resources(channels, self.tip_rack)
      wells = self._get_resources(channels, self.plate)

      with self.lh.use_channels(channels):
        await self.lh.pick_up_tips(tips)

      for well in wells:
        well.tracker.set_volume(100)

      mix = Mix(volume=50, repetitions=3, flow_rate=100)

      start_time = anyio.current_time()
      with self.lh.use_channels(channels):
        await self.lh.aspirate(wells, vols=[50] * len(wells), mix=[mix] * len(wells))
      end_time = anyio.current_time()

      duration = end_time - start_time
      print(f"Aspirate with mix took {duration} seconds with channels {channels}")
      assert duration > 3.5

  @channels_test
  async def test_wrongly_ordered_resources(self, channels):
    async with self._setup_lh():
      tips = self._get_resources(channels, self.tip_rack)
      reversed_tips = list(reversed(tips))

      with self.lh.use_channels(channels):
        try:
          await self.lh.pick_up_tips(reversed_tips)
          print(f"Wrongly ordered resources PASSED for channels {channels}")
        except Exception as e:
          print(
            f"Wrongly ordered resources FAILED for channels {channels} with:"
            f" {type(e).__name__}: {e}"
          )

  @simulator_test
  async def test_timing_head96_pick_up_tips(self):
    async with self._setup_lh():
      start_time = anyio.current_time()
      await self.lh.pick_up_tips96(self.tip_rack)
      end_time = anyio.current_time()

      duration = end_time - start_time
      print(f"Head96 pick up tips took {duration} seconds")
      assert duration > 1.0
      assert self.backend._head96_position.has_tip is True

  @simulator_test
  async def test_timing_head96_aspirate_dispense(self):
    async with self._setup_lh():
      await self.lh.pick_up_tips96(self.tip_rack)

      for well in self.plate.get_all_items():
        well.tracker.set_volume(100)

      assert self.backend._head96_position.has_tip is True
      start_time = anyio.current_time()
      await self.lh.aspirate96(self.plate, volume=50)
      assert self.backend._head96_position.dispensing_drive_position == pytest.approx(271.59)
      await self.lh.dispense96(self.plate, volume=50)
      end_time = anyio.current_time()

      assert self.backend._head96_position.dispensing_drive_position == pytest.approx(218.19)

      duration = end_time - start_time
      print(f"Head96 aspirate/dispense took {duration} seconds")
      assert duration > 1.0

  @simulator_test
  async def test_timing_iswap_move_plate(self):
    async with self._setup_lh():
      other_slot = self.plt_car[1]

      start_time = anyio.current_time()
      await self.lh.move_plate(self.plate, to=other_slot, use_arm="iswap")
      end_time = anyio.current_time()

      duration = end_time - start_time
      print(f"iSWAP move plate took {duration} seconds")
      assert duration > 2.0

      assert self.backend._iswap_state.holding_resource is False

      # Grip FRONT (1) is a compact reversed wrist configuration (W_front and T_reverse)
      rot_stops = self.backend.iswap_information.rotation_drive_predefined_increments
      wrist_stops = self.backend.iswap_information.wrist_drive_predefined_increments
      rot_scale = self.backend.iswap_information.rotation_deg_per_increment
      wrist_scale = self.backend.iswap_information.wrist_deg_per_increment

      expected_w = rot_stops[STARBackend.RotationDriveOrientation.FRONT] * rot_scale
      expected_t = wrist_stops[STARBackend.WristDriveOrientation.REVERSE] * wrist_scale

      assert self.backend._iswap_state.current_w == pytest.approx(expected_w)
      assert self.backend._iswap_state.current_t == pytest.approx(expected_t)

  @simulator_test
  async def test_timing_free_iswap_y_range(self):
    async with self._setup_lh():
      start_time = anyio.current_time()
      await self.backend.position_components_for_free_iswap_y_range()
      end_time = anyio.current_time()

      duration = end_time - start_time
      print(f"Free iSWAP Y range took {duration} seconds")
      assert duration > 1.5

      min_y = self.backend.extended_conf.left_arm_min_y_position
      assert self.backend.channels[7].current_y == pytest.approx(min_y)
      assert self.backend.channels[0].current_y == pytest.approx(min_y + 7 * 9.0)
      for i in range(8):
        assert self.backend.channels[i].current_z == pytest.approx(360.0)

  @simulator_test
  async def test_narrow_contract_assertions(self):
    import copy

    from pylabrobot.liquid_handling.backends.hamilton.STAR_chatterbox import (
      _DEFAULT_EXTENDED_CONFIGURATION,
    )

    custom_extended = copy.deepcopy(_DEFAULT_EXTENDED_CONFIGURATION)
    custom_extended.left_x_drive.core_96_head_installed = False
    custom_extended.left_x_drive.iswap_installed = False

    backend = STARSimulatorBackend(extended_configuration=custom_extended)

    # 1. Assert queries raise loud errors
    with pytest.raises(RuntimeError, match="96-head is not installed"):
      await backend.head96_request_tip_presence()

    with pytest.raises(RuntimeError, match="iSWAP is not installed"):
      await backend.iswap_gripper_request_width()

    # 2. Assert commands raise loud errors
    with pytest.raises(RuntimeError, match="96-head is not installed"):
      await backend.send_command(module="C0", command="EP")

    with pytest.raises(RuntimeError, match="iSWAP is not installed"):
      await backend.send_command(module="C0", command="FI")

  @simulator_test
  async def test_iswap_park_with_resource_held_assertion(self):
    async with self._setup_lh():
      # Pre-set the state: iSWAP is holding a resource!
      self.backend._iswap_state.holding_resource = True

      with pytest.raises(RuntimeError, match="Cannot park iSWAP while holding a resource"):
        await self.backend.park_iswap()
