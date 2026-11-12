"""OT-2 lifecycle, deck ownership, and physical pipette discovery."""

import asyncio
import math
import time
from typing import List, Optional, Tuple, Union

from pylabrobot.io.http import HTTP, HTTPError
from pylabrobot.opentrons.api import HTTP_API_VERSION, OpentronsAPI
from pylabrobot.opentrons.labware import (
  LabwareRegistry,
  build_tip_rack_definition,
)
from pylabrobot.opentrons.operations import OperationLock
from pylabrobot.opentrons.ot2.pipette import (
  _PIPETTE_SPECS,
  OT2_8ChannelPipette,
  OT2SingleChannelPipette,
)
from pylabrobot.opentrons.run import OpentronsRun
from pylabrobot.opentrons.types import (
  DoorStatus,
  ModuleInfo,
  MountedPipette,
  RobotInfo,
  RunInfo,
)
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.opentrons import OT2RobotGeometry, OTDeck
from pylabrobot.resources.tip import Tip
from pylabrobot.resources.tip_rack import TipRack


class OT2:
  """Opentrons OT-2 controlled through its robot-server HTTP API.

  ``connect()`` opens the transport for queries. ``setup()`` also creates a
  run, binds mounted pipettes, and optionally homes the robot. Physical
  operations are exposed on ``left_pipette`` and ``right_pipette``.
  """

  def __init__(
    self,
    host: str,
    port: int = 31950,
    deck: Optional[OTDeck] = None,
    traversal_height: float = 120,
    command_timeout: float = 30,
    command_poll_interval: float = 0.05,
    io: Optional[HTTP] = None,
  ) -> None:
    if "://" in host:
      raise ValueError("host must be a hostname or IP address without a URL scheme")
    if not 1 <= port <= 65535:
      raise ValueError("port must be between 1 and 65535")
    if not math.isfinite(traversal_height) or traversal_height < 0:
      raise ValueError("traversal_height must be finite and non-negative")
    if not math.isfinite(command_timeout) or command_timeout <= 0:
      raise ValueError("command_timeout must be finite and greater than zero")
    if not math.isfinite(command_poll_interval) or command_poll_interval < 0:
      raise ValueError("command_poll_interval must be finite and non-negative")

    self.host = host
    self.port = port
    self.deck = deck or OTDeck()
    self.geometry = OT2RobotGeometry()
    self.traversal_height = traversal_height
    self.command_timeout = command_timeout
    self.command_poll_interval = command_poll_interval
    self.io = io or HTTP(
      human_readable_device_name="Opentrons OT-2",
      base_url=f"http://{host}:{port}",
      headers={"Opentrons-Version": HTTP_API_VERSION},
      timeout=command_timeout,
    )
    self._api = OpentronsAPI(self.io)
    self._connected = False
    self._run: Optional[OpentronsRun] = None
    self._labware: Optional[LabwareRegistry] = None
    self.left_pipette: Optional[Union[OT2SingleChannelPipette, OT2_8ChannelPipette]] = None
    self.right_pipette: Optional[Union[OT2SingleChannelPipette, OT2_8ChannelPipette]] = None
    self._operation_lock = OperationLock()

  @property
  def pipettes(self) -> List[Union[OT2SingleChannelPipette, OT2_8ChannelPipette]]:
    """Mounted pipettes, left first."""
    return [p for p in (self.left_pipette, self.right_pipette) if p is not None]

  @property
  def software_version(self) -> Optional[str]:
    """Software version used to configure the active run, or None before setup."""
    return self._run.software_version if self._run is not None else None

  async def connect(self) -> None:
    """Open the transport for queries, without creating a run or moving the robot."""
    async with self._operation_lock:
      await self._connect()

  async def _connect(self) -> None:
    if not self._connected:
      await self.io.setup()
      self._connected = True

  def _require_connected(self) -> None:
    if not self._connected:
      raise RuntimeError("The OT-2 is not connected; call connect() or setup() first")

  async def get_health(self) -> RobotInfo:
    """Return a fresh health reading without changing device or run state."""
    self._require_connected()
    return await self._api.get_health()

  async def get_mounted_pipettes(self) -> Tuple[MountedPipette, ...]:
    """Read physical pipette identities without loading or replacing pipette objects."""
    self._require_connected()
    return await self._api.get_mounted_pipettes()

  async def list_connected_modules(self) -> Tuple[ModuleInfo, ...]:
    """Read connected module identities without loading modules into the run."""
    self._require_connected()
    return await self._api.get_connected_modules()

  async def get_runs(self) -> Tuple[RunInfo, ...]:
    """Read server runs without selecting, stopping, or replacing the active run."""
    self._require_connected()
    return await self._api.get_runs()

  async def get_run(self, run_id: str) -> RunInfo:
    """Read one server run by id, without selecting or stopping it."""
    self._require_connected()
    return await self._api.get_run(run_id)

  async def get_unfinished_run(self) -> Optional[RunInfo]:
    """Return the robot's current run when it is not finished, or None.

    Such a run is left by another client or an earlier session: a script stopped half way, or a
    protocol loaded or paused in the Opentrons App. The robot creates no other run beside it.
    """
    for run in await self.get_runs():
      if run.current and not run.finished:
        return run
    return None

  async def stop_run(self, run_id: str, timeout: Optional[float] = None) -> RunInfo:
    """Stop a run the robot holds and wait until the robot reports it finished.

    For a run this object did not create, such as the one ``get_unfinished_run`` returns. A run
    already finished is left as it is. This object's own run is stopped by ``stop()``.

    Args:
      run_id: the run to stop.
      timeout: how long to wait for it to finish, in s. ``command_timeout`` when None.

    Returns:
      The run as last read.

    Raises:
      ValueError: If ``timeout`` is not finite and greater than zero.
      RuntimeError: If ``run_id`` is this object's own run.
      OpentronsError: If the robot does not take the stop request.
      TimeoutError: If the run has not finished within ``timeout``.
    """
    wait = self.command_timeout if timeout is None else timeout
    if not math.isfinite(wait) or wait <= 0:
      raise ValueError("timeout must be finite and greater than zero")
    async with self._operation_lock:
      self._require_connected()
      if self._run is not None and self._run.id == run_id:
        raise RuntimeError("This is the OT-2's own run; stop it with stop()")
      run = await self._api.get_run(run_id)
      if run.finished:
        return run
      await self._api.stop_run(run_id)
      deadline = time.monotonic() + wait
      while True:
        try:
          run = await self._api.get_run(run_id)
        except HTTPError as error:
          if error.status == 404:  # The legacy stop route can delete the run.
            return RunInfo(run_id, "stopped")
          raise
        if run.finished:
          return run
        if time.monotonic() >= deadline:
          raise TimeoutError(
            f"Run {run_id} did not finish within {wait:g} s (status {run.status!r})"
          )
        await asyncio.sleep(self.command_poll_interval)

  async def get_rail_lights(self) -> bool:
    """Read whether the rail lights are on. Needs a connection, not a run."""
    self._require_connected()
    return await self._api.get_rail_lights()

  async def set_rail_lights(self, on: bool) -> None:
    """Turn the rail lights on or off. Needs a connection, not a run.

    Args:
      on: True for on, False for off.

    Raises:
      TypeError: If ``on`` is not a bool.
    """
    if not isinstance(on, bool):
      raise TypeError(f"on must be a bool, got {on!r}")
    async with self._operation_lock:
      self._require_connected()
      await self._api.set_rail_lights(on)

  async def get_door_status(self) -> DoorStatus:
    """Read the front door's switch. Needs a connection, not a run."""
    self._require_connected()
    return await self._api.get_door_status()

  async def setup(self, skip_home: bool = False) -> None:
    """Connect, discover and bind pipettes to a run, and optionally home.

    Raises:
      RuntimeError: If the robot holds an unfinished run (see ``get_unfinished_run``).
    """
    async with self._operation_lock:
      if self._run is not None:
        raise RuntimeError("The OT-2 is already set up")
      await self._connect()
      try:
        health = await self.get_health()
        mounted = await self.get_mounted_pipettes()
        for pipette in mounted:
          if pipette.name not in _PIPETTE_SPECS:
            raise ValueError(f"Unsupported OT-2 pipette {pipette.name!r} on {pipette.mount}")
        unfinished = await self.get_unfinished_run()
        if unfinished is not None:
          raise RuntimeError(
            f"The OT-2 holds an unfinished run {unfinished.id} ({unfinished.status}, created "
            f"{unfinished.created_at}), so it cannot create its own. Stop it with stop_run() or "
            "the Opentrons App."
          )
        receipt = await self._api.create_run()
        self._run = OpentronsRun(
          self._api,
          receipt.id,
          health.software_version,
          command_timeout=self.command_timeout,
          command_poll_interval=self.command_poll_interval,
        )
        self._labware = LabwareRegistry(self._run)
        await self._load_pipettes(mounted)
        if not skip_home:
          await self._api.home()
      except BaseException:
        await self._stop()
        raise

  async def _load_pipettes(self, mounted: Tuple[MountedPipette, ...]) -> None:
    run = self._require_run()
    for pipette in mounted:
      pipette_id = await run.load_pipette(pipette.name, pipette.mount)
      bound: Union[OT2SingleChannelPipette, OT2_8ChannelPipette]
      if _PIPETTE_SPECS[pipette.name].channels == 8:
        bound = OT2_8ChannelPipette(self, pipette.mount, pipette.name, pipette_id)
      else:
        bound = OT2SingleChannelPipette(self, pipette.mount, pipette.name, pipette_id)
      if pipette.mount == "left":
        self.left_pipette = bound
      else:
        self.right_pipette = bound

  async def stop(self) -> None:
    """Stop the run and close the transport, retaining state if run shutdown fails."""
    async with self._operation_lock:
      await self._stop()

  async def _stop(self) -> None:
    if self._run is not None:
      await self._run.stop()
    self._run = None
    self._labware = None
    self.left_pipette = None
    self.right_pipette = None
    if self._connected:
      await self.io.stop()
      self._connected = False

  def _require_run(self) -> OpentronsRun:
    if self._run is None or not self._run.active:
      raise RuntimeError("The OT-2 is not set up")
    return self._run

  def _require_labware(self) -> LabwareRegistry:
    self._require_run()
    if self._labware is None:
      raise RuntimeError("The OT-2 labware registry is unavailable")
    return self._labware

  async def home(self) -> None:
    """Home the gantry and pipette axes.

    Homing is a robot endpoint rather than a run command, so it needs a connection and no run:
    after ``stop_run``, it brings the robot back to a known position before ``setup()``.
    """
    async with self._operation_lock:
      self._require_connected()
      await self._api.home()

  async def _load_tip_rack(self, tip_rack: TipRack, tip: Tip) -> None:
    slot = self.deck.get_slot(tip_rack)
    if slot is None:
      raise ValueError("tip rack must be assigned directly to an OT-2 deck slot")
    registry = self._require_labware()
    if registry.is_loaded(tip_rack):
      identity = registry.get(tip_rack).identity
    else:
      definition = build_tip_rack_definition(tip_rack, tip)
      identity = await registry.define(tip_rack, definition)
    await registry.load(tip_rack, str(slot), identity)

  def _deck_to_robot_frame(self, location: Coordinate) -> Coordinate:
    return location - self.deck.slot_locations[0]
