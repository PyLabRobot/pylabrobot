"""BMG Labtech CLARIOstar plate reader."""

import os
from typing import Literal, Optional, Union

from pylabrobot.io.ftdi import FTDI
from pylabrobot.io.usb import USB
from pylabrobot.resources import Coordinate, PlateHolder, Resource

from .driver.features.absorbance import Absorbance
from .driver.features.drawer import Drawer
from .driver.features.fluorescence import Fluorescence
from .driver.features.luminescence import Luminescence
from .driver.features.temperature_controller import TemperatureController
from .driver.master import CLARIOstarDriver
from .driver.simulator import CLARIOstarSimulationDriver

# Preliminary: the nominal size, rounded to the cm, the same for the CLARIOstar
# and the CLARIOstar Plus; not yet measured.
SIZE_X = 450.0
SIZE_Y = 510.0
SIZE_Z = 400.0

# The recording a simulated CLARIOstar Plus takes its configuration from, by default.
RECORDING_CLARIOSTAR_PLUS = os.path.join(
  os.path.dirname(__file__),
  "driver",
  "recordings",
  "clariostar_plus_1_35_temperature_controller.json",
)

# The models a factory builds, as the reader reports them (INIT mainboard revision).
MODELS = ("CLARIOstar", "CLARIOstarPlus")


class _CLARIOstarPlateHolder(PlateHolder):
  """The drawer's plate holder: a plate goes on or comes off only while the drawer is sensed
  open, and only onto an empty holder."""

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
      model="clariostar_plate_holder",
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
    """Raise unless the drawer is open and the holder is empty or already holds `resource`."""
    self._require_drawer_open(f"place {resource.name} on {self.name}")
    super().check_can_drop_resource_here(resource, reassign=reassign)

  def assign_child_resource(
    self,
    resource: Resource,
    location: Optional[Coordinate] = None,
    reassign: bool = True,
  ):
    """Place `resource`, checked as a drop is (`check_can_drop_resource_here`)."""
    self.check_can_drop_resource_here(resource, reassign=reassign)
    return super().assign_child_resource(resource, location, reassign)

  def unassign_child_resource(self, resource: Resource):
    """Take `resource` off; only while the drawer is open."""
    self._require_drawer_open(f"remove {resource.name} from {self.name}")
    return super().unassign_child_resource(resource)


