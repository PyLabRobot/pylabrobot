"""KingFisher Flex control using local BindIt command scripts."""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Literal, Optional

from pylabrobot.io.hid import HID
from pylabrobot.resources import PlateHolder, Resource
from pylabrobot.thermo_fisher.kingfisher.flex.actions import (
  FlexAction,
  create_actions,
  expected_inventory,
)
from pylabrobot.thermo_fisher.kingfisher.flex.connection import (
  FlexConnection,
  FlexError,
  FlexMessage,
  FlexTranscriptEntry,
  validate_timeout,
)
from pylabrobot.thermo_fisher.kingfisher.flex.protocol import (
  FlexBoundProtocol,
  FlexCommand,
  FlexProtocolError,
  loading_slot,
)
from pylabrobot.thermo_fisher.kingfisher.flex.resource_model import create_carousel

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
  prompt: Optional[FlexAction] = None


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
  Execution uses local BDZ exports; onboard protocol management is unsupported.

  Args:
    serial_number: Select one instrument when multiple Flex devices are attached.
    timeout: HID write/acknowledgment and abort deadline, in seconds.
    operation_timeout: Base motion deadline and margin for timed operations, in seconds.
    name: Resource-name prefix. Use distinct names for instruments in the same resource tree.
  """

  _events_closed: bool
  _events: asyncio.Queue[Optional[FlexEvent]]
  _processing_slot: Optional[int]
  _current_command: Optional[FlexCommand]
  _current_prompt: Optional[FlexAction]
  _run_actions: dict[int, FlexAction]
  _action_acknowledgement: Optional[Literal["host", "instrument"]]
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
    self._inventory_verified = False
    self._processing_slot = None
    self.state = "uncertain"
    self._session_failed.set()
    self._publish(FlexEvent("failure", str(error)))
    self._close_events()

  def _inventory_changed(self, resource: Resource) -> None:
    """Require explicit reconciliation after changes to the carousel resource tree."""
    self._inventory_verified = False
    if self._operations.locked() and self.state == "running" and self._current_prompt is None:
      self._connection.invalidate(
        FlexError("Inventory is uncertain: resource tree changed outside an active action")
      )

  def _prompt_for_command(self, command: FlexCommand, orientation: int) -> FlexAction:
    """Use validated source metadata while checking the completed physical orientation."""
    prompt = self._run_actions[command.line_number]
    if prompt.processing_slot != orientation:
      raise FlexError("Completed orientation differs from the validated prompt")
    return prompt

  def _inventory_snapshot(self) -> tuple[Optional[Resource], ...]:
    """Read current occupancy directly from the eight persistent holders."""
    return tuple(self.carousel[tray].resource for tray in range(1, 9))

  def _require_inventory(self) -> None:
    """Reject execution until the current resource tree is explicitly trusted."""
    if not self._inventory_verified:
      raise FlexError("Inventory is uncertain; inspect the carousel and call reconcile_inventory()")

  def _complete_action(self, action: FlexAction, expected: tuple[Optional[Resource], ...]) -> None:
    """Verify modeled transfers after explicit completion, never from assignment events."""
    self._connection.check_open()
    actual = self._inventory_snapshot()
    for tray, (resource, wanted) in enumerate(zip(actual, expected), 1):
      if resource is not wanted:
        raise FlexError(
          f"Line {action.line_number}: {action.action} {action.plate_name!r} completed with "
          f"incorrect occupancy in tray {tray}; expected "
          f"{wanted.name if wanted is not None else 'empty'}, found "
          f"{resource.name if resource is not None else 'empty'}"
        )
    self._inventory_verified = True

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
    *,
    name: str = "kingfisher_flex",
  ) -> None:
    """Configure a Flex without enumerating or opening USB devices."""
    validate_timeout(timeout, "timeout")
    validate_timeout(operation_timeout, "operation_timeout")
    if not name.strip():
      raise ValueError("name must not be empty")
    self.name = name
    self.carousel = create_carousel(name)
    self._inventory_verified = False
    self.carousel.register_will_assign_resource_callback(self._inventory_changed)
    self.carousel.register_will_unassign_resource_callback(self._inventory_changed)
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
    self._run_actions = {}
    self._action_acknowledgement = None
    self._physical_pause_started = False
    self._physical_pause_completed = False
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

  @property
  def loading_slot(self) -> Optional[int]:
    """Return the printed tray at the loading station, or None during uncertain motion."""
    return None if self._processing_slot is None else loading_slot(self._processing_slot)

  @property
  def processing_holder(self) -> Optional[PlateHolder]:
    """Return the persistent holder at processing, without moving its assigned plate."""
    return None if self._processing_slot is None else self.carousel[self._processing_slot]

  @property
  def loading_holder(self) -> Optional[PlateHolder]:
    """Return the persistent holder accessible at the single loading station."""
    slot = self.loading_slot
    return None if slot is None else self.carousel[slot]

  @property
  def inventory_verified(self) -> bool:
    """Whether the caller has reconciled the current tree with physical contents.

    False means unknown contents, even if every holder is empty in the model.
    This is caller confirmation, not an instrument sensor reading.
    """
    return self._inventory_verified

  def reconcile_inventory(self) -> None:
    """Confirm that every holder's assignment matches the physical carousel.

    First assign/remove plates using the resource tree, then call this method
    after checking all eight trays. An empty carousel also needs confirmation.
    This performs no USB I/O and cannot run during a device operation or lifecycle
    transition. Reconnection and interruption invalidate this confirmation.
    Protocol handlers must update resource assignments before confirming transfers.
    """
    if self._operations.locked() or self._lifecycle.locked():
      raise FlexError("Cannot reconcile inventory during an active Flex operation")
    self._inventory_verified = True

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
      self._inventory_verified = False
      self._processing_slot = None
      logger.warning(
        "KingFisher Flex is experimental. Hardware checks cover firmware 1.01.0 on macOS "
        "with 96DW mixing/bead methods, 37 C heating and host confirmations. "
        "Partial tip pickup remains unresolved. "
        "Resource-aware host and physical-Start transfers and a host reagent-addition "
        "intervention are tested. See the Flex guide for tested scope."
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
      self._processing_slot = None
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

  async def wait_for_physical_start(self, action: FlexAction) -> None:
    """Send the selected action's original PAU and await its firmware completion once.

    Only callable while handling this exact active action with instrument
    acknowledgment selected. An acknowledgment adapter can await this inside a
    ManualOperator action, so model updates occur after PAU END 0. No unsolicited
    Start event is assumed, and this cannot release an already active PAU.
    """
    self._connection.check_open()
    if self._current_prompt is not action or self._action_acknowledgement != "instrument":
      raise FlexError("Physical Start requires the active action with instrument acknowledgment")
    if self._physical_pause_started:
      raise FlexError("Physical acknowledgment has already been requested for this action")
    self._physical_pause_started = True
    # The enclosing action wait supplies the operator deadline and failure monitoring.
    await self._connection.command(action.command.text, None)
    self._physical_pause_completed = True

  async def _await_action_handler(
    self,
    prompt: FlexAction,
    confirm: Callable[[FlexAction], Awaitable[bool]],
    timeout: Optional[float],
  ) -> None:
    """Await explicit handler completion while monitoring the session and action deadline."""
    self._connection.check_open()
    if self._processing_slot is None:
      raise FlexError("Action confirmation requires a completed carousel orientation")
    self._current_prompt = prompt
    self.state = "waiting"
    if self._action_acknowledgement == "host":
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
        raise FlexError("Session ended while awaiting action completion")
      if confirmed not in done:
        raise asyncio.TimeoutError("Action confirmation timed out")
      if await confirmed is not True:
        raise FlexError("Handler did not confirm the requested action")
      if self._action_acknowledgement == "instrument" and not self._physical_pause_completed:
        raise FlexError(
          "Physical handler must await wait_for_physical_start(action) before completing"
        )
    finally:
      for task in (confirmed, failed):
        if not task.done():
          task.cancel()
      await asyncio.gather(confirmed, failed, return_exceptions=True)

  async def run_protocol(
    self,
    protocol: FlexBoundProtocol,
    *,
    operator_timeout: Optional[float] = None,
    handle_action: Optional[Callable[[FlexAction], Awaitable[bool]]] = None,
    acknowledgement: Optional[
      Literal["host", "instrument"] | Callable[[FlexAction], Literal["host", "instrument"]]
    ] = None,
  ) -> FlexRunResult:
    """Stream a supported BindIt export and await terminal completion.

    Requires ``read_flex_bdz(source).bind_plates({bdz_name: plr_plate, ...})``.
    Every BDZ layout plate must be designated, including the tip-comb plate.
    Raw exports and paths cannot be executed without bindings. Reconcile initial
    inventory explicitly, even when empty. Plates may be loaded as the run proceeds.
    ``handle_action`` handles every load, unload and intervention.
    ``acknowledgement`` selects host confirmation or physical Start, either for the
    run or through a synchronous per-action selector. The default is host when a
    handler is supplied, otherwise instrument. Selection occurs before sending PAU.
    Instrument-mode handlers must await ``wait_for_physical_start(action)`` exactly
    once, directly or through an acknowledgment provider, before returning True.
    Requests include the bound plate, presented holder and original script line.
    Handlers must update modeled transfers and clear the motion envelope before
    returning True. Occupancy is checked before any subsequent command. Assignment
    events alone never advance the script. Preloaded matching plates are accepted,
    but still require confirmation. Without a handler, the original PAU is sent;
    resource updates must be completed externally before physical Start.
    Preflight finishes before any I/O. Initialization must have been requested
    separately. Host acknowledgment omits PAU; instrument
    acknowledgment preserves it. Pause messages are descriptive text, not timers.
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
    if acknowledgement is not None and not callable(acknowledgement):
      if acknowledgement not in ("host", "instrument"):
        raise ValueError("acknowledgement must be 'host', 'instrument', or a selector")
    if handle_action is not None and not callable(handle_action):
      raise TypeError("handle_action must be an async callable")
    if acknowledgement == "host" and handle_action is None:
      raise ValueError("Host acknowledgment requires an action handler")
    if not isinstance(protocol, FlexBoundProtocol):
      raise FlexProtocolError(
        "Plate bindings are required before execution; use "
        "read_flex_bdz(source).bind_plates({bdz_plate_name: plr_plate, ...})"
      )
    parsed = protocol.protocol
    commands = parsed.preflight()
    actions = create_actions(protocol, self.carousel, commands)
    self._check_operation()
    if not self._initialized:
      raise FlexError("Call initialize() explicitly before running a protocol")
    self._require_inventory()
    planned = self._inventory_snapshot()
    for action in actions.values():
      planned = expected_inventory(action, planned)
    async with self._operations:
      self._run_actions = actions
      self.state = "running"
      self._publish(FlexEvent("run_started", parsed.name))
      timer_duration = 0.0
      completed = 0
      host_confirmations = 0
      try:
        # BindIt repeats this preamble at run start, separately from connection startup.
        for line in ("EAK", "MOI 0", "MOS 0", "MOM 0", "MOH 0 100", "TEM 0 300"):
          self._require_inventory()
          await self._connection.command(line, self.operation_timeout)
          self._require_inventory()
        for command in commands:
          self._require_inventory()
          self._current_command = command
          if command.opcode == "PAU":
            if self._processing_slot is None:
              raise FlexError("Action requires a completed carousel orientation")
            action = self._prompt_for_command(command, self._processing_slot)
            expected = expected_inventory(action, self._inventory_snapshot())
            self._current_prompt = action
            self._inventory_verified = False
            handler = handle_action
            if callable(acknowledgement):
              mode = acknowledgement(action)
            elif acknowledgement is None:
              mode = "host" if handler is not None else "instrument"
            else:
              mode = acknowledgement
            if mode not in ("host", "instrument"):
              raise ValueError("Acknowledgment selector must return 'host' or 'instrument'")
            if mode == "host" and handler is None:
              raise FlexError("Host acknowledgment requires an action handler")
            self._action_acknowledgement = mode
            self._physical_pause_started = False
            self._physical_pause_completed = False
            if handler is not None:
              await self._await_action_handler(action, handler, operator_timeout)
            else:
              await asyncio.wait_for(self.wait_for_physical_start(action), operator_timeout)
            if mode == "instrument":
              completed += 1
            else:
              host_confirmations += 1
            self._complete_action(action, expected)
            self._publish(
              FlexEvent(
                "host_prompt_completed" if mode == "host" else "prompt_completed",
                action.label,
                action,
              )
            )
            self._current_prompt = None
            self._action_acknowledgement = None
            self.state = "running"
            continue
          fields = command.text.split()
          deadline: Optional[float] = self.operation_timeout
          if command.opcode == "MOH" and len(fields) == 6:
            deadline = int(fields[4]) / 100 + self.operation_timeout
          elif command.opcode == "WAI":
            if len(fields) == 3:
              timer_duration = int(fields[2]) / 100
            else:
              deadline = timer_duration + self.operation_timeout
          if command.opcode == "MOR":
            self._processing_slot = None
          await self._connection.command(command.text, deadline)
          self._require_inventory()
          completed += 1
          if command.opcode == "MOR":
            self._processing_slot = int(fields[1])
        self._current_command = None
        for line in ('TXT "END_OF_RUN"', "TEM 0 300", "TEM 0 300"):
          self._require_inventory()
          await self._connection.command(line, self.operation_timeout)
          self._require_inventory()
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
        self._run_actions = {}
        self._action_acknowledgement = None
        self._physical_pause_started = False
        self._physical_pause_completed = False

  async def abort(self) -> None:
    """Request PC abort without waiting for the active command or operator pause."""
    await self._connection.abort()

  async def stop(self) -> None:
    """Disconnect and invalidate position; never claim that disconnect stops motion."""
    async with self._lifecycle:
      self._inventory_verified = False
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
