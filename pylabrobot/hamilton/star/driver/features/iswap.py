"""The iSWAP: the arm that picks plates up and puts them down."""

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple, cast

if TYPE_CHECKING:
  from pylabrobot.hamilton.star.driver.master import STARDriver

logger = logging.getLogger(__name__)

# What the rotation drive's stored position table holds, slot by slot. The tenth slot is the arm
# length, read separately. The extra slots are addressable but have no documented meaning.
ROTATION_DRIVE_SLOTS = (
  "home",
  "left",
  "front",
  "right",
  "parking",
  "extra_1",
  "extra_2",
  "extra_3",
  "extra_4",
)

# The same for the wrist twist drive.
WRIST_DRIVE_SLOTS = (
  "home",
  "right",
  "straight",
  "left",
  "reverse",
  "extra_1",
  "extra_2",
  "extra_3",
  "extra_4",
)

# And for the Y carriage, whose table is all position and has no arm length.
Y_SLOTS = (
  "home",
  "lower_limit",
  "upper_limit",
  "parking",
  "pre_parking",
  "extra_1",
  "extra_2",
  "extra_3",
  "extra_4",
  "extra_5",
)

# What the arm the device facts below were recorded from reports for its firmware version. An arm
# reporting something else is a generation those values were not taken from.
RECORDED_FIRMWARE_PREFIX = "4."

# Where the arm is left when parked, in mm: it travels at this height on the way there.
PARK_TRAVERSAL_HEIGHT = 280.0


@dataclass
class iSWAPConfiguration:
  """Device parameters for the installed iSWAP.

  Ported from the legacy `iSWAPInformation`. Two kinds of value: per-machine calibration read from
  the machine at setup - link lengths, calibrated stops, offsets - which is None until read; and
  device facts of the 4th-generation iSWAP, the only generation supported, which are defaulted.
  Neither changes at runtime.
  """

  firmware_version: Optional[str] = None

  # -- X --
  rotation_drive_x_offset: Optional[float] = None
  """Deck X distance from the X-arm carriage center to the rotation drive (mm). Stored in master
  EEPROM. The Hamilton factory default is 34.0 mm."""

  # -- Y --
  rotation_drive_y_max: Optional[float] = None

  # -- rotation drive --
  rotation_drive_predefined_increments: Optional[Dict[str, int]] = None
  link_1_length: Optional[float] = None
  """rotation joint (joint 1) to the wrist joint (joint 2); default: 138.0 mm."""

  # -- wrist drive --
  wrist_drive_predefined_increments: Optional[Dict[str, int]] = None
  link_2_length: Optional[float] = None
  """wrist joint (joint 2) to the gripper finger center, in mm. default: 138.0 mm."""

  # === Device facts of the 4th-generation iSWAP: per-drive area-of-operation ranges and encoder
  # resolutions. The same across units of a generation, so they are defaulted - but only that
  # generation's are held. On an arm of another generation every conversion below would be wrong,
  # so discovery says so when the arm reports a firmware version these were not taken from. ===

  # -- Y --
  y_increment_range: Tuple[int, int] = (0, 14_000)
  y_mm_per_increment: float = 0.046302083
  y_speed_increment_range: Tuple[int, int] = (50, 8_000)  # increments/sec

  # -- Z --
  z_increment_range: Tuple[int, int] = (-187, 26_661)
  z_mm_per_increment: float = 0.01072765
  z_speed_increment_range: Tuple[int, int] = (50, 15_000)  # increments/sec
  z_acceleration_increment_range: Tuple[int, int] = (5, 999)  # 1000 increments/sec^2

  # -- rotation drive (joint 1) --
  rotation_increment_range: Tuple[int, int] = (-30_032, 30_032)
  rotation_deg_per_increment: float = 0.00309619077

  # -- wrist drive (joint 2) --
  wrist_increment_range: Tuple[int, int] = (-30_000, 30_000)
  wrist_deg_per_increment: float = 0.00507968798

  # -- gripper --
  gripper_increment_range: Tuple[int, int] = (12_780, 24_120)  # jaw width
  gripper_mm_per_increment: float = 0.00554337

  # -- conversions: the wire counts in increments, the driver speaks mm and degrees ----------

  def y_increments_to_mm(self, increments: int) -> float:
    """A Y-carriage position in mm, from the increments the drive counts in."""
    return round(increments * self.y_mm_per_increment, 1)

  def y_mm_to_increments(self, mm: float) -> int:
    """A Y-carriage position in increments, from mm."""
    return round(mm / self.y_mm_per_increment)

  def z_increments_to_mm(self, increments: int) -> float:
    """A Z position in mm, from increments."""
    return round(increments * self.z_mm_per_increment, 1)

  def z_mm_to_increments(self, mm: float) -> int:
    """A Z position in increments, from mm."""
    return round(mm / self.z_mm_per_increment)

  def rotation_increments_to_deg(self, increments: int) -> float:
    """A rotation-drive angle in degrees, from increments."""
    return increments * self.rotation_deg_per_increment

  def rotation_deg_to_increments(self, deg: float) -> int:
    """A rotation-drive angle in increments, from degrees."""
    return round(deg / self.rotation_deg_per_increment)

  def wrist_increments_to_deg(self, increments: int) -> float:
    """A wrist-drive angle in degrees, from increments."""
    return increments * self.wrist_deg_per_increment

  def wrist_deg_to_increments(self, deg: float) -> int:
    """A wrist-drive angle in increments, from degrees."""
    return round(deg / self.wrist_deg_per_increment)

  def gripper_increments_to_mm(self, increments: int) -> float:
    """A gripper jaw width in mm, from increments. One decimal, as the machine resolves it."""
    return round(increments * self.gripper_mm_per_increment, 1)

  def gripper_mm_to_increments(self, mm: float) -> int:
    """A gripper jaw width in increments, from mm."""
    return round(mm / self.gripper_mm_per_increment)


