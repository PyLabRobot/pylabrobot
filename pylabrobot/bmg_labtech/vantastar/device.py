"""BMG Labtech VANTAstar plate reader as a PyLabRobot resource."""

from typing import Any, Awaitable, Callable, Optional

from pylabrobot.io.usb import USB
from pylabrobot.resources import Coordinate, PlateHolder, Resource

from .driver.features.absorbance import Absorbance
from .driver.features.drawer import Drawer
from .driver.features.fluorescence import Fluorescence
from .driver.features.luminescence import Luminescence
from .driver.features.shaker import Shaker
from .driver.features.temperature_controller import TemperatureController
from .driver.master import VANTAstarDriver

SIZE_X = 420.0
SIZE_Y = 520.0
SIZE_Z = 295.0


class _VANTAstarPlateHolder(PlateHolder):
  """The drawer's plate holder: a plate goes on or comes off only while the drawer is open."""

  def __init__(self, name: str, drawer: Drawer):
    """
    Args:
      name: the resource name.
      drawer: the drawer that carries this plate holder.
    """
    super().__init__(
      name=name,
      size_x=127.76,
      size_y=85.48,
      size_z=0,
      pedestal_size_z=0,
      child_location=Coordinate.zero(),
      model="vantastar_plate_holder",
    )
    self._drawer = drawer

  def _require_drawer_open(self, action: str) -> None:
    """Raise unless the drawer was out when last sensed.

    Raises:
      RuntimeError: if the drawer has not been sensed yet, or was sensed closed.
    """
    if self._drawer.drawer_open is None:
      raise RuntimeError(f"cannot {action}: the drawer has not been sensed; open it first")
    if not self._drawer.drawer_open:
      raise RuntimeError(f"cannot {action}: the drawer is closed; open it first")

  def check_can_drop_resource_here(self, resource: Resource, *, reassign: bool = True) -> None:
    """Raise unless the drawer is open and the holder can take ``resource``."""
    self._require_drawer_open(f"place {resource.name} on {self.name}")
    super().check_can_drop_resource_here(resource, reassign=reassign)

  def assign_child_resource(
    self,
    resource: Resource,
    location: Optional[Coordinate] = None,
    reassign: bool = True,
  ):
    """Place ``resource``, checked as a drop is (``check_can_drop_resource_here``)."""
    self.check_can_drop_resource_here(resource, reassign=reassign)
    return super().assign_child_resource(resource, location, reassign)

  def unassign_child_resource(self, resource: Resource):
    """Take ``resource`` off; only while the drawer is open."""
    self._require_drawer_open(f"remove {resource.name} from {self.name}")
    return super().unassign_child_resource(resource)


class VANTAstarDevice(Resource):
  """The VANTAstar as a PyLabRobot resource: its plate holder, its features (``drawer``,
  ``absorbance``, ``fluorescence``, ``luminescence``, ``shaker``, ``temperature_controller``), and the driver
  that runs it.

  Build one with the ``VANTAstar`` factory.
  """

  def __init__(
    self,
    name: str = "VANTAstar",
    *,
    device_id: Optional[str] = None,
    io: Optional[USB] = None,
    size_x: float = SIZE_X,
    size_y: float = SIZE_Y,
    size_z: float = SIZE_Z,
  ):
    """
    Args:
      name: the resource name.
      device_id: the USB serial number; None uses the first reader found.
      io: an already-built ``USB``, used instead of building one.
      size_x: width, in mm.
      size_y: depth, in mm.
      size_z: height, in mm.
    """
    super().__init__(
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      category="device",
      model="VANTAstar",
    )
    self.driver = VANTAstarDriver(device_id=device_id, io=io, name=name)
    self.plate_holder = _VANTAstarPlateHolder(f"{name}_plate_holder", self.driver.drawer)
    self.assign_child_resource(self.plate_holder, location=Coordinate.zero())
    self.driver.drawer.plate_holder = self.plate_holder

  def check_can_drop_resource_here(self, resource: Resource, *, reassign: bool = True) -> None:
    """Raise: a plate goes on the plate holder, not on the reader."""
    raise RuntimeError(
      f"cannot place {resource.name} on {self.name}: place it on {self.plate_holder.name}"
    )

  def assign_child_resource(
    self,
    resource: Resource,
    location: Optional[Coordinate],
    reassign: bool = True,
  ):
    """Assign the reader's own plate holder; anything else goes on the plate holder."""
    if resource is not self.plate_holder:
      self.check_can_drop_resource_here(resource, reassign=reassign)
    return super().assign_child_resource(resource, location, reassign)

  @property
  def drawer(self) -> Drawer:
    """The drawer."""
    return self.driver.drawer

  @property
  def absorbance(self) -> Absorbance:
    """Absorbance measurement."""
    return self.driver.absorbance

  @property
  def fluorescence(self) -> Fluorescence:
    """Top fluorescence intensity measurement, and the automatic gain and focus."""
    return self.driver.fluorescence

  @property
  def luminescence(self) -> Luminescence:
    """Top luminescence measurement; not tested on a reader with luminescence."""
    return self.driver.luminescence

  @property
  def shaker(self) -> Shaker:
    """The shaker."""
    return self.driver.shaker

  @property
  def temperature_controller(self) -> TemperatureController:
    """The temperature controller (the incubator)."""
    return self.driver.temperature_controller

  async def setup(self) -> None:
    """Connect and initialize the reader (about 10 s)."""
    await self.driver.setup()

  async def estimate_duration(self, read: Callable[[], Awaitable[Any]]) -> int:
    """The run time in s the reader expects for a read, without reading: ``read`` is called as
    usual, e.g. ``lambda: reader.absorbance.measure([450])``, and ends at the read's RUN with the
    reader's estimate (``VANTAstarDriver.estimate_duration``).  Shaking before the read and
    ``pause_before_reading`` are skipped and left out of the estimate; nothing moves.

    Raises:
      ValueError: if ``read`` ends without a read.
      RuntimeError: if ``read`` sends a command other than a read's (a drawer move, a gain and
        focus adjustment), or as the read raises it (the drawer is open, no plate).
    """
    return await self.driver.estimate_duration(read)

  async def stop(self, accept_plate_left_in_device: bool = False) -> None:
    """Stop a running measurement, close the drawer, and disconnect.

    Raises:
      RuntimeError: if a plate is still in the reader, unless ``accept_plate_left_in_device``; the
        drawer is then open for it to be taken out.
    """
    await self.driver.stop(accept_plate_left_in_device=accept_plate_left_in_device)


def VANTAstar(
  name: str = "VANTAstar",
  *,
  device_id: Optional[str] = None,
  io: Optional[USB] = None,
  size_x: float = SIZE_X,
  size_y: float = SIZE_Y,
  size_z: float = SIZE_Z,
) -> VANTAstarDevice:
  """Build a VANTAstar plate reader resource.

  Args:
    name: the resource name.
    device_id: the USB serial number; None uses the first reader found.
    io: an already-built ``USB``; used instead of building one.
    size_x: width, in mm.
    size_y: depth, in mm.
    size_z: height, in mm.
  """
  return VANTAstarDevice(
    name=name,
    device_id=device_id,
    io=io,
    size_x=size_x,
    size_y=size_y,
    size_z=size_z,
  )
