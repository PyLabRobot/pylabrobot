import asyncio
import serial
import time
from typing import Optional
from pylabrobot.sealing.backend import SealerBackend


class A4S(SealerBackend):
  def __init__(self, port: str, timeout = 10) -> None:
    self.port = port
    self.dev: Optional[serial.Serial] = None
    self.timeout = timeout

  async def setup(self):
    self.dev = serial.Serial(
      port=self.port,
      baudrate=19200,
      bytesize=serial.EIGHTBITS,
      parity=serial.PARITY_NONE,
      stopbits=serial.STOPBITS_ONE,
      xonxoff=False,
      rtscts=False,
      dsrdtr=False,
    )
    await self.system_reset()

  async def stop(self):
    self.dev.close()

  async def send_command(self, command: str):
    self.dev.write(command.encode())
    await asyncio.sleep(0.1)

    start = time.time()
    r, x = b"", b""
    # TODO: just reads a bunch of bytes??
    # while x != b"" or (len(r) == 0 and x == b""):
    #   r += x
    #   x = self.dev.read()
    #   print("  read char", x)
    #   if time.time() - start > self.timeout:
    #     raise TimeoutError("Timeout while waiting for response")

    return r

  async def seal(self, temperature: int, duration: float):
    await self.set_temperature(temperature)
    await self.set_time(duration)
    return await self.send_command("*00GS=zz!")  # Command to conduct seal action

  async def set_temperature(self, degree: int):
    if not (0 <= degree <= 999):
      raise ValueError("Temperature out of range. Please enter a value between 0 and 999.")
    command = f"*00DH={degree:03d}zz!"
    return await self.send_command(command)

  async def set_time(self, seconds: float):
    deciseconds = seconds * 10
    if not (0 <= deciseconds <= 9999):
      raise ValueError("Time out of range. Please enter a value between 0 and 9999.")
    command = f"*00DT={deciseconds:04d}zz!"
    return await self.send_command(command)

  async def open_shuttle(self):
    return await self.send_command("*00MO=zz!")

  async def close_shuttle(self):
    return await self.send_command("*00MC=zz!")

  async def system_reset(self):
    return await self.send_command("*00SR=zz!")
