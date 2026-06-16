"""Nimbus device: orchestrates transport, instrument info, and peer construction."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.liquid_handling.pip import PIP
from pylabrobot.device import Device
from pylabrobot.resources.hamilton.nimbus_decks import NimbusDeck

from .channels import NimbusChannelMap
from .chatterbox import NimbusChatterboxDriver
from .commands import InitializeSmartRoll, Park, SetChannelConfiguration
from .core import NimbusCoreGripper, NimbusCoreGripperFactory, NimbusGripperArm
from .door import NimbusDoor
from .driver import NimbusDriver, NimbusSetupParams
from .info import NimbusInstrumentInfo
from .pip_backend import NimbusPIPBackend

logger = logging.getLogger(__name__)


class Nimbus(Device):
  """Hamilton Nimbus liquid handler.

  Setup connects to firmware, bootstraps instrument info, initializes channels,
  and constructs all peers (PIP, door, CoRe gripper factory).
  """

  def __init__(
    self,
    deck: NimbusDeck,
    chatterbox: bool = False,
    host: Optional[str] = None,
    port: int = 2000,
  ):
    if chatterbox:
      driver: NimbusDriver = NimbusChatterboxDriver()
    else:
      if not host:
        raise ValueError("host must be provided when chatterbox is False.")
      driver = NimbusDriver(host=host, port=port)
    super().__init__(driver=driver)
    self.driver: NimbusDriver = driver
    self.deck = deck
    self.info: NimbusInstrumentInfo = NimbusInstrumentInfo(driver)
    self.pip: Optional[PIP] = None
    self.door: Optional[NimbusDoor] = None
    self._core_factory: Optional[NimbusCoreGripperFactory] = None
    self._core_gripper_arm: Optional[NimbusGripperArm] = None

  def _normalize_setup_params(self, backend_params: Optional[BackendParams]) -> NimbusSetupParams:
    if backend_params is None:
      return NimbusSetupParams(deck=self.deck)
    if isinstance(backend_params, NimbusSetupParams):
      if backend_params.deck is None:
        return NimbusSetupParams(
          deck=self.deck,
          require_door_lock=backend_params.require_door_lock,
          force_initialize=backend_params.force_initialize,
        )
      return backend_params
    raise TypeError(
      "Nimbus.setup expected NimbusSetupParams | None for backend_params, "
      f"got {type(backend_params).__name__}"
    )

  async def setup(self, backend_params: Optional[BackendParams] = None):
    """Connect, bootstrap info, initialize SmartRoll, construct peers."""
    params = self._normalize_setup_params(backend_params)
    try:
      await self.driver.setup(backend_params=params)
      await self.info._on_setup()
      await self._initialize_instrument(params)

      channel_map = NimbusChannelMap.from_info(self.info)
      pipette_address = await self.driver.resolve_path("NimbusCORE.Pipette")
      pip_backend = NimbusPIPBackend(
        driver=self.driver,
        deck=params.deck,
        address=pipette_address,
        num_channels=self.info.num_channels,
        channel_map=channel_map,
      )
      self.pip = PIP(backend=pip_backend)
      self._capabilities = [self.pip]
      await self.pip._on_setup()

      door_address = await self._try_resolve("NimbusCORE.DoorLock")
      if door_address is not None:
        self.door = NimbusDoor(driver=self.driver)
        await self.door._on_setup()
      elif params.require_door_lock:
        raise RuntimeError("DoorLock is required but not available on this instrument.")

      self._core_factory = NimbusCoreGripperFactory(driver=self.driver)
      self._setup_finished = True
    except Exception:
      await self.info._on_stop()
      await self.driver.stop()
      raise

  async def _try_resolve(self, path: str):
    """Resolve a firmware path; return None if absent."""
    try:
      return await self.driver.resolve_path(path)
    except (KeyError, RuntimeError):
      return None

  async def _initialize_instrument(self, params: NimbusSetupParams) -> None:
    """Run InitializeSmartRoll when the instrument reports as uninitialized."""
    if not params.force_initialize:
      try:
        already = await self.info.is_initialized()
      except Exception as e:
        logger.error("IsInitialized failed; cannot decide whether to init: %s", e)
        raise
      if already:
        logger.info("Nimbus already initialized, skipping SmartRoll init")
        return

    await self._initialize_smart_roll(params)
    logger.info(
      "Nimbus initialization complete%s",
      " (force_initialize=True)" if params.force_initialize else "",
    )

  async def _initialize_smart_roll(self, params: NimbusSetupParams) -> None:
    """Configure channels and run InitializeSmartRoll with waste positions."""
    if params.deck is None:
      raise RuntimeError("Deck must be provided to run InitializeSmartRoll.")

    num_channels = self.info.num_channels
    for channel in range(1, num_channels + 1):
      await self.driver.send_command(
        SetChannelConfiguration(
          channel=channel,
          indexes=[1, 3, 4],
          enables=[True, False, False, False],
        )
      )
    logger.info("Channel configuration set for %d channels", num_channels)

    # Build a temporary pip_backend to use the waste coordinate helpers.
    # The real one is constructed after this method returns.
    pipette_address = await self.driver.resolve_path("NimbusCORE.Pipette")
    temp_pip = NimbusPIPBackend(
      driver=self.driver,
      deck=params.deck,
      address=pipette_address,
      num_channels=num_channels,
    )
    all_channels = list(range(num_channels))
    (
      x_positions,
      y_positions,
      begin_tip_deposit,
      end_tip_deposit,
      z_end,
      roll_distances,
    ) = temp_pip._build_waste_position_params(use_channels=all_channels)

    await self.driver.send_command(
      InitializeSmartRoll(
        x_positions=x_positions,
        y_positions=y_positions,
        begin_tip_deposit_process=begin_tip_deposit,
        end_tip_deposit_process=end_tip_deposit,
        z_position_at_end_of_a_command=z_end,
        roll_distances=roll_distances,
      )
    )
    logger.info("NimbusCORE initialized with InitializeSmartRoll successfully")

  async def stop(self):
    """Tear down all peers and close the driver connection."""
    if not self._setup_finished:
      return
    if self._core_gripper_arm is not None:
      logger.warning(
        "Nimbus.stop() called with CoRe grippers still mounted. "
        "Call `await nimbus.return_core_grippers()` first if you want the tools returned."
      )
      self._core_gripper_arm = None
    if self.pip is not None:
      await self.pip._on_stop()
    await self.info._on_stop()
    await self.driver.stop()
    self._capabilities = []
    self.pip = None
    self.door = None
    self._core_factory = None
    self._setup_finished = False

  # -- CoRe grippers ------------------------------------------------------------

  @property
  def core_gripper_arm(self) -> NimbusGripperArm:
    """The mounted CoRe gripper arm. Raises if grippers are not currently picked up."""
    if self._core_gripper_arm is None:
      raise RuntimeError(
        "CoRe grippers not mounted. Call `await nimbus.pick_up_core_grippers()` first, "
        "or use `async with nimbus.core_grippers() as arm:`."
      )
    return self._core_gripper_arm

  @property
  def core_grippers_mounted(self) -> bool:
    return self._core_gripper_arm is not None

  async def pick_up_core_grippers(
    self,
    x: float,
    y_ch1: float,
    y_ch2: float,
    *,
    channel1: int = 1,
    channel2: int = 8,
    backend_params: Optional[BackendParams] = None,
  ) -> NimbusGripperArm:
    """Pick up the CoRe gripper tools and return the mounted arm."""
    if self._core_gripper_arm is not None:
      raise RuntimeError("CoRe grippers already mounted")
    if self._core_factory is None or self.pip is None:
      raise RuntimeError("Nimbus.setup() has not run.")

    pip_backend = self.pip.backend
    assert isinstance(pip_backend, NimbusPIPBackend)
    backend = self._core_factory.build_backend(pip=pip_backend)

    await backend.pick_up_tool(
      x=x,
      y_ch1=y_ch1,
      y_ch2=y_ch2,
      channel1=channel1,
      channel2=channel2,
      backend_params=backend_params,
    )

    self._core_gripper_arm = NimbusGripperArm(
      backend=backend, reference_resource=self.deck, grip_axis="y"
    )
    return self._core_gripper_arm

  async def return_core_grippers(
    self,
    x: float,
    y_ch1: float,
    y_ch2: float,
    *,
    channel1: int = 1,
    channel2: int = 8,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Drop the CoRe gripper tools back to their parking position."""
    if self._core_gripper_arm is None:
      return
    backend = self._core_gripper_arm.backend
    assert isinstance(backend, NimbusCoreGripper)
    try:
      await backend.drop_tool(
        x=x,
        y_ch1=y_ch1,
        y_ch2=y_ch2,
        channel1=channel1,
        channel2=channel2,
        backend_params=backend_params,
      )
    finally:
      self._core_gripper_arm = None

  @asynccontextmanager
  async def core_grippers(
    self,
    x: float,
    y_ch1: float,
    y_ch2: float,
    *,
    channel1: int = 1,
    channel2: int = 8,
    pickup_params: Optional[BackendParams] = None,
    drop_params: Optional[BackendParams] = None,
  ) -> AsyncIterator[NimbusGripperArm]:
    """Context manager: pick up CoRe grippers, yield the arm, then return the tools."""
    arm = await self.pick_up_core_grippers(
      x=x,
      y_ch1=y_ch1,
      y_ch2=y_ch2,
      channel1=channel1,
      channel2=channel2,
      backend_params=pickup_params,
    )
    try:
      yield arm
    finally:
      await self.return_core_grippers(
        x=x,
        y_ch1=y_ch1,
        y_ch2=y_ch2,
        channel1=channel1,
        channel2=channel2,
        backend_params=drop_params,
      )

  # -- Convenience methods -------------------------------------------------------

  async def park(self) -> None:
    """Park the instrument."""
    await self.driver.send_command(Park())

  async def lock_door(self) -> None:
    """Lock the door."""
    if self.door is None:
      raise RuntimeError("Door lock is not available on this instrument.")
    await self.door.lock()

  async def unlock_door(self) -> None:
    """Unlock the door."""
    if self.door is None:
      raise RuntimeError("Door lock is not available on this instrument.")
    await self.door.unlock()

  async def is_door_locked(self) -> bool:
    """Check if the door is locked."""
    if self.door is None:
      raise RuntimeError("Door lock is not available on this instrument.")
    return await self.door.is_locked()
