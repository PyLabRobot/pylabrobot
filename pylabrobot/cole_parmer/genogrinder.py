import asyncio
import logging
import time
from typing import Literal, Optional, Sequence, Union

from pylabrobot.io.serial import Serial

logger = logging.getLogger(__name__)


# Commands and replies are carriage-return terminated.
CR = "\r"

# The wire format packs the duration into three digits and the speed into four,
# so both are bounded by their field widths.
MAX_DURATION_SECONDS = 999
MAX_SPEED_RPM = 9999

DEFAULT_SPEED_RPM = 1750
DEFAULT_DURATION_SECONDS = 30

# Reply substrings the device sends to acknowledge each command.
_INITIALIZED = ("Initialize Complete", "Initialized")
_HOME_COMPLETE = "Home Complete"
_CLAMP_OPEN = "Clamp Open"
_CLAMP_CLOSED = "Clamp Closed"
_PARAMETERS_SET = "Parameters Set"
_RUNNING = "Running Sample"
_RUN_COMPLETE = "Run Complete"
_STANDBY = "Standby"
# Transient states reported while a run is in progress.
_MIXING_STATES = ("Running Sample", "Locking", "Mixing", "Unlocking")

ClampState = Literal["open", "closed", "unknown"]


class GenoGrinderError(Exception):
  """Exception raised by a GenoGrinder."""

  def __init__(self, title: str, message: Optional[str] = None) -> None:
    self.title = title
    self.message = message

  def __str__(self) -> str:
    return f"{self.title}: {self.message}" if self.message else self.title


