from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.tilting.tilter import Tilter
from pylabrobot.tilting.hamilton_backend import HamiltonTiltModuleBackend


class HamiltonTiltModule(Tilter):
  """ A Hamilton tilt module. """

  def __init__(
    self,
    name: str,
    com_port: str,
    child_resource_location: Coordinate = Coordinate(1.0, 3.0, 83.55),
    pedestal_size_z: float = 3.47,
    write_timeout: float = 3,
    timeout: float = 3,
  ):
    """ Initialize a Hamilton tilt module.

    Args:
      com_port: The communication port.
      child_resource_location: The location of the child resource.
      pedestal_size_z: The size of the pedestal in the z dimension.
      write_timeout: The write timeout. Defaults to 3.
      timeout: The timeout. Defaults to 3.
    """

    super().__init__(
      name=name,
      size_x=132,
      size_y=92.57,
      size_z=85.81,
      backend=HamiltonTiltModuleBackend(
        com_port=com_port,
        write_timeout=write_timeout,
        timeout=timeout),
      hinge_coordinate=Coordinate(6.18, 0, 72.85),
      child_resource_location=child_resource_location,
      category="tilter",
      model=HamiltonTiltModule.__name__,
    )

    self.pedestal_size_z = pedestal_size_z
