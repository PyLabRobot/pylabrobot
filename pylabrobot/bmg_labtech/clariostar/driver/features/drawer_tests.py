"""Drawer tests over a fake io, with replies recorded from a CLARIOstar Plus."""

import pytest

from pylabrobot.bmg_labtech.clariostar.driver.errors import CommandRefusedError
from pylabrobot.bmg_labtech.clariostar.driver.master_tests import (
  STATUS_BUSY,
  STATUS_DRAWER_OPEN,
  STATUS_IDLE,
  STATUS_PLATE_DETECTED,
  STATUS_QUERY_FRAME,
  FakeFTDI,
  make_driver,
)
from pylabrobot.bmg_labtech.protocol.errors import FrameError
from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.resources import Coordinate, PlateHolder
from pylabrobot.resources.corning.plates import cor_96_wellplate_2mL_Vb, cor_96_wellplate_360uL_Fb
from pylabrobot.testing.concurrency import AnyioTestBase

# Frames this driver exchanged with a CLARIOstar Plus (FTDI, firmware 1.35) on its first runs.
OPEN_DRAWER_FRAME = bytes.fromhex("02000e0c0301000000000000200d")
OPEN_DRAWER_REPLY = bytes.fromhex("0200180c012506240000030000000000000000c00001390d")
CLOSE_DRAWER_FRAME = bytes.fromhex("02000e0c03000000000000001f0d")
CLOSE_DRAWER_REPLY = bytes.fromhex("0200180c012506210000030000000000000000c00001360d")
# An open the reader refused while still busy, received by an earlier driver.
REFUSED_OPEN_DRAWER_BUSY = bytes.fromhex("0200180c013507260000ca0000000000000000c00002130d")

# Made-up locations: the real ones are not measured yet.
LOCATION_IN = Coordinate(10, 20, 30)
LOCATION_OUT = Coordinate(10, -200, 30)


def make_plate_holder() -> PlateHolder:
  return PlateHolder(
    name="plate_holder",
    size_x=127.76,
    size_y=85.48,
    size_z=0,
    pedestal_size_z=0,
    child_location=Coordinate.zero(),
  )


class TestDrawer(AnyioTestBase):
  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    await super()._enter_lifespan(stack)
    self.io = FakeFTDI()
    self.driver = make_driver(self.io)
    self.plate_holder = make_plate_holder()
    self.plate_holder.location = Coordinate.zero()
    drawer = self.driver.drawer
    drawer.plate_holder = self.plate_holder
    drawer.location_in = LOCATION_IN
    drawer.location_out = LOCATION_OUT

  async def test_open_waits_for_idle_and_open_then_updates_out(self):
    self.io.replies = [
      STATUS_IDLE,  # idle before the command is sent
      OPEN_DRAWER_REPLY,
      STATUS_BUSY,
      STATUS_DRAWER_OPEN,  # idle and open: done
      STATUS_DRAWER_OPEN,  # read for the model
    ]
    await self.driver.drawer.open()
    assert self.io.written == [STATUS_QUERY_FRAME, OPEN_DRAWER_FRAME] + [STATUS_QUERY_FRAME] * 3
    assert self.plate_holder.location == LOCATION_OUT

  async def test_close_updates_in(self):
    self.plate_holder.location = LOCATION_OUT
    self.io.replies = [STATUS_DRAWER_OPEN, CLOSE_DRAWER_REPLY, STATUS_IDLE, STATUS_IDLE]
    await self.driver.drawer.close()
    assert self.io.written == [STATUS_QUERY_FRAME, CLOSE_DRAWER_FRAME] + [STATUS_QUERY_FRAME] * 2
    assert self.plate_holder.location == LOCATION_IN

  async def test_close_with_a_plate_too_tall_sends_nothing(self):
    self.plate_holder.assign_child_resource(cor_96_wellplate_2mL_Vb("deep_well"))  # 43.5 mm
    with pytest.raises(ValueError):
      await self.driver.drawer.close()
    assert self.io.written == []

  async def test_close_with_a_plate_at_the_limit_closes(self):
    plate = cor_96_wellplate_360uL_Fb("plate")
    self.plate_holder.assign_child_resource(plate)
    self.driver.drawer._maximum_plate_size_z = plate.get_size_z()
    self.io.replies = [STATUS_DRAWER_OPEN, CLOSE_DRAWER_REPLY, STATUS_IDLE, STATUS_IDLE]
    await self.driver.drawer.close()
    assert self.io.written == [STATUS_QUERY_FRAME, CLOSE_DRAWER_FRAME] + [STATUS_QUERY_FRAME] * 2

  async def test_a_lid_counts_towards_the_height(self):
    plate = cor_96_wellplate_360uL_Fb("lidded", with_lid=True)
    self.plate_holder.assign_child_resource(plate)
    self.driver.drawer._maximum_plate_size_z = plate.get_size_z()  # the lid stands above it
    with pytest.raises(ValueError, match="with its lid"):
      await self.driver.drawer.close()
    assert self.io.written == []

  async def test_idle_but_not_open_times_out(self):
    self.driver.drawer.default_move_timeout = 0
    self.io.replies = [STATUS_IDLE, OPEN_DRAWER_REPLY, STATUS_IDLE, STATUS_IDLE]
    with pytest.raises(TimeoutError):
      await self.driver.drawer.open()
    assert self.plate_holder.location == LOCATION_IN

  async def test_refused_open_raises_and_updates_where_the_drawer_is(self):
    self.io.replies = [STATUS_IDLE, REFUSED_OPEN_DRAWER_BUSY, STATUS_IDLE]
    with pytest.raises(CommandRefusedError) as ctx:
      await self.driver.drawer.open()
    assert ctx.value.quit_codes[0] == 202
    assert self.plate_holder.location == LOCATION_IN

  async def test_failed_move_still_updates_where_the_drawer_is(self):
    truncated = CLOSE_DRAWER_REPLY[:10]
    self.io.replies = [STATUS_DRAWER_OPEN, truncated, truncated, truncated, STATUS_DRAWER_OPEN]
    with pytest.raises(FrameError):
      await self.driver.drawer.close()
    assert self.io.written == (
      [STATUS_QUERY_FRAME] + [CLOSE_DRAWER_FRAME] * 3 + [STATUS_QUERY_FRAME]
    )
    assert self.plate_holder.location == LOCATION_OUT

  async def test_failed_read_after_failed_move_keeps_the_move_error(self):
    self.io.replies = [STATUS_DRAWER_OPEN] + [CLOSE_DRAWER_REPLY[:10]] * 3
    with pytest.raises(FrameError):
      await self.driver.drawer.close()
    assert self.plate_holder.location == Coordinate.zero()

  async def test_without_locations_the_model_is_left_alone(self):
    self.driver.drawer.location_out = None
    self.io.replies = [STATUS_DRAWER_OPEN]
    assert await self.driver.drawer.sense_drawer_open()
    assert self.plate_holder.location == Coordinate.zero()

  async def test_plate_present(self):
    self.io.replies = [STATUS_PLATE_DETECTED]
    assert await self.driver.drawer.sense_plate_present()