class GenoGrinder:
  """Cole-Parmer SPEX GenoGrinder plate shaker / homogenizer.

  A microplate clamp shaker: it locks a plate (or plate stack) into a clamp and
  mixes it for a fixed duration at a fixed speed.

  Product page:
    https://www.coleparmer.com/i/cole-parmer-sampleprep-hg-600-230-geno-grinder-2010-tissue-homogenizer-and-cell-lyser-230-vac-50-hz/0457684

  Serial settings:
    9600 baud, 8 data bits, no parity, 1 stop bit, "\\r" terminator.

  Commands are ``*NN*`` frames; each is acknowledged with a reply whose text
  identifies the reached state::

    *01*              status poll (Standby / Running Sample / Locking / Mixing /
                      Unlocking / Run Complete)
    *02,<sss>,<ssss>* set run parameters (duration seconds, speed rpm)
    *03*              initialize
    *04*              start the run
    *05*              clear error
    *06*              stop the run
    *10*              home the clamp
    *11* / *12*       open / close the clamp
    *15*              clamp status

  A command that does not reach its expected state clears the error (*05*) and
  raises ``GenoGrinderError``.

  Not verified: this driver has NOT been tested against hardware in PyLabRobot.
  A warning is emitted at setup.
  """

  def __init__(
    self,
    port: str,
    use_clamp_commands: bool = True,
    timeout: float = 50.0,
    command_settle: float = 0.5,
    status_poll_interval: float = 1.0,
    mix_timeout_margin: float = 60.0,
  ):
    """
    Args:
      port: serial port the GenoGrinder is on.
      use_clamp_commands: whether the clamp is motorized and accepts open/close
        commands. When ``False``, :meth:`open_clamp` / :meth:`close_clamp` are
        no-ops (the plate is clamped by fixed hardware).
      timeout: serial read timeout, in seconds.
      command_settle: pause after starting a run before polling, in seconds.
      status_poll_interval: delay between status polls while waiting, in seconds.
      mix_timeout_margin: grace added to the run duration before a wait for the
        run to complete is considered timed out, in seconds.
    """
    self.use_clamp_commands = use_clamp_commands
    self.command_settle = command_settle
    self.status_poll_interval = status_poll_interval
    self.mix_timeout_margin = mix_timeout_margin
    self.io = Serial(
      human_readable_device_name="Cole-Parmer SPEX GenoGrinder",
      port=port,
      baudrate=9600,
      bytesize=8,
      parity="N",
      stopbits=1,
      timeout=timeout,
    )

  async def setup(self) -> None:
    logger.warning(
      "GenoGrinder has NOT been tested against hardware in PyLabRobot. "
      "Please make a PR to remove this message if you have verified it on your hardware."
    )
    await self.io.setup()
    await self._initialize()
    logger.info("[GenoGrinder %s] connected", self.io.port)

  async def stop(self) -> None:
    """Stop any run in progress and close the serial connection."""
    try:
      await self.io.write((("*06*") + CR).encode("ascii"))
    finally:
      await self.io.stop()

  # === Command layer ===

  async def _read_line(self, timeout: Optional[float] = None) -> str:
    """Read one CR-terminated reply, skipping empty lines.

    Returns the trimmed reply, or "" if nothing arrives within the read timeout.
    """

    async def _read() -> str:
      buf = bytearray()
      while True:
        char = await self.io.read(1)
        if char == b"":  # read timed out
          break
        if char == b"\r":
          if buf:
            break
          continue  # bare terminator between messages
        if char == b"\n":
          continue
        buf += char
      return buf.decode("ascii", errors="replace").strip()

    if timeout is None:
      reply = await _read()
    else:
      with self.io.temporary_timeout(timeout):
        reply = await _read()
    logger.debug("[GenoGrinder] recv: %s", reply)
    return reply

  async def _command(
    self, command: str, double_read: bool = False, timeout: Optional[float] = None
  ) -> str:
    """Send a command frame and return its reply.

    Initialization emits a progress line followed by the completion line; set
    ``double_read`` to return the second line.
    """
    await self.io.reset_input_buffer()
    await self.io.write((command + CR).encode("ascii"))
    logger.debug("[GenoGrinder] send: %s", command)
    reply = await self._read_line(timeout=timeout)
    if double_read:
      reply = await self._read_line(timeout=timeout)
    return reply

  async def _expect(self, reply: str, tokens: Union[str, Sequence[str]], title: str) -> None:
    """Raise if ``reply`` contains none of ``tokens``, clearing the error first."""
    if isinstance(tokens, str):
      tokens = (tokens,)
    if not any(token in reply for token in tokens):
      await self._command("*05*")
      raise GenoGrinderError(title=title, message=f"received {reply!r}")

  async def _initialize(self) -> None:
    """Run the instrument's power-on routine: home the mechanism, ready the clamp.

    The reply doubles as a communication check.
    """
    reply = await self._command("*03*", double_read=True)
    await self._expect(reply, _INITIALIZED, "Instrument did not initialize")
    logger.info("[GenoGrinder %s] initialized", self.io.port)

  # === Public API ===

  async def home_clamp(self) -> None:
    """Home the clamp."""
    reply = await self._command("*10*")
    await self._expect(reply, _HOME_COMPLETE, "Clamp did not home")

  async def request_clamp_state(self) -> ClampState:
    """Query the clamp position."""
    reply = await self._command("*15*")
    if _CLAMP_OPEN in reply:
      return "open"
    if _CLAMP_CLOSED in reply:
      return "closed"
    return "unknown"

  async def open_clamp(self) -> None:
    """Open the clamp. No-op if it is already open or clamp control is disabled."""
    if not self.use_clamp_commands:
      return
    if await self.request_clamp_state() == "open":
      return
    reply = await self._command("*11*")
    await self._expect(reply, _CLAMP_OPEN, "Clamp did not open")

  async def close_clamp(self) -> None:
    """Close the clamp. No-op if it is already closed or clamp control is disabled."""
    if not self.use_clamp_commands:
      return
    if await self.request_clamp_state() == "closed":
      return
    reply = await self._command("*12*")
    await self._expect(reply, _CLAMP_CLOSED, "Clamp did not close")

  async def request_status(self) -> str:
    """Return the raw status reply (*01*)."""
    return await self._command("*01*")

  async def shake(
    self,
    duration: int = DEFAULT_DURATION_SECONDS,
    speed: int = DEFAULT_SPEED_RPM,
  ) -> None:
    """Run a mix and block until it completes.

    Sets the run parameters, starts the run, then polls status until the device
    returns to standby / run-complete.

    Args:
      duration: mix time in seconds (1..999).
      speed: mix speed in rpm (1..9999).
    """
    if not 1 <= duration <= MAX_DURATION_SECONDS:
      raise ValueError(f"duration must be 1..{MAX_DURATION_SECONDS} seconds")
    if not 1 <= speed <= MAX_SPEED_RPM:
      raise ValueError(f"speed must be 1..{MAX_SPEED_RPM} rpm")

    reply = await self._command(f"*02,{duration:03d},{speed:04d}*")
    await self._expect(reply, _PARAMETERS_SET, "Instrument did not set parameters")

    await asyncio.sleep(self.command_settle)
    reply = await self._command("*04*")
    await self._expect(reply, _RUNNING, "Instrument did not start the run")

    await asyncio.sleep(self.command_settle)
    await self._wait_for_run_complete(duration)
    logger.info("[GenoGrinder %s] mixed %ds at %drpm", self.io.port, duration, speed)

  async def stop_shaking(self) -> None:
    """Stop a run in progress."""
    await self._command("*06*")

  # === Wait helpers ===

  async def _wait_for_run_complete(self, duration: int) -> None:
    deadline = time.monotonic() + duration + self.mix_timeout_margin
    while True:
      reply = await self._command("*01*")
      if _STANDBY in reply or _RUN_COMPLETE in reply:
        return
      if not any(state in reply for state in _MIXING_STATES):
        raise GenoGrinderError(
          title="Invalid mixing state; check the device and retry",
          message=f"received {reply!r}",
        )
      if time.monotonic() > deadline:
        raise GenoGrinderError(
          title="Timed out waiting for the run to complete",
          message=f"duration {duration}s + {self.mix_timeout_margin:.0f}s margin",
        )
      await asyncio.sleep(self.status_poll_interval)
