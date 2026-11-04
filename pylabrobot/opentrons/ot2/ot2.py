"""OT-2 lifecycle, deck ownership, and physical pipette discovery."""

import math
from typing import List, Optional, Tuple, Union

import anyio

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.io.http import HTTP
from pylabrobot.opentrons.api import HTTP_API_VERSION, OpentronsAPI
from pylabrobot.opentrons.labware import (
  LabwareRegistry,
  build_tip_rack_definition,
  official_tip_rack_identity,
)
from pylabrobot.opentrons.ot2.pipette import (
  _PIPETTE_SPECS,
  OT2_8ChannelPipette,
  OT2SingleChannelPipette,
)
from pylabrobot.opentrons.run import OpentronsRun, RobotOperationLock
from pylabrobot.opentrons.types import (
  LabwareIdentity,
  ModuleInfo,
  MountedPipette,
  RobotInfo,
  RunInfo,
)
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.opentrons import OT2RobotGeometry, OTDeck
from pylabrobot.resources.tip import Tip
from pylabrobot.resources.tip_rack import TipRack


class OT2(HasLifespan):
  """Opentrons OT-2 controlled through its robot-server HTTP API.

  The lifespan owns its default HTTP transport and run. ``query_only=True``
  scopes discovery without creating a run or moving the robot. Physical
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
    super().__init__()
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
    self._owns_io = io is None
    self.io = (
      io
      if io is not None
      else HTTP(
        human_readable_device_name="Opentrons OT-2",
        base_url=f"http://{host}:{port}",
        headers={"Opentrons-Version": HTTP_API_VERSION},
        timeout=command_timeout,
      )
    )
    self._api = OpentronsAPI(self.io)
    self._connected = False
    self._run: Optional[OpentronsRun] = None
    self._labware: Optional[LabwareRegistry] = None
    self.left_pipette: Optional[Union[OT2SingleChannelPipette, OT2_8ChannelPipette]] = None
    self.right_pipette: Optional[Union[OT2SingleChannelPipette, OT2_8ChannelPipette]] = None
    self._operation_lock = RobotOperationLock()

  async def _enter_lifespan(
    self,
    stack: AsyncExitStackWithShielding,
    *,
    skip_home: bool = False,
    query_only: bool = False,
  ) -> None:
    """Own HTTP if constructed here and release the run before HTTP closes."""
    await super()._enter_lifespan(stack)
    if self._owns_io:
      await stack.enter_async_context(self.io.lifespan())
    self._connected = True
    stack.callback(self._clear_connection)
    async with self._operation_lock:
      if query_only:
        return
      if self._run is not None:
        await self._stop_run()
      health = await self.get_health()
      mounted = await self.get_mounted_pipettes()
      for pipette in mounted:
        if pipette.name not in _PIPETTE_SPECS:
          raise ValueError(f"Unsupported OT-2 pipette {pipette.name!r} on {pipette.mount}")
      await anyio.lowlevel.checkpoint()
      with anyio.CancelScope(shield=True):
        receipt = await self._api.create_run()
        self._run = OpentronsRun(
          self._api,
          receipt.id,
          health.software_version,
          command_timeout=self.command_timeout,
          command_poll_interval=self.command_poll_interval,
        )
        stack.push_shielded_async_callback(self._stop_run, shield_timeout=self.command_timeout + 5)
        self._labware = LabwareRegistry(self._run)
      await self._load_pipettes(mounted)
      if not skip_home:
        await anyio.lowlevel.checkpoint()
        with anyio.CancelScope(shield=True):
          await self._api.home()
        await anyio.lowlevel.checkpoint()

  def _clear_connection(self) -> None:
    """Invalidate the query-only connection after run cleanup."""
    self._connected = False

  async def _stop_run(self) -> None:
    """Stop the selected run while its transport is still available."""
    async with self._operation_lock:
      if self._run is not None:
        await self._run.stop()
      self._run = None
      self._labware = None
      self.left_pipette = None
      self.right_pipette = None

  @property
  def pipettes(self) -> List[Union[OT2SingleChannelPipette, OT2_8ChannelPipette]]:
    """Mounted pipettes, left first."""
    return [p for p in (self.left_pipette, self.right_pipette) if p is not None]

  @property
  def software_version(self) -> Optional[str]:
    """Software version used to configure the active run, or None before setup."""
    return self._run.software_version if self._run is not None else None

  async def connect(self) -> None:
    """Require an active lifespan for query-only access."""
    self._require_connected()

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
    """Home the gantry and pipette axes."""
    async with self._operation_lock:
      self._require_run()
      await anyio.lowlevel.checkpoint()
      with anyio.CancelScope(shield=True):
        await self._api.home()
      await anyio.lowlevel.checkpoint()

  async def _load_tip_rack(self, tip_rack: TipRack, tip: Tip) -> None:
    slot = self.deck.get_slot(tip_rack)
    if slot is None:
      raise ValueError("tip rack must be assigned directly to an OT-2 deck slot")
    registry = self._require_labware()
    identity: Optional[LabwareIdentity]
    if registry.is_loaded(tip_rack):
      identity = registry.get(tip_rack).identity
    else:
      identity = official_tip_rack_identity(tip_rack)
      if identity is None:
        definition = build_tip_rack_definition(tip_rack, tip)
        identity = await registry.define(tip_rack, definition)
    await registry.load(tip_rack, str(slot), identity)

  def _deck_to_robot_frame(self, location: Coordinate) -> Coordinate:
    return location - self.deck.slot_locations[0]
