import unittest
import unittest.mock
from unittest.mock import call

from pylabrobot.liquid_handling import LiquidHandler
from pylabrobot.liquid_handling.backends.tecan.EVO import EVO, LiHa, RoMa
from pylabrobot.liquid_handling.standard import (
  Pickup,
  Aspiration,
  Dispense,
  Move,
  GripDirection
)
from pylabrobot.resources import (
  Coordinate,
  EVO150Deck,
  DeepWell_96_Well,
  DiTi_100ul_Te_MO,
  DiTi_SBS_3_Pos_MCA96,
  MP_3Pos_PCR
)


class EVOTests(unittest.IsolatedAsyncioTestCase):
  """ Test that the EVO backend is calling `send_command` correctly. """

  def setUp(self) -> None:
    super().setUp()

    # mock the EVO
    self.evo = EVO(diti_count=8)
    self.evo.send_command = unittest.mock.AsyncMock() # type: ignore[method-assign]
    async def send_command(module, command, params=None): # pylint: disable=unused-argument
      if command == "RPX": # report_x_param
        return {"data": [9000]} # park position roma
      if command == "RPY": # report_y_param
        return {"data": [90]} # park position roma
      if command == "RPZ": # report_z_param
        return {"data": [2000]} # dummy value
      return {"data": None}
    self.evo.send_command.side_effect = send_command # type: ignore[method-assign]

    self.deck = EVO150Deck()
    self.lh = LiquidHandler(backend=self.evo, deck=self.deck)

    # setup
    self.evo.setup = unittest.mock.AsyncMock() # type: ignore[method-assign]
    # pylint: disable=protected-access
    self.evo._num_channels = 8
    self.evo._x_range = 2000 # TODO: override report_x_param
    self.evo._y_range = 2000
    self.evo._z_range = 2000
    self.evo._roma_connected = True
    self.evo._liha_connected = True
    self.evo.liha = LiHa(self.evo, EVO.LIHA)
    self.evo.roma = RoMa(self.evo, EVO.ROMA)

    # deck setup
    self.tr_carrier = DiTi_SBS_3_Pos_MCA96(name="tip_rack_carrier")
    self.tr_carrier[0] = self.tr = DiTi_100ul_Te_MO(name="tip_rack")
    self.deck.assign_child_resource(self.tr_carrier, rails=10)

    self.plate_carrier = MP_3Pos_PCR(name="plate_carrier")
    self.plate_carrier[0] = self.plate = DeepWell_96_Well(name="plate")
    self.deck.assign_child_resource(self.plate_carrier, rails=16)

    self.evo.send_command.reset_mock()

  async def test_pick_up_tip(self):
    op = Pickup(
      resource=self.tr.get_item("A1"),
      offset=Coordinate.zero(),
      tip=self.tr.get_tip("A1")
    )
    await self.evo.pick_up_tips([op], use_channels=[0])

    self.evo.send_command.assert_has_calls([ # type: ignore[attr-defined]
      call(module="C5", command="SHZ", params=[2000, 2000, 2000, 2000, 2000, 2000, 2000, 2000]),
      call(module="C5", command="RPX", params=[0]),
      call(module="C5", command="PAA",
           params=[2380, 1991, 90, 2000, 2000, 2000, 2000, 2000, 2000, 2000, 2000]),
      call(module="C5", command="PVL", params=[0, None, None, None, None, None, None, None]),
      call(module="C5", command="SEP", params=[420, None, None, None, None, None, None, None]),
      call(module="C5", command="PPR", params=[30, None, None, None, None, None, None, None]),
      call(module="C5", command="AGT", params=[1, 768, 210, 0])
    ])

  # TODO: add Trash to Tecan deck or allow dropping tips in a non-Trash area
  # async def test_drop_tip(self):
  #   op = Drop(
  #     resource=self.deck.get_trash_area(),
  #     offset=None,
  #     tip=self.tr.get_tip("A1")
  #   )
  #   await self.evo.drop_tips([op], use_channels=[0])
  #   self.evo.send_command.assert_has_calls([])

  async def test_aspirate(self):
    op = Aspiration(
      resource=self.plate.get_item("A1"),
      offset=Coordinate.zero(),
      tip=self.tr.get_tip("A1"),
      volume=100,
      flow_rate=100,
      liquid_height=10,
      blow_out_air_volume=0,
      liquids=[(None, 100)]
    )
    await self.evo.aspirate([op], use_channels=[0])
    self.evo.send_command.assert_has_calls([ # type: ignore[attr-defined]
      call(module="C5", command="SHZ", params=[2000, 2000, 2000, 2000, 2000, 2000, 2000, 2000]),
      call(module="C5", command="RPX", params=[0]),
      call(module="C5", command="PAA",
           params=[3829, 2051, 90, 1455, 2000, 2000, 2000, 2000, 2000, 2000, 2000]),
      call(module="C5", command="PVL", params=[0, None, None, None, None, None, None, None]),
      call(module="C5", command="SEP", params=[840, None, None, None, None, None, None, None]),
      call(module="C5", command="PPR", params=[30, None, None, None, None, None, None, None]),
      call(module="C5", command="SDM", params=[7, 1]),
      call(module="C5", command="SSL", params=[600, None, None, None, None, None, None, None]),
      call(module="C5", command="SDL", params=[40, None, None, None, None, None, None, None]),
      call(module="C5", command="STL", params=[1375, None, None, None, None, None, None, None]),
      call(module="C5", command="SML", params=[985, 2000, 2000, 2000, 2000, 2000, 2000, 2000]),
      call(module="C5", command="SBL", params=[20, None, None, None, None, None, None, None]),
      call(module="C5", command="SHZ", params=[1455, 1455, 1455, 1455, 1455, 1455, 1455, 1455]),
      call(module="C5", command="MDT", params=[1, None, None, None, 30, 0, 0, 0, 0, 0, 0, 0]),
      call(module="C5", command="SHZ", params=[2000, 2000, 2000, 2000, 2000, 2000, 2000, 2000]),
      call(module="C5", command="SSZ", params=[30, None, None, None, None, None, None, None]),
      call(module="C5", command="SEP", params=[1200, None, None, None, None, None, None, None]),
      call(module="C5", command="STZ", params=[-30, None, None, None, None, None, None, None]),
      call(module="C5", command="MTR", params=[626, None, None, None, None, None, None, None]),
      call(module="C5", command="SSZ", params=[200, None, None, None, None, None, None, None]),
      call(module="C5", command="MAZ", params=[1375, None, None, None, None, None, None, None]),
      call(module="C5", command="PVL", params=[0, None, None, None, None, None, None, None]),
      call(module="C5", command="SEP", params=[840, None, None, None, None, None, None, None]),
      call(module="C5", command="PPR", params=[60, None, None, None, None, None, None, None])
    ])

  async def test_dispense(self):
    op = Dispense(
      resource=self.plate.get_item("A1"),
      offset=Coordinate.zero(),
      tip=self.tr.get_tip("A1"),
      volume=100,
      flow_rate=100,
      liquid_height=10,
      blow_out_air_volume=0,
      liquids=[(None, 100)]
    )
    await self.evo.dispense([op], use_channels=[0])
    self.evo.send_command.assert_has_calls([ # type: ignore[attr-defined]
      call(module="C5", command="RPX", params=[0]),
      call(module="C5", command="PAA",
           params=[3829, 2051, 90, 1355, 2000, 2000, 2000, 2000, 2000, 2000, 2000]),
      call(module="C5", command="SEP", params=[7200, None, None, None, None, None, None, None]),
      call(module="C5", command="SPP", params=[4800, None, None, None, None, None, None, None]),
      call(module="C5", command="STZ", params=[0, None, None, None, None, None, None, None]),
      call(module="C5", command="MTR", params=[-716, None, None, None, None, None, None, None])
    ])

  async def test_move_resource(self):
    op = Move(
      resource=self.plate,
      destination=self.plate_carrier[0].get_absolute_location(),
      resource_offset=Coordinate.zero(),
      destination_offset=Coordinate.zero(),
      pickup_distance_from_top=13.2,
      get_direction=GripDirection.FRONT,
      put_direction=GripDirection.FRONT,
    )
    await self.evo.move_resource(op)
    self.evo.send_command.assert_has_calls([  # type: ignore[attr-defined]
      call(module="C1", command="RPZ", params=[5]),
      call(module="C1", command="SSM", params=[1]),
      call(module="C1", command="SFX", params=[10000, None]),
      call(module="C1", command="SFY", params=[5000, 1500]),
      call(module="C1", command="SFZ", params=[1300, None]),
      call(module="C1", command="SFR", params=[5000, 1500]),
      call(module="C1", command="RPX", params=[0]),
      call(module="C1", command="SAA", params=[1, 5561, 2368, 1054, 900, None, 1, 0, 0]),
      call(module="C1", command="AAC"),
      call(module="C1", command="RPX", params=[0]),
      call(module="C1", command="SSM", params=[0]),
      call(module="C1", command="PAG", params=[900]),
      call(module="C1", command="STW", params=[1, 0, 0, 0, 135, 0]),
      call(module="C1", command="RPX", params=[0]),
      call(module="C1", command="SAA", params=[1, 5561, 2368, 687, 900, None, 1, 0, 1]),
      call(module="C1", command="RPX", params=[0]),
      call(module="C1", command="SAA", params=[1, 5561, 2368, 59, 900, None, 1, 0, 0]),
      call(module="C1", command="AAC"),
      call(module="C1", command="RPX", params=[0]),
      call(module="C1", command="SFY", params=[3500, 1000]),
      call(module="C1", command="SFR", params=[2000, 600]),
      call(module="C1", command="SGG", params=[100, 75, None]),
      call(module="C1", command="AGR", params=[754]),
      call(module="C1", command="STW", params=[1, 0, 0, 0, 135, 0]),
      call(module="C1", command="STW", params=[2, 0, 0, 0, 53, 0]),
      call(module="C1", command="STW", params=[3, 0, 0, 0, 55, 0]),
      call(module="C1", command="STW", params=[4, 45, 0, 0, 0, 0]),
      call(module="C1", command="RPX", params=[0]),
      call(module="C1", command="SAA", params=[1, 5561, 2368, 59, 900, None, 1, 0, 1]),
      call(module="C1", command="RPX", params=[0]),
      call(module="C1", command="SAA", params=[2, 5561, 2368, 687, 900, None, 1, 0, 2]),
      call(module="C1", command="RPX", params=[0]),
      call(module="C1", command="SAA", params=[3, 5561, 2368, 1054, 900, None, 1, 0, 3]),
      call(module="C1", command="RPX", params=[0]),
      call(module="C1", command="SAA", params=[4, 5561, 2368, 1054, 900, None, 1, 0, 4]),
      call(module="C1", command="RPX", params=[0]),
      call(module="C1", command="SAA", params=[5, 5561, 2368, 687, 900, None, 1, 0, 3]),
      call(module="C1", command="RPX", params=[0]),
      call(module="C1", command="SAA", params=[6, 5561, 2368, 59, 900, None, 1, 0, 0]),
      call(module="C1", command="AAC"),
      call(module="C1", command="RPX", params=[0]),
      call(module="C1", command="PAG", params=[900]),
      call(module="C1", command="SFY", params=[5000, 1500]),
      call(module="C1", command="SFR", params=[5000, 1500]),
      call(module="C1", command="RPX", params=[0]),
      call(module="C1", command="SAA", params=[1, 5561, 2368, 59, 900, None, 1, 0, 1]),
      call(module="C1", command="RPX", params=[0]),
      call(module="C1", command="SAA", params=[2, 5561, 2368, 687, 900, None, 1, 0, 2]),
      call(module="C1", command="RPX", params=[0]),
      call(module="C1", command="SAA", params=[3, 5561, 2368, 1054, 900, None, 1, 0, 0]),
      call(module="C1", command="AAC"),
      call(module="C1", command="RPX", params=[0]),
      call(module="C1", command="SFY", params=[3500, 1000]),
      call(module="C1", command="SFR", params=[2000, 600])
    ])
