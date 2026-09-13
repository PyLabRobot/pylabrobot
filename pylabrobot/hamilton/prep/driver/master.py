"""Prep driver: orchestrates transport, the device's configuration, and peer construction."""

from __future__ import annotations

import asyncio
import json
import logging
import random
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, Optional, Tuple

from pylabrobot.hamilton.transport.tcp.introspection import FirmwareTreeNode
from pylabrobot.hamilton.transport.tcp.packets import Address
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.deck import Deck
from pylabrobot.resources.hamilton.prep_decks import PrepDeck
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.hamilton.core_grippers import HamiltonCoreGrippers

from . import prep_commands as PrepCmd
from .features.calibration import PrepCalibration
from .features.pipettes import (
  CHANNEL_MODEL,
  CHANNEL_SIZE_Z,
  CHANNEL_WIDTH,
  CHANNEL_X_REFERENCE_ANCHOR,
  PrepChannels,
  build_prep_channels,
)
from .simulator import PrepChatterboxClient
from .client import (
  DECK_CONFIGURATION_OBJECT_PATH,
  MLPREP_CPU_OBJECT_PATH,
  MLPREP_OBJECT_PATH,
  MLPREP_SERVICE_OBJECT_PATH,
  MODULE_INFORMATION_OBJECT_PATH,
  PrepClient,
)
from .features.core_grippers import PrepGripper, PrepGripperArm
from .features.head8 import PrepHead8
from .configuration import DeviceConfiguration, read_configuration, to_jsonable
from .errors import PrepMethodNotFoundError
from .features.method import PrepMethodLifecycle
from .features.x_arm import PrepXArm

logger = logging.getLogger(__name__)

# What a declaration and a device have to agree on for the one to stand for the other: what is
# fitted. Identity and set-up are the device's own.
_DECLARATION_MUST_MATCH = ("num_channels", "has_mph", "has_enclosure")


