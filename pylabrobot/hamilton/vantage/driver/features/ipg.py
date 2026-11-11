"""The Vantage's internal plate gripper (IPG), which is the module `A1RM`."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
  from pylabrobot.hamilton.vantage.driver.master import VantageDriver


@dataclass
class IpgConfiguration:
  """What the IPG is asked, and how the machine answers."""

  increments_per_mm: int = 10
  """The wire carries lengths in tenths of a millimeter."""

  move_read_timeout: int = 300
  """How long to wait for the IPG to initialize or park, in seconds. The arm itself waits that long
  for a module."""


@dataclass
class IpgParkConfiguration:
  """Where the IPG parks."""

  y: float
  """Along Y, in mm."""

  z: float
  """Along Z, in mm."""

  gripper_width: float
  """How wide the gripper stands, in mm."""

  arm_angle: int
  """The angle of the arm, in degrees."""

  hand_angle: int
  """The angle of the hand, relative to the arm, in degrees."""


class Ipg:
  """The internal plate gripper.

  Reached as `driver.ipg`, which exists only on a machine that has one. Everything here is a
  question to the IPG, except `initialize` and `park`, which move it.
  """

  def __init__(self, driver: "VantageDriver"):
    self._driver = driver
    self.configuration = IpgConfiguration()

  async def request_initialization_status(self) -> bool:
    """Whether the IPG is initialized."""
    resp = await self._driver.send_command(module="A1RM", command="QW", fmt={"qw": "int"})
    return bool(resp["qw"] == 1)

  async def request_parking_status(self) -> bool:
    """Whether the IPG is in its park position."""
    resp = await self._driver.send_command(module="A1RM", command="RG", fmt={"rg": "int"})
    return bool(resp["rg"] == 1)

  async def request_plate_presence(self) -> bool:
    """Whether the IPG holds a plate."""
    resp = await self._driver.send_command(module="A1RM", command="QA", fmt={"rt": "int"})
    return bool(resp["rt"] == 1)

  async def request_angles(self) -> List[int]:
    """The angle of the arm and of the hand, relative to the arm, in degrees."""
    resp = await self._driver.send_command(module="A1RM", command="RR", fmt={"rr": "[int]"})
    return list(resp["rr"])

  async def request_park_configuration(self) -> IpgParkConfiguration:
    """Where the IPG parks."""
    resp = await self._driver.send_command(
      module="A1RM", command="RS", fmt={"rs": "[int]", "rr": "[int]"}
    )
    y, z, width = (value / self.configuration.increments_per_mm for value in resp["rs"])
    arm_angle, hand_angle = resp["rr"]
    return IpgParkConfiguration(
      y=y, z=z, gripper_width=width, arm_angle=arm_angle, hand_angle=hand_angle
    )

  async def initialize(self) -> None:
    """Initialize all the drives of the IPG. Moves it."""
    await self._driver.send_command(
      module="A1RM", command="DI", read_timeout=self.configuration.move_read_timeout
    )

  async def park(self) -> None:
    """Move the IPG to its park position. Moves it."""
    await self._driver.send_command(
      module="A1RM", command="GP", read_timeout=self.configuration.move_read_timeout
    )
