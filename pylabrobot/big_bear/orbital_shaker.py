import asyncio
import enum
import logging
from typing import Optional

from pylabrobot.io.serial import Serial

logger = logging.getLogger(__name__)


# Line terminator: commands and replies are carriage-return terminated.
CR = "\r"

# Reply returned when a command is rejected or the shaker cannot be reached.
ERROR_REPLY = "?:"

MIN_RPM = 60
MAX_RPM = 3570
MIN_ACCELERATION = 1
MAX_ACCELERATION = 10
MIN_SHAKERS = 1
MAX_SHAKERS = 16


class OrbitalShakerSequence(enum.IntEnum):
  """Rotation direction of a shake."""

  CW = 1
  CCW = 2


class BigBearError(Exception):
  """Exceptions raised by a BigBear orbital shaker."""

  def __init__(self, title: str, message: Optional[str] = None) -> None:
    self.title = title
    self.message = message

  def __str__(self) -> str:
    return f"{self.title}: {self.message}" if self.message else self.title


class BigBearOrbitalShaker:
  """BigBear orbital shaker.

  A daisy-chained orbital shaker: a single serial line drives up to 16 shaker
  positions ("nests"), each addressed independently by a 1-based id sent as a
  hexadecimal digit.

  Serial settings:
    9600 baud, 8 data bits, no parity, 1 stop bit, no handshake, "\\r"
    terminator.

  Commands (each addressed command is "@<id>" followed by the command chars,
  where <id> is the nest number in hex; a value-setting command is followed by
  a "@<id>?<c>" read-back query):
    U            enter daisy-chain mode (unaddressed)
    ~            report the number of shakers on the chain (unaddressed)
    @<id>V<rpm>  set speed (60..3570 rpm)
    @<id>A<n>    set acceleration (1..10, device scale)
    @<id>+       set direction clockwise
    @<id>-       set direction counter-clockwise
    @<id>G       start shaking
    @<id>S       stop shaking
    @<id>F       find home
    @<id>Q       poll status (reply is read back)
    @<id>Y/X/Z   serial-number fields
  A rejected command or a comms failure replies "?:".

  Not verified: has NOT been tested against hardware in PyLabRobot. A warning is
  emitted at setup.
  """

  def __init__(
    self,
    port: str,
    num_shakers: int = 1,
    timeout: float = 10.0,
    command_delay: float = 0.1,
    daisy_chain_settle: float = 5.0,
    stop_settle: float = 6.0,
  ):
    if not MIN_SHAKERS <= num_shakers <= MAX_SHAKERS:
      raise ValueError(f"num_shakers must be {MIN_SHAKERS}..{MAX_SHAKERS}")
    self.num_shakers = num_shakers
    self.command_delay = command_delay
    self.daisy_chain_settle = daisy_chain_settle
    self.stop_settle = stop_settle
    self.io = Serial(
      human_readable_device_name="BigBear Orbital Shaker",
      port=port,
      baudrate=9600,
      bytesize=8,
      parity="N",
      stopbits=1,
      timeout=timeout,
    )

  async def setup(self) -> None:
    logger.warning(
      "BigBearOrbitalShaker has NOT been tested against hardware in PyLabRobot. "
      "Please make a PR to remove this message if you have verified it on your hardware."
    )
    await self.io.setup()
    await self._enter_daisy_chain()
    await self._check_num_shakers()
    logger.info("[BigBear %s] connected: num_shakers=%d", self.io.port, self.num_shakers)

  async def stop(self) -> None:
    """Stop all shakers and close the serial connection."""
    try:
      await self.stop_all()
    finally:
      await self.io.stop()

  # === Command layer ===

  @staticmethod
  def _address(device_id: int) -> str:
    return "@" + format(device_id, "X")

  def _validate_device_id(self, device_id: int) -> None:
    if not 1 <= device_id <= self.num_shakers:
      raise ValueError(f"device_id must be 1..{self.num_shakers}")

  async def _send(self, command: str) -> None:
    """Write a command and its terminator, then pace the bus."""
    await self.io.write((command + CR).encode("ascii"))
    logger.debug("[BigBear] send: %s", command)
    await asyncio.sleep(self.command_delay)

  async def _read_reply(self, timeout: Optional[float] = None) -> str:
    """Read one CR-terminated reply, skipping empty lines.

    Returns the trimmed reply, or "" if the device sends nothing within the
    serial read timeout.
    """
    original = self.io.get_read_timeout()
    if timeout is not None:
      self.io.set_read_timeout(timeout)
    try:
      buf = bytearray()
      while True:
        char = await self.io.read(1)
        if char == b"":  # read timed out
          break
        if char == b"\r":
          if buf:
            break
          continue  # skip a bare terminator / empty line
        if char == b"\n":
          continue
        buf += char
    finally:
      if timeout is not None:
        self.io.set_read_timeout(original)
    reply = buf.decode("ascii", errors="replace").strip()
    logger.debug("[BigBear] recv: %s", reply)
    return reply

  async def _poll(self, device_id: int, read_timeout: Optional[float] = None) -> str:
    """Issue the status poll ("Q") for a nest and return its reply."""
    await self._send(self._address(device_id) + "Q")
    return await self._read_reply(timeout=read_timeout)

  # === Parameters ===

  async def _set_speed(self, device_id: int, rpm: int) -> None:
    if not MIN_RPM <= rpm <= MAX_RPM:
      raise ValueError(f"rpm must be {MIN_RPM}..{MAX_RPM}")
    address = self._address(device_id)
    await self._send(f"{address}V{rpm}")
    await self._send(f"{address}?V{rpm}")
    await self._read_reply()

  async def _set_acceleration(self, device_id: int, acceleration: int) -> None:
    if not MIN_ACCELERATION <= acceleration <= MAX_ACCELERATION:
      raise ValueError(f"acceleration must be {MIN_ACCELERATION}..{MAX_ACCELERATION}")
    address = self._address(device_id)
    await self._send(f"{address}A{acceleration}")
    await self._send(f"{address}?A{acceleration}")
    await self._read_reply()

  async def _set_sequence(self, device_id: int, sequence: OrbitalShakerSequence) -> None:
    address = self._address(device_id)
    direction = "+" if sequence is OrbitalShakerSequence.CW else "-"
    await self._send(f"{address}{direction}")
    await self._send(f"{address}?{direction}")
    await self._read_reply()

  # === Public API ===

  async def start_shaking(
    self,
    rpm: int,
    acceleration: int = 1,
    sequence: OrbitalShakerSequence = OrbitalShakerSequence.CW,
    device_id: int = 1,
  ) -> None:
    """Set the parameters for a nest and start it shaking.

    Args:
      rpm: shaking speed (60..3570).
      acceleration: acceleration on the device's 1..10 scale.
      sequence: rotation direction (clockwise or counter-clockwise).
      device_id: nest to address (1..num_shakers).
    """
    self._validate_device_id(device_id)
    await self._set_acceleration(device_id, acceleration)
    await self._set_speed(device_id, rpm)
    await self._set_sequence(device_id, sequence)
    await self._send(self._address(device_id) + "G")
    if await self._poll(device_id) == ERROR_REPLY:
      raise BigBearError(title="Starting the shaker failed", message=f"nest {device_id}")
    logger.info("[BigBear %s] nest %d shaking at %d rpm", self.io.port, device_id, rpm)

  async def stop_shaking(self, device_id: int = 1) -> None:
    """Stop shaking on a single nest."""
    self._validate_device_id(device_id)
    await self._send(self._address(device_id) + "S")
    if await self._poll(device_id, read_timeout=self.stop_settle) == ERROR_REPLY:
      raise BigBearError(title="Stopping the shaker failed", message=f"nest {device_id}")
    logger.info("[BigBear %s] nest %d stopped", self.io.port, device_id)

  async def stop_all(self) -> None:
    """Stop shaking on every nest on the chain."""
    for device_id in range(1, self.num_shakers + 1):
      await self._send(self._address(device_id) + "S")
      await self._poll(device_id, read_timeout=self.stop_settle)

  async def find_home(self, device_id: int = 1) -> None:
    """Home a single nest."""
    self._validate_device_id(device_id)
    await self._send(self._address(device_id) + "F")
    await self._poll(device_id)
    logger.info("[BigBear %s] nest %d homed", self.io.port, device_id)

  async def home_all(self) -> None:
    """Home every nest on the chain."""
    for device_id in range(1, self.num_shakers + 1):
      await self._send(self._address(device_id) + "F")
      await self._poll(device_id)

  async def get_serial_number(self, device_id: int = 1) -> str:
    """Read the serial-number fields (Y, X, Z) of a nest and join them.

    The reply format is device-defined; the raw fields are returned as-is.
    """
    self._validate_device_id(device_id)
    address = self._address(device_id)
    fields = []
    for command in ("Y", "X", "Z"):
      await self._send(address + command)
      fields.append(await self._read_reply())
    return "".join(fields)

  # === Setup helpers ===

  async def _enter_daisy_chain(self) -> None:
    await self._send("U")
    await asyncio.sleep(self.daisy_chain_settle)
    await self._read_reply()

  async def _check_num_shakers(self) -> None:
    await self._send("U")
    await self._send("~")
    reply = await self._read_reply()
    if str(self.num_shakers) not in reply:
      raise BigBearError(
        title="Shaker count mismatch",
        message=f"expected {self.num_shakers}, device reported {reply!r}",
      )
