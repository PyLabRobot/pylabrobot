import asyncio
import json

from asyncio import StreamReader, StreamWriter

from typing import Union, Optional

from pylabrobot.arms.backend import RoboticArmBackend


class   WaveshareArm(RoboticArmBackend): # TODO: add logging for debugging
  def __init__(self, host: str, port: Union[int, str]) -> None:
    self.host = host
    self.port = port
    self.reader: Optional[StreamReader] = None
    self.writer: Optional[StreamWriter] = None

  async def setup(self):
    self.reader, self.writer = await asyncio.open_connection(self.host, self.port)

  async def send_command(self, command: dict):
    assert self.writer is not None, "Connection not established"
    assert self.reader is not None, "Connection not established"
    self.writer.write(json.dumps(command).encode())
    await self.writer.drain()

    data = await self.reader.read(4096)
    return json.loads(data.decode())

  async def move(self, x: int, y: int, z: int, grip_angle=3.14):
    command = {
      "type": "move_xyzt",
      "x": x,
      "y": y,
      "z": z,
      "grip_angle": grip_angle
    }
    return await self.send_command(command)

  async def move_interpolate(self, x, y, z, grip_angle=3.14, speed=0.25):
    command = {
      "type": "move_xyzt_interp",
      "x": x,
      "y": y,
      "z": z,
      "grip_angle": grip_angle,
      "speed": speed
    }
    return await self.send_command(command)

  async def stop(self):
    if self.writer:
      self.writer.close()
      await self.writer.wait_closed()
