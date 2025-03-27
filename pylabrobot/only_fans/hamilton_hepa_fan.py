import asyncio

from pylabrobot.io.ftdi import FTDI

from .backend import FanBackend


class HamiltonHepaFan(FanBackend):
  """Backend for Hepa fan attachment on Hamilton Liquid Handler"""

  def __init__(self, vid=0x0856, pid=0xAC11, serial_number=None, device_id=None):
    self.vid = vid
    self.pid = pid
    self.serial_number = serial_number
    self.io = FTDI(device_id=device_id)

  async def setup(self):
    self.io.setup()
    self.io.set_baudrate(9600)
    self.io.set_line_property(8, 0, 0)  # 8N1
    self.io.set_latency_timer(16)
    self.io.set_flowctrl(512)
    self.io.set_dtr(True)
    self.io.set_rts(True)

    await self.send(b"\x55\xc1\x01\x02\x23\x4b")
    await self.send(b"\x55\xc1\x01\x08\x08\x6a")
    await self.send(b"\x55\xc1\x01\x09\x6a\x09")
    await self.send(b"\x55\xc1\x01\x0a\x2f\x4f")
    await self.send(b"\x15\x61\x01\x8a")

  async def turn_on(self, intensity):  # Speed is an integer percent between 0 and 100
    if int(intensity) != intensity or not 0 <= intensity <= 100:
      raise ValueError("Intensity is not an int value between 0 and 100")
    await self.send(b"\x35\x41\x01\xff\x75")  # turn on

    speed_array = [
      "55c10111007b",
      "55c101110279",
      "55c10111057e",
      "55c10111077c",
      "55c101110a71",
      "55c101110c77",
      "55c101110f74",
      "55c10111116a",
      "55c10111146f",
      "55c10111166d",
      "55c101111962",
      "55c101111c67",
      "55c101111e65",
      "55c10111215a",
      "55c101112358",
      "55c10111265d",
      "55c101112853",
      "55c101112b50",
      "55c101112d56",
      "55c10111304b",
      "55c101113249",
      "55c10111354e",
      "55c101113843",
      "55c101113a41",
      "55c101113d46",
      "55c101113f44",
      "55c101114239",
      "55c10111443f",
      "55c10111473c",
      "55c101114932",
      "55c101114c37",
      "55c101114f34",
      "55c10111512a",
      "55c10111542f",
      "55c10111562d",
      "55c101115922",
      "55c101115b20",
      "55c101115e25",
      "55c10111601b",
      "55c101116318",
      "55c10111651e",
      "55c101116813",
      "55c101116b10",
      "55c101116d16",
      "55c10111700b",
      "55c101117209",
      "55c10111750e",
      "55c10111770c",
      "55c101117a01",
      "55c101117c07",
      "55c101117f04",
      "55c1011182f9",
      "55c1011184ff",
      "55c1011187fc",
      "55c1011189f2",
      "55c101118cf7",
      "55c101118ef5",
      "55c1011191ea",
      "55c1011193e8",
      "55c1011196ed",
      "55c1011198e3",
      "55c101119be0",
      "55c101119ee5",
      "55c10111a0db",
      "55c10111a3d8",
      "55c10111a5de",
      "55c10111a8d3",
      "55c10111aad1",
      "55c10111add6",
      "55c10111afd4",
      "55c10111b2c9",
      "55c10111b5ce",
      "55c10111b7cc",
      "55c10111bac1",
      "55c10111bcc7",
      "55c10111bfc4",
      "55c10111c1ba",
      "55c10111c4bf",
      "55c10111c6bd",
      "55c10111c9b2",
      "55c10111cbb0",
      "55c10111ceb5",
      "55c10111d1aa",
      "55c10111d3a8",
      "55c10111d6ad",
      "55c10111d8a3",
      "55c10111dba0",
      "55c10111dda6",
      "55c10111e09b",
      "55c10111e299",
      "55c10111e59e",
      "55c10111e893",
      "55c10111ea91",
      "55c10111ed96",
      "55c10111ef94",
      "55c10111f289",
      "55c10111f48f",
      "55c10111f78c",
      "55c10111f982",
      "55c10111fc87",
      "5c10111fe85",
    ]

    await self.send(bytes.fromhex(speed_array[intensity]))  # set speed

  async def turn_off(self):
    await self.send(b"\x55\xc1\x01\x11\x00\x7b")

  async def stop(self):
    await self.io.stop()

  async def send(self, command):
    self.io.write(command)
    await asyncio.sleep(0.1)
    self.io.read(64)
