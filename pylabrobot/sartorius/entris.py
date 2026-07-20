import asyncio
import logging
import time
from typing import Optional

from pylabrobot.io.serial import Serial

logger = logging.getLogger(__name__)


ESC = "\x1b"

# Device error codes. A status line beginning with "Stat" whose remainder
# matches one of these keys is an error condition.
DEVICE_ERRORS = {
  "APP.ERR": "Invalid weight value (applied weight too low, negative, or no sample on pan).",
  "DIS.ERR": "Value cannot be shown in the display (incompatible with the display format).",
  "High": "Overloaded: maximum weighing capacity exceeded.",
  "ERR 55": "Overloaded: maximum weighing capacity exceeded.",
  "Low": "Weighing converter modulation too low (no pan, weight removed, or a system fault).",
  "ERR 54": "Weighing converter modulation too low (no pan, weight removed, or a system fault).",
  "COMM.ERR": "No weight values received (no communication between control module and weigh cell).",
  "PRT.ERR": "[Print] key is locked (data interface set to xBPI mode).",
  "SYS.ERR": "System data is faulty.",
  "ERR 02": "Cannot calibrate: zero-point error (device not zeroed, or loaded).",
  "ERR 10": "Taring not possible: tare memory reserved by an application program.",
  "ERR 11": "Weight value cannot be saved to tare memory (value is negative or zero).",
}


class SartoriusError(Exception):
  """Exceptions raised by a Sartorius Entris II balance."""

  def __init__(self, title: str, message: Optional[str] = None) -> None:
    self.title = title
    self.message = message

  def __str__(self) -> str:
    return f"{self.title}: {self.message}" if self.message else self.title


class SartoriusEntris2:
  """Sartorius Entris II balance.

  Interface spec: https://www.sartorius.hr/media/dypfvdsn/entris-ii-technical-note-en-sartorius.pdf
  (archived: https://archive.vn/NU6DW)

  Serial settings:
    9600 baud, 8 data bits, ODD parity, 1 stop bit, no handshake, "\\r\\n"
    terminator.

  Commands (SBI control commands, format "<Esc> <char> CR LF"):
    <Esc>P     print current weight value
    <Esc>T     tare (Zero/Tara command)
    <Esc>V     zero (Key ZERO)
    <Esc>x1_   query balance model
    <Esc>x2_   query serial number
    The trailing "_" in x1_/x2_ is a literal underscore. <Esc> is the escape
    control byte 0x1B; per the spec it is optional, and CR LF terminates.

  Not verified: has NOT been tested against hardware in PyLabRobot. A warning
  is emitted at setup.
  """

  def __init__(
    self,
    port: Optional[str] = None,
    vid: int = 0x0403,
    pid: int = 0x6001,
    tare_settle_s: float = 2.0,
  ):
    self.tare_settle_s = tare_settle_s
    self.serial_number: Optional[str] = None
    self.io = Serial(
      human_readable_device_name="Sartorius Entris II",
      port=port,
      vid=vid,
      pid=pid,
      baudrate=9600,
      bytesize=8,
      parity="O",
      stopbits=1,
      timeout=15,
    )

  async def setup(self) -> None:
    logger.warning(
      "SartoriusEntris2 has NOT been tested against hardware in PyLabRobot. "
      "Please make a PR to remove this message if you have verified it on your hardware."
    )
    await self.io.setup()
    # Bring online: send <ESC>P up to twice, tolerating errors.
    for _ in range(2):
      try:
        await self.send_command("P")
        break
      except (SartoriusError, TimeoutError):
        continue
    self.serial_number = await self.send_command("x2_")
    logger.info("[Sartorius %s] connected: serial_number=%s", self.io.port, self.serial_number)

  async def stop(self) -> None:
    await self.io.stop()

  # === Command layer ===

  def _frame(self, token: str) -> bytes:
    """Build the on-wire bytes for a command token: <Esc> <token> CR LF."""
    return (ESC + token + "\r\n").encode("ascii")

  async def send_command(self, token: str, timeout: int = 15, read_reply: bool = True) -> str:
    """Send a command token and return the trimmed, de-spaced reply.

    Args:
      token: command token, e.g. "P", "T", "x1_" (ESC prefix added here).
      timeout: seconds to wait for a response.
      read_reply: if False, do not read a reply (used for tare, which sends no
        parseable line).
    """

    await self.io.reset_input_buffer()
    frame = self._frame(token)
    logger.debug("[Sartorius] send: %s", frame)
    await self.io.write(frame)

    if not read_reply:
      return ""

    raw_response = b""
    timeout_time = time.time() + timeout
    while True:
      raw_response = await self.io.readline()
      await asyncio.sleep(0.001)
      if time.time() > timeout_time:
        raise TimeoutError("Timeout while waiting for response from scale.")
      if raw_response != b"":
        break
    logger.debug("[Sartorius] recv: %s", raw_response)
    response = raw_response.decode("ascii", errors="replace").strip()
    if not response:
      raise SartoriusError(title="No response from device", message=f"for command {token!r}")

    self._raise_for_device_error(response)
    return response.replace(" ", "")

  @staticmethod
  def _raise_for_device_error(response: str) -> None:
    """Raise SartoriusError if the reply is a "Stat" error status line."""
    if response.startswith("Stat"):
      remainder = response.replace("Stat", "").strip()
      if any(k in remainder for k in ("ERR", "High", "Low")):
        raise SartoriusError(
          title=f"Device error: {remainder}",
          message=DEVICE_ERRORS.get(remainder),
        )

  @staticmethod
  def _parse_weight(response: str) -> float:
    """Extract a float weight from a print reply.

    Keeps only [0-9 . + -] then parses, rejecting more than one decimal point.
    """
    filtered = "".join(c for c in response if c.isdigit() or c in ".+-")
    if filtered.count(".") > 1 or filtered in ("", "+", "-"):
      raise SartoriusError(title="Invalid weight value", message=repr(response))
    try:
      return float(filtered)
    except ValueError as exc:
      raise SartoriusError(title="Invalid weight value", message=repr(response)) from exc

  # === Public API ===

  async def read_weight(self) -> float:
    """Read the current weight in grams (<ESC>P)."""
    weight = self._parse_weight(await self.send_command("P"))
    logger.info("[Sartorius %s] weight read: weight_g=%s", self.serial_number, weight)
    return weight

  async def tare(self) -> None:
    """Tare the balance (<ESC>T)."""
    await self.send_command("T", read_reply=False)
    await asyncio.sleep(self.tare_settle_s)
    logger.info("[Sartorius %s] tared", self.serial_number)

  async def zero(self) -> None:
    """Zero the balance (<Esc>V, Key ZERO)."""
    await self.send_command("V", read_reply=False)
    await asyncio.sleep(self.tare_settle_s)
    logger.info("[Sartorius %s] zeroed", self.serial_number)

  async def get_model(self) -> str:
    """Query the balance model string (<ESC>x1_)."""
    return await self.send_command("x1_")

  async def get_serial_number(self) -> str:
    """Query the balance serial number (<ESC>x2_)."""
    return await self.send_command("x2_")
