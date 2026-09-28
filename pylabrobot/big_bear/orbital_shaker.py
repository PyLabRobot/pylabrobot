import logging
from contextlib import asynccontextmanager
from functools import partial
from typing import AsyncIterator, Dict, Literal, Optional

import anyio

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan, attempt_in_order
from pylabrobot.io.serial import Serial

Sequence = Literal["CW", "CCW"]

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


class BigBearError(Exception):
  """Exceptions raised by a BigBear orbital shaker."""

  def __init__(self, title: str, message: Optional[str] = None) -> None:
    self.title = title
    self.message = message

  def __str__(self) -> str:
    return f"{self.title}: {self.message}" if self.message else self.title


class BigBearOrbitalShaker(HasLifespan):
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

  ::

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
    super().__init__()
    self.num_shakers = num_shakers
    self.command_delay = command_delay
    self.timeout = timeout
    self.daisy_chain_settle = daisy_chain_settle
    self.stop_settle = stop_settle
    self.io = Serial(
      human_readable_device_name="BigBear Orbital Shaker",
      port=port,
      baudrate=9600,
      bytesize=8,
      parity="N",
      stopbits=1,
    )
    self._shaking_transition_lock = anyio.Lock()
    self._shaking_active: Dict[int, bool] = {
      device_id: False for device_id in range(1, num_shakers + 1)
    }
    self._shaking_state_unknown: Dict[int, bool] = {
      device_id: False for device_id in range(1, num_shakers + 1)
    }
    self._shaking_scope_tokens: Dict[int, object] = {}

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    """Own the serial transport and bring up the daisy chain for one session."""
    await super()._enter_lifespan(stack)
    logger.warning(
      "BigBearOrbitalShaker has NOT been tested against hardware in PyLabRobot. "
      "Please make a PR to remove this message if you have verified it on your hardware."
    )
    await stack.enter_async_context(self.io.lifespan())
    stack.push_shielded_async_callback(self._stop_all_for_exit)
    await self._enter_daisy_chain()
    await self._check_num_shakers()
    logger.info("[BigBear %s] connected: num_shakers=%d", self.io.port, self.num_shakers)

  async def _stop_all_for_exit(self) -> None:
    """Stop every configured nest, attempting all even after an earlier failure."""
    await attempt_in_order(
      *(partial(self._force_stop_nest, device_id) for device_id in range(1, self.num_shakers + 1))
    )

  # === Command layer ===

  @staticmethod
  def _address(device_id: int) -> str:
    return "@" + format(device_id, "X")

  def _validate_device_id(self, device_id: int) -> None:
    if not 1 <= device_id <= self.num_shakers:
      raise ValueError(f"device_id must be 1..{self.num_shakers}")

  async def _write(self, command: str) -> None:
    """Write a command and its terminator."""
    await self.io.write((command + CR).encode("ascii"))
    logger.debug("[BigBear] send: %s", command)

  async def _send(self, command: str) -> None:
    """Write a command and its terminator, then pace the bus."""
    await self._write(command)
    await anyio.sleep(self.command_delay)

  async def _read_reply(self, timeout: Optional[float] = None) -> str:
    """Read one CR-terminated reply, skipping empty lines.

    Returns the trimmed reply, or "" if the device sends nothing within the
    serial read timeout.
    """
    buf = bytearray()
    with anyio.move_on_after(self.timeout if timeout is None else timeout):
      while True:
        char = await self.io.read(1)
        if char == b"\r":
          if buf:
            break
          continue  # skip a bare terminator / empty line
        if char == b"\n":
          continue
        buf += char
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

  async def _set_sequence(self, device_id: int, sequence: Sequence) -> None:
    if sequence not in ("CW", "CCW"):
      raise ValueError('sequence must be "CW" or "CCW"')
    address = self._address(device_id)
    direction = "+" if sequence == "CW" else "-"
    await self._send(f"{address}{direction}")
    await self._send(f"{address}?{direction}")
    await self._read_reply()

  # === Public API ===

  async def start_shaking(
    self,
    rpm: int,
    acceleration: int = 1,
    sequence: Sequence = "CW",
    device_id: int = 1,
  ) -> None:
    """Set the parameters for a nest and start it shaking.

    Args:
      rpm: shaking speed (60..3570).
      acceleration: acceleration on the device's 1..10 scale.
      sequence: rotation direction, "CW" (clockwise) or "CCW" (counter-clockwise).
      device_id: nest to address (1..num_shakers).
    """
    self._validate_device_id(device_id)
    async with self._shaking_transition_lock:
      if device_id in self._shaking_scope_tokens:
        raise RuntimeError(f"Cannot start shaking on nest {device_id} while its scope is active")
      await self._start_shaking_transition(
        rpm, acceleration=acceleration, sequence=sequence, device_id=device_id
      )

  async def _start_shaking_transition(
    self,
    rpm: int,
    acceleration: int,
    sequence: Sequence,
    device_id: int,
    rollback_on_failure: bool = False,
  ) -> None:
    """Start one nest while its caller holds the command-transition lock."""
    await self._set_acceleration(device_id, acceleration)
    await self._set_speed(device_id, rpm)
    await self._set_sequence(device_id, sequence)
    address = self._address(device_id)
    started = False
    try:
      await anyio.lowlevel.checkpoint()
      with anyio.CancelScope(shield=True):
        await self._write(address + "G")
        started = True
        self._shaking_active[device_id] = True
        self._shaking_state_unknown[device_id] = True
      await anyio.sleep(self.command_delay)
      if await self._poll(device_id) == ERROR_REPLY:
        raise BigBearError(title="Starting the shaker failed", message=f"nest {device_id}")
    except anyio.get_cancelled_exc_class():
      if started:
        with anyio.CancelScope(shield=True):
          await self._stop_scoped_shaking(device_id)
      raise
    except BaseException:
      if started and rollback_on_failure:
        with anyio.CancelScope(shield=True):
          await self._stop_scoped_shaking(device_id)
      raise
    self._shaking_active[device_id] = True
    self._shaking_state_unknown[device_id] = False
    logger.info("[BigBear %s] nest %d shaking at %d rpm", self.io.port, device_id, rpm)

  async def stop_shaking(self, device_id: int = 1) -> None:
    """Stop shaking on a single nest."""
    self._validate_device_id(device_id)
    async with self._shaking_transition_lock:
      if device_id in self._shaking_scope_tokens:
        raise RuntimeError(f"Cannot stop shaking on nest {device_id} while its scope is active")
      await self._stop_scoped_shaking(device_id)
    logger.info("[BigBear %s] nest %d stopped", self.io.port, device_id)

  async def _stop_scoped_shaking(self, device_id: int) -> None:
    """Stop one tracked nest and retain retryable state if cleanup fails."""
    try:
      await self._stop_nest(device_id)
    except BaseException:
      self._shaking_active[device_id] = True
      self._shaking_state_unknown[device_id] = True
      raise
    self._shaking_active[device_id] = False
    self._shaking_state_unknown[device_id] = False

  async def _stop_nest(self, device_id: int) -> None:
    """Stop one nest and validate its status response."""
    await self._send(self._address(device_id) + "S")
    if await self._poll(device_id, read_timeout=self.stop_settle) == ERROR_REPLY:
      raise BigBearError(title="Stopping the shaker failed", message=f"nest {device_id}")

  async def stop_all(self) -> None:
    """Stop shaking on every nest on the chain."""
    async with self._shaking_transition_lock:
      if self._shaking_scope_tokens:
        raise RuntimeError("Cannot stop all nests while a shaking scope is active")
      for device_id in range(1, self.num_shakers + 1):
        await self._send(self._address(device_id) + "S")
        reply = await self._poll(device_id, read_timeout=self.stop_settle)
        failed = reply == ERROR_REPLY
        self._shaking_active[device_id] = failed
        self._shaking_state_unknown[device_id] = failed

  @asynccontextmanager
  async def shaking(
    self,
    rpm: int,
    acceleration: int = 1,
    sequence: Sequence = "CW",
    device_id: int = 1,
  ) -> AsyncIterator[None]:
    """Temporarily shake one nest and stop only that nest on scope exit."""
    self._validate_device_id(device_id)
    token = object()
    async with self._shaking_transition_lock:
      if not self.is_active:
        raise RuntimeError("Shaking scopes require an active BigBearOrbitalShaker lifespan")
      if device_id in self._shaking_scope_tokens:
        raise RuntimeError(f"Shaking on nest {device_id} is already scoped")
      if self._shaking_active[device_id] or self._shaking_state_unknown[device_id]:
        raise RuntimeError(f"Shaking on nest {device_id} is already active")
      await self._start_shaking_transition(
        rpm,
        acceleration=acceleration,
        sequence=sequence,
        device_id=device_id,
        rollback_on_failure=True,
      )
      self._shaking_scope_tokens[device_id] = token

    try:
      await anyio.lowlevel.checkpoint()
      yield
    finally:
      with anyio.CancelScope(shield=True):
        async with self._shaking_transition_lock:
          if self._shaking_scope_tokens.get(device_id) is token:
            try:
              await self._stop_scoped_shaking(device_id)
            finally:
              self._shaking_scope_tokens.pop(device_id, None)

  async def _force_stop_nest(self, device_id: int) -> None:
    """Stop one nest for parent cleanup, bypassing public scope guards."""
    async with self._shaking_transition_lock:
      try:
        await self._stop_nest(device_id)
      except BaseException:
        self._shaking_active[device_id] = True
        self._shaking_state_unknown[device_id] = True
        raise
      else:
        self._shaking_active[device_id] = False
        self._shaking_state_unknown[device_id] = False
      finally:
        self._shaking_scope_tokens.pop(device_id, None)

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
    await anyio.sleep(self.daisy_chain_settle)
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
