"""The PreciseFlex arms as resources: the device, and the base plate the driver builds it on.

A frame factory builds the base plate the device stands on. Everything it carries - the column,
the carriage, the arm, the gripper - is hung off it by the driver, from a configuration.
"""

import logging
import os
from typing import Optional

from pylabrobot.brooks.precise_flex.driver.features.arm import PreciseFlexArm
from pylabrobot.brooks.precise_flex.driver.features.gripper import PreciseFlexGripper
from pylabrobot.brooks.precise_flex.driver.features.rail import PreciseFlexRail
from pylabrobot.brooks.precise_flex.driver.master import PreciseFlexDriver
from pylabrobot.brooks.precise_flex.driver.simulator import PreciseFlexSimulationDriver
from pylabrobot.brooks.precise_flex.resource_model.pf400_manipulator import (
  BASE_PLATE_SIZE,
  base_plate,
)
from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.resource import Resource

logger = logging.getLogger(__name__)

_RECORDINGS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "driver", "recordings")
# What an extended-reach PreciseFlex 400 with 400 mm of Z travel reported about itself.
RECORDING_PF400 = os.path.join(_RECORDINGS, "pf400_extended_400mm.json")


class PreciseFlexDevice(Resource, HasLifespan):
  """The complete modelling and control interface for a PreciseFlex arm.

  The device is itself a resource and its base plate is its child: one tree, rooted here.
  `device.driver` speaks to the arm in its own terms and stays reachable.

  Open the device with ``async with device:`` (or ``async with device.lifespan(skip_home=True):``).
  """

  def __init__(
    self,
    base_plate: Resource,
    driver: PreciseFlexDriver,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    model: Optional[str] = None,
  ) -> None:
    """Bind a PreciseFlex driver to its base plate resource.

    Args:
      base_plate: what the device stands on, and the driver builds the arm on. It becomes a
        child of the device.
      driver: the driver to drive the device through.
      name: what to call this device in the resource tree.
      size_x: how wide the device is, in mm.
      size_y: how deep it is, in mm.
      size_z: how tall it is, in mm.
      model: which device this is. Defaults to the class name.
    """
    super().__init__(
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      category="device",
      model=model if model is not None else self.__class__.__name__,
    )
    HasLifespan.__init__(self)
    self.base_plate = base_plate
    self.driver = driver
    if self.driver.base_plate is not None and self.driver.base_plate is not base_plate:
      logger.warning("the driver was given another base plate; modelling onto this device's")
    self.driver.base_plate = base_plate
    self.assign_child_resource(base_plate, location=Coordinate.zero())

  @property
  def arm(self) -> PreciseFlexArm:
    """The arm, as the driver has it."""
    return self.driver.arm

  @property
  def gripper(self) -> PreciseFlexGripper:
    """The gripper, as the driver has it."""
    return self.driver.gripper

  @property
  def rail(self) -> Optional[PreciseFlexRail]:
    """The rail, if the arm stands on one."""
    return self.driver.rail

  async def _enter_lifespan(
    self,
    stack: AsyncExitStackWithShielding,
    *,
    skip_home: bool = False,
  ) -> None:
    """Open the underlying driver lifespan.

    Args:
      stack: exit stack managing lifespan resources.
      skip_home: as ``PreciseFlexDriver.lifespan`` takes it.
    """
    await super()._enter_lifespan(stack)
    await stack.enter_async_context(self.driver.lifespan(skip_home=skip_home))


def PreciseFlex400(
  simulation: bool = False,
  declared_configuration_json: Optional[str] = None,
  driver: Optional[PreciseFlexDriver] = None,
  name: str = "PreciseFlex400",
) -> PreciseFlexDevice:
  """A PreciseFlex 400: its base plate, for the driver to build the arm on.

  Nothing about the arm is declared here. The column, the carriage, the links and the gripper are
  built when the lifespan opens, from the configuration the controller answers - a simulated one
  answers as its declared configuration. Where the controller reports from is
  `pf400_manipulator.SHOULDER_AXIS`, within the base plate.

  Args:
    simulation: whether to build a simulated arm, which answers without being connected.
      Superseded by `driver`, which says exactly what to drive.
    declared_configuration_json: path to a declared configuration, for the simulated arm this
      builds. Defaults to `RECORDING_PF400`. A driver given outright brings its own.
    driver: the driver to drive the device through.
    name: what to call this one.

  Returns:
    The device.

  Raises:
    ValueError: If neither a driver nor simulation is given, since there is then nothing to drive.
  """
  if driver is None:
    if not simulation:
      raise ValueError("pass a driver, or `simulation=True` to build a simulated one")
    driver = PreciseFlexSimulationDriver(
      declared_configuration_json=declared_configuration_json or RECORDING_PF400
    )
  else:
    if simulation:
      logger.warning("both a driver and simulation given; driving the driver")
    if declared_configuration_json is not None:
      logger.warning("a driver was given, so it brings its own declared configuration")
  device = PreciseFlexDevice(
    base_plate=base_plate(name=f"{name}_base_plate"),
    driver=driver,
    name=name,
    # The device's own cuboid is the base plate it stands on; what it carries reaches above that.
    size_x=BASE_PLATE_SIZE[0],
    size_y=BASE_PLATE_SIZE[1],
    size_z=BASE_PLATE_SIZE[2],
    model=PreciseFlex400.__name__,
  )
  return device
