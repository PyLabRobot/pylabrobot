import asyncio
import inspect
import logging
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple, Union, cast

from pylabrobot import utils
from pylabrobot.io import LOG_LEVEL_IO
from pylabrobot.legacy.liquid_handling.backends.backend import (
  LiquidHandlerBackend,
)
from pylabrobot.legacy.liquid_handling.errors import NoChannelError
from pylabrobot.legacy.liquid_handling.standard import (
  Drop,
  DropTipRack,
  MultiHeadAspirationContainer,
  MultiHeadAspirationPlate,
  MultiHeadDispenseContainer,
  MultiHeadDispensePlate,
  Pickup,
  PickupTipRack,
  ResourceDrop,
  ResourceMove,
  ResourcePickup,
  SingleChannelAspiration,
  SingleChannelDispense,
)
from pylabrobot.resources import (
  Coordinate,
  Tip,
)
from pylabrobot.resources.opentrons import OTDeck
from pylabrobot.resources.tip_rack import TipRack

try:
  import ot_api

  USE_OT = True
except ImportError as e:
  USE_OT = False
  _OT_IMPORT_ERROR = e


# https://github.com/Opentrons/opentrons/issues/14590
# https://labautomation.io/t/connect-pylabrobot-to-ot2/2862/18
_OT_DECK_IS_ADDRESSABLE_AREA_VERSION = "7.1.0"

logger = logging.getLogger(__name__)

# One request/response with the robot-server. A read of robot state, not a motion.
DEFAULT_REQUEST_TIMEOUT = 30.0

# One command, including the motion it performs. The OT-2's slowest single move is a
# full-stroke aspirate or dispense at a viscous-liquid flow rate.
DEFAULT_COMMAND_TIMEOUT = 120.0

# Delay between two reads of a running command's status.
DEFAULT_STATUS_POLL_INTERVAL = 0.05


def _seconds_left(deadline: float) -> float:
  return max(deadline - time.monotonic(), 0.0)


def _well_location(x: float, y: float, z: float, origin: str = "bottom") -> Dict[str, Any]:
  """The ``wellLocation`` shape every well-addressed command takes."""
  return {"origin": origin, "offset": {"x": x, "y": y, "z": z}}


def _in_place(
  volume: float,
  flow_rate: float,
  pipette_id: str,
  push_out: Optional[bool] = None,
) -> Dict[str, Any]:
  """Params for an ``aspirateInPlace``/``dispenseInPlace`` command."""
  params: Dict[str, Any] = {"flowRate": flow_rate, "volume": volume, "pipetteId": pipette_id}
  if push_out is not None:
    params["pushOut"] = push_out
  return params