class iSWAP:
  """The internal Swivel Arm Plate (iSWAP) handler.

  Reached as `driver.iswap`, on a machine that has one. It is addressed as `R0`, but the commands
  that move it go to the master, so this capability speaks to both.
  """

  def __init__(self, driver: "STARDriver", configuration: Optional[iSWAPConfiguration] = None):
    """
    Args:
      driver: the driver to send commands through.
      configuration: the iSWAP's device facts. Defaults to `iSWAPConfiguration()`.
    """
    self._driver = driver
    self.configuration = configuration or iSWAPConfiguration()

  # -- session / discovery ---------------------------------------------------

  async def request_firmware_version(self) -> str:
    """Request the iSWAP's firmware version.

    Returns:
      The version string, as reported.
    """
    resp: str = await self._driver.send_command(module="R0", command="RF")
    return resp.split("rf")[-1]

  async def request_rotation_drive_x_offset(self) -> float:
    """Request the X distance from the X-arm carriage center to the rotation drive.

    Stored in the master's own memory, as the 96-head's offset is.

    Returns:
      The offset in mm.
    """
    resp = await self._driver.send_command(module="C0", command="RA", ra="kg", fmt="kg###")
    return cast(int, resp["kg"]) / 10.0

  async def request_rotation_drive_positions(self) -> Dict[str, int]:
    """Request the rotation drive's stored position table.

    The machine returns ten signed slots; the nine position slots are returned here, and the tenth
    is the arm length, which `request_link_1_length` reads.

    Returns:
      Each named stop's motor increments.
    """
    return dict(zip(ROTATION_DRIVE_SLOTS, await self._request_slots("pw")))

  async def request_wrist_drive_positions(self) -> Dict[str, int]:
    """Request the wrist twist drive's stored position table.

    Returns:
      Each named stop's motor increments.
    """
    return dict(zip(WRIST_DRIVE_SLOTS, await self._request_slots("pt")))

  async def request_y_positions(self) -> Dict[str, float]:
    """Request the Y carriage's stored position table.

    Returns:
      Each named position in mm.
    """
    slots = await self._request_slots("py")
    return {name: self.configuration.y_increments_to_mm(slot) for name, slot in zip(Y_SLOTS, slots)}

  async def request_link_1_length(self) -> float:
    """Request the distance from the rotation joint to the wrist joint.

    Returns:
      The length in mm.
    """
    return round((await self._request_slots("pw"))[9] / 10, 1)

  async def request_link_2_length(self) -> float:
    """Request the distance from the wrist joint to the gripper finger center.

    Returns:
      The length in mm.
    """
    return round((await self._request_slots("pt"))[9] / 10, 1)

  async def _request_slots(self, table: str) -> List[int]:
    """One of the iSWAP's stored tables, as the ten signed slots the machine returns."""
    resp = await self._driver.send_command(
      module="R0", command="RA", ra=table, fmt=f"{table}##### (n)"
    )
    return cast(List[int], resp[table])

  async def discover(self):
    """Read this iSWAP's calibration. Read-only: nothing moves."""
    c = self.configuration
    c.firmware_version = await self.request_firmware_version()
    if not c.firmware_version.startswith(RECORDED_FIRMWARE_PREFIX):
      logger.warning(
        "this iSWAP reports firmware %s; the ranges and resolutions here were recorded from an arm "
        "reporting %sx, so every position, angle and width converted from them may be wrong. Set "
        "them on iSWAPConfiguration to correct it.",
        c.firmware_version,
        RECORDED_FIRMWARE_PREFIX,
      )
    c.rotation_drive_x_offset = await self.request_rotation_drive_x_offset()
    c.rotation_drive_y_max = (await self.request_y_positions())["parking"]

    rotation = await self._request_slots("pw")
    c.rotation_drive_predefined_increments = dict(zip(ROTATION_DRIVE_SLOTS, rotation))
    c.link_1_length = round(rotation[9] / 10, 1)

    wrist = await self._request_slots("pt")
    c.wrist_drive_predefined_increments = dict(zip(WRIST_DRIVE_SLOTS, wrist))
    c.link_2_length = round(wrist[9] / 10, 1)

  # -- initialization --------------------------------------------------------

  async def initialize(self):
    """Initialize the iSWAP. This moves it."""
    return await self._driver.send_command(module="C0", command="FI")

  # -- parking ---------------------------------------------------------------

  async def park(self, traversal_height: float = PARK_TRAVERSAL_HEIGHT):
    """Close the gripper and park the arm. This moves it.

    Args:
      traversal_height: the minimum height to travel at on the way, in mm.

    Raises:
      ValueError: If the traversal height is outside what the command accepts.
    """
    if not 0 <= traversal_height <= 360:
      raise ValueError(f"traversal_height must be between 0 and 360 mm, is {traversal_height}")
    return await self._driver.send_command(
      module="C0", command="PG", th=round(traversal_height * 10)
    )
