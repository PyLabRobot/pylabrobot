"""Formulatrix Mantis liquid dispenser as a PyLabRobot resource."""

import os
from typing import Optional

from pylabrobot.io.ftdi import FTDI
from pylabrobot.resources import Coordinate, Deck, Resource
from pylabrobot.resources.resource_holder import ResourceHolder

from .driver.configuration import RECORDING_MANTIS_V4
from .driver.features.plate_clamp import PlateClamp
from .driver.features.rfid import Rfid
from .driver.master import MantisDriver
from .driver.simulator import MantisSimulationDriver
from .resource_model import MANIFEST, chip_rack

# The main box's size, in mm, from the manufacturer's model: up to the top plate's surface.
SIZE_X, SIZE_Y, SIZE_Z = MANIFEST["size"]
# The plate holder on the deck: an SBS footprint pushed into the back-left of the pin nest, in mm.
PLATE_HOLDER_LOCATION = Coordinate(23.62, 15.02, 0.0)
PLATE_HOLDER_SIZE_X, PLATE_HOLDER_SIZE_Y = 127.76, 85.48


class MantisDevice(Resource):
  """A Formulatrix Mantis, v3 or v4: its main box, the parts on it and the driver that runs it.

  The main box carries the deck with its plate holder and the two chip racks. The generation is
  read at setup (``driver.configuration.generation``).
  """

  def __init__(
    self,
    name: str = "Mantis",
    *,
    device_id: Optional[str] = None,
    io: Optional[FTDI] = None,
    rfid_io: Optional[FTDI] = None,
    size_x: float = SIZE_X,
    size_y: float = SIZE_Y,
    size_z: float = SIZE_Z,
    declared_configuration_json: Optional[str] = None,
    oem_config_directory: Optional[str] = None,
    ftdi_library: Optional[str] = None,
    simulation: bool = False,
  ):
    """
    Args:
      name: the resource name.
      device_id: the FTDI serial number. None uses the first Mantis found.
      io: an already-built ``FTDI`` for interface B, used instead of building one.
      rfid_io: an already-built ``FTDI`` for interface A, the RFID reader.
      size_x: width, in mm.
      size_y: depth, in mm.
      size_z: height, in mm.
      declared_configuration_json: a file ``save_configuration`` wrote; setup refuses a device that
        disagrees on what is fitted. Simulated, the configuration; None uses
        ``RECORDING_MANTIS_V4``.
      oem_config_directory: the unit's vendor ``Data/Device/Configs`` folder; where it and the
        declared file both hold a unit value, they must agree.
      ftdi_library: "libftdi" or "d2xx"; None picks D2XX on Windows when FTDI's D2XX library
        loads, else libftdi.
      simulation: build a simulated Mantis, with no link.

    Raises:
      ValueError: if ``simulation`` is given with ``device_id``, ``io``, ``rfid_io`` or
      ``ftdi_library``.
    """
    super().__init__(
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      category="device",
      model="Mantis",
    )
    self.driver: MantisDriver
    if simulation:
      if any(link is not None for link in (device_id, io, rfid_io, ftdi_library)):
        raise ValueError(
          "a simulated Mantis has no link: no device_id, io, rfid_io or ftdi_library"
        )
      self.driver = MantisSimulationDriver(
        declared_configuration_json=declared_configuration_json or RECORDING_MANTIS_V4,
        name=name,
        oem_config_directory=oem_config_directory,
      )
    else:
      self.driver = MantisDriver(
        device_id=device_id,
        io=io,
        rfid_io=rfid_io,
        name=name,
        declared_configuration_json=declared_configuration_json,
        oem_config_directory=oem_config_directory,
        ftdi_library=ftdi_library,
      )
    self.mesh = {
      "path": os.path.join(os.path.dirname(__file__), "resource_model", MANIFEST["base"]),
      "units": "mm",
      "up": "Z",
    }
    measured_at = {
      part: Coordinate(*module["measured_at"]) for part, module in MANIFEST["modules"].items()
    }

    self.deck = Deck(name=f"{name}_deck", size_x=SIZE_X, size_y=SIZE_Y, size_z=0.0)
    self.assign_child_resource(self.deck, location=Coordinate(0.0, 0.0, SIZE_Z))
    self.plate_holder = ResourceHolder(
      name=f"{name}_plate_holder",
      size_x=PLATE_HOLDER_SIZE_X,
      size_y=PLATE_HOLDER_SIZE_Y,
      size_z=0.0,
      model="mantis_plate_holder",
    )
    # The viewer draws the holder in its colour: a step darker than the deck, so the nest shows.
    self.plate_holder.appearance = {"color": 0x8C9198}  # type: ignore[attr-defined]
    self.deck.assign_child_resource(self.plate_holder, location=PLATE_HOLDER_LOCATION)

    # Both chip racks, always: whether a side carries a carousel instead is not read from the
    # device; cb declares it (chip_layout), and carousels are not supported yet.
    self.left_chip_rack = chip_rack(name=f"{name}_left_chip_rack", side="left")
    self.assign_child_resource(self.left_chip_rack, location=measured_at["left_chip_rack"])
    self.right_chip_rack = chip_rack(name=f"{name}_right_chip_rack", side="right")
    self.assign_child_resource(self.right_chip_rack, location=measured_at["right_chip_rack"])

  @property
  def plate_clamp(self) -> Optional[PlateClamp]:
    """The plate clamp, once setup found the LC3 board; None before or without it."""
    return self.driver.plate_clamp

  @property
  def rfid(self) -> Optional[Rfid]:
    """The RFID reader, once setup found it on interface A; None before or without it."""
    return self.driver.rfid

  async def setup(self) -> None:
    """Connect, read the device's configuration and check it. Nothing moves."""
    await self.driver.setup()

  async def stop(self) -> None:
    """Disconnect."""
    await self.driver.stop()


