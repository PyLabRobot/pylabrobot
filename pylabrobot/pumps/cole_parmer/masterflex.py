from typing import Optional

import serial # type: ignore

from pylabrobot.pumps.backend import PumpBackend


class Masterflex(PumpBackend):
  """ Backend for the Cole Parmer Masterflex L/S 07551-20 pump

  Documentation available at:
    - https://pim-resources.coleparmer.com/instruction-manual/a-1299-1127b-en.pdf
    - https://web.archive.org/web/20210924061132/https://pim-resources.coleparmer.com/
      instruction-manual/a-1299-1127b-en.pdf
  """

  def __init__(self, com_port: str):
    self.com_port = com_port
    self.ser: Optional[serial.Serial] = None

  async def setup(self):
    self.ser = serial.Serial(
      port=self.com_port,
      baudrate=4800,
      timeout=1,
      parity=serial.PARITY_ODD,
      stopbits=serial.STOPBITS_ONE,
      bytesize=serial.SEVENBITS
    )

    self.ser.write(b"\x05") # Enquiry; ready to send.
    self.ser.write(b"\x05P02\r")

  async def stop(self):
    assert self.ser is not None, "Pump not initialized"
    self.ser.close()
    self.ser = None

  def send_command(self, command: str):
    assert self.ser is not None
    command = "\x02P02" + command + "\x0d"
    self.ser.write(command.encode())
    return self.ser.read()

  def run_revolutions(self, num_revolutions: float):
    num_revolutions = round(num_revolutions, 2)
    cmd = f"V{num_revolutions}G"
    self.send_command(cmd)

  def run_continuously(self, speed: float):
    if speed == 0:
      self.halt()
      return

    direction = "+" if speed > 0 else "-"
    speed = int(abs(speed))
    cmd = f"S{direction}{speed}G0"
    self.send_command(cmd)

  def halt(self):
    self.send_command("H")
