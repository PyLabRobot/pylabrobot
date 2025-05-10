"""
This file defines interfaces for all supported Tecan liquid handling robots.
"""

import asyncio
import logging
import time
from abc import ABCMeta, abstractmethod
from typing import (
  Dict,
  List,
  Optional,
  Sequence,
  Tuple,
  TypeVar,
  Union,
)

from pylabrobot.liquid_handling.backends.backend import (
  LiquidHandlerBackend,
)
from pylabrobot.liquid_handling.backends.tecan.errors import (
  TecanError,
  error_code_to_exception,
)
from pylabrobot.liquid_handling.liquid_classes.tecan import (
  TecanLiquidClass,
  get_liquid_class,
)
from pylabrobot.liquid_handling.standard import (
  Aspiration,
  AspirationContainer,
  AspirationPlate,
  Dispense,
  DispenseContainer,
  DispensePlate,
  Drop,
  DropTipRack,
  Pickup,
  PickupTipRack,
  ResourceDrop,
  ResourceMove,
  ResourcePickup,
)
from pylabrobot.machines.backends import USBBackend
from pylabrobot.resources import (
  Coordinate,
  Liquid,
  Resource,
  TecanPlate,
  TecanPlateCarrier,
  TecanTip,
  TecanTipRack,
  Trash,
)

T = TypeVar("T")

# Configure logger
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)  # Set logging level to INFO for more detailed output

# Add a handler if not already present
if not logger.handlers:
  handler = logging.StreamHandler()
  formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
  handler.setFormatter(formatter)
  logger.addHandler(handler)


class TecanLiquidHandler(LiquidHandlerBackend, USBBackend, metaclass=ABCMeta):
  """
  Abstract base class for Tecan liquid handling robot backends.
  """

  @abstractmethod
  def __init__(
    self,
    packet_read_timeout: int = 120,
    read_timeout: int = 300,
    write_timeout: int = 300,
  ):
    """

    Args:
      packet_read_timeout: The timeout for reading packets from the Tecan machine in seconds.
      read_timeout: The timeout for reading from the Tecan machine in seconds.
    """

    USBBackend.__init__(
      self,
      packet_read_timeout=packet_read_timeout,
      read_timeout=read_timeout,
      write_timeout=write_timeout,
      id_vendor=0x0C47,
      id_product=0x4000,
    )
    LiquidHandlerBackend.__init__(self)

    self._cache: Dict[str, List[Optional[int]]] = {}

  def _assemble_command(self, module: str, command: str, params: List[Optional[int]]) -> str:
    """Assemble a firmware command to the Tecan machine.

    Args:
      module: 2 character module identifier (C5 for LiHa, ...)
      command: 3 character command identifier
      params: list of integer parameters

    Returns:
      A string containing the assembled command.
    """

    cmd = module + command + ",".join(str(a) if a is not None else "" for a in params)
    return f"\02{cmd}\00"

  def parse_response(self, resp: bytearray) -> Dict[str, Union[str, int, List[int]]]:
    """Parse a machine response string

    Args:
      resp: The response string to parse.

    Returns:
      A dictionary containing the parsed values.
    """
    # Defensive handling of empty or short responses
    if resp is None or len(resp) < 4:
      logger.warning(f"Received incomplete response: {resp}")
      return {"module": "XX", "data": []}

    try:
      s = resp.decode("utf-8", "ignore")
      module = s[1:3] if len(s) > 2 else "XX"
      ret = int(resp[3]) ^ (1 << 7) if len(resp) > 3 else 0
      if ret != 0:
        logger.warning(f"Response error from {module}: Error code {ret}")
        raise error_code_to_exception(module, ret)

      data: List[int] = [int(x) for x in s[3:-1].split(",") if x]
      return {"module": module, "data": data}
    except (IndexError, ValueError) as e:
      logger.warning(f"Error parsing response: {e}, response: {resp}")
      return {"module": "XX", "data": []}

  async def send_command(
    self,
    module: str,
    command: str,
    params: Optional[List[Optional[int]]] = None,
    write_timeout: Optional[int] = None,
    read_timeout: Optional[int] = None,
    wait=True,
    retry_count=3,
    ignore_errors=False,  # Add option to ignore specific errors
  ):
    """Send a firmware command to the Tecan machine. Caches `set` commands and ignores if
    redundant.

    Args:
      module: 2 character module identifier (C5 for LiHa, ...)
      command: 3 character command identifier
      params: list of integer parameters
      write_timeout: write timeout in seconds. If None, `self.write_timeout` is used.
      read_timeout: read timeout in seconds. If None, `self.read_timeout` is used.
      wait: If True, wait for a response. If False, return `None` immediately after sending.
      retry_count: Number of retries if command fails
      ignore_errors: If True, log errors but don't raise exceptions

    Returns:
      A dictionary containing the parsed response, or None if no response was read within `timeout`.
    """

    if command[0] == "S" and params is not None:
      k = module + command
      if k in self._cache and self._cache[k] == params:
        return
      self._cache[k] = params

    cmd = self._assemble_command(module, command, [] if params is None else params)

    # Log the command for debugging
    logger.debug(f"Sending command: {cmd}")

    for attempt in range(retry_count):
      try:
        self.write(cmd, timeout=write_timeout)
        if not wait:
          return None

        resp = self.read(timeout=read_timeout)
        return self.parse_response(resp)
      except Exception as e:
        logger.warning(f"Command failed (attempt {attempt+1}/{retry_count}): {e}")
        if attempt < retry_count - 1:
          time.sleep(1)  # Wait before retry
        elif ignore_errors:
          logger.error(f"Command failed after {retry_count} attempts: {e}, but ignoring error")
          return {"module": module, "data": []}
        else:
          logger.error(f"Command failed after {retry_count} attempts: {e}")
          raise

  async def setup(self):
    await LiquidHandlerBackend.setup(self)
    await USBBackend.setup(self)


