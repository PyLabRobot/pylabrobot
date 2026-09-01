"""Prep device: orchestrates transport, instrument info, and peer construction."""

from __future__ import annotations

import asyncio
import logging
import random
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional, Tuple

from pylabrobot.resources.deck import Deck
from pylabrobot.resources.hamilton.hamilton_decks import HamiltonCoreGrippers

from . import prep_commands as PrepCmd
from .calibration import PrepCalibration
from .channels import PrepChannels, build_prep_channels
from .chatterbox import PrepChatterboxClient, PrepChatterboxInstrumentInfo
from .client import PrepClient
from .gripper import PrepGripper, PrepGripperArm
from .head8 import PrepHead8
from .info import PrepInstrumentInfo
from .method import PrepMethodLifecycle

logger = logging.getLogger(__name__)


class Prep:
  """Hamilton Prep liquid handler.

  Setup constructs peers (``channels``, ``head8``, ``method``, ``calibration``,
  gripper factory) directly. Firmware paths live on each :class:`PrepCommand`
  subclass and are resolved JIT by :meth:`PrepClient.send_command`.
  """

  def __init__(
    self,
    deck: Deck,
    chatterbox: bool = False,
    host: Optional[str] = None,
    port: int = 2000,
  ):
    if chatterbox:
      client: PrepClient = PrepChatterboxClient()
    else:
      if not host:
        raise ValueError("host must be provided when chatterbox is False.")
      client = PrepClient(host=host, port=port)
    self.client: PrepClient = client
    self.deck = deck
    self.info = PrepChatterboxInstrumentInfo(client) if chatterbox else PrepInstrumentInfo(client)
    self._core_gripper_arm: Optional[PrepGripperArm] = None
    self.channels: Optional[PrepChannels] = None
    self.head8: Optional[PrepHead8] = None
    self.gripper: Optional[PrepGripper] = None
    self.method: Optional[PrepMethodLifecycle] = None
    self.calibration: Optional[PrepCalibration] = None
    self._setup_finished: bool = False

  async def setup(
    self,
    *,
    smart: bool = True,
    force_initialize: bool = False,
    default_traverse_height: Optional[float] = None,
    use_v1_aspirate_dispense: bool = False,
  ):
    """Connect, bootstrap info, initialize MLPrep, construct peers."""
    try:
      await self.client.setup()
      await self.info._on_setup()
      await self._initialize_instrument(smart=smart, force_initialize=force_initialize)

      self.method = PrepMethodLifecycle(self.client)
      self.calibration = PrepCalibration(driver=self.client, info=self.info)
      channels = PrepChannels(
        client=self.client,
        info=self.info,
        deck=self.deck,
        default_traverse_height=default_traverse_height,
        use_v1_aspirate_dispense=use_v1_aspirate_dispense,
      )
      channels.channels = await build_prep_channels(self.client, self.info)
      self.channels = channels
      await channels._on_setup()

      if channels.has_mph:
        head8 = PrepHead8(
          client=self.client,
          info=self.info,
          default_traverse_height=default_traverse_height,
          use_v1_aspirate_dispense=use_v1_aspirate_dispense,
        )
        head8.channels = await build_prep_channels(
          self.client, self.info, root_name="MPH Channel Root", num_channels=8
        )
        self.head8 = head8
        await head8._on_setup()

      self.gripper = PrepGripper(client=self.client, channels=channels)
      self._setup_finished = True
    except Exception:
      await self.info._on_stop()
      await self.client.stop()
      raise

  async def _initialize_instrument(self, *, smart: bool, force_initialize: bool) -> None:
    """Send ``MLPrep.Initialize`` when needed."""
    if not force_initialize:
      try:
        already = await self.info.is_initialized()
      except Exception as e:
        logger.error("GetIsInitialized failed; cannot decide whether to init: %s", e)
        raise
      if already:
        logger.info("MLPrep already initialized, skipping Initialize")
        return

    await self.client.send_command(
      PrepCmd.PrepInitialize(
        smart=smart,
        tip_drop_params=PrepCmd.InitTipDropParameters(
          default_values=True,
          x_position=287.0,
          rolloff_distance=3,
          channel_parameters=[],
        ),
      )
    )
    logger.info(
      "Prep initialization complete%s",
      " (force_initialize=True)" if force_initialize else "",
    )

  async def stop(self):
    if not self._setup_finished:
      return
    if self._core_gripper_arm is not None:
      logger.warning(
        "Prep.stop() called with CoRe grippers still mounted. "
        "stop() only manages connection teardown and will NOT move the instrument. "
        "Call `await prep.return_core_grippers()` first if you want the tools returned."
      )
      self._core_gripper_arm = None
    if self.channels is not None:
      await self.channels._on_stop()
    if self.head8 is not None:
      await self.head8._on_stop()
    await self.client.stop()
    await self.info._on_stop()
    self.channels = None
    self.head8 = None
    self.gripper = None
    self.method = None
    self.calibration = None
    self._setup_finished = False

  # -- CoRe grippers -----------------------------------------------------------

  @property
  def core_gripper_arm(self) -> PrepGripperArm:
    """The mounted CoRe gripper arm. Raises if grippers are not currently picked up."""
    if self._core_gripper_arm is None:
      raise RuntimeError(
        "CoRe grippers not mounted. Call `await prep.pick_up_core_grippers()` first, "
        "or use `async with prep.core_grippers() as arm:`."
      )
    return self._core_gripper_arm

  @property
  def core_grippers_mounted(self) -> bool:
    return self._core_gripper_arm is not None

  async def pick_up_core_grippers(self) -> PrepGripperArm:
    """Pick up the CoRe gripper tools and return the mounted arm."""
    if self._core_gripper_arm is not None:
      raise RuntimeError("CoRe grippers already mounted")
    if self.channels is None or self.gripper is None:
      raise RuntimeError("Prep.setup() has not run.")

    mount = self.deck.get_resource("core_grippers")
    if not isinstance(mount, HamiltonCoreGrippers):
      raise TypeError(
        "deck must have a resource named 'core_grippers' of type HamiltonCoreGrippers"
      )

    loc = mount.get_location_wrt(self.deck)
    await self.gripper.pick_up_tool(
      tool_position_x=loc.x,
      tool_position_z=loc.z,
      front_channel_position_y=loc.y + mount.front_channel_y_center,
      rear_channel_position_y=loc.y + mount.back_channel_y_center,
      tool_seek=loc.z + 10.0,
    )

    self._core_gripper_arm = PrepGripperArm(
      backend=self.gripper, reference_resource=self.deck, grip_axis="y"
    )
    return self._core_gripper_arm

  async def return_core_grippers(self) -> None:
    if self._core_gripper_arm is None:
      return
    try:
      await self._core_gripper_arm.backend.drop_tool()
    finally:
      self._core_gripper_arm = None

  @asynccontextmanager
  async def core_grippers(self) -> AsyncIterator[PrepGripperArm]:
    arm = await self.pick_up_core_grippers()
    try:
      yield arm
    finally:
      await self.return_core_grippers()

  # -- Motion, power, lights (MLPrep via client transport) --------------------

  async def park(self) -> None:
    await self.client.send_command(PrepCmd.PrepPark())

  async def spread(self) -> None:
    await self.client.send_command(PrepCmd.PrepSpread())

  async def is_parked(self) -> bool:
    result = await self.client.send_command(PrepCmd.PrepIsParked())
    if result is None:
      return False
    return bool(result.value)

  async def is_spread(self) -> bool:
    result = await self.client.send_command(PrepCmd.PrepIsSpread())
    if result is None:
      return False
    return bool(result.value)

  async def power_down_request(self) -> None:
    await self.client.send_command(PrepCmd.PrepPowerDownRequest())

  async def confirm_power_down(self) -> None:
    await self.client.send_command(PrepCmd.PrepConfirmPowerDown())

  async def cancel_power_down(self) -> None:
    await self.client.send_command(PrepCmd.PrepCancelPowerDown())

  async def get_deck_light(self) -> Tuple[int, int, int, int]:
    result = await self.client.send_command(PrepCmd.PrepGetDeckLight())
    if result is None:
      raise ValueError("No response from GetDeckLight.")
    return (result.white, result.red, result.green, result.blue)

  async def set_deck_light(self, white: int, red: int, green: int, blue: int) -> None:
    await self.client.send_command(
      PrepCmd.PrepSetDeckLight(white=white, red=red, green=green, blue=blue)
    )

  async def disco_mode(self) -> None:
    """Easter egg: cycle deck lights then restore previous state."""
    white, red, green, blue = await self.get_deck_light()
    try:
      for _ in range(69):
        await self.set_deck_light(
          white=random.randint(1, 255),
          red=random.randint(1, 255),
          green=random.randint(1, 255),
          blue=random.randint(1, 255),
        )
        await asyncio.sleep(0.1)
    finally:
      await self.set_deck_light(white=white, red=red, green=green, blue=blue)
