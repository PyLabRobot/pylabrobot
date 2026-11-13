"""KingFisher Flex control using local BindIt command scripts."""

import asyncio
import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Literal, Optional, Union

from pylabrobot.io.hid import HID
from pylabrobot.thermo_fisher.kingfisher.flex.connection import (
  FlexConnection,
  FlexError,
  FlexMessage,
  FlexTranscriptEntry,
  validate_timeout,
)
from pylabrobot.thermo_fisher.kingfisher.flex.protocol import (
  FlexCommand,
  FlexPrompt,
  FlexProtocol,
  ProtocolSource,
  _command_prompt,
  read_flex_bdz,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FlexEvent:
  """An execution event distinguishing instrument waits from host confirmations."""

  kind: Literal[
    "temperature",
    "step",
    "load",
    "unload",
    "pause",
    "prompt_completed",
    "host_load",
    "host_unload",
    "host_pause",
    "host_prompt_completed",
    "run_started",
    "run_completed",
    "failure",
  ]
  text: str
  prompt: Optional[FlexPrompt] = None


@dataclass(frozen=True)
class FlexRunResult:
  """Completion counts and the SHA-256 digest of the source BDZ."""

  protocol_name: str
  source_sha256: str
  completed_commands: int
  host_confirmations: int = 0
  source_kind: Literal["bdz"] = "bdz"


class KingFisherFlex:
  """Run supported 96DW BindIt exports over USB HID.

  Hardware validation covers firmware 1.01.0 on macOS, including mixing, bead
  collection/release, drying, 37 C heating, loops and host confirmations.
  Firmware completion does not verify correct tip-comb attachment.
  ``setup`` identifies the device; ``initialize`` moves its axes.
  Commands execute serially. ``stop`` disconnects; ``abort`` requests a motion stop.
  Protocol upload and remote advancement of onboard pauses are unsupported.

  Args:
    serial_number: Select one instrument when multiple Flex devices are attached.
    timeout: HID write/acknowledgment and abort deadline, in seconds.
    operation_timeout: Base motion deadline and margin for timed operations, in seconds.
  """

  _events_closed: bool
  _events: asyncio.Queue[Optional[FlexEvent]]
  _processing_slot: Optional[int]
  _current_command: Optional[FlexCommand]
  _current_prompt: Optional[FlexPrompt]
  _run_prompts: dict[int, FlexPrompt]
  state: Literal["disconnected", "idle", "initializing", "ready", "running", "waiting", "uncertain"]

  def _publish(self, event: FlexEvent) -> None:
    """Publish workflow events to one consumer and the module logger."""
    if not self._events_closed:
      self._events.put_nowait(event)
    logger.info("Flex %s: %s", event.kind, event.text)

  def _close_events(self) -> None:
    """Wake an event consumer when the session ends."""
    if not self._events_closed:
      self._events_closed = True
      self._events.put_nowait(None)

  def _on_failure(self, error: FlexError) -> None:
    """Forget inferred geometry and initialization on every uncertain outcome."""
    self._initialized = False
    self._processing_slot = None
    self.state = "uncertain"
    self._session_failed.set()
    self._publish(FlexEvent("failure", str(error)))
    self._close_events()

  def _prompt_for_command(self, command: FlexCommand, orientation: int) -> FlexPrompt:
    """Use validated source metadata while checking the completed physical orientation."""
    prompt = self._run_prompts.get(command.line_number)
    if prompt is None:
      return _command_prompt(command, orientation)
    if prompt.processing_slot != orientation:
      raise FlexError("Completed orientation differs from the validated prompt")
    return prompt

  def _on_message(self, message: FlexMessage) -> None:
    """Publish telemetry and prompt/step events only after command acknowledgment."""
    if self._events_closed:
      return
    if message.opcode == "TEM" and message.kind == "report":
      self._publish(FlexEvent("temperature", message.text))
    command = self._current_command
    if message.kind != "ack" or command is None or message.opcode != command.opcode:
      return
    if command.opcode == "PAU" and self._processing_slot is not None:
      prompt = self._prompt_for_command(command, self._processing_slot)
      self._current_prompt = prompt
      self.state = "waiting"
      self._publish(FlexEvent(prompt.action, prompt.label, prompt))
    elif command.opcode == "TXT":
      self._publish(FlexEvent("step", command.text.split('"')[1]))

  def __init__(
    self,
    serial_number: Optional[str] = None,
    timeout: float = 30.0,
    operation_timeout: float = 120.0,
  ) -> None:
    """Configure a Flex without enumerating or opening USB devices."""
    validate_timeout(timeout, "timeout")
    validate_timeout(operation_timeout, "operation_timeout")
    self.io = HID("KingFisher Flex", vid=0x0AB6, pid=0x02C7, serial_number=serial_number)
    self.timeout = timeout
    self.operation_timeout = operation_timeout
    self._connection = FlexConnection(self.io, self._on_message, self._on_failure, timeout)
    self._lifecycle = asyncio.Lock()
    self._operations = asyncio.Lock()
    self._events = asyncio.Queue()
    self._session_failed = asyncio.Event()
    self._events_closed = False
    self._connected = False
    self._initialized = False
    self._processing_slot = None
    self._current_command = None
    self._current_prompt = None
    self._run_prompts = {}
    self.state = "disconnected"
    self.version: Optional[str] = None
    self.serial: Optional[str] = None

  @property
  def transcript(self) -> tuple[FlexTranscriptEntry, ...]:
    """Return the session transcript, including messages after interruption."""
    return tuple(self._connection.transcript)

  @property
  def processing_slot(self) -> Optional[int]:
    """Return the last completed MOR target, or None when physical state is unknown."""
    return self._processing_slot

  async def events(self) -> AsyncIterator[FlexEvent]:
    """Consume session events from one task until disconnect or failure.

    Run this iterator alongside ``run_protocol``. A successful run emits
    ``run_completed``; the session remains open for telemetry until ``stop``.
    """
    queue = self._events
    while True:
      event = await queue.get()
      if event is None:
        queue.put_nowait(None)
        return
      yield event

  async def setup(self) -> None:
    """Identify the instrument without EAK, fault acknowledgment or motion commands."""
    async with self._lifecycle:
      if self._connected:
        self._connection.check_open()
        return
      logger.warning(
        "KingFisher Flex is experimental. Hardware checks cover firmware 1.01.0 on macOS "
        "with 96DW mixing/bead methods, 37 C heating and host confirmations. "
        "Partial tip pickup remains unresolved. "
        "See the Flex validation guide for tested scope and remaining limitations."
      )
      self._events = asyncio.Queue()
      self._session_failed = asyncio.Event()
      self._events_closed = False
      self._connection.io = self.io
      try:
        await self._connection.setup()
        response = await self._connection.command("VER", self.timeout)
        identity = [line.split() for line in response if line.startswith("VER KingFisherFlex ")]
        if len(identity) != 1 or len(identity[0]) != 4:
          raise FlexError(f"Expected one KingFisherFlex identification, received {response!r}")
        self.version, self.serial = identity[0][2:]
        self._connected = True
        self.state = "idle"
        logger.info("Connected to Flex firmware %s, serial %s", self.version, self.serial)
      except BaseException:
        await self._connection.stop()
        self.state = "disconnected"
        self.version = self.serial = None
        raise

  def _check_operation(self) -> None:
    """Reject concurrent operations rather than queuing an unintended second run."""
    self._connection.check_open()
    if self._operations.locked():
      raise FlexError("Another Flex operation is active")

  async def list_protocols(self) -> tuple[str, ...]:
    """Read onboard names with REP 4 without initialization or motion.

    Includes maintenance and factory entries. Numeric inventory metadata is
    retained in ``transcript`` without interpreting its undocumented fields.
    Unexpected formats fail explicitly instead of returning an incomplete list.
    """
    self._check_operation()
    async with self._operations:
      response = await self._connection.command("REP 4", self.timeout)
      names = []
      for line in response:
        match = re.fullmatch(r'REP 4 [0-9]+ [0-9]+ "((?:[^"\r\n]|"")+)" [0-9]+\s*', line)
        if match:
          names.append(match[1].replace('""', '"'))
        elif re.fullmatch(r"REP 4(?: [0-9]+){5}\s*", line) is None:
          raise FlexError(f"Unrecognized protocol inventory response: {line!r}", "REP")
      return tuple(names)

  async def initialize(self) -> None:
    """Initialize axes and disable heating; this can move axes and present tray 4 for loading.

    Call only after checking the instrument and consumables. Initialization
    acknowledges faults, lowers the heater and resets head/magnet positions.
    Repeated calls in the same initialized session do not repeat motion.
    """
    self._check_operation()
    if self._initialized:
      return
    async with self._operations:
      self.state = "initializing"
      try:
        for command in ("EAK", "TEM 0 300", "MOI 0", "MOS 0", "MOM 0", "MOH 0 100", "MOR 1"):
          await self._connection.command(command, self.operation_timeout)
        self._processing_slot = 1
        self._initialized = True
        self.state = "ready"
      except BaseException:
        self._initialized = False
        self._processing_slot = None
        self.state = "uncertain"
        raise

  async def _confirm_host_prompt(
    self,
    command: FlexCommand,
    confirm: Callable[[FlexPrompt], Awaitable[bool]],
    timeout: Optional[float],
  ) -> None:
    """Wait for positive host confirmation while monitoring session failure."""
    self._connection.check_open()
    if self._processing_slot is None:
      raise FlexError("Host confirmation requires a completed carousel orientation")
    prompt = self._prompt_for_command(command, self._processing_slot)
    self._current_prompt = prompt
    self.state = "waiting"
    self._publish(
      FlexEvent(
        "host_pause"
        if prompt.action == "pause"
        else "host_load"
        if prompt.action == "load"
        else "host_unload",
        prompt.label,
        prompt,
      )
    )

    async def confirmation() -> bool:
      """Call the operator handler inside a cancellable task."""
      return await confirm(prompt)

    confirmed = asyncio.create_task(confirmation())
    failed = asyncio.create_task(self._session_failed.wait())
    try:
      done, _ = await asyncio.wait(
        (confirmed, failed), timeout=timeout, return_when=asyncio.FIRST_COMPLETED
      )
      self._connection.check_open()
      if self._session_failed.is_set():
        raise FlexError("Session ended while awaiting host confirmation")
      if confirmed not in done:
        raise asyncio.TimeoutError("Host confirmation timed out")
      if await confirmed is not True:
        raise FlexError("Host did not confirm the requested action")
      self._publish(FlexEvent("host_prompt_completed", prompt.label, prompt))
      self._current_prompt = None
      self.state = "running"
    finally:
      for task in (confirmed, failed):
        if not task.done():
          task.cancel()
      await asyncio.gather(confirmed, failed, return_exceptions=True)

  async def run_protocol(
    self,
    protocol: Union[FlexProtocol, ProtocolSource],
    *,
    operator_timeout: Optional[float] = None,
    confirm_prompt: Optional[Callable[[FlexPrompt], Awaitable[bool]]] = None,
    confirm_pause: Optional[Callable[[FlexPrompt], Awaitable[bool]]] = None,
  ) -> FlexRunResult:
    """Stream a supported BindIt export and await terminal completion.

    Preflight finishes before any I/O. Initialization must have been requested
    separately. By default PAU waits for physical Start. Supplying ``confirm_prompt``
    opts into experimental host confirmation: PAU 1/2 are NOT sent, and the async
    callback must return exactly True after each load/unload before streaming continues.
    Standalone PAU 0 requires a separate ``confirm_pause`` callback to replace its
    instrument wait. Otherwise PAU 0 is sent even when plate confirmations use PLR.
    Its message is descriptive text, never a timer. Host pause handling emits
    host_pause followed by host_prompt_completed.
    This does not release an instrument PAU already in progress. Host waits emit
    host_load/host_unload and host_prompt_completed, never a fictitious PAU completion.
    Dispensing pauses include required per-well additions in both the prompt label
    and ``prompt.additions``. Confirmation means those additions are complete.
    Heating commands set a target without waiting for it to be reached; temperature
    reports are telemetry, not command completion. Documented setpoints are supported;
    hardware validation covers 37 C.
    ``operator_timeout=None`` waits indefinitely while the
    reader continues to detect disconnects and errors. Cancellation does not
    abort hardware: it invalidates the session and requires explicit recovery.
    """
    validate_timeout(operator_timeout, "operator_timeout", allow_none=True)
    if confirm_prompt is not None and not callable(confirm_prompt):
      raise TypeError("confirm_prompt must be an async callable")
    if confirm_pause is not None and not callable(confirm_pause):
      raise TypeError("confirm_pause must be an async callable")
    parsed = protocol if isinstance(protocol, FlexProtocol) else read_flex_bdz(protocol)
    commands = parsed.preflight()
    prompts = {p.line_number: p for p in parsed.prompts()}
    self._check_operation()
    if not self._initialized:
      raise FlexError("Call initialize() explicitly before running a protocol")
    async with self._operations:
      self._run_prompts = prompts
      self.state = "running"
      self._publish(FlexEvent("run_started", parsed.name))
      timer_duration = 0.0
      completed = 0
      host_confirmations = 0
      try:
        # BindIt repeats this preamble at run start, separately from connection startup.
        for line in ("EAK", "MOI 0", "MOS 0", "MOM 0", "MOH 0 100", "TEM 0 300"):
          await self._connection.command(line, self.operation_timeout)
        for command in commands:
          self._current_command = command
          if command.opcode == "PAU":
            handler = confirm_pause if command.text.startswith("PAU 0 ") else confirm_prompt
            if handler is not None:
              await self._confirm_host_prompt(command, handler, operator_timeout)
              host_confirmations += 1
              continue
          fields = command.text.split()
          deadline: Optional[float] = self.operation_timeout
          if command.opcode == "PAU":
            deadline = operator_timeout
          elif command.opcode == "MOH" and len(fields) == 6:
            deadline = int(fields[4]) / 100 + self.operation_timeout
          elif command.opcode == "WAI":
            if len(fields) == 3:
              timer_duration = int(fields[2]) / 100
            else:
              deadline = timer_duration + self.operation_timeout
          if command.opcode == "MOR":
            self._processing_slot = None
          await self._connection.command(command.text, deadline)
          completed += 1
          if command.opcode == "MOR":
            self._processing_slot = int(fields[1])
          if command.opcode == "PAU":
            self._publish(FlexEvent("prompt_completed", command.text, self._current_prompt))
            self._current_prompt = None
            self.state = "running"
        self._current_command = None
        for line in ('TXT "END_OF_RUN"', "TEM 0 300", "TEM 0 300"):
          await self._connection.command(line, self.operation_timeout)
        self._connection.check_open()
        self.state = "ready"
        self._publish(FlexEvent("run_completed", parsed.name))
        return FlexRunResult(parsed.name, parsed.sha256, completed, host_confirmations)
      except BaseException as error:
        self._connection.invalidate(FlexError(f"Protocol interrupted or failed: {error}"))
        self._initialized = False
        self._processing_slot = None
        self.state = "uncertain"
        raise
      finally:
        self._current_command = None
        self._current_prompt = None
        self._run_prompts = {}

  async def resume(self) -> None:
    """Refuse to invent a software equivalent of the physical Start button."""
    raise NotImplementedError("Flex remote Start is unverified; press physical Start at the prompt")

  async def start_stored_protocol(
    self, name: str, *, completion_timeout: Optional[float] = None
  ) -> None:
    """Start an onboard sequence using BGN and wait for its terminal response.

    Experimental, based on the Flex Service Manual v1.1, section 7.1.11.2;
    validated through the first loading prompt only. Motion may begin immediately. The exact name
    must occur once in a fresh onboard inventory and is sent without truncation.
    Name length acceptance is determined by the instrument firmware.

    Internal commands and loading pauses are not reported individually by BGN.
    Host prompt callbacks therefore cannot advance them. Acknowledgment alone
    is not completion. Timeout or cancellation invalidates the session but does
    not stop motion; explicitly call ``abort`` from a supervising task.

    Args:
      name: Exact stored name (nonblank printable ASCII).
      completion_timeout: Seconds to wait for BGN END, or None for operator waits.
    """
    if not isinstance(name, str) or re.fullmatch(r"[ -~]+", name) is None or not name.strip():
      raise ValueError("Stored protocol name must be nonblank printable ASCII")
    validate_timeout(completion_timeout, "completion_timeout", allow_none=True)
    names = await self.list_protocols()
    if names.count(name) != 1:
      raise FlexError("Stored protocol name must occur exactly once in the onboard inventory")
    self._check_operation()
    async with self._operations:
      self._initialized = False
      self._processing_slot = None
      self.state = "running"
      logger.warning("Experimental onboard launch: %s; motion may begin immediately", name)
      self._publish(FlexEvent("run_started", name))
      try:
        quoted_name = name.replace('"', '""')
        await self._connection.command(f'BGN "{quoted_name}"', completion_timeout)
        self.state = "idle"
        self._publish(FlexEvent("run_completed", name))
      except BaseException:
        self.state = "uncertain"
        raise

  async def abort(self) -> None:
    """Request PC abort without waiting for the active command or operator pause."""
    await self._connection.abort()

  async def stop(self) -> None:
    """Disconnect and invalidate position; never claim that disconnect stops motion."""
    async with self._lifecycle:
      if not self._connected and self.state == "disconnected":
        self._close_events()
        return
      self._session_failed.set()
      try:
        await self._connection.stop()
        async with self._operations:
          pass
      finally:
        self._connected = False
        self._initialized = False
        self._processing_slot = None
        self._current_command = None
        self._current_prompt = None
        self.version = self.serial = None
        self.state = "disconnected"
        self._close_events()