def Mantis(
  name: str = "Mantis",
  *,
  device_id: Optional[str] = None,
  io: Optional[FTDI] = None,
  rfid_io: Optional[FTDI] = None,
  size_x: float = SIZE_X,
  size_y: float = SIZE_Y,
  size_z: float = SIZE_Z,
  declared_configuration_json: Optional[str] = None,
  oem_config_directory: Optional[str] = None,
  ftdi_library: Optional[str] = None,
  simulation: bool = False,
) -> MantisDevice:
  """A Formulatrix Mantis, v3 or v4.

  Args:
    name: the resource name.
    device_id: the FTDI serial number. None uses the first Mantis found.
    io: an already-built ``FTDI`` for interface B, used instead of building one.
    rfid_io: an already-built ``FTDI`` for interface A, the RFID reader.
    size_x: width, in mm.
    size_y: depth, in mm.
    size_z: height, in mm.
    declared_configuration_json: a file ``save_configuration`` wrote; setup refuses a device that
      disagrees on what is fitted. Simulated, the configuration; None uses ``RECORDING_MANTIS_V4``.
    oem_config_directory: the unit's vendor ``Data/Device/Configs`` folder; where it and the
      declared file both hold a unit value, they must agree.
    ftdi_library: "libftdi" or "d2xx"; None picks D2XX on Windows when FTDI's D2XX library loads,
      else libftdi.
    simulation: build a simulated Mantis, with no link.

  Raises:
    ValueError: if ``simulation`` is given with ``device_id``, ``io``, ``rfid_io`` or
      ``ftdi_library``.
  """
  return MantisDevice(
    name=name,
    device_id=device_id,
    io=io,
    rfid_io=rfid_io,
    size_x=size_x,
    size_y=size_y,
    size_z=size_z,
    declared_configuration_json=declared_configuration_json,
    oem_config_directory=oem_config_directory,
    ftdi_library=ftdi_library,
    simulation=simulation,
  )