class PrepDriver:
  """Hamilton Prep liquid handler.

  Setup constructs peers (``channels``, ``head8``, ``method``, ``calibration``,
  gripper factory) directly. Firmware paths live on each :class:`PrepCommand`
  subclass and are resolved JIT by :meth:`PrepClient.execute`.
  """

  def __init__(
    self,
    deck: Deck,
    chatterbox: bool = False,
    host: Optional[str] = None,
    port: int = 2000,
    declared_configuration_json: Optional[str] = None,
  ):
    """
    Args:
      deck: the deck positions are measured from.
      chatterbox: whether to drive a simulated device, which answers without one being connected.
      host: the address the Prep answers on. Required unless `chatterbox` is set.
      port: the port it answers on.
      declared_configuration_json: path to a JSON file holding a declared configuration, as
        `save_configuration` writes one. The only way a configuration is read from a file. Against a
        physical device, discovery cross-checks it against what the device answers; against the
        chatterbox, the device answers as it says.
    """
    # What was declared this device is, read once here. Empty when nothing was declared.
    self.declared_configuration_json = declared_configuration_json
    self.declared: Dict[str, Any] = (
      {} if declared_configuration_json is None else read_configuration(declared_configuration_json)
    )
    if chatterbox:
      client: PrepClient = PrepChatterboxClient(configuration=self.declared.get("device"))
    else:
      if not host:
        raise ValueError("host must be provided when chatterbox is False.")
      client = PrepClient(host=host, port=port)
    self.client: PrepClient = client
    self.deck = deck
    # What the device reports about itself, read by `discover`. None until setup has run.
    self.configuration: Optional[DeviceConfiguration] = None
    self._core_gripper_arm: Optional[PrepGripperArm] = None
    self.channels: Optional[PrepChannels] = None
    self.head8: Optional[PrepHead8] = None
    self.gripper: Optional[PrepGripper] = None
    self.method: Optional[PrepMethodLifecycle] = None
    self.calibration: Optional[PrepCalibration] = None
    # The gantry the channels ride. Built at setup, and kept across setups like the configuration.
    self.x_arm: Optional[PrepXArm] = None
    self._setup_finished: bool = False

  async def setup(
    self,
    *,
    smart: bool = True,
    force_initialize: bool = False,
    default_traverse_height: Optional[float] = None,
    use_v1_aspirate_dispense: bool = False,
  ):
    """Connect, discover the device, initialize MLPrep, construct peers."""
    try:
      await self.client.setup()
      await self.discover()
      await self._initialize_instrument(smart=smart, force_initialize=force_initialize)

      self.method = PrepMethodLifecycle(self.client)
      self.calibration = PrepCalibration(client=self.client, driver=self, deck=self.deck)
      channels = PrepChannels(
        client=self.client,
        driver=self,
        deck=self.deck,
        default_traverse_height=default_traverse_height,
        use_v1_aspirate_dispense=use_v1_aspirate_dispense,
      )
      channels.channels = await build_prep_channels(self.client, self.configuration)
      self.channels = channels
      await channels._on_setup()

      if channels.has_mph:
        head8 = PrepHead8(
          client=self.client,
          driver=self,
          deck=self.deck,
          default_traverse_height=default_traverse_height,
          use_v1_aspirate_dispense=use_v1_aspirate_dispense,
        )
        head8.channels = await build_prep_channels(
          self.client, self.configuration, root_name="MPH Channel Root", num_channels=8
        )
        self.head8 = head8
        await head8._on_setup()

      self.gripper = PrepGripper(client=self.client, channels=channels)
      if self.x_arm is None:
        self.x_arm = PrepXArm(self)
      # What was found, as resources on the deck - when the driver was given a Prep deck to reflect into.
      if self.deck is not None:
        await self._create_capability_resources()
      self._setup_finished = True
    except Exception:
      await self.client.stop()
      raise

  async def _initialize_instrument(self, *, smart: bool, force_initialize: bool) -> None:
    """Send ``MLPrep.Initialize`` when needed."""
    if not force_initialize:
      try:
        already = await self.request_initialization_status()
      except Exception as e:
        logger.error("GetIsInitialized failed; cannot decide whether to init: %s", e)
        raise
      if already:
        logger.info("MLPrep already initialized, skipping Initialize")
        return

    await self.client.execute(
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
        "PrepDriver.stop() called with CoRe grippers still mounted. "
        "stop() only manages connection teardown and will NOT move the instrument. "
        "Call `await prep.return_core_grippers()` first if you want the tools returned."
      )
      self._core_gripper_arm = None
    if self.channels is not None:
      await self.channels._on_stop()
    if self.head8 is not None:
      await self.head8._on_stop()
    await self.client.stop()
    self.channels = None
    self.head8 = None
    self.gripper = None
    self.method = None
    self.calibration = None
    self._setup_finished = False

  # -- The device's configuration -------------------------------------------------

  async def _resolve_optional(self, path: str) -> Optional[Address]:
    """The address of the object at `path`, or None when this instrument has no such object."""
    try:
      return await self.client.resolve_path(path)
    except (KeyError, RuntimeError, TypeError):
      return None

  async def _request_by_name(self, path: str, name: str) -> bytes:
    """`PrepClient.request_by_name`, saying which firmware this Prep runs when the method is missing.

    Raises:
      PrepMethodNotFoundError: If this Prep's firmware has no method of that name on that object.
    """
    try:
      return await self.client.request_by_name(path, name)
    except PrepMethodNotFoundError as error:
      version = None if self.configuration is None else self.configuration.firmware_version
      if version is None:
        raise
      raise PrepMethodNotFoundError(f"{error} (this Prep runs {version})") from None

  async def request_present_channels(self) -> Optional[Tuple[PrepCmd.ChannelIndex, ...]]:
    """Request which channels are present (GetPresentChannels on MLPrepService)."""
    service_addr = await self._resolve_optional(MLPREP_SERVICE_OBJECT_PATH)
    if service_addr is None:
      return None
    try:
      resp = await self.client.execute(PrepCmd.PrepGetPresentChannels(dest=service_addr))
      if resp is None or not resp.channels:
        return None
      return tuple(
        PrepCmd.ChannelIndex(v) if v in (0, 1, 2, 3) else PrepCmd.ChannelIndex.InvalidIndex
        for v in resp.channels
      )
    except (
      TimeoutError,
      ConnectionError,
      ConnectionResetError,
      ConnectionAbortedError,
      BrokenPipeError,
      OSError,
    ):
      raise
    except Exception as e:
      logger.warning("Failed to query present channels: %s", e)
      return None

  async def request_device_configuration(self) -> DeviceConfiguration:
    """Request what the device carries and how it is set up.

    Aggregates MLPrep, the deck configuration and MLPrepService. Which device it is - its serial and
    firmware - is read separately, by `discover`.

    Raises:
      RuntimeError: If the deck configuration object does not resolve.
    """
    client = self.client
    mlprep = client.mlprep_address
    enc_resp = await client.execute(PrepCmd.PrepGetIsEnclosurePresent(dest=mlprep))
    safe_resp = await client.execute(PrepCmd.PrepGetSafeSpeedsEnabled(dest=mlprep))
    height_resp = await client.execute(PrepCmd.PrepGetDefaultTraverseHeight(dest=mlprep))
    has_enclosure = bool(enc_resp.value) if enc_resp else False
    safe_speeds_enabled = bool(safe_resp.value) if safe_resp else False
    default_traverse_height = float(height_resp.value) if height_resp else None

    deck_bounds: Optional[PrepCmd.DeckBounds] = None
    deck_sites: Tuple[PrepCmd.DeckSiteInfo, ...] = ()
    waste_sites: Tuple[PrepCmd.WasteSiteInfo, ...] = ()
    deck_addr = await self._resolve_optional(DECK_CONFIGURATION_OBJECT_PATH)
    if deck_addr is None:
      raise RuntimeError("DeckConfiguration path did not resolve — cannot load instrument config")

    bounds_resp = await client.execute(PrepCmd.PrepGetDeckBounds(dest=deck_addr))
    if bounds_resp:
      deck_bounds = PrepCmd.DeckBounds(
        min_x=bounds_resp.min_x,
        max_x=bounds_resp.max_x,
        min_y=bounds_resp.min_y,
        max_y=bounds_resp.max_y,
        min_z=bounds_resp.min_z,
        max_z=bounds_resp.max_z,
      )

    sites_resp = await client.execute(PrepCmd.PrepGetDeckSiteDefinitions(dest=deck_addr))
    if sites_resp and sites_resp.sites:
      deck_sites = tuple(
        PrepCmd.DeckSiteInfo(
          id=int(s.id),
          left_bottom_front_x=float(s.left_bottom_front_x),
          left_bottom_front_y=float(s.left_bottom_front_y),
          left_bottom_front_z=float(s.left_bottom_front_z),
          length=float(s.length),
          width=float(s.width),
          height=float(s.height),
        )
        for s in sites_resp.sites
      )
      logger.debug("Discovered %d deck sites", len(deck_sites))

    waste_resp = await client.execute(PrepCmd.PrepGetWasteSiteDefinitions(dest=deck_addr))
    if waste_resp and waste_resp.sites:
      waste_sites = tuple(
        PrepCmd.WasteSiteInfo(
          index=int(s.index),
          x_position=float(s.x_position),
          y_position=float(s.y_position),
          z_position=float(s.z_position),
          z_seek=float(s.z_seek),
        )
        for s in waste_resp.sites
      )
      logger.debug("Discovered %d waste sites: %s", len(waste_sites), waste_sites)

    present = await self.request_present_channels()
    if present is not None:
      dual = [
        c
        for c in present
        if c in (PrepCmd.ChannelIndex.FrontChannel, PrepCmd.ChannelIndex.RearChannel)
      ]
      num_channels = len(dual)
      has_mph = PrepCmd.ChannelIndex.MPHChannel in present
    else:
      num_channels = 2
      has_mph = False

    return DeviceConfiguration(
      num_channels=num_channels,
      has_mph=has_mph,
      has_enclosure=has_enclosure,
      safe_speeds_enabled=safe_speeds_enabled,
      default_traverse_height=default_traverse_height,
      deck_bounds=deck_bounds,
      deck_sites=deck_sites,
      waste_sites=waste_sites,
    )

  async def request_initialization_status(self) -> bool:
    """Whether MLPrep reports as initialized (GetIsInitialized, cmd=2)."""
    result = await self.client.execute(PrepCmd.PrepGetIsInitialized(dest=self.client.mlprep_address))
    if result is None:
      return False
    return bool(result.value)

  async def request_tip_and_needle_definitions(self) -> Tuple[PrepCmd.TipDefinition, ...]:
    """Tip/needle definitions (GetTipAndNeedleDefinitions, cmd=11)."""
    result = await self.client.execute(
      PrepCmd.PrepGetTipAndNeedleDefinitions(dest=self.client.mlprep_address)
    )
    if result is None or not result.definitions:
      return ()
    return tuple(result.definitions)

  async def request_firmware_version(self) -> Optional[str]:
    """Request what MLPrepCpu runs, or None when this instrument has no such object."""
    addr = await self._resolve_optional(MLPREP_CPU_OBJECT_PATH)
    if addr is None:
      return None
    return await self.client._query_firmware_string(addr, cmd_id=8)

  async def request_device_serial_number(self) -> Optional[str]:
    """Request what the device calls itself, or None when this instrument has no such object."""
    addr = await self._resolve_optional(MLPREP_CPU_OBJECT_PATH)
    if addr is None:
      return None
    return await self.client._query_firmware_string(addr, cmd_id=9)

  async def request_bootloader_version(self) -> Optional[str]:
    """Request MLPrepCpu's bootloader version, or None when this instrument has no such object."""
    addr = await self._resolve_optional(MLPREP_CPU_OBJECT_PATH)
    if addr is None:
      return None
    return await self.client._query_firmware_string(addr, cmd_id=2, iface_id=2)

  async def request_module_part_number(self) -> Optional[str]:
    """Request the pipettor module's part number, or None when it has no such object."""
    addr = await self._resolve_optional(MODULE_INFORMATION_OBJECT_PATH)
    if addr is None:
      return None
    return await self.client._query_firmware_string(addr, cmd_id=5)

  async def request_firmware_tree(self, refresh: bool = False) -> FirmwareTreeNode:
    """Firmware object tree. ``print(await prep.request_firmware_tree())`` for a diagnostic dump."""
    return await self.client.introspection.get_firmware_tree(refresh=refresh)

  def _check_declared_against(self, discovered: DeviceConfiguration) -> None:
    """Raise if what was declared cannot stand for what the device answered.

    Only what decides whether the two are the same kind of device: what is fitted. Identity and
    set-up are left out, since a declaration taken off one device describes another of the same build.

    Args:
      discovered: what the device answered.

    Raises:
      ValueError: If any of those disagree, naming each.
    """
    declared = self.declared.get("device")
    if declared is None:
      return
    differences = [
      f"{name}: declared {getattr(declared, name)!r}, device answers {getattr(discovered, name)!r}"
      for name in _DECLARATION_MUST_MATCH
      if getattr(declared, name) != getattr(discovered, name)
    ]
    if differences:
      raise ValueError(
        "the declared configuration does not describe this device:\n  " + "\n  ".join(differences)
      )

  async def discover(self) -> DeviceConfiguration:
    """Read what device is on the other end: what it carries, how it is set up, and which it is.

    Read-only: nothing moves.

    Returns:
      The configuration, which is also kept as `self.configuration`.

    Raises:
      ValueError: If a declared configuration does not describe this device.
    """
    configuration = await self.request_device_configuration()
    # Which device answered, and what it is running. A device that will not say keeps nothing rather
    # than failing setup: nothing the driver does depends on it.
    try:
      configuration.serial_number = await self.request_device_serial_number()
    except Exception:
      logger.warning("the device did not say what it calls itself; leaving its serial unrecorded")
    try:
      configuration.firmware_version = await self.request_firmware_version()
    except Exception:
      logger.warning("the device did not report its firmware; leaving its version unrecorded")
    self._check_declared_against(configuration)
    self.configuration = configuration
    return configuration

  def _saved_configuration(self) -> Dict[str, Any]:
    """What `save_configuration` writes.

    Raises:
      RuntimeError: If nothing has been read off the device yet.
    """
    if self.configuration is None:
      raise RuntimeError("nothing has been read off this device; call `setup` first")
    return {"device": to_jsonable(self.configuration)}

  def save_configuration(self, path: str, indent: Optional[int] = 2) -> None:
    """Write what this device reported to a file, to be declared or simulated from later.

    Args:
      path: where to write it.
      indent: how far to indent the JSON, or None to write it on one line.

    Raises:
      RuntimeError: If nothing has been read off the device yet.
    """
    with open(path, "w", encoding="utf-8") as f:
      json.dump(self._saved_configuration(), f, indent=indent)

  # -- Resource model -----------------------------------------------------------

  async def _create_capability_resources(self) -> None:
    """Put the X-arm on the deck where it is, and hang a resource for each pipetting channel from it.

    As the STAR driver does. Only on a `PrepDeck`, which is what knows where a Prep's arm goes. What is
    already on the deck is reused, and repeated setups do not duplicate it.
    """
    if not isinstance(self.deck, PrepDeck) or self.x_arm is None or self.channels is None:
      return
    positions = await self.channels.request_channel_positions()
    if not positions:
      logger.warning("the channels reported no positions, so the arm and channels are not modelled")
      return
    arm, c = self.x_arm, self.x_arm.configuration
    # The arm rides at the top of the channels' travel: what their bounds say, or the traverse height
    # when no bounds were read.
    tops = [bounds["z_max"] for bounds in self.channels._channel_bounds]
    if tops:
      z = max(tops)
    elif self.configuration is not None and self.configuration.default_traverse_height is not None:
      z = self.configuration.default_traverse_height
    else:
      z = self.deck.get_absolute_size_z()
    arm.resource = self.deck.get_or_create_x_arm(
      name="x_arm",
      x=positions[0].x,
      z=z,
      size_x=c.size_x,
      size_z=c.size_z,
      reference_point_from_left=c.reference_point_from_left,
      model=c.model,
    )

    # One resource per channel, a child of the arm's as on the STAR: the channels share the arm's X,
    # and each has its own Y and Z.
    self.channels.resources = []
    for channel in range(len(positions)):
      name = f"pipette_channel_{channel}"
      resource = next((child for child in arm.resource.children if child.name == name), None)
      if resource is None:
        resource = Resource(
          name=name,
          size_x=CHANNEL_WIDTH,
          size_y=CHANNEL_WIDTH,
          size_z=CHANNEL_SIZE_Z,
          category="pipette_channel",
          model=CHANNEL_MODEL,
        )
        anchor = resource.get_anchor(x=CHANNEL_X_REFERENCE_ANCHOR)
        arm.resource.assign_child_resource(
          resource, location=Coordinate(c.reference_point_from_left - anchor.x, 0.0, 0.0)
        )
      self.channels.add_tip_mounting_shaft(resource)
      self.channels.resources.append(resource)
    # Seat each where it was read, now that there is something to record it on.
    self.channels._record_positions(positions)

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
      raise RuntimeError("PrepDriver.setup() has not run.")

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
    await self.client.execute(PrepCmd.PrepPark())

  async def spread(self) -> None:
    await self.client.execute(PrepCmd.PrepSpread())

  async def is_parked(self) -> bool:
    """Whether MLPrep reports itself parked.

    Looked up by name: the method's id is not the same on every firmware version, and older
    firmware does not have it.

    Raises:
      PrepMethodNotFoundError: If this firmware has no IsParked.
    """
    data = await self._request_by_name(MLPREP_OBJECT_PATH, "IsParked")
    return bool(PrepCmd.PrepIsParked.parse_response_parameters(data).value)

  async def is_spread(self) -> bool:
    """Whether MLPrep reports its channels spread.

    Looked up by name, as `is_parked` is.

    Raises:
      PrepMethodNotFoundError: If this firmware has no IsSpread.
    """
    data = await self._request_by_name(MLPREP_OBJECT_PATH, "IsSpread")
    return bool(PrepCmd.PrepIsSpread.parse_response_parameters(data).value)

  async def power_down_request(self) -> None:
    await self.client.execute(PrepCmd.PrepPowerDownRequest())

  async def confirm_power_down(self) -> None:
    await self.client.execute(PrepCmd.PrepConfirmPowerDown())

  async def cancel_power_down(self) -> None:
    await self.client.execute(PrepCmd.PrepCancelPowerDown())

  async def get_deck_light(self) -> Tuple[int, int, int, int]:
    result = await self.client.execute(PrepCmd.PrepGetDeckLight())
    if result is None:
      raise ValueError("No response from GetDeckLight.")
    return (result.white, result.red, result.green, result.blue)

  async def set_deck_light(self, white: int, red: int, green: int, blue: int) -> None:
    await self.client.execute(
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