class EVO(TecanLiquidHandler):
  """
  Interface for the Tecan Freedom EVO series
  """

  LIHA = "C5"
  LIHA2 = "C7"  # LIHA2 module identifier is C7 (not C6)
  ROMA = "C1"
  MCA = "C3"
  PNP = "W1"

  # Default positions for each arm in 1/10 mm
  DEFAULT_POSITIONS = {
    LIHA: 4045,
    LIHA2: 10045,
    ROMA: 12000,
  }

  # Minimum safe distance between arms in 1/10 mm
  SAFE_DISTANCE = 3000  # Increased for even safer operation

  def __init__(
    self,
    diti_count: int = 0,
    diti_count2: int = 0,
    packet_read_timeout: int = 120,
    read_timeout: int = 300,
    write_timeout: int = 300,
    skip_initialization: bool = False,  # Skip arm initialization to prevent homing
    movement_speed_factor: float = 0.2,  # Default to slower speed (20% of maximum)
    skip_roma: bool = False,  # Option to skip RoMa initialization completely
  ):
    """Create a new EVO interface.

    Args:
      diti_count: Number of disposable tip positions for first LIHA
      diti_count2: Number of disposable tip positions for second LIHA
      packet_read_timeout: timeout in seconds for reading a single packet
      read_timeout: timeout in seconds for reading a full response
      write_timeout: timeout in seconds for writing a command
      skip_initialization: If True, skip arm initialization which causes homing to X=0
      movement_speed_factor: Factor to control the movement speed (0.0-1.0)
      skip_roma: If True, completely skip RoMa initialization
    """

    super().__init__(
      packet_read_timeout=packet_read_timeout,
      read_timeout=read_timeout,
      write_timeout=write_timeout,
    )

    self._num_channels: Optional[int] = None
    self._num_channels2: Optional[int] = None
    self.diti_count = diti_count
    self.diti_count2 = diti_count2
    self.skip_initialization = skip_initialization
    self.skip_roma = skip_roma

    # Set arm movement speeds based on the factor (0.0-1.0)
    self.liha_speed = int(5000 * movement_speed_factor)  # LIHA default speed
    self.liha2_speed = int(5000 * movement_speed_factor)  # LIHA2 default speed
    self.roma_speed = int(2500 * movement_speed_factor)  # RoMa default speed (slower than others)

    # Initialize positions dictionary to store current arm positions
    self._arm_positions = self.DEFAULT_POSITIONS.copy()
    EVOArm._pos_cache = self._arm_positions.copy()

    # Connection status for each arm
    self._liha_connected: Optional[bool] = None
    self._liha2_connected: Optional[bool] = None
    self._roma_connected: Optional[bool] = None
    self._pnp_connected: Optional[bool] = None
    self._mca_connected: Optional[bool] = None

  @property
  def num_channels(self) -> int:
    """The number of pipette channels present on the first LIHA arm."""

    if self._num_channels is None:
      raise RuntimeError("has not loaded num_channels, forgot to call `setup`?")
    return self._num_channels

  @property
  def num_channels2(self) -> int:
    """The number of pipette channels present on the second LIHA arm."""

    if self._num_channels2 is None:
      raise RuntimeError("has not loaded num_channels2, forgot to call `setup`?")
    return self._num_channels2

  @property
  def liha_connected(self) -> bool:
    """Whether first LiHa arm is present on the robot."""

    if self._liha_connected is None:
      raise RuntimeError("liha_connected not set, forgot to call `setup`?")
    return self._liha_connected

  @property
  def liha2_connected(self) -> bool:
    """Whether second LiHa arm is present on the robot."""

    if self._liha2_connected is None:
      raise RuntimeError("liha2_connected not set, forgot to call `setup`?")
    return self._liha2_connected

  @property
  def roma_connected(self) -> bool:
    """Whether RoMa arm is present on the robot."""

    if self._roma_connected is None:
      raise RuntimeError("roma_connected not set, forgot to call `setup`?")
    return self._roma_connected

  @property
  def pnp_connected(self) -> bool:
    """Whether PnP arm is present on the robot."""

    if self._pnp_connected is None:
      raise RuntimeError("pnp_connected not set, forgot to call `setup`?")
    return self._pnp_connected

  @property
  def mca_connected(self) -> bool:
    """Whether MCA arm is present on the robot."""

    if self._mca_connected is None:
      raise RuntimeError("mca_connected not set, forgot to call `setup`?")
    return self._mca_connected

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "packet_read_timeout": self.packet_read_timeout,
      "read_timeout": self.read_timeout,
      "write_timeout": self.write_timeout,
      "diti_count": self.diti_count,
      "diti_count2": self.diti_count2,
      "skip_initialization": self.skip_initialization,
      "skip_roma": self.skip_roma,
      "arm_positions": self._arm_positions,
      "liha_speed": self.liha_speed,
      "liha2_speed": self.liha2_speed,
      "roma_speed": self.roma_speed,
    }

  async def setup(self):
    """Setup

    Creates a USB connection and finds read/write interfaces.
    """
    logger.info("Starting EVO setup")
    await super().setup()

    # Detect connected arms without initializing them
    self._liha_connected = await self.detect_arm(EVO.LIHA)
    logger.info(f"LIHA connected: {self._liha_connected}")

    self._liha2_connected = await self.detect_arm(EVO.LIHA2)
    logger.info(f"LIHA2 connected: {self._liha2_connected}")

    self._roma_connected = await self.detect_arm(EVO.ROMA)
    logger.info(f"RoMa connected: {self._roma_connected}")

    # Create arm objects
    if self._liha_connected:
      self.liha = LiHa(self, EVO.LIHA)

    if self._liha2_connected:
      self.liha2 = LiHa(self, EVO.LIHA2)

    if self._roma_connected:
      self.roma = RoMa(self, EVO.ROMA)

    # Get information about connected arms without initializing them
    if self._liha_connected:
      try:
        self._num_channels = await self.liha.report_number_tips()
        self._x_range = await self.liha.report_x_param(5)
        self._y_range = (await self.liha.report_y_param(5))[0]
        self._z_range = (await self.liha.report_z_param(5))[0]
        logger.info(f"LIHA has {self._num_channels} channels")
      except Exception as e:
        logger.error(f"Error getting LIHA information: {e}")
        self._num_channels = 8  # Default
        self._z_range = 2000  # Default

    if self._liha2_connected:
      try:
        self._num_channels2 = await self.liha2.report_number_tips()
        self._x_range2 = await self.liha2.report_x_param(5)
        self._y_range2 = (await self.liha2.report_y_param(5))[0]
        self._z_range2 = (await self.liha2.report_z_param(5))[0]
        logger.info(f"LIHA2 has {self._num_channels2} channels")
      except Exception as e:
        logger.error(f"Error getting LIHA2 information: {e}")
        self._num_channels2 = 8  # Default
        self._z_range2 = 2000  # Default

    # Skip any initialization that might cause arms to home to X=0 if requested
    if self.skip_initialization:
      logger.info("Skipping arm initialization due to skip_initialization flag")
      return

    # Initialize the LIHA and LIHA2 arms first
    try:
      if self._liha2_connected:
        await self.move_liha2_to_position(self._arm_positions[EVO.LIHA2])
        await asyncio.sleep(2)  # Ensure LIHA2 is fully positioned
    except Exception as e:
      logger.error(f"Error initializing LIHA2: {e}")

    try:
      if self._liha_connected:
        await self.move_liha_to_position(self._arm_positions[EVO.LIHA])
        await asyncio.sleep(2)  # Ensure LIHA is fully positioned
    except Exception as e:
      logger.error(f"Error initializing LIHA: {e}")

    # Initialize RoMa last, and only if not skipped
    if not self.skip_roma:
      try:
        if self._roma_connected:
          # Simplified RoMa initialization - just position instead of vector movement
          await self.simplified_roma_position(self._arm_positions[EVO.ROMA])
          await asyncio.sleep(3)  # Longer delay for RoMa to ensure completion
      except Exception as e:
        logger.error(f"Error initializing RoMa: {e}")
        # Continue despite RoMa errors

    # Initialize plungers for LIHA
    if self._liha_connected:
      try:
        await self.initialize_liha_plungers()
      except Exception as e:
        logger.error(f"Error initializing LIHA plungers: {e}")

    # Initialize plungers for LIHA2
    if self._liha2_connected:
      try:
        await self.initialize_liha2_plungers()
      except Exception as e:
        logger.error(f"Error initializing LIHA2 plungers: {e}")

    logger.info("EVO setup completed")

  async def detect_arm(self, module: str) -> bool:
    """Detect if an arm is connected without initializing it.

    Args:
      module: Module identifier for the arm

    Returns:
      True if arm is connected, False otherwise
    """
    try:
      # Just check if the arm is responsive using a status command
      await self.send_command(module, command="BMX", params=[2], retry_count=1)
      return True
    except Exception as e:
      logger.warning(f"Arm {module} not connected or not responding: {e}")
      return False

  async def move_liha_to_position(self, x_position: int):
    """Moves LIHA to a specified X position directly with controlled speed.

    Args:
      x_position: Target X position in 1/10 mm
    """
    logger.info(f"Moving LIHA to position {x_position}")

    # Set reduced speed for X-axis movement
    await self.send_command(EVO.LIHA, command="SFX", params=[self.liha_speed], ignore_errors=True)

    # Get current position
    current_x = (
      await self.liha.report_x_param(0)
      if hasattr(self, "liha")
      else self._arm_positions.get(EVO.LIHA, 4045)
    )

    # Move Z-axis up first for safety
    if hasattr(self, "_z_range"):
      z_channels = [self._z_range] * self.num_channels
      await self.liha.set_z_travel_height(z_channels)

    # Move X-axis to target position with reduced speed
    await self.send_command(EVO.LIHA, command="PAX", params=[x_position])

    # Add a small delay to ensure movement completes
    await asyncio.sleep(1.5)

    self._arm_positions[EVO.LIHA] = x_position
    EVOArm._pos_cache[EVO.LIHA] = x_position

  async def move_liha2_to_position(self, x_position: int):
    """Moves LIHA2 to a specified X position directly with controlled speed.

    Args:
      x_position: Target X position in 1/10 mm
    """
    logger.info(f"Moving LIHA2 to position {x_position}")

    # Set reduced speed for X-axis movement
    await self.send_command(EVO.LIHA2, command="SFX", params=[self.liha2_speed], ignore_errors=True)

    # Get current position
    current_x = (
      await self.liha2.report_x_param(0)
      if hasattr(self, "liha2")
      else self._arm_positions.get(EVO.LIHA2, 10045)
    )

    # Move Z-axis up first for safety
    if hasattr(self, "_z_range2"):
      z_channels = [self._z_range2] * self.num_channels2
      await self.liha2.set_z_travel_height(z_channels)

    # Move X-axis to target position with reduced speed
    await self.send_command(EVO.LIHA2, command="PAX", params=[x_position])

    # Add a small delay to ensure movement completes
    await asyncio.sleep(1.5)

    self._arm_positions[EVO.LIHA2] = x_position
    EVOArm._pos_cache[EVO.LIHA2] = x_position

  async def simplified_roma_position(self, x_position: int):
    """Simpler RoMa positioning that avoids vector coordinate movements.

    Args:
      x_position: Target X position in 1/10 mm
    """
    logger.info(f"Moving RoMa to position {x_position} using simplified method")

    # Set slower speed
    await self.send_command(EVO.ROMA, command="SFX", params=[self.roma_speed], ignore_errors=True)

    # Basic positioning - direct movement to target position
    try:
      # Just move to X position directly, which is safer than vector movements
      await self.send_command(EVO.ROMA, command="PAX", params=[x_position], ignore_errors=True)
      await asyncio.sleep(2)  # Wait for movement to complete

      # Try to set Z to a safe height
      await self.send_command(EVO.ROMA, command="PAZ", params=[2000], ignore_errors=True)

      # Update position cache
      self._arm_positions[EVO.ROMA] = x_position
      EVOArm._pos_cache[EVO.ROMA] = x_position

      logger.info(f"RoMa positioned successfully at {x_position}")
    except Exception as e:
      logger.error(f"Failed to position RoMa: {e}")
      # Continue despite errors - RoMa isn't critical for basic operation

  async def move_roma_to_position(self, x_position: int):
    """Moves RoMa to a specified X position with safer path and reduced speed.
    This method uses vector coordinate movements which may cause issues.

    Args:
      x_position: Target X position in 1/10 mm
    """
    logger.info(f"Moving RoMa to position {x_position}")

    # Get current position
    current_x = (
      await self.roma.report_x_param(0)
      if hasattr(self, "roma")
      else self._arm_positions.get(EVO.ROMA, 9000)
    )

    # Set up RoMa parameters with SLOWER speed
    await self.send_command(
      EVO.ROMA, command="SSM", params=[1], ignore_errors=True
    )  # Set smooth mode
    await self.send_command(EVO.ROMA, command="SFX", params=[self.roma_speed], ignore_errors=True)
    await self.send_command(
      EVO.ROMA, command="SFY", params=[self.roma_speed // 2], ignore_errors=True
    )
    await self.send_command(
      EVO.ROMA, command="SFZ", params=[self.roma_speed // 2], ignore_errors=True
    )

    try:
      # First, raise the Z-axis to a safe height to avoid horizontal collisions
      await self.send_command(
        EVO.ROMA,
        command="SAA",
        params=[1, current_x, 2000, 1800, 1800, None, 1, 0, 0],
        ignore_errors=True,
      )
      await self.send_command(EVO.ROMA, command="AAC", ignore_errors=True)

      # Add a small delay to ensure Z movement completes
      await asyncio.sleep(1.5)

      # Now move to target X position while staying high
      await self.send_command(
        EVO.ROMA,
        command="SAA",
        params=[1, x_position, 2000, 1800, 1800, None, 1, 0, 0],
        ignore_errors=True,
      )
      await self.send_command(EVO.ROMA, command="AAC", ignore_errors=True)

      # Add another delay
      await asyncio.sleep(1.5)

      # Finally, lower to the normal Z position
      await self.send_command(
        EVO.ROMA,
        command="SAA",
        params=[1, x_position, 2000, 2464, 1800, None, 1, 0, 0],
        ignore_errors=True,
      )
      await self.send_command(EVO.ROMA, command="AAC", ignore_errors=True)

      self._arm_positions[EVO.ROMA] = x_position
      EVOArm._pos_cache[EVO.ROMA] = x_position
    except Exception as e:
      logger.error(f"Error in vector-based RoMa movement: {e}")
      # Try simpler positioning as fallback
      await self.simplified_roma_position(x_position)

  async def initialize_liha_plungers(self):
    """Initialize plungers for the LIHA."""
    logger.info("Initializing LIHA plungers")

    # Assume LIHA is already at its default position
    channels = self.num_channels
    z_range = self._z_range if hasattr(self, "_z_range") else 2000

    # Set travel height
    await self.liha.set_z_travel_height([z_range] * channels)

    # Position to plunger init position
    await self.liha.position_absolute_all_axis(
      self._arm_positions[EVO.LIHA], 1031, 90, [1200] * channels
    )

    # Initialize plungers
    await self.liha.initialize_plunger(self._bin_use_channels(list(range(channels))))

    # Cycle plungers to ensure proper initialization
    await self.liha.position_valve_logical([1] * channels)
    await self.liha.move_plunger_relative([100] * channels)
    await self.liha.position_valve_logical([0] * channels)
    await self.liha.set_end_speed_plunger([1000] * channels)
    await self.liha.move_plunger_relative([-100] * channels)

    # Return to default position
    await self.liha.position_absolute_all_axis(
      self._arm_positions[EVO.LIHA], 1031, 90, [z_range] * channels
    )

  async def initialize_liha2_plungers(self):
    """Initialize plungers for the LIHA2."""
    logger.info("Initializing LIHA2 plungers")

    # Assume LIHA2 is already at its default position
    channels = self.num_channels2
    z_range = self._z_range2 if hasattr(self, "_z_range2") else 2000

    try:
      # Set travel height
      await self.liha2.set_z_travel_height([z_range] * channels)

      # Position to plunger init position - but stay at current X
      current_x = self._arm_positions[EVO.LIHA2]
      await self.liha2.position_absolute_all_axis(current_x, 1031, 90, [1200] * channels)

      # Initialize plungers
      await self.liha2.initialize_plunger(self._bin_use_channels2(list(range(channels))))

      # Cycle plungers to ensure proper initialization
      await self.liha2.position_valve_logical([1] * channels)
      await self.liha2.move_plunger_relative([100] * channels)
      await self.liha2.position_valve_logical([0] * channels)
      await self.liha2.set_end_speed_plunger([1000] * channels)
      await self.liha2.move_plunger_relative([-100] * channels)

      # Return to default position
      await self.liha2.position_absolute_all_axis(current_x, 1031, 90, [z_range] * channels)

    except Exception as e:
      logger.error(f"Error in LIHA2 plunger initialization: {e}")
      # Recover from errors
      try:
        await self.move_liha2_to_position(self._arm_positions[EVO.LIHA2])
      except Exception:
        pass

  async def _park_liha(self):
    """Park the LIHA arm at its home position."""
    if not hasattr(self, "liha") or not self._liha_connected:
      return

    channels = self.num_channels
    z_range = self._z_range if hasattr(self, "_z_range") else 2000

    # Set reduced speed for parking
    await self.send_command(EVO.LIHA, command="SFX", params=[self.liha_speed], ignore_errors=True)

    # Raise Z axis first
    await self.liha.set_z_travel_height([z_range] * channels)

    # Then move to park position
    await self.liha.position_absolute_all_axis(
      self._arm_positions[EVO.LIHA], 1031, 90, [z_range] * channels
    )

  async def _park_liha2(self):
    """Park the LIHA2 arm at its home position."""
    if not hasattr(self, "liha2") or not self._liha2_connected:
      return

    channels = self.num_channels2
    z_range = self._z_range2 if hasattr(self, "_z_range2") else 2000

    # Set reduced speed for parking
    await self.send_command(EVO.LIHA2, command="SFX", params=[self.liha2_speed], ignore_errors=True)

    # Raise Z axis first
    await self.liha2.set_z_travel_height([z_range] * channels)

    # Then move to park position
    await self.liha2.position_absolute_all_axis(
      self._arm_positions[EVO.LIHA2], 1031, 90, [z_range] * channels
    )

  async def _park_roma(self):
    """Park the RoMa arm at its home position."""
    if not hasattr(self, "roma") or not self._roma_connected:
      return

    await self.simplified_roma_position(self._arm_positions[EVO.ROMA])

  # ============== LiquidHandlerBackend methods ==============

  async def aspirate(self, ops: List[Aspiration], use_channels: List[int], use_liha2: bool = False):
    """Aspirate liquid from the specified channels.

    Args:
      ops: The aspiration operations to perform.
      use_channels: The channels to use for the operations.
      use_liha2: Whether to use the second LIHA arm (default: False).
    """
    # Select the appropriate LIHA arm
    liha = self.liha2 if use_liha2 else self.liha
    num_channels = self.num_channels2 if use_liha2 else self.num_channels
    z_range = self._z_range2 if use_liha2 else self._z_range

    x_positions, y_positions, z_positions = self._liha_positions(ops, use_channels)

    tecan_liquid_classes = [
      get_liquid_class(
        target_volume=op.volume,
        liquid_class=op.liquids[-1][0] or Liquid.WATER,
        tip_type=op.tip.tip_type,
      )
      if isinstance(op.tip, TecanTip)
      else None
      for op in ops
    ]

    ys = int(ops[0].resource.get_absolute_size_y() * 10)
    zadd: List[Optional[int]] = [0] * num_channels
    for i, channel in enumerate(use_channels):
      par = ops[i].resource.parent
      if par is None:
        continue
      if not isinstance(par, TecanPlate):
        raise ValueError(f"Operation is not supported by resource {par}.")
      # TODO: calculate defaults when area is not specified
      zadd[channel] = round(ops[i].volume / par.area * 10)

    # moves such that first channel is over first location
    x, _ = self._first_valid(x_positions)
    y, yi = self._first_valid(y_positions)
    assert x is not None and y is not None
    await liha.set_z_travel_height([z_range] * num_channels)
    await liha.position_absolute_all_axis(
      x,
      y - yi * ys,
      ys,
      [z if z else z_range for z in z_positions["travel"]],
    )
    # TODO check channel positions match resource positions

    # aspirate airgap
    pvl, sep, ppr = self._aspirate_airgap(use_channels, tecan_liquid_classes, "lag")
    if any(ppr):
      await liha.position_valve_logical(pvl)
      await liha.set_end_speed_plunger(sep)
      await liha.move_plunger_relative(ppr)

    # perform liquid level detection
    # TODO: verify for other liquid detection modes
    if any(tlc.aspirate_lld if tlc is not None else None for tlc in tecan_liquid_classes):
      tlc, _ = self._first_valid(tecan_liquid_classes)
      assert tlc is not None
      detproc = tlc.lld_mode  # must be same for all channels?
      sense = tlc.lld_conductivity
      await liha.set_detection_mode(detproc, sense)
      ssl, sdl, sbl = self._liquid_detection(use_channels, tecan_liquid_classes)
      await liha.set_search_speed(ssl)
      await liha.set_search_retract_distance(sdl)
      await liha.set_search_z_start(z_positions["start"])
      await liha.set_search_z_max(list(z if z else z_range for z in z_positions["max"]))
      await liha.set_search_submerge(sbl)
      shz = [min(z for z in z_positions["travel"] if z)] * num_channels
      await liha.set_z_travel_height(shz)
      await liha.move_detect_liquid(
        self._bin_use_channels(use_channels)
        if not use_liha2
        else self._bin_use_channels2(use_channels),
        zadd,
      )
      await liha.set_z_travel_height([z_range] * num_channels)

    # aspirate + retract
    # SSZ: z_add / (vol / asp_speed)
    zadd = [min(z, 32) if z else None for z in zadd]
    ssz, sep, stz, mtr, ssz_r = self._aspirate_action(ops, use_channels, tecan_liquid_classes, zadd)
    await liha.set_slow_speed_z(ssz)
    await liha.set_end_speed_plunger(sep)
    await liha.set_tracking_distance_z(stz)
    await liha.move_tracking_relative(mtr)
    await liha.set_slow_speed_z(ssz_r)
    await liha.move_absolute_z(z_positions["start"])  # TODO: use retract_position and offset

    # aspirate airgap
    pvl, sep, ppr = self._aspirate_airgap(use_channels, tecan_liquid_classes, "tag")
    await liha.position_valve_logical(pvl)
    await liha.set_end_speed_plunger(sep)
    await liha.move_plunger_relative(ppr)

  async def dispense(self, ops: List[Dispense], use_channels: List[int], use_liha2: bool = False):
    """Dispense liquid from the specified channels.

    Args:
      ops: The dispense operations to perform.
      use_channels: The channels to use for the dispense operations.
      use_liha2: Whether to use the second LIHA arm (default: False).
    """
    # Select the appropriate LIHA arm
    liha = self.liha2 if use_liha2 else self.liha
    num_channels = self.num_channels2 if use_liha2 else self.num_channels
    z_range = self._z_range2 if use_liha2 else self._z_range

    x_positions, y_positions, z_positions = self._liha_positions(ops, use_channels)
    ys = int(ops[0].resource.get_absolute_size_y() * 10)

    tecan_liquid_classes = [
      get_liquid_class(
        target_volume=op.volume,
        liquid_class=op.liquids[-1][0] or Liquid.WATER,
        tip_type=op.tip.tip_type,
      )
      if isinstance(op.tip, TecanTip)
      else None
      for op in ops
    ]

    x, _ = self._first_valid(x_positions)
    y, yi = self._first_valid(y_positions)
    assert x is not None and y is not None
    await liha.set_z_travel_height(z if z else z_range for z in z_positions["travel"])
    await liha.position_absolute_all_axis(
      x,
      y - yi * ys,
      ys,
      [z if z else z_range for z in z_positions["dispense"]],
    )

    sep, spp, stz, mtr = self._dispense_action(ops, use_channels, tecan_liquid_classes)
    await liha.set_end_speed_plunger(sep)
    await liha.set_stop_speed_plunger(spp)
    await liha.set_tracking_distance_z(stz)
    await liha.move_tracking_relative(mtr)

  async def pick_up_tips(self, ops: List[Pickup], use_channels: List[int], use_liha2: bool = False):
    """Pick up tips from a resource.

    Args:
      ops: The pickup operations to perform.
      use_channels: The channels to use for the pickup operations.
      use_liha2: Whether to use the second LIHA arm (default: False).
    """
    # Select the appropriate LIHA arm and parameters
    liha = self.liha2 if use_liha2 else self.liha
    diti_count = self.diti_count2 if use_liha2 else self.diti_count
    num_channels = self.num_channels2 if use_liha2 else self.num_channels
    z_range = self._z_range2 if use_liha2 else self._z_range

    assert (
      min(use_channels) >= num_channels - diti_count
    ), f"DiTis can only be configured for the last {diti_count} channels"

    x_positions, y_positions, _ = self._liha_positions(ops, use_channels)

    # move channels
    ys = int(ops[0].resource.get_absolute_size_y() * 10)
    x, _ = self._first_valid(x_positions)
    y, yi = self._first_valid(y_positions)
    assert x is not None and y is not None
    await liha.set_z_travel_height([z_range] * num_channels)
    await liha.position_absolute_all_axis(x, y - yi * ys, ys, [z_range] * num_channels)

    # aspirate airgap
    pvl: List[Optional[int]] = [None] * num_channels
    sep: List[Optional[int]] = [None] * num_channels
    ppr: List[Optional[int]] = [None] * num_channels
    for channel in use_channels:
      pvl[channel] = 0
      sep[channel] = 70 * 6  # ? 12, always 70?
      ppr[channel] = 10 * 3  # ? 6
    await liha.position_valve_logical(pvl)
    await liha.set_end_speed_plunger(sep)
    await liha.move_plunger_relative(ppr)

    # get tips
    bin_channels = (
      self._bin_use_channels2(use_channels) if use_liha2 else self._bin_use_channels(use_channels)
    )
    await liha.get_disposable_tip(bin_channels, 768, 210)
    # TODO: check z params

  async def drop_tips(self, ops: List[Drop], use_channels: List[int], use_liha2: bool = False):
    """Drops tips to waste.

    Args:
      ops: The drop operations to perform.
      use_channels: The channels to use for the drop operations.
      use_liha2: Whether to use the second LIHA arm (default: False).
    """
    # Select the appropriate LIHA arm and parameters
    liha = self.liha2 if use_liha2 else self.liha
    diti_count = self.diti_count2 if use_liha2 else self.diti_count
    num_channels = self.num_channels2 if use_liha2 else self.num_channels
    z_range = self._z_range2 if use_liha2 else self._z_range

    assert (
      min(use_channels) >= num_channels - diti_count
    ), f"DiTis can only be configured for the last {diti_count} channels"
    assert all(isinstance(op.resource, Trash) for op in ops), "Must drop in waste container"

    x_positions, y_positions, _ = self._liha_positions(ops, use_channels)

    # move channels
    ys = 90
    x, _ = self._first_valid(x_positions)
    y, _ = self._first_valid(y_positions)
    assert x is not None and y is not None
    await liha.set_z_travel_height([z_range] * num_channels)
    await liha.position_absolute_all_axis(x, int(y - ys * 3.5), ys, [z_range] * num_channels)

    # discard tips
    bin_channels = (
      self._bin_use_channels2(use_channels) if use_liha2 else self._bin_use_channels(use_channels)
    )
    await liha.discard_disposable_tip(bin_channels)

  async def pick_up_tips96(self, pickup: PickupTipRack):
    raise NotImplementedError()

  async def drop_tips96(self, drop: DropTipRack):
    raise NotImplementedError()

  async def aspirate96(self, aspiration: Union[AspirationPlate, AspirationContainer]):
    raise NotImplementedError()

  async def dispense96(self, dispense: Union[DispensePlate, DispenseContainer]):
    raise NotImplementedError()

  async def pick_up_resource(self, pickup: ResourcePickup):
    # TODO: implement PnP for moving tubes
    assert self.roma_connected

    z_range = await self.roma.report_z_param(5)
    x, y, z = self._roma_positions(
      pickup.resource, pickup.resource.get_absolute_location(), z_range
    )
    h = int(pickup.resource.get_absolute_size_y() * 10)

    # move to resource
    await self.roma.set_smooth_move_x(1)
    await self.roma.set_fast_speed_x(self.roma_speed)
    await self.roma.set_fast_speed_y(
      self.roma_speed // 2, self.roma_speed // 4
    )  # Reduced speed and acceleration
    await self.roma.set_fast_speed_z(self.roma_speed // 2)
    await self.roma.set_fast_speed_r(self.roma_speed // 2, self.roma_speed // 4)
    await self.roma.set_vector_coordinate_position(1, x, y, z["safe"], 900, None, 1, 0)
    await self.roma.action_move_vector_coordinate_position()
    await self.roma.set_smooth_move_x(0)

    # pick up resource
    await self.roma.position_absolute_g(900)  # TODO: verify
    await self.roma.set_target_window_class(1, 0, 0, 0, 135, 0)
    await self.roma.set_vector_coordinate_position(1, x, y, z["travel"], 900, None, 1, 1)
    # TODO verify z param
    await self.roma.set_vector_coordinate_position(1, x, y, z["end"], 900, None, 1, 0)
    await self.roma.action_move_vector_coordinate_position()
    await self.roma.set_fast_speed_y(
      self.roma_speed // 3, self.roma_speed // 5
    )  # Even slower for gripping
    await self.roma.set_fast_speed_r(self.roma_speed // 3, self.roma_speed // 5)
    await self.roma.set_gripper_params(self.roma_speed // 5, 75)  # Slower gripper operation
    await self.roma.grip_plate(h - 100)

  async def move_picked_up_resource(self, move: ResourceMove):
    raise NotImplementedError()

  async def drop_resource(self, drop: ResourceDrop):
    """Drop a resource like a plate or a lid using the integrated robotic arm."""

    z_range = await self.roma.report_z_param(5)
    x, y, z = self._roma_positions(drop.resource, drop.resource.get_absolute_location(), z_range)
    xt, yt, zt = self._roma_positions(drop.resource, drop.destination, z_range)

    # Set slower speeds for safer movement
    await self.roma.set_fast_speed_x(self.roma_speed)
    await self.roma.set_fast_speed_y(self.roma_speed // 2, self.roma_speed // 4)
    await self.roma.set_fast_speed_z(self.roma_speed // 2)
    await self.roma.set_fast_speed_r(self.roma_speed // 2, self.roma_speed // 4)

    # move to target
    await self.roma.set_target_window_class(1, 0, 0, 0, 135, 0)
    await self.roma.set_target_window_class(2, 0, 0, 0, 53, 0)
    await self.roma.set_target_window_class(3, 0, 0, 0, 55, 0)
    await self.roma.set_target_window_class(4, 45, 0, 0, 0, 0)
    await self.roma.set_vector_coordinate_position(1, x, y, z["end"], 900, None, 1, 1)
    await self.roma.set_vector_coordinate_position(2, x, y, z["travel"], 900, None, 1, 2)
    await self.roma.set_vector_coordinate_position(3, x, y, z["safe"], 900, None, 1, 3)
    await self.roma.set_vector_coordinate_position(4, xt, yt, zt["safe"], 900, None, 1, 4)
    await self.roma.set_vector_coordinate_position(5, xt, yt, zt["travel"], 900, None, 1, 3)
    await self.roma.set_vector_coordinate_position(6, xt, yt, zt["end"], 900, None, 1, 0)
    await self.roma.action_move_vector_coordinate_position()

    # release resource
    await self.roma.position_absolute_g(900)
    await self.roma.set_fast_speed_y(self.roma_speed // 2, self.roma_speed // 4)
    await self.roma.set_fast_speed_r(self.roma_speed // 2, self.roma_speed // 4)
    await self.roma.set_vector_coordinate_position(1, xt, yt, zt["end"], 900, None, 1, 1)
    await self.roma.set_vector_coordinate_position(2, xt, yt, zt["travel"], 900, None, 1, 2)
    await self.roma.set_vector_coordinate_position(3, xt, yt, zt["safe"], 900, None, 1, 0)
    await self.roma.action_move_vector_coordinate_position()
    await self.roma.set_fast_speed_y(self.roma_speed // 3, self.roma_speed // 5)
    await self.roma.set_fast_speed_r(self.roma_speed // 3, self.roma_speed // 5)

  def _first_valid(self, lst: List[Optional[T]]) -> Tuple[Optional[T], int]:
    """Returns first item in list that is not None"""

    for i, v in enumerate(lst):
      if v is not None:
        return v, i
    return None, -1

  def _bin_use_channels(self, use_channels: List[int]) -> int:
    """Converts use_channels to a binary coded tip representation for first LIHA."""

    b = 0
    for channel in use_channels:
      b += 1 << channel
    return b

  def _bin_use_channels2(self, use_channels: List[int]) -> int:
    """Converts use_channels to a binary coded tip representation for second LIHA."""

    b = 0
    for channel in use_channels:
      b += 1 << channel
    return b

  def _liha_positions(
    self,
    ops: Sequence[Union[Aspiration, Dispense, Pickup, Drop]],
    use_channels: List[int],
  ) -> Tuple[
    List[Optional[int]],
    List[Optional[int]],
    Dict[str, List[Optional[int]]],
  ]:
    """Creates lists of x, y, and z positions used by LiHa ops."""

    # Get the total number of channels from first valid op
    # This works for either LIHA1 or LIHA2 since the structure is the same
    first_channel = use_channels[0] if use_channels else 0
    num_channels = (
      max(self.num_channels, self.num_channels2)
      if hasattr(self, "num_channels2")
      else self.num_channels
    )

    x_positions: List[Optional[int]] = [None] * num_channels
    y_positions: List[Optional[int]] = [None] * num_channels
    z_positions: Dict[str, List[Optional[int]]] = {
      "travel": [None] * num_channels,
      "start": [None] * num_channels,
      "dispense": [None] * num_channels,
      "max": [None] * num_channels,
    }

    def get_z_position(z, z_off, tip_length, additional_offset=0):
      # additional_offset = 0
      return int(
        self._z_range - z + z_off * 10 + tip_length + additional_offset
      )  # TODO: verify z formula

    for i, channel in enumerate(use_channels):
      location = ops[i].resource.get_absolute_location() + ops[i].resource.center()
      x_positions[channel] = int((location.x - 100) * 10)
      y_positions[channel] = int((346.5 - location.y) * 10)  # TODO: verify

      par = ops[i].resource.parent
      if not isinstance(par, (TecanPlate, TecanTipRack)):
        raise ValueError(f"Operation is not supported by resource {par}.")
      # TODO: calculate defaults when z-attribs are not specified
      tip_length = int(ops[i].tip.total_tip_length * 10)
      print(
        f"[Channel {channel}] z_dispense={par.z_dispense}, abs_z={par.get_absolute_location().z}, tip_len={tip_length}"
      )
      print(
        f" -> final Z_dispense: {get_z_position(par.z_dispense, par.get_absolute_location().z, tip_length, additional_offset=300)}"
      )

      z_positions["travel"][channel] = get_z_position(
        par.z_travel, par.get_absolute_location().z, tip_length, additional_offset=100
      )
      z_positions["start"][channel] = get_z_position(
        par.z_start, par.get_absolute_location().z, tip_length
      )
      z_positions["dispense"][channel] = get_z_position(
        par.z_dispense, par.get_absolute_location().z, tip_length, additional_offset=300
      )
      z_positions["max"][channel] = get_z_position(
        par.z_max, par.get_absolute_location().z, tip_length
      )

    return x_positions, y_positions, z_positions

  def _aspirate_airgap(
    self,
    use_channels: List[int],
    tecan_liquid_classes: List[Optional[TecanLiquidClass]],
    airgap: str,
  ) -> Tuple[List[Optional[int]], List[Optional[int]], List[Optional[int]]]:
    """Creates parameters used to aspirate airgaps.

    Args:
      airgap: `lag` for leading airgap, `tag` for trailing airgap.

    Returns:
      pvl: position_valve_logial
      sep: set_end_speed_plunger
      ppr: move_plunger_relative
    """
    # Get the total number of channels from first valid channel
    first_channel = use_channels[0] if use_channels else 0
    num_channels = (
      max(self.num_channels, self.num_channels2)
      if hasattr(self, "num_channels2")
      else self.num_channels
    )

    pvl: List[Optional[int]] = [None] * num_channels
    sep: List[Optional[int]] = [None] * num_channels
    ppr: List[Optional[int]] = [None] * num_channels

    for i, channel in enumerate(use_channels):
      tlc = tecan_liquid_classes[i]
      assert tlc is not None
      pvl[channel] = 0
      if airgap == "lag":
        sep[channel] = int(tlc.aspirate_lag_speed * 12)  # 6? TODO: verify step unit
        ppr[channel] = int(tlc.aspirate_lag_volume * 6)  # 3?
      elif airgap == "tag":
        sep[channel] = int(tlc.aspirate_tag_speed * 12)  # 6?
        ppr[channel] = int(tlc.aspirate_tag_volume * 6)  # 3?

    return pvl, sep, ppr

  def _liquid_detection(
    self,
    use_channels: List[int],
    tecan_liquid_classes: List[Optional[TecanLiquidClass]],
  ) -> Tuple[List[Optional[int]], List[Optional[int]], List[Optional[int]]]:
    """Creates parameters use for liquid detection.

    Returns:
      ssl: set_search_speed
      sdl: set_search_retract_distance
      sbl: set_search_submerge
    """
    # Get the total number of channels from first valid channel
    first_channel = use_channels[0] if use_channels else 0
    num_channels = (
      max(self.num_channels, self.num_channels2)
      if hasattr(self, "num_channels2")
      else self.num_channels
    )

    ssl: List[Optional[int]] = [None] * num_channels
    sdl: List[Optional[int]] = [None] * num_channels
    sbl: List[Optional[int]] = [None] * num_channels

    for i, channel in enumerate(use_channels):
      tlc = tecan_liquid_classes[i]
      assert tlc is not None
      ssl[channel] = int(tlc.lld_speed * 10)
      sdl[channel] = int(tlc.lld_distance * 10)
      sbl[channel] = int(tlc.aspirate_lld_offset * 10)

    return ssl, sdl, sbl

  def _aspirate_action(
    self,
    ops: Sequence[Union[Aspiration, Dispense]],
    use_channels: List[int],
    tecan_liquid_classes: List[Optional[TecanLiquidClass]],
    zadd: List[Optional[int]],
  ) -> Tuple[
    List[Optional[int]],
    List[Optional[int]],
    List[Optional[int]],
    List[Optional[int]],
    List[Optional[int]],
  ]:
    """Creates parameters used for aspiration action.

    Args:
      zadd: distance moved while aspirating

    Returns:
      ssz: set_slow_speed_z
      sep: set_end_speed_plunger
      stz: set_tracking_distance_z
      mtr: move_tracking_relative
      ssz_r: set_slow_speed_z retract
    """
    # Get the total number of channels from first valid channel
    first_channel = use_channels[0] if use_channels else 0
    num_channels = (
      max(self.num_channels, self.num_channels2)
      if hasattr(self, "num_channels2")
      else self.num_channels
    )

    ssz: List[Optional[int]] = [None] * num_channels
    sep: List[Optional[int]] = [None] * num_channels
    stz: List[Optional[int]] = [-z if z else None for z in zadd]  # TODO: verify max cutoff
    mtr: List[Optional[int]] = [None] * num_channels
    ssz_r: List[Optional[int]] = [None] * num_channels

    for i, channel in enumerate(use_channels):
      tlc = tecan_liquid_classes[i]
      z = zadd[channel]
      assert tlc is not None and z is not None
      sep[channel] = int(tlc.aspirate_speed * 12)  # 6?
      ssz[channel] = round(z * tlc.aspirate_speed / ops[i].volume)
      volume = tlc.compute_corrected_volume(ops[i].volume)
      mtr[channel] = round(volume * 6)  # 3?
      ssz_r[channel] = int(tlc.aspirate_retract_speed * 10)

    return ssz, sep, stz, mtr, ssz_r

  def _dispense_action(
    self,
    ops: Sequence[Union[Aspiration, Dispense]],
    use_channels: List[int],
    tecan_liquid_classes: List[Optional[TecanLiquidClass]],
  ) -> Tuple[
    List[Optional[int]],
    List[Optional[int]],
    List[Optional[int]],
    List[Optional[int]],
  ]:
    """Creates parameters used for dispense action.

    Returns:
      sep: set_end_speed_plunger
      spp: set_stop_speed_plunger
      stz: set_tracking_distance_z
      mtr: move_tracking_relative
    """
    # Get the total number of channels from first valid channel
    first_channel = use_channels[0] if use_channels else 0
    num_channels = (
      max(self.num_channels, self.num_channels2)
      if hasattr(self, "num_channels2")
      else self.num_channels
    )

    sep: List[Optional[int]] = [None] * num_channels
    spp: List[Optional[int]] = [None] * num_channels
    stz: List[Optional[int]] = [None] * num_channels
    mtr: List[Optional[int]] = [None] * num_channels

    for i, channel in enumerate(use_channels):
      tlc = tecan_liquid_classes[i]
      assert tlc is not None
      sep[channel] = int(tlc.dispense_speed * 12)  # 6?
      spp[channel] = int(tlc.dispense_breakoff * 12)  # 6?
      stz[channel] = 0
      volume = (
        tlc.compute_corrected_volume(ops[i].volume)
        + tlc.aspirate_lag_volume
        + tlc.aspirate_tag_volume
      )
      mtr[channel] = -round(volume * 6)  # 3?

    return sep, spp, stz, mtr

  def _roma_positions(
    self, resource: Resource, offset: Coordinate, z_range: int
  ) -> Tuple[int, int, Dict[str, int]]:
    """Creates x, y, and z positions used by RoMa ops."""

    par = resource.parent
    if par is None:
      raise ValueError(f"Operation is not supported by resource {resource}.")
    par = par.parent
    if not isinstance(par, TecanPlateCarrier):
      raise ValueError(f"Operation is not supported by resource {par}.")

    if (
      par.roma_x is None
      or par.roma_y is None
      or par.roma_z_safe is None
      or par.roma_z_travel is None
      or par.roma_z_end is None
    ):
      raise ValueError(f"Operation is not supported by resource {par}.")
    x_position = int((offset.x - 100) * 10 + par.roma_x)
    y_position = int((347.1 - (offset.y + resource.get_absolute_size_y())) * 10 + par.roma_y)
    z_positions = {
      "safe": z_range - int(par.roma_z_safe),
      "travel": z_range - int(par.roma_z_travel - offset.z * 10),
      "end": z_range - int(par.roma_z_end - offset.z * 10),
    }

    return x_position, y_position, z_positions


class EVOArm:
  """
  Provides firmware commands for EVO arms. Caches arm positions.
  """

  _pos_cache: Dict[str, int] = {}

  def __init__(self, backend: EVO, module: str):
    self.backend = backend
    self.module = module

  async def report_x_param(self, param: int) -> int:
    """Report current parameter for x-axis.

    Args:
      param: 0 - current position, 5 - actual machine range
    """
    try:
      resp = await self.backend.send_command(
        module=self.module, command="RPX", params=[param], retry_count=1
      )
      if resp and "data" in resp and resp["data"]:
        return resp["data"][0]

      # If no valid response, use cached position if available
      if self.module in EVOArm._pos_cache:
        return EVOArm._pos_cache[self.module]

      # Default fallback values
      if self.module == EVO.LIHA:
        return EVO.DEFAULT_POSITIONS[EVO.LIHA]
      elif self.module == EVO.LIHA2:
        return EVO.DEFAULT_POSITIONS[EVO.LIHA2]
      elif self.module == EVO.ROMA:
        return EVO.DEFAULT_POSITIONS[EVO.ROMA]
      return 0
    except Exception as e:
      logger.warning(f"Error getting X parameter for {self.module}: {e}")
      return 0

  async def report_y_param(self, param: int) -> List[int]:
    """Report current parameters for y-axis.

    Args:
      param: 0 - current position, 5 - actual machine range
    """
    try:
      resp = await self.backend.send_command(
        module=self.module, command="RPY", params=[param], retry_count=1
      )
      if resp and "data" in resp and resp["data"]:
        return resp["data"]
      return [1031]  # Default Y position
    except Exception as e:
      logger.warning(f"Error getting Y parameter for {self.module}: {e}")
      return [1031]


class LiHa(EVOArm):
  """
  Provides firmware commands for the LiHa
  """

  async def initialize_plunger(self, tips):
    """Initializes plunger and valve drive

    Args:
      tips: binary coded tip select
    """
    await self.backend.send_command(module=self.module, command="PID", params=[tips])

  async def report_z_param(self, param: int) -> List[int]:
    """Report current parameters for z-axis.

    Args:
      param: 0 - current position, 5 - actual machine range
    """
    try:
      resp = await self.backend.send_command(
        module=self.module, command="RPZ", params=[param], retry_count=1
      )
      if resp and "data" in resp and resp["data"]:
        return resp["data"]
      return [2000]  # Default Z range
    except Exception as e:
      logger.warning(f"Error getting Z parameter for {self.module}: {e}")
      return [2000]

  async def report_number_tips(self) -> int:
    """Report number of tips on arm."""
    try:
      resp = await self.backend.send_command(
        module=self.module, command="RNT", params=[1], retry_count=1
      )
      if resp and "data" in resp and resp["data"]:
        return resp["data"][0]
      return 8  # Default to 8 channels
    except Exception as e:
      logger.warning(f"Error getting number of tips for {self.module}: {e}")
      return 8

  async def position_absolute_all_axis(self, x: int, y: int, ys: int, z: List[int]):
    """Position absolute for all LiHa axes.

    Args:
      x: aboslute x position in 1/10 mm, must be in allowed machine range
      y: absolute y position in 1/10 mm, must be in allowed machine range
      ys: absolute y spacing in 1/10 mm, must be between 90 and 380
      z: absolute z position in 1/10 mm for each channel, must be in
         allowed machine range

    Raises:
      TecanError: if moving to the target position causes a collision
    """

    cur_x = EVOArm._pos_cache.setdefault(self.module, await self.report_x_param(0))
    for module, pos in EVOArm._pos_cache.items():
      if module == self.module:
        continue

      # Check for potential collision with increased safety distance
      if cur_x < x and cur_x < pos < x:  # moving right
        logger.warning(
          f"Checking collision: {self.module} moving from {cur_x} to {x}, {module} at {pos}"
        )
        if abs(pos - x) < EVO.SAFE_DISTANCE:
          logger.warning(
            f"Potential collision detected with {module} at {pos} when moving {self.module} from {cur_x} to {x}"
          )
          raise TecanError("Invalid command (collision)", self.module, 2)

      if cur_x > x and cur_x > pos > x:  # moving left
        logger.warning(
          f"Checking collision: {self.module} moving from {cur_x} to {x}, {module} at {pos}"
        )
        if abs(pos - x) < EVO.SAFE_DISTANCE:
          logger.warning(
            f"Potential collision detected with {module} at {pos} when moving {self.module} from {cur_x} to {x}"
          )
          raise TecanError("Invalid command (collision)", self.module, 2)

    await self.backend.send_command(module=self.module, command="PAA", params=list([x, y, ys] + z))

    EVOArm._pos_cache[self.module] = x

  async def position_valve_logical(self, param: List[Optional[int]]):
    """Position valve logical for each channel.

    Args:
      param: 0 - outlet, 1 - inlet, 2 - bypass
    """

    await self.backend.send_command(module=self.module, command="PVL", params=param)

  async def set_end_speed_plunger(self, speed: List[Optional[int]]):
    """Set end speed for plungers.

    Args:
      speed: speed for each plunger in half step per second, must be between
             5 and 6000
    """

    await self.backend.send_command(module=self.module, command="SEP", params=speed)

  async def move_plunger_relative(self, rel: List[Optional[int]]):
    """Move plunger relative upwards (dispense) or downards (aspirate).

    Args:
      rel: relative position for each plunger in full steps, must be between
           -3150 and 3150
    """

    await self.backend.send_command(module=self.module, command="PPR", params=rel)

  async def set_detection_mode(self, proc: int, sense: int):
    """Set liquid detection mode.

    Args:
      proc: detection procedure (7 for double detection sequential with
            retract and extra submerge)
      sense: conductivity (1 for high)
    """

    await self.backend.send_command(module=self.module, command="SDM", params=[proc, sense])

  async def set_search_speed(self, speed: List[Optional[int]]):
    """Set search speed for liquid search commands.

    Args:
      speed: speed for each channel in 1/10 mm/s, must be between 1 and 1500
    """

    await self.backend.send_command(module=self.module, command="SSL", params=speed)

  async def set_search_retract_distance(self, dist: List[Optional[int]]):
    """Set z-axis retract distance for liquid search commands.

    Args:
      dist: retract distance for each channel in 1/10 mm, must be in allowed
            machine range
    """

    await self.backend.send_command(module=self.module, command="SDL", params=dist)

  async def set_search_submerge(self, dist: List[Optional[int]]):
    """Set submerge for liquid search commands.

    Args:
      dist: submerge distance for each channel in 1/10 mm, must be between
            -1000 and max z range
    """

    await self.backend.send_command(module=self.module, command="SBL", params=dist)

  async def set_search_z_start(self, z: List[Optional[int]]):
    """Set z-start for liquid search commands.

    Args:
      z: start height for each channel in 1/10 mm, must be in allowed machine range
    """

    await self.backend.send_command(module=self.module, command="STL", params=z)

  async def set_search_z_max(self, z: List[Optional[int]]):
    """Set z-max for liquid search commands.

    Args:
      z: max for each channel in 1/10 mm, must be in allowed machine range
    """

    await self.backend.send_command(module=self.module, command="SML", params=z)

  async def set_z_travel_height(self, z):
    """Set z-travel height.

    Args:
      z: travel heights in absolute 1/10 mm for each channel, must be in allowed
         machine range + 20
    """
    await self.backend.send_command(module=self.module, command="SHZ", params=z)

  async def move_detect_liquid(self, channels: int, zadd: List[Optional[int]]):
    """Move tip, detect liquid, submerge.

    Args:
      channels: binary coded tip select
      zadd: required distance to travel downwards in 1/10 mm for each channel,
            must be between 0 and z-start - z-max
    """

    await self.backend.send_command(
      module=self.module,
      command="MDT",
      params=[channels] + [None] * 3 + zadd,
    )

  async def set_slow_speed_z(self, speed: List[Optional[int]]):
    """Set slow speed for z.

    Args:
      speed: speed in 1/10 mm/s for each channel, must be between 1 and 4000
    """

    await self.backend.send_command(module=self.module, command="SSZ", params=speed)

  async def set_tracking_distance_z(self, rel: List[Optional[int]]):
    """Set z-axis relative tracking distance used by dispense and aspirate.

    Args:
      rel: relative value in 1/10 mm for each channel, must be between
            -2100 and 2100
    """

    await self.backend.send_command(module=self.module, command="STZ", params=rel)

  async def move_tracking_relative(self, rel: List[Optional[int]]):
    """Move tracking relative. Starts the z-drives and dilutors simultaneously
        to achieve a synchronous tracking movement.

    Args:
      rel: relative position for each plunger in full steps, must be between
           -3150 and 3150
    """

    await self.backend.send_command(module=self.module, command="MTR", params=rel)

  async def move_absolute_z(self, z: List[Optional[int]]):
    """Position absolute with slow speed z-axis

    Args:
      z: absolute position in 1/10 mm for each channel, must be in
         allowed machine range
    """

    await self.backend.send_command(module=self.module, command="MAZ", params=z)

  async def set_stop_speed_plunger(self, speed: List[Optional[int]]):
    """Set stop speed for plungers

    Args:
      speed: speed for each plunger in half step per second, must be between
             50 and 2700
    """

    await self.backend.send_command(module=self.module, command="SPP", params=speed)

  async def get_disposable_tip(self, tips, z_start, z_search):
    """Picks up tips

    Args:
      tips: binary coded tip select
      z_start: position in 1/10 mm where searching begins
      z_search: search distance in 1/10 mm, range within a tip must be found
    """

    await self.backend.send_command(
      module=self.module,
      command="AGT",
      params=[tips, z_start, z_search, 0],
    )

  async def discard_disposable_tip(self, tips):
    """Drops tips

    Args:
      tips: binary coded tip select
    """

    await self.backend.send_command(module=self.module, command="ADT", params=[tips])


class RoMa(EVOArm):
  """
  Provides firmware commands for the RoMa plate robot
  """

  async def report_z_param(self, param: int) -> int:
    """Report current parameter for z-axis.

    Args:
      param: 0 - current position, 5 - actual machine range
    """
    try:
      resp = await self.backend.send_command(
        module=self.module, command="RPZ", params=[param], retry_count=1
      )
      if resp and "data" in resp and resp["data"]:
        return resp["data"][0]
      return 2464  # Default Z position
    except Exception as e:
      logger.warning(f"Error getting Z parameter for RoMa: {e}")
      return 2464

  async def report_r_param(self, param: int) -> int:
    """Report current parameter for r-axis.

    Args:
      param: 0 - current position, 5 - actual machine range
    """
    try:
      resp = await self.backend.send_command(
        module=self.module, command="RPR", params=[param], retry_count=1
      )
      if resp and "data" in resp and resp["data"]:
        return resp["data"][0]
      return 1800  # Default R position
    except Exception as e:
      logger.warning(f"Error getting R parameter for RoMa: {e}")
      return 1800

  async def report_g_param(self, param: int) -> int:
    """Report current parameter for g-axis.

    Args:
      param: 0 - current position, 5 - actual machine range
    """
    try:
      resp = await self.backend.send_command(
        module=self.module, command="RPG", params=[param], retry_count=1
      )
      if resp and "data" in resp and resp["data"]:
        return resp["data"][0]
      return 900  # Default G position
    except Exception as e:
      logger.warning(f"Error getting G parameter for RoMa: {e}")
      return 900

  async def set_smooth_move_x(self, mode: int):
    """Sets X-axis smooth move.

    Args:
      mode: 0 - active, 1 - normal acceleration and speed used
    """

    await self.backend.send_command(module=self.module, command="SSM", params=[mode])

  async def set_fast_speed_x(self, speed: Optional[int], accel: Optional[int] = None):
    """Set fast speed and acceleration for X-axis.

    Args:
      speed: fast speed in 1/10 mm/s
      accel: acceleration in 1/10 mm/s^2
    """

    await self.backend.send_command(module=self.module, command="SFX", params=[speed, accel])

  async def set_fast_speed_y(self, speed: Optional[int], accel: Optional[int] = None):
    """Set fast speed and acceleration for Y-axis.

    Args:
      speed: fast speed in 1/10 mm/s
      accel: acceleration in 1/10 mm/s^2
    """

    await self.backend.send_command(module=self.module, command="SFY", params=[speed, accel])

  async def set_fast_speed_z(self, speed: Optional[int], accel: Optional[int] = None):
    """Set fast speed and acceleration for Z-axis.

    Args:
      speed: fast speed in 1/10 mm/s
      accel: acceleration in 1/10 mm/s^2
    """

    await self.backend.send_command(module=self.module, command="SFZ", params=[speed, accel])

  async def set_fast_speed_r(self, speed: Optional[int], accel: Optional[int] = None):
    """Set fast speed and acceleration for R-axis.

    Args:
      speed: fast speed in 1/10 dg/s
      accel: acceleration in 1/10 dg/s^2
    """

    await self.backend.send_command(module=self.module, command="SFR", params=[speed, accel])

  async def set_vector_coordinate_position(
    self,
    v: int,
    x: int,
    y: int,
    z: int,
    r: int,
    g: Optional[int],
    speed: int,
    tw: int = 0,
  ):
    """Sets vector coordinate positions into table.

    Args:
      v: vector to be defined, must be between 1 and 100
      x: aboslute x position in 1/10 mm
      y: aboslute y position in 1/10 mm
      z: aboslute z position in 1/10 mm
      r: aboslute r position in 1/10 mm
      g: aboslute g position in 1/10 mm
      speed: speed select, 0 - slow, 1 - fast
      tw: target window class, set with STW

    Raises:
      TecanError: if moving to the target position causes a collision
    """

    cur_x = EVOArm._pos_cache.setdefault(self.module, await self.report_x_param(0))
    for module, pos in EVOArm._pos_cache.items():
      if module == self.module:
        continue

      # Check for potential collision with increased safety distance
      if cur_x < x and cur_x < pos < x:  # moving right
        logger.warning(
          f"Checking collision: {self.module} moving from {cur_x} to {x}, {module} at {pos}"
        )
        if abs(pos - x) < EVO.SAFE_DISTANCE:
          logger.warning(
            f"Potential collision detected with {module} at {pos} when moving {self.module} from {cur_x} to {x}"
          )
          raise TecanError("Invalid command (collision)", self.module, 2)

      if cur_x > x and cur_x > pos > x:  # moving left
        logger.warning(
          f"Checking collision: {self.module} moving from {cur_x} to {x}, {module} at {pos}"
        )
        if abs(pos - x) < EVO.SAFE_DISTANCE:
          logger.warning(
            f"Potential collision detected with {module} at {pos} when moving {self.module} from {cur_x} to {x}"
          )
          raise TecanError("Invalid command (collision)", self.module, 2)

    await self.backend.send_command(
      module=self.module,
      command="SAA",
      params=[v, x, y, z, r, g, speed, 0, tw],
    )

  async def action_move_vector_coordinate_position(self):
    """Starts coordinate movement, built by vector coordinate table."""

    await self.backend.send_command(module=self.module, command="AAC")

    # Update position cache after movement
    try:
      new_pos = await self.report_x_param(0)
      EVOArm._pos_cache[self.module] = new_pos
    except Exception as e:
      logger.warning(f"Failed to update position cache after movement: {e}")

  async def position_absolute_g(self, g: int):
    """Position absolute for G-axis

    Args:
      g: absolute position in 1/10 mm
    """

    await self.backend.send_command(module=self.module, command="PAG", params=[g])

  async def set_gripper_params(self, speed: int, pwm: int, cur: Optional[int] = None):
    """Set gripper parameters

    Args:
      speed: gripper search speed in 1/10 mm/s
      pwm: pulse width modification limit
      cur: maximal allowed current
    """

    await self.backend.send_command(module=self.module, command="SGG", params=[speed, pwm, cur])

  async def grip_plate(self, pos: int):
    """Grips plate at current X/Y/Z/R-position

    Args:
      pos: target position, plate must be fetched within current and target position
    """

    await self.backend.send_command(module=self.module, command="AGR", params=[pos])

  async def set_target_window_class(self, wc: int, x: int, y: int, z: int, r: int, g: int):
    """Sets drive parameters for the AAC command.

    Args:
      wc: window class, must be between 1 and 100
      x: target window for x-axis in 1/10 mm
      y: target window for y-axis in 1/10 mm
      z: target window for z-axis in 1/10 mm
      r: target window for r-axis in 1/10 deg
      g: target window for g-axis in 1/10 mm
    """

    await self.backend.send_command(module=self.module, command="STW", params=[wc, x, y, z, r, g])