class CLARIOstarDevice(Resource):
  """The CLARIOstar as a resource: its plate holder, its drawer, and the driver that runs it.

  Build one with the `CLARIOstar` or `CLARIOstarPlus` factory. ``use_io`` picks how the reader is
  wired: ``"ftdi"`` (the default) for readers with the FTDI serial bridge, ``"usb"`` for readers
  with native USB. Commands are the same on both. A given ``io`` sets it by its type.
  """

  def __init__(
    self,
    name: str = "Generic CLARIOstar Device",
    *,
    use_io: Optional[Literal["ftdi", "usb"]] = None,
    device_id: Optional[str] = None,
    io: Optional[Union[FTDI, USB]] = None,
    size_x: float = SIZE_X,
    size_y: float = SIZE_Y,
    size_z: float = SIZE_Z,
    model: Optional[str] = None,
    declared_configuration_json: Optional[str] = None,
    simulation: bool = False,
  ):
    """
    Args:
      name: the resource name.
      use_io: ``"ftdi"`` or ``"usb"``; None takes it from ``io``, or ``"ftdi"``.
      device_id: the FTDI device id, or the USB serial number. None uses the first reader
        found.
      io: an already-built ``FTDI`` or ``USB``, used instead of building one.
      size_x: width, in mm.
      size_y: depth, in mm.
      size_z: height including the feet, in mm.
      model: which reader this is. Defaults to the class name, which says only that it is a
        CLARIOstar.
      declared_configuration_json: a file `save_configuration` wrote, passed to the driver; a
        simulated reader's configuration.
      simulation: build a simulated reader, with no link, from ``declared_configuration_json``.

    Raises:
      ValueError: if ``simulation`` is given without ``declared_configuration_json``, or with
        ``use_io``, ``device_id`` or ``io``.
    """
    super().__init__(
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      category="device",
      model=model if model is not None else self.__class__.__name__,
    )
    self.driver: CLARIOstarDriver
    if simulation:
      if declared_configuration_json is None:
        raise ValueError("a simulated reader needs declared_configuration_json")
      if use_io is not None or device_id is not None or io is not None:
        raise ValueError("a simulated reader has no link: no use_io, device_id or io")
      self.driver = CLARIOstarSimulationDriver(declared_configuration_json, name=name)
    else:
      self.driver = CLARIOstarDriver(
        use_io=use_io,
        device_id=device_id,
        io=io,
        name=name,
        declared_configuration_json=declared_configuration_json,
      )
    self.plate_holder = _CLARIOstarPlateHolder(f"{name}_plate_holder", self.driver.drawer)
    self.assign_child_resource(self.plate_holder, location=Coordinate.zero())
    # The drawer moves the plate holder once its in and out locations are set; neither is measured.
    self.driver.drawer.plate_holder = self.plate_holder

  def check_can_drop_resource_here(self, resource: Resource, *, reassign: bool = True) -> None:
    """Raise: a plate goes on the plate holder, not on the reader."""
    raise RuntimeError(
      f"cannot place {resource.name} on {self.name}: place it on {self.plate_holder.name}"
    )

  def assign_child_resource(
    self, resource: Resource, location: Optional[Coordinate], reassign: bool = True
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
  def temperature_controller(self) -> Optional[TemperatureController]:
    """The temperature controller, or None if the reader has none; known after `setup`."""
    return self.driver.temperature_controller

  @property
  def absorbance(self) -> Optional[Absorbance]:
    """Absorbance measurement, or None if the reader has none; known after `setup`."""
    return self.driver.absorbance

  @property
  def fluorescence(self) -> Optional[Fluorescence]:
    """Fluorescence measurement, or None if the reader has none; known after `setup`."""
    return self.driver.fluorescence

  @property
  def luminescence(self) -> Optional[Luminescence]:
    """Luminescence measurement, or None if the reader has none; known after `setup`."""
    return self.driver.luminescence

  async def setup(self) -> None:
    """Connect, initialize the reader (about 13 s), and check it is the model this device is.

    Raises:
      RuntimeError: if the reader reports another model than the factory built.
    """
    await self.driver.setup()
    reported = self.driver.configuration.model
    if self.model in MODELS and reported is not None and reported != self.model:
      raise RuntimeError(f"{self.name} was built as a {self.model}, the reader is a {reported}")

  async def stop(self, accept_plate_left_in_device: bool = False) -> None:
    """Stop a running measurement, switch heating off, close the drawer, and disconnect.

    Raises:
      RuntimeError: if a plate is still in the reader, unless `accept_plate_left_in_device`; the
        drawer is opened and the reader stays connected.
    """
    await self.driver.stop(accept_plate_left_in_device=accept_plate_left_in_device)


def CLARIOstar(
  name: str = "CLARIOstar",
  *,
  use_io: Optional[Literal["ftdi", "usb"]] = None,
  device_id: Optional[str] = None,
  io: Optional[Union[FTDI, USB]] = None,
  size_x: float = SIZE_X,
  size_y: float = SIZE_Y,
  size_z: float = SIZE_Z,
  declared_configuration_json: Optional[str] = None,
  simulation: bool = False,
) -> CLARIOstarDevice:
  """A CLARIOstar. No recording of one exists, so simulating one needs a declared configuration."""
  return CLARIOstarDevice(
    name=name,
    use_io=use_io,
    device_id=device_id,
    io=io,
    size_x=size_x,
    size_y=size_y,
    size_z=size_z,
    model=CLARIOstar.__name__,
    declared_configuration_json=declared_configuration_json,
    simulation=simulation,
  )


def CLARIOstarPlus(
  name: str = "CLARIOstarPlus",
  *,
  use_io: Optional[Literal["ftdi", "usb"]] = None,
  device_id: Optional[str] = None,
  io: Optional[Union[FTDI, USB]] = None,
  size_x: float = SIZE_X,
  size_y: float = SIZE_Y,
  size_z: float = SIZE_Z,
  declared_configuration_json: Optional[str] = None,
  simulation: bool = False,
) -> CLARIOstarDevice:
  """A CLARIOstar Plus. Simulated, it defaults to ``RECORDING_CLARIOSTAR_PLUS``."""
  if simulation and declared_configuration_json is None:
    declared_configuration_json = RECORDING_CLARIOSTAR_PLUS
  return CLARIOstarDevice(
    name=name,
    use_io=use_io,
    device_id=device_id,
    io=io,
    size_x=size_x,
    size_y=size_y,
    size_z=size_z,
    model=CLARIOstarPlus.__name__,
    declared_configuration_json=declared_configuration_json,
    simulation=simulation,
  )