async def _call_off_loop(call: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
  """Run a blocking call on a thread of its own and await its result.

  Not ``asyncio.to_thread``: that borrows the loop's shared executor, whose threads
  ``asyncio.run()`` joins on the way out, so one request nobody ever answers would
  hang the process at shutdown and burn a pool slot every other backend shares. A
  daemon thread holds neither.

  A thread per call, not a queue of one, and that is deliberate. ``ot_api`` gives
  ``urlopen`` no socket timeout, so a request the robot never answers leaks its
  thread for the life of the process. Queueing behind it would mean the stop that
  contains a timed-out move could never reach the robot either, which is the worse
  failure: the robot would keep executing what we stopped waiting for. The cost is
  that after a timeout a second call can be in flight against the same robot while
  the first is still stuck.
  """
  loop = asyncio.get_running_loop()
  future: "asyncio.Future[Any]" = loop.create_future()

  def _settle(setter: Callable[[Any], None], value: Any) -> None:
    if not future.done():
      setter(value)

  def _deliver(setter: Callable[[Any], None], value: Any) -> None:
    try:
      loop.call_soon_threadsafe(_settle, setter, value)
    except RuntimeError:
      pass  # the loop is gone, so nobody is waiting for this answer any more

  def _run() -> None:
    try:
      result = call(*args, **kwargs)
    except BaseException as exc:
      _deliver(future.set_exception, exc)
    else:
      _deliver(future.set_result, result)

  threading.Thread(target=_run, name="opentrons-request", daemon=True).start()
  return await future


class _IOLogger:
  """Transparent proxy over the ``ot_api`` module that logs every call at
  ``LOG_LEVEL_IO``.

  The OT-2 talks HTTP through ``ot_api`` rather than a pylabrobot.io transport, so
  this wrapper gives it the same wire-level logging every other backend gets from
  its io object. Submodules (``lh``, ``health``, ...) are wrapped recursively;
  plain attributes (e.g. ``run_id``) pass through untouched.
  """

  def __init__(self, target: Any, prefix: str = ""):
    self._target = target
    self._prefix = prefix

  def __getattr__(self, name: str) -> Any:
    attr = getattr(self._target, name)
    qualified = f"{self._prefix}.{name}" if self._prefix else name
    if inspect.ismodule(attr):
      return _IOLogger(attr, qualified)
    if callable(attr):

      def _logged(*args, **kwargs):
        parts = [repr(a) for a in args] + [f"{k}={v!r}" for k, v in kwargs.items()]
        logger.log(LOG_LEVEL_IO, "%s(%s)", qualified, ", ".join(parts))
        return attr(*args, **kwargs)

      # Without this every wrapped call answers to "_logged", and anything that
      # names the call it is reporting on (a timeout message) names the proxy.
      _logged.__name__ = _logged.__qualname__ = qualified
      return _logged
    return attr


class OpentronsOT2Backend(LiquidHandlerBackend):
  """Backends for the Opentrons OT2 liquid handling robots."""

  pipette_name2volume = {
    "p10_single": 10,
    "p10_multi": 10,
    "p20_single_gen2": 20,
    "p20_multi_gen2": 20,
    "p50_single": 50,
    "p50_multi": 50,
    "p300_single": 300,
    "p300_multi": 300,
    "p300_single_gen2": 300,
    "p300_multi_gen2": 300,
    "p1000_single": 1000,
    "p1000_single_gen2": 1000,
    "p300_single_gen3": 300,
    "p1000_single_gen3": 1000,
  }

  def __init__(
    self,
    host: str,
    port: int = 31950,
    request_timeout: float = DEFAULT_REQUEST_TIMEOUT,
    command_timeout: float = DEFAULT_COMMAND_TIMEOUT,
    status_poll_interval: float = DEFAULT_STATUS_POLL_INTERVAL,
  ):
    """Args:
    host: the robot's address.
    port: the robot-server's port.
    request_timeout: how long one request/response with the robot may take, in
      seconds. Includes the wait for whatever request is already in flight.
    command_timeout: how long a command that moves the robot may take, in seconds.
      Covers the motion itself, not just the request that started it.
    status_poll_interval: delay between two reads of a running command's status.
    """
    super().__init__()

    self._init_wire_state(request_timeout, command_timeout, status_poll_interval)

    if not USE_OT:
      raise RuntimeError(
        "Opentrons is not installed. Please run pip install pylabrobot[opentrons]."
        f" Import error: {_OT_IMPORT_ERROR}."
      )

    self.host = host
    self.port = port

    # All hardware I/O goes through this handle so a subclass (e.g. the chatterbox)
    # can dry-run the backend by swapping it for a recording stand-in. The real handle
    # wraps ot_api to log every HTTP call at LOG_LEVEL_IO, like other backends' io.
    self._ot: Any = _IOLogger(ot_api)

    self._ot.set_host(host)
    self._ot.set_port(port)

    self.ot_api_version: Optional[str] = None
    self.left_pipette: Optional[Dict[str, str]] = None
    self.right_pipette: Optional[Dict[str, str]] = None

    self.traversal_height = 120  # test
    self._tip_racks: Dict[str, int] = {}  # tip_rack.name -> slot index
    self._plr_name_to_load_name: Dict[str, str] = {}

  def _init_wire_state(
    self,
    request_timeout: float,
    command_timeout: float,
    status_poll_interval: float,
  ) -> None:
    """Check and record the three budgets, and the state the wire layer keeps.

    Shared with the chatterbox, which skips this ``__init__`` because it has no
    ``ot_api`` to talk to and would otherwise drift from what the wire layer expects.
    """
    for name, value in (
      ("request_timeout", request_timeout),
      ("command_timeout", command_timeout),
      ("status_poll_interval", status_poll_interval),
    ):
      if value <= 0:
        raise ValueError(f"{name} must be greater than 0, got {value}")
    self.request_timeout = request_timeout
    self.command_timeout = command_timeout
    self.status_poll_interval = status_poll_interval
    # Built on first use, not here: on 3.9 a Lock binds to whatever loop is current
    # when it is constructed, and a backend is routinely built before asyncio.run().
    self._request_lock: Optional[asyncio.Lock] = None
    # Set by a command timeout: the robot is still holding a command we stopped
    # waiting for, so its pose is no longer ours to describe.
    self._robot_state_unknown = False

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "host": self.host,
      "port": self.port,
    }

  async def _request(
    self,
    call: Callable[..., Any],
    *args: Any,
    timeout: Optional[float] = None,
    **kwargs: Any,
  ) -> Any:
    """Issue one ``ot_api`` call off the event loop, and give up after ``timeout``.

    ``ot_api`` reaches the robot with ``urlopen`` and passes it no socket timeout, so
    an unanswered request blocks its thread for good. Running it off the loop keeps
    the rest of the process going, and the wait_for ends OUR wait: a thread cannot be
    cancelled, so the abandoned one finishes on its own.

    The budget covers the wait for the lock as well as the request. A caller told a
    read is bounded at seven seconds must not sit behind someone else's mix for ten
    minutes first, so the clock starts before the queue, not after it.
    """

    budget = self.request_timeout if timeout is None else timeout
    try:
      return await asyncio.wait_for(self._locked_call(call, *args, **kwargs), timeout=budget)
    except asyncio.TimeoutError as exc:
      # Before 3.11 asyncio.TimeoutError is not builtins.TimeoutError, so re-raising
      # is what gives this backend one give-up type on every supported version.
      raise TimeoutError(
        f"{getattr(call, '__name__', call)} did not answer within {budget:g}s"
      ) from exc

  async def _locked_call(self, call: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """One ``ot_api`` call at a time: the robot runs one queue and ``ot_api`` keeps the
    run id in a module global.

    Per call, not per command. The blocking calls this replaced held the event loop for
    a whole command; here the lock is released between an enqueue and its polls, so
    keeping two commands off one robot is the caller's job.
    """
    if self._request_lock is None:
      self._request_lock = asyncio.Lock()
    async with self._request_lock:
      return await _call_off_loop(call, *args, **kwargs)

  async def _command(
    self,
    command_type: str,
    params: Dict[str, Any],
    timeout: Optional[float] = None,
    abandon_run_on_timeout: bool = True,
  ) -> Dict[str, Any]:
    """Enqueue one run command and wait for the robot to finish it.

    ``ot_api``'s own command wrappers are not used for anything that moves: their
    decorator hard-codes a 30s ceiling no caller can raise, and polls with no delay
    between reads. Enqueue-and-poll here honours ``command_timeout`` instead.

    ``timeout`` bounds the waiting, not the whole call: the enqueue and each status
    read carry a request budget of their own, so one unanswered request can carry the
    call past its deadline by up to ``request_timeout``. A read that does not answer
    is retried until the deadline: one lost GET says nothing about the motion, and
    ending a ten-minute mix at the eight-second mark would take the abort decision
    away from whoever asked for ten minutes.

    Set ``abandon_run_on_timeout`` False for a command that moves nothing. Giving up
    on one of those leaves no motion outstanding, so halting the robot and refusing
    everything after it would cost more than the timeout did.
    """

    self._refuse_if_state_unknown()
    budget = self.command_timeout if timeout is None else timeout
    deadline = time.monotonic() + budget
    try:
      command_id = await self._request(
        self._ot.runs.enqueue_command,
        command_type,
        params,
        intent="setup",
        timeout=min(self.request_timeout, _seconds_left(deadline)),
      )
      while True:
        # A read is a request, so it gets a request budget. Handing it whatever is
        # left of the deadline gives it milliseconds it cannot answer in, and then a
        # command the robot finished reads as a timeout.
        try:
          result: Dict[str, Any] = await self._request(self._ot.runs.get_command, command_id)
        except TimeoutError as exc:
          remaining = _seconds_left(deadline)
          if remaining <= 0:
            raise TimeoutError(f"{command_type} did not finish within {budget:g}s") from exc
          logger.warning(
            "status read for %s did not answer; %.0fs of its budget left", command_type, remaining
          )
          await asyncio.sleep(min(self.status_poll_interval, remaining))
          continue
        data = result["data"]
        status = data["status"]
        if status == "failed":
          error = data["error"]
          raise RuntimeError(f"{command_type} failed with {error['errorType']}: {error['detail']}")
        if status not in ("queued", "running"):
          return result
        # The deadline is checked after the read, so the read that follows the last
        # sleep still happens: that is the one that sees a command which finished
        # while we were asleep.
        remaining = _seconds_left(deadline)
        if remaining <= 0:
          raise TimeoutError(f"{command_type} did not finish within {budget:g}s")
        await asyncio.sleep(min(self.status_poll_interval, remaining))
    except TimeoutError:
      if abandon_run_on_timeout:
        await self._abandon_run()
      raise

  def _refuse_if_state_unknown(self) -> None:
    if self._robot_state_unknown:
      raise RuntimeError(
        "A command timed out and the OT-2 was left holding it, so its pose is "
        "unknown. Recover with setup(), which starts a fresh run the stale command "
        "cannot execute in, and homes. Check the pipettes by eye first: the OT-2 has "
        "no tip sensor, ending a run does not drop tips, and setup() records both "
        "mounts as empty, so a tip left on will be pressed into the rack."
      )

  async def _abandon_run(self) -> None:
    """Stop the run a timed-out command is still sitting in, and refuse the next one.

    Giving up on the wait does not take the command out of the robot's queue: it will
    still execute, so a caller who retries makes the robot aspirate twice from one
    well. The stop action is what prevents that, but the robot-server only schedules
    it and answers 201 straight away, so nothing here can confirm the run halted. The
    refusal is the part that holds: it stands until ``setup()`` builds a new run,
    which the stale command cannot execute in whatever the old one did.
    """
    self._robot_state_unknown = True
    run_id = getattr(self._ot, "run_id", None)
    if not run_id:
      return
    try:
      await self._stop_run(run_id)
    except Exception:
      logger.warning("could not stop run %s after a command timed out", run_id, exc_info=True)

  async def _stop_run(self, run_id: str) -> None:
    """Halt a run, so nothing still queued in it executes."""
    await self._request(
      self._ot.requestor.post,
      f"/runs/{run_id}/actions",
      {"data": {"actionType": "stop"}},
    )

  async def setup(self, skip_home: bool = False):
    # create run
    run_id = await self._request(self._ot.runs.create)
    self._ot.set_run(run_id)

    # Only now is the unknown-state refusal lifted. Creating the run is what orphans
    # whatever an earlier timeout left queued, and it is also the step most likely to
    # fail here: the robot-server answers 409 while it still holds the old run.
    self._robot_state_unknown = False

    # get pipettes, then assign them. This reads /pipettes and then loads each one,
    # so it needs the command budget rather than a single request's.
    self.left_pipette, self.right_pipette = await self._request(
      self._ot.lh.add_mounted_pipettes, timeout=self.command_timeout
    )

    self.left_pipette_has_tip = self.right_pipette_has_tip = False

    # get api version
    health = await self._request(self._ot.health.get)
    self.ot_api_version = health["api_version"]

    if not skip_home:
      await self.home()

  @property
  def num_channels(self) -> int:
    return len([p for p in [self.left_pipette, self.right_pipette] if p is not None])

  async def stop(self):
    """Cancel any active OT run, then clear labware definitions."""
    self._plr_name_to_load_name = {}
    self._tip_racks = {}
    self.left_pipette = None
    self.right_pipette = None

    # release the run so the official Opentrons app can drive the robot again. Halt
    # first: deleting a run the robot is still working through leaves it working.
    run_id = getattr(self._ot, "run_id", None)
    if run_id:
      try:
        await self._stop_run(run_id)
      except Exception:
        logger.warning("could not stop run %s", run_id, exc_info=True)
      try:
        await self._request(self._ot.requestor.delete, f"/runs/{run_id}")
      except Exception:
        logger.warning("could not delete run %s", run_id, exc_info=True)

  def get_ot_name(self, plr_resource_name: str) -> str:
    """Opentrons only allows names in ^[a-z0-9._]+$, but in PLR we are flexible.
    So we map PLR names to OT names here.
    """
    if plr_resource_name not in self._plr_name_to_load_name:
      ot_load_name = uuid.uuid4().hex
      self._plr_name_to_load_name[plr_resource_name] = ot_load_name
    return self._plr_name_to_load_name[plr_resource_name]

  def select_tip_pipette(self, tip: Tip, with_tip: bool) -> Optional[str]:
    """Select a pipette based on maximum tip volume for tip pick up or drop.

    The volume of the head must match the maximum tip volume. If both pipettes have the same
    maximum volume, the left pipette is selected.

    Args:
      with_tip: If True, get a channel that has a tip.

    Returns:
      The id of the pipette, or None if no pipette is available.
    """

    if self.can_pick_up_tip(0, tip) and with_tip == self.left_pipette_has_tip:
      assert self.left_pipette is not None
      return cast(str, self.left_pipette["pipetteId"])

    if self.can_pick_up_tip(1, tip) and with_tip == self.right_pipette_has_tip:
      assert self.right_pipette is not None
      return cast(str, self.right_pipette["pipetteId"])

    return None

  async def _assign_tip_rack(self, tip_rack: TipRack, tip: Tip):
    ot_slot_size_y = 86
    lw = {
      "schemaVersion": 2,
      "version": 1,
      "namespace": "pylabrobot",
      "metadata": {
        "displayName": self.get_ot_name(tip_rack.name),
        "displayCategory": "tipRack",
        "displayVolumeUnits": "µL",
      },
      "brand": {
        "brand": "unknown",
      },
      "parameters": {
        "format": "96Standard",
        "isTiprack": True,
        # should we get the tip length from calibration on the robot? /calibration/tip_length
        "tipLength": tip.total_tip_length,
        "tipOverlap": tip.fitting_depth,
        "loadName": self.get_ot_name(tip_rack.name),
        "isMagneticModuleCompatible": False,  # do we really care? If yes, store.
      },
      "ordering": utils.reshape_2d(
        [self.get_ot_name(tip_spot.name) for tip_spot in tip_rack.get_all_items()],
        (tip_rack.num_items_x, tip_rack.num_items_y),
      ),
      "cornerOffsetFromSlot": {
        "x": 0,
        "y": ot_slot_size_y
        - tip_rack.get_absolute_size_y(),  # hinges push it to the back (PLR is LFB, OT is LBB)
        "z": 0,
      },
      "dimensions": {
        "xDimension": tip_rack.get_absolute_size_x(),
        "yDimension": tip_rack.get_absolute_size_y(),
        "zDimension": tip_rack.get_absolute_size_z(),
      },
      "wells": {
        self.get_ot_name(child.name): {
          "depth": child.get_absolute_size_z(),
          "x": cast(Coordinate, child.location).x + child.get_absolute_size_x() / 2,
          "y": cast(Coordinate, child.location).y + child.get_absolute_size_y() / 2,
          "z": cast(Coordinate, child.location).z,
          "shape": "circular",
          "diameter": child.get_absolute_size_x(),
          "totalLiquidVolume": tip.maximal_volume,
        }
        for child in tip_rack.children
      },
      "groups": [
        {
          "wells": [self.get_ot_name(tip_spot.name) for tip_spot in tip_rack.get_all_items()],
          "metadata": {
            "displayName": None,
            "displayCategory": "tipRack",
            "wellBottomShape": "flat",  # required even for tip racks
          },
        }
      ],
    }

    data = await self._request(self._ot.labware.define, lw)
    namespace, definition, version = data["data"]["definitionUri"].split("/")

    # assign labware to robot
    labware_uuid = self.get_ot_name(tip_rack.name)

    deck = tip_rack.parent
    while deck is not None and not isinstance(deck, OTDeck):
      deck = deck.parent  # labware sits in a slot holder, whose parent is the deck
    assert isinstance(deck, OTDeck)
    slot = deck.get_slot(tip_rack)
    assert slot is not None, "tip rack must be on deck"

    await self._command(
      "loadLabware",
      {
        "location": {"slotName": str(slot)},
        "loadName": definition,
        "namespace": namespace,
        "version": version,
        "labwareId": labware_uuid,
        "displayName": self.get_ot_name(tip_rack.name),
      },
    )

    self._tip_racks[tip_rack.name] = slot

  def _get_pickup_pipette(self, ops: List[Pickup]) -> str:
    """Get the pipette for a tip pick-up, or raise."""
    assert len(ops) == 1, "only one channel supported for now"
    op = ops[0]
    assert op.resource.parent is not None, "must not be a floating resource"
    pipette_id = self.select_tip_pipette(op.tip, with_tip=False)
    if not pipette_id:
      raise NoChannelError("No pipette channel of right type with no tip available.")
    return pipette_id

  def _get_drop_pipette(self, ops: List[Drop]) -> str:
    """Get the pipette for a tip drop, or raise."""
    assert len(ops) == 1, "only one channel supported for now"
    op = ops[0]
    assert op.resource.parent is not None, "must not be a floating resource"
    pipette_id = self.select_tip_pipette(op.tip, with_tip=True)
    if not pipette_id:
      raise NoChannelError("No pipette channel of right type with tip available.")
    return pipette_id

  def _get_liquid_pipette(
    self, ops: Union[List[SingleChannelAspiration], List[SingleChannelDispense]]
  ) -> str:
    """Get the pipette for an aspirate/dispense, or raise."""
    assert len(ops) == 1, "only one channel supported for now"
    pipette_id = self.select_liquid_pipette(ops[0].volume)
    if pipette_id is None:
      raise NoChannelError("No pipette channel of right type with tip available.")
    return pipette_id

  def _set_tip_state(self, pipette_id: str, has_tip: bool):
    """Update tip-mounted state for the pipette that was used.

    This method now validates the provided ``pipette_id`` against both the left
    and right pipette configurations. It updates the state only if the ID
    matches a known, configured pipette; otherwise it raises an error to avoid
    silently putting the backend into an inconsistent state.
    """
    if self.left_pipette is not None and pipette_id == self.left_pipette["pipetteId"]:
      self.left_pipette_has_tip = has_tip
      return

    if self.right_pipette is not None and pipette_id == self.right_pipette["pipetteId"]:
      self.right_pipette_has_tip = has_tip
      return

    raise ValueError(f"Unknown or unconfigured pipette_id {pipette_id!r} in _set_tip_state.")

  async def pick_up_tips(self, ops: List[Pickup], use_channels: List[int]):
    """Pick up tips from the specified resource."""

    pipette_id = self._get_pickup_pipette(ops)
    op = ops[0]

    offset_x, offset_y, offset_z = (
      op.offset.x,
      op.offset.y,
      op.offset.z,
    )

    # define tip rack JIT if it's not already assigned
    tip_rack = op.resource.parent
    assert isinstance(tip_rack, TipRack), "TipSpot's parent must be a TipRack."
    if tip_rack.name not in self._tip_racks:
      await self._assign_tip_rack(tip_rack, op.tip)

    offset_z += op.tip.total_tip_length

    await self._command(
      "pickUpTip",
      {
        "labwareId": self.get_ot_name(tip_rack.name),
        "wellName": self.get_ot_name(op.resource.name),
        "wellLocation": _well_location(offset_x, offset_y, offset_z),
        "pipetteId": pipette_id,
      },
    )

    self._set_tip_state(pipette_id, True)

  async def drop_tips(self, ops: List[Drop], use_channels: List[int]):
    """Drop tips from the specified resource."""

    pipette_id = self._get_drop_pipette(ops)
    op = ops[0]

    use_fixed_trash = (
      cast(str, self.ot_api_version) >= _OT_DECK_IS_ADDRESSABLE_AREA_VERSION
      and op.resource.name == "trash"
    )
    if use_fixed_trash:
      labware_id = "fixedTrash"
    else:
      tip_rack = op.resource.parent
      assert isinstance(tip_rack, TipRack), "TipSpot's parent must be a TipRack."
      if tip_rack.name not in self._tip_racks:
        await self._assign_tip_rack(tip_rack, op.tip)
      labware_id = self.get_ot_name(tip_rack.name)

    offset_x, offset_y, offset_z = (
      op.offset.x,
      op.offset.y,
      op.offset.z,
    )

    # ad-hoc offset adjustment that makes it smoother.
    offset_z += 10

    if use_fixed_trash:
      await self._command(
        "moveToAddressableAreaForDropTip",
        {
          "pipetteId": pipette_id,
          "addressableAreaName": "fixedTrash",
          "wellName": "A1",
          "wellLocation": _well_location(offset_x, offset_y, offset_z, origin="default"),
          "alternateDropLocation": False,
        },
      )
      await self._command("dropTipInPlace", {"pipetteId": pipette_id})
    else:
      await self._command(
        "dropTip",
        {
          "labwareId": labware_id,
          "wellName": self.get_ot_name(op.resource.name),
          "wellLocation": _well_location(offset_x, offset_y, offset_z),
          "pipetteId": pipette_id,
        },
      )

    self._set_tip_state(pipette_id, False)

  def select_liquid_pipette(self, volume: float) -> Optional[str]:
    """Select a pipette based on volume for an aspiration or dispense.

    The volume of the tip mounted on the head must be greater than the volume to aspirate or
    dispense. If both pipettes have the same maximum volume, the left pipette is selected.

    Only heads with a tip are considered.

    Args:
      volume: The volume to aspirate or dispense.

    Returns:
      The id of the pipette, or None if no pipette is available.
    """

    if self.left_pipette is not None:
      left_volume = OpentronsOT2Backend.pipette_name2volume[self.left_pipette["name"]]
      if left_volume >= volume and self.left_pipette_has_tip:
        return cast(str, self.left_pipette["pipetteId"])

    if self.right_pipette is not None:
      right_volume = OpentronsOT2Backend.pipette_name2volume[self.right_pipette["name"]]
      if right_volume >= volume and self.right_pipette_has_tip:
        return cast(str, self.right_pipette["pipetteId"])

    return None

  def get_pipette_name(self, pipette_id: str) -> str:
    """Get the name of a pipette from its id."""

    if self.left_pipette is not None and pipette_id == self.left_pipette["pipetteId"]:
      return cast(str, self.left_pipette["name"])
    if self.right_pipette is not None and pipette_id == self.right_pipette["pipetteId"]:
      return cast(str, self.right_pipette["name"])
    raise ValueError(f"Unknown pipette id: {pipette_id}")

  def _get_default_aspiration_flow_rate(self, pipette_name: str) -> float:
    """Get the default aspiration flow rate for the specified pipette in uL/s.

    Data from https://archive.ph/ZUN9f
    """

    return {
      "p300_multi_gen2": 94,
      "p10_single": 5,
      "p10_multi": 5,
      "p50_single": 25,
      "p50_multi": 25,
      "p300_single": 150,
      "p300_multi": 150,
      "p1000_single": 500,
      "p20_single_gen2": 3.78,
      "p300_single_gen2": 46.43,
      "p1000_single_gen2": 137.35,
      "p20_multi_gen2": 7.6,
    }[pipette_name]

  def _deck_to_robot_frame(self, location: Coordinate) -> Coordinate:
    """Convert a deck-frame coordinate to the OT-2 robot frame.

    pylabrobot positions OT deck slots from the deck plate corner, whereas the OT-2 motion API
    expects coordinates in the robot frame whose origin is slot 1's corner. The two frames differ by
    slot 1's position in the deck frame, so subtract it.
    """
    return location - cast(OTDeck, self.deck).slot_locations[0]

  async def aspirate(self, ops: List[SingleChannelAspiration], use_channels: List[int]):
    """Aspirate liquid from the specified resource using pip."""

    pipette_id = self._get_liquid_pipette(ops)
    op = ops[0]
    volume = op.volume

    pipette_name = self.get_pipette_name(pipette_id)
    flow_rate = op.flow_rate or self._get_default_aspiration_flow_rate(pipette_name)

    location = self._deck_to_robot_frame(
      op.resource.get_location_wrt(self.deck, "c", "c", "cavity_bottom")
      + op.offset
      + Coordinate(z=op.liquid_height or 0)
    )

    await self.move_pipette_head(
      location=location,
      minimum_z_height=self.traversal_height,
      pipette_id=pipette_id,
    )

    if op.mix is not None:
      for _ in range(op.mix.repetitions):
        await self._command(
          "aspirateInPlace", _in_place(op.mix.volume, op.mix.flow_rate, pipette_id)
        )
        await self._command(
          "dispenseInPlace", _in_place(op.mix.volume, op.mix.flow_rate, pipette_id, push_out=False)
        )

    await self._command("aspirateInPlace", _in_place(volume, flow_rate, pipette_id))

    traversal_location = self._deck_to_robot_frame(
      op.resource.get_location_wrt(self.deck, "c", "c", "cavity_bottom") + op.offset
    )
    traversal_location.z = self.traversal_height
    await self.move_pipette_head(
      location=traversal_location,
      minimum_z_height=self.traversal_height,
      pipette_id=pipette_id,
    )

  def _get_default_dispense_flow_rate(self, pipette_name: str) -> float:
    """Get the default dispense flow rate for the specified pipette.

    Data from https://archive.ph/ZUN9f

    Returns:
      The default flow rate in ul/s.
    """

    return {
      "p300_multi_gen2": 94,
      "p10_single": 10,
      "p10_multi": 10,
      "p50_single": 50,
      "p50_multi": 50,
      "p300_single": 300,
      "p300_multi": 300,
      "p1000_single": 1000,
      "p20_single_gen2": 7.56,
      "p300_single_gen2": 92.86,
      "p1000_single_gen2": 274.7,
      "p20_multi_gen2": 7.6,
    }[pipette_name]

  async def dispense(self, ops: List[SingleChannelDispense], use_channels: List[int]):
    """Dispense liquid from the specified resource using pip."""

    pipette_id = self._get_liquid_pipette(ops)
    op = ops[0]
    volume = op.volume

    pipette_name = self.get_pipette_name(pipette_id)
    flow_rate = op.flow_rate or self._get_default_dispense_flow_rate(pipette_name)

    location = self._deck_to_robot_frame(
      op.resource.get_location_wrt(self.deck, "c", "c", "cavity_bottom")
      + op.offset
      + Coordinate(z=op.liquid_height or 0)
    )
    await self.move_pipette_head(
      location=location,
      minimum_z_height=self.traversal_height,
      pipette_id=pipette_id,
    )

    await self._command("dispenseInPlace", _in_place(volume, flow_rate, pipette_id, push_out=False))

    if op.mix is not None:
      for _ in range(op.mix.repetitions):
        await self._command(
          "aspirateInPlace", _in_place(op.mix.volume, op.mix.flow_rate, pipette_id)
        )
        await self._command(
          "dispenseInPlace", _in_place(op.mix.volume, op.mix.flow_rate, pipette_id, push_out=False)
        )

    traversal_location = self._deck_to_robot_frame(
      op.resource.get_location_wrt(self.deck, "c", "c", "cavity_bottom") + op.offset
    )
    traversal_location.z = self.traversal_height
    await self.move_pipette_head(
      location=traversal_location,
      minimum_z_height=self.traversal_height,
      pipette_id=pipette_id,
    )

  async def home(self):
    await self._request(self._ot.health.home, timeout=self.command_timeout)

  async def pick_up_tips96(self, pickup: PickupTipRack):
    raise NotImplementedError("The Opentrons backend does not support the 96 head.")

  async def drop_tips96(self, drop: DropTipRack):
    raise NotImplementedError("The Opentrons backend does not support the 96 head.")

  async def aspirate96(
    self, aspiration: Union[MultiHeadAspirationPlate, MultiHeadAspirationContainer]
  ):
    raise NotImplementedError("The Opentrons backend does not support the 96 head.")

  async def dispense96(self, dispense: Union[MultiHeadDispensePlate, MultiHeadDispenseContainer]):
    raise NotImplementedError("The Opentrons backend does not support the 96 head.")

  async def pick_up_resource(self, pickup: ResourcePickup):
    raise NotImplementedError("The Opentrons backend does not support the robotic arm.")

  async def move_picked_up_resource(self, move: ResourceMove):
    raise NotImplementedError("The Opentrons backend does not support the robotic arm.")

  async def drop_resource(self, drop: ResourceDrop):
    raise NotImplementedError("The Opentrons backend does not support the robotic arm.")

  async def list_connected_modules(self) -> List[dict]:
    """List all connected temperature modules."""
    return cast(List[dict], await self._request(self._ot.modules.list_connected_modules))

  def _pipette_id_for_channel(self, channel: int) -> str:
    pipettes = []
    if self.left_pipette is not None:
      pipettes.append(self.left_pipette["pipetteId"])
    if self.right_pipette is not None:
      pipettes.append(self.right_pipette["pipetteId"])
    if channel < 0 or channel >= len(pipettes):
      raise NoChannelError(f"Channel {channel} not available on this OT-2 setup.")
    return pipettes[channel]

  async def _save_position(self, pipette_id: str) -> Dict[str, Any]:
    """Ask the robot where a pipette is, and wait for the answer.

    A read rather than a move, so it gets the request budget rather than the command
    one: nothing here waits on motion.
    """

    return await self._command(
      "savePosition",
      {"pipetteId": pipette_id},
      timeout=self.request_timeout,
      abandon_run_on_timeout=False,
    )

  async def _current_channel_position(self, channel: int) -> Tuple[str, Coordinate]:
    """Return the pipette id and current coordinate for a given channel."""

    pipette_id = self._pipette_id_for_channel(channel)
    try:
      res = await self._save_position(pipette_id)
      pos = res["data"]["result"]["position"]
      current = Coordinate(pos["x"], pos["y"], pos["z"])
    except Exception as exc:
      raise RuntimeError(f"Failed to query current pipette position: {exc}") from exc

    return pipette_id, current

  async def prepare_for_manual_channel_operation(self, channel: int):
    """Validate channel exists (no-op otherwise for OT-2)."""

    _ = self._pipette_id_for_channel(channel)

  async def get_channel_position(self, channel: int) -> Coordinate:
    """Where a channel is right now, in the OT-2 robot frame (this file's own name
    for the frame ``_deck_to_robot_frame`` converts PLR deck coordinates into)."""

    _, current = await self._current_channel_position(channel)
    return current

  async def move_channel_to(
    self,
    channel: int,
    x: Optional[float] = None,
    y: Optional[float] = None,
    z: Optional[float] = None,
  ):
    """Move a channel to an absolute position, holding the axes left out.

    One coordinated move rather than the per-axis calls chained: the robot lifts to the traversal
    height and travels once, where three separate moves each descend and can clip labware between
    them.
    """

    pipette_id, current = await self._current_channel_position(channel)
    target = Coordinate(
      x=current.x if x is None else x,
      y=current.y if y is None else y,
      z=current.z if z is None else z,
    )
    await self.move_pipette_head(
      location=target, minimum_z_height=self.traversal_height, pipette_id=pipette_id
    )

  async def move_channel_x(self, channel: int, x: float):
    """Move a channel to an absolute x coordinate using savePosition to seed pose."""

    pipette_id, current = await self._current_channel_position(channel)
    target = Coordinate(x=x, y=current.y, z=current.z)
    await self.move_pipette_head(
      location=target, minimum_z_height=self.traversal_height, pipette_id=pipette_id
    )

  async def move_channel_y(self, channel: int, y: float):
    """Move a channel to an absolute y coordinate using savePosition to seed pose."""

    pipette_id, current = await self._current_channel_position(channel)
    target = Coordinate(x=current.x, y=y, z=current.z)
    await self.move_pipette_head(
      location=target, minimum_z_height=self.traversal_height, pipette_id=pipette_id
    )

  async def move_channel_z(self, channel: int, z: float):
    """Move a channel to an absolute z coordinate using savePosition to seed pose."""

    pipette_id, current = await self._current_channel_position(channel)
    target = Coordinate(x=current.x, y=current.y, z=z)
    await self.move_pipette_head(
      location=target, minimum_z_height=self.traversal_height, pipette_id=pipette_id
    )

  async def move_pipette_head(
    self,
    location: Coordinate,
    speed: Optional[float] = None,
    minimum_z_height: Optional[float] = None,
    pipette_id: Optional[str] = None,
    force_direct: bool = False,
  ):
    """Move the pipette head to the specified location. When a tip is mounted, the location refers
    to the bottom of the tip. If no tip is mounted, the location refers to the bottom of the
    pipette head.

    Args:
      location: The location to move to.
      speed: The speed to move at, in mm/s.
      minimum_z_height: The minimum z height to move to. Appears to be broken in the Opentrons API.
      pipette_id: The id of the pipette to move. If `"left"` or `"right"`, the left or right
        pipette is used.
      force_direct: If True, move the pipette head directly in all dimensions.
    """

    if self.left_pipette is not None and pipette_id == "left":
      pipette_id = self.left_pipette["pipetteId"]
    elif self.right_pipette is not None and pipette_id == "right":
      pipette_id = self.right_pipette["pipetteId"]

    if pipette_id is None:
      raise ValueError("No pipette id given or left/right pipette not available.")

    params: Dict[str, Any] = {
      "pipetteId": pipette_id,
      "coordinates": {"x": location.x, "y": location.y, "z": location.z},
      "forceDirect": force_direct,
    }
    if minimum_z_height is not None:
      params["minimumZHeight"] = minimum_z_height
    if speed is not None:
      params["speed"] = speed
    await self._command("moveToCoordinates", params)

  def can_pick_up_tip(self, channel_idx: int, tip: Tip) -> bool:
    def supports_tip(channel_vol: float, tip_vol: float) -> bool:
      if channel_vol == 20:
        return tip_vol in {10, 20}
      if channel_vol == 300:
        return tip_vol in {200, 300}
      if channel_vol == 1000:
        return tip_vol in {1000}
      raise ValueError(f"Unknown channel volume: {channel_vol}")

    if channel_idx == 0:
      if self.left_pipette is None:
        return False
      left_volume = OpentronsOT2Backend.pipette_name2volume[self.left_pipette["name"]]
      return supports_tip(left_volume, tip.maximal_volume)
    if channel_idx == 1:
      if self.right_pipette is None:
        return False
      right_volume = OpentronsOT2Backend.pipette_name2volume[self.right_pipette["name"]]
      return supports_tip(right_volume, tip.maximal_volume)
    return False
