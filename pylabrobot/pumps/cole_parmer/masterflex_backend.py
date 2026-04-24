try:
  import serial  # type: ignore

  HAS_SERIAL = True
except ImportError as e:
  HAS_SERIAL = False
  _SERIAL_IMPORT_ERROR = e

from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.io.serial import Serial
from pylabrobot.pumps.backend import PumpBackend


class MasterflexBackend(PumpBackend):
  """Backend for the Cole Parmer Masterflex L/S pump

  tested on:
  07551-20

  should be same as:
  07522-20
  07522-30
  07551-30
  07575-30
  07575-40

  Documentation available at:
    - https://pim-resources.coleparmer.com/instruction-manual/a-1299-1127b-en.pdf
    - https://web.archive.org/web/20210924061132/https://pim-resources.coleparmer.com/
      instruction-manual/a-1299-1127b-en.pdf
  """

  def __init__(self, com_port: str):
    if not HAS_SERIAL:
      raise RuntimeError(
        "pyserial is not installed. Install with: pip install pylabrobot[serial]. "
        f"Import error: {_SERIAL_IMPORT_ERROR}"
      )
    self.com_port = com_port
    self.io = Serial(
      port=self.com_port,
      baudrate=4800,
      timeout=1,
      parity=serial.PARITY_ODD,
      stopbits=serial.STOPBITS_ONE,
      bytesize=serial.SEVENBITS,
      human_readable_device_name="Masterflex Pump",
    )

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding):
    await super()._enter_lifespan(stack)
    await stack.enter_async_context(self.io)
    await self.io.write(b"\x05")  # Enquiry; ready to send.
    await self.io.write(b"\x05P02\r")

  def serialize(self):
    return {**super().serialize(), "com_port": self.com_port}

  async def send_command(self, command: str):
    command = "\x02P02" + command + "\x0d"
    await self.io.write(command.encode())
    return self.io.read()

  async def run_revolutions(self, num_revolutions: float):
    num_revolutions = round(num_revolutions, 2)
    cmd = f"V{num_revolutions}G"
    await self.send_command(cmd)

  async def run_continuously(self, speed: float):
    if speed == 0:
      self.halt()
      return

    direction = "+" if speed > 0 else "-"
    speed = int(abs(speed))
    cmd = f"S{direction}{speed}G0"
    await self.send_command(cmd)

  async def halt(self):
    await self.send_command("H")


# Deprecated alias with warning # TODO: remove mid May 2025 (giving people 1 month to update)
# https://github.com/PyLabRobot/pylabrobot/issues/466


class Masterflex:
  def __init__(self, *args, **kwargs):
    raise RuntimeError("`Masterflex` is deprecated. Please use `MasterflexBackend` instead.")
