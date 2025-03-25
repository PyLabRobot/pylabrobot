import unittest

from pylabrobot.liquid_handling.backends.hamilton.prep import (
  ParameterTypes,
  Prep,
  encode_data_fragment,
)
from pylabrobot.liquid_handling.liquid_handler import LiquidHandler
from pylabrobot.resources.celltreat.plates import CellTreat_96_wellplate_350ul_Ub
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.hamilton.hamilton_decks import PrepDeck
from pylabrobot.resources.hamilton.tip_racks import STF


class PrepTransportLayerTests(unittest.TestCase):
  ip_packet_data = bytes.fromhex(
    "2000063000000200040001000100010004BF020002101C0000000000010001000000"
  )
  harp_packet_data = bytes.fromhex("0200040001000100010004BF020002101C0000000000010001000000")
  hoi_packet_data = bytes.fromhex("010001000000")

  def test_decode_ip_packet(self):
    ip_packet = Prep.IpPacket.decode(self.ip_packet_data)
    assert ip_packet.size == 32
    assert ip_packet.protocol == 6
    assert ip_packet.version == (3, 0)
    assert ip_packet.options_length == 0
    assert ip_packet.options == None
    assert ip_packet.payload == self.harp_packet_data

  def test_encode_ip_packet(self):
    ip_packet = Prep.IpPacket(
      protocol=Prep.IpPacket.TransportableProtocol.Harp2,
      version=(3, 0),
      options=None,
      payload=self.harp_packet_data,
    )
    data = ip_packet.encode()
    assert data == self.ip_packet_data

  def test_decode_harp_packet(self):
    harp_packet = Prep.HarpPacket.decode(self.harp_packet_data)
    assert harp_packet.source == Prep.HarpPacket.HarpAddress((0x0002, 0x0004, 0x0001))
    assert harp_packet.destination == Prep.HarpPacket.HarpAddress((0x0001, 0x0001, 0xBF04))
    assert harp_packet.sequence_number == 2
    assert harp_packet.reserved_1 == 0
    assert harp_packet.protocol == 2
    assert harp_packet.action == Prep.HarpPacket.Action(0x10)
    assert harp_packet.length == 28
    assert harp_packet.options_length == 0
    assert harp_packet.options == []
    assert harp_packet.version == 0
    assert harp_packet.reserved_2 == 0
    assert harp_packet.payload == self.hoi_packet_data

  def test_encode_harp_packet(self):
    harp_packet = Prep.HarpPacket(
      source=Prep.HarpPacket.HarpAddress((0x0002, 0x0004, 0x0001)),
      destination=Prep.HarpPacket.HarpAddress((0x0001, 0x0001, 0xBF04)),
      sequence_number=2,
      reserved_1=0,
      protocol=Prep.HarpPacket.HarpTransportableProtocol.Hoi2,
      action=Prep.HarpPacket.Action(0x10),
      options=[],
      version=0,
      reserved_2=0,
      payload=self.hoi_packet_data,
    )
    data = harp_packet.encode()
    assert data == self.harp_packet_data

  def test_decode_hoi_packet(self):
    hoi_packet = Prep.HoiPacket2.decode(self.hoi_packet_data)
    assert hoi_packet.interface_id == 1
    assert hoi_packet.action == 0
    assert hoi_packet.action_id == 1
    assert hoi_packet.version == 0
    assert hoi_packet.number_of_fragments == 0

  def test_encode_hoi_packet(self):
    hoi_packet = Prep.HoiPacket2(
      interface_id=1, action=0, action_id=1, version=0, data_fragments=[]
    )
    data = hoi_packet.encode()
    assert data == self.hoi_packet_data

  def test_encode_data_fragment(self):
    assert encode_data_fragment(152.600, ParameterTypes.Real32Bit) == bytes.fromhex(
      "280004009A991843"
    )
    assert encode_data_fragment(False, ParameterTypes.Bool) == bytes.fromhex("170102000000")
    assert encode_data_fragment(True, ParameterTypes.Bool) == bytes.fromhex("170102000100")


class PrepFirmwareInterfaceTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.prep = Prep()
    self.prep.socket = unittest.mock.MagicMock()
    self.deck = PrepDeck()
    self.lh = LiquidHandler(backend=self.prep, deck=self.deck)

    self.tip_rack = STF(name="tr")
    self.deck.assign_child_resource(
      self.tip_rack, location=Coordinate(x=140.9, y=98.53, z=49.57)
    )  # spot 7
    self.plate = CellTreat_96_wellplate_350ul_Ub(name="plate")
    self.deck.assign_child_resource(self.plate, location=Coordinate(x=1.55, y=76.58, z=0))  # spot 3
    return await super().asyncSetUp()

  async def test_setup(self):
    data = bytes.fromhex(
      "440006300000020004000400010001000015070002134000000000000103010000021701020000001e001a001701020001002800040000808f4328000400000040401f000000"
    )
    self.prep._id = 0x6
    self.prep.socket.recv.return_value = bytes.fromhex(
      "200006300000010001000015020004000400010002041c0000000000010401000000"
    )

    await self.lh.setup()
    self.prep.socket.send.assert_called_with(data)

  async def test_park(self):
    data = bytes.fromhex("200006300000020004000400010001000015150002131C0000000000010303000000")
    self.prep._id = 0x14
    self.prep.socket.recv.return_value = bytes.fromhex(
      "200006300000010001000015020004000400090002041c0000000000010403000000"
    )
    await self.prep.park()
    self.prep.socket.send.assert_called_with(data)

  async def test_z_travel_configuration(self):
    data = bytes.fromhex(
      "28000630000002000400050001000100f0be0a00021324000000000001030d0000012000040003000000"
    )
    self.prep._id = 0x9
    self.prep.socket.recv.return_value = bytes.fromhex(
      "20000630000001000100f0be0200040005000a0002041c000000000001040d000000"
    )
    await self.prep.z_travel_configuration(unknown=3)
    self.prep.socket.send.assert_called_with(data)

  async def test_pick_up_tips(self):
    await self.test_setup()

    data = bytes.fromhex(
      "e2000630000002000700060000e00100001008000213de00000000000103090000071f0064001e002e001701020000002000040002000000280004009a991843280004007b5419432800040048e16b4228000400a4f08d421e002e001701020000002000040001000000280004009a991843280004007b5410432800040048e16b4228000400a4f08d422800040071bdf74228000400000070411e003000170102000000280004000000b443280004009a994f42200004000200000017010200010017010200000017010200000017010200000028000400000000002800040000007a43"
    )
    self.prep.socket.recv.return_value = bytes.fromhex(
      "20000630000000e001000010020007000600100002041c000000000001040c000000"
    )

    self.prep._id = 0x07
    await self.lh.pick_up_tips(self.tip_rack["C1", "D1"])
    self.prep.socket.send.assert_called_with(data)

  async def test_aspirate(self):
    await self.test_pick_up_tips()
    data = bytes.fromhex(
      "60010630000002000700060000e0010000100b0002135c01000000000103010000011f003c011e00380117010200000020000400020000001e0026001701020000002800040066667c41280004005c6f1643280004000000000028000400000000001e00640017010200000017010200010028000400c3f5a0c028000400a4f0c1422800040000000040280004000000c842280004000000c84228000400000000002800040033334b4028000400000000002800040000000000280004000000803f06000400000000001e002c0017010200000028000400a4f0bd4228000400a4f0c142170102000000280004000000004028000400000000001e002400170102000100280004000000000028000400000000000401020000002800040000007a431e00140017010200010017010200010028000400000090401e002400170102000100170102000000170102000000050002001e00050002001e00050002001400"
    )
    self.prep._id = 0x0A
    self.prep.socket.recv.return_value = bytes.fromhex(
      "20000630000000e0010000100200070006000b0002041c0000000000010401000000"
    )
    await self.lh.aspirate(self.plate["A1"], vols=[100])
    self.prep.socket.send.assert_called_with(data)

  async def test_dispense(self):
    await self.test_aspirate()
    data = bytes.fromhex(
      "50010630000002000700060000e0010000100d0002134c01000000000103050000011f002c011e00280117010200000020000400020000001e0026001701020000002800040066667c41280004005c6f16432800040000000000280004000000c8421e00640017010200000017010200010028000400c3f5a0c028000400a4f0c1422800040000000040280004000000c842280004000000c84228000400000000002800040033334b4028000400000000002800040000000000280004000000000006000400000000001e002c0017010200000028000400a4f0bd4228000400f628c642170102000000280004000000004028000400000000001e002400170102000100280004000000000028000400000000000401020000002800040000007a431e00140017010200010017010200010028000400000090401e0014001701020001000500020000002000040001000000"
    )
    self.prep._id = 0x0C
    self.prep.socket.recv.return_value = bytes.fromhex(
      "20000630000000e0010000100200070006000d0002041c0000000000010405000000"
    )
    await self.lh.dispense(self.plate["A1"], vols=[100])
    self.prep.socket.send.assert_called_with(data)

  async def test_drop_tips(self):
    await self.test_pick_up_tips()
    data = bytes.fromhex(
      "b0000630000002000700060000e00100001004000213ac000000000001030c0000041f0074001e0036001701020000002000040002000000280004009a991843280004007b5419432800040048e16b4228000400a4f08d4220000400000000001e0036001701020000002000040001000000280004009a991843280004007b5410432800040048e16b4228000400a4f08d4220000400000000002800040071bdf74228000400000020412800040000000000"
    )
    self.prep._id = 0x03
    self.prep.socket.recv.return_value = bytes.fromhex(
      "20000630000000e001000010020007000600040002041c000000000001040c000000"
    )
    await self.lh.return_tips()
    self.prep.socket.send.assert_called_with(data)

  async def test_move_z_up_to_safe(self):
    data = bytes.fromhex(
      "2c000630000002000700060000e0010000100500021328000000000001031c000001230008000100000002000000"
    )
    self.prep.socket.recv.return_value = bytes.fromhex(
      "20000630000000e001000010020007000600050002041c000000000001041c000000"
    )
    self.prep._id = 0x04
    await self.prep.move_z_up_to_safe(
      channels=[Prep.ChannelIndex.FrontChannel, Prep.ChannelIndex.RearChannel]
    )
    self.prep.socket.send.assert_called_with(data)

  async def test_move_to_position(self):
    self.prep.socket.recv.return_value = bytes.fromhex(
      "20000630000000e001000010020007000600060002041c000000000001041a000000"
    )
    self.prep._id = 0x05
    await self.prep.move_to_position(
      move_parameters=Prep.GantryMoveXYZParameters(
        default_values=False,
        gantry_x_position=100,
        axis_parameters=[
          Prep.ChannelYZMoveParameters(
            default_values=True,
            channel=Prep.ChannelIndex.RearChannel,
            y_position=185.2,
            z_position=100,
          ),
          Prep.ChannelYZMoveParameters(
            default_values=False,
            channel=Prep.ChannelIndex.FrontChannel,
            y_position=0,
            z_position=100,
          ),
        ],
      )
    )
    data = bytes.fromhex(
      "7a000630000002000700060000e0010000100600021376000000000001031a0000011e005600170102000000280004000000c8421f0044001e001e0017010200010020000400020000002800040033333943280004000000c8421e001e0017010200000020000400010000002800040000000000280004000000c842"
    )
    self.prep.socket.send.assert_called_with(data)

  async def test_move_to_position_via_lane(self):
    self.prep.socket.recv.return_value = bytes.fromhex(
      "20000630000000e001000010020007000600070002041c000000000001041b000000"
    )
    self.prep._id = 0x06
    await self.prep.move_to_position_via_lane(
      move_parameters=Prep.GantryMoveXYZParameters(
        default_values=False,
        gantry_x_position=152.6,
        axis_parameters=[
          Prep.ChannelYZMoveParameters(
            default_values=True,
            channel=Prep.ChannelIndex.RearChannel,
            y_position=153.33,
            z_position=70.97,
          ),
          Prep.ChannelYZMoveParameters(
            default_values=False,
            channel=Prep.ChannelIndex.FrontChannel,
            y_position=144.33,
            z_position=70.97,
          ),
        ],
      )
    )
    data = bytes.fromhex(
      "7a000630000002000700060000e0010000100700021376000000000001031b0000011e005600170102000000280004009a9918431f0044001e001e001701020001002000040002000000280004007b54194328000400a4f08d421e001e001701020000002000040001000000280004007b54104328000400a4f08d42"
    )
    self.prep.socket.send.assert_called_with(data)

  async def test_move_channel(self):
    # await self.lh.move_channel_x()
    pass
