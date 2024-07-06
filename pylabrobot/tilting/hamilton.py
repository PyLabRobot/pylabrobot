from pylabrobot.resources.carrier import CarrierSite, create_homogeneous_carrier_sites
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.tilting.tilter import Tilter
from pylabrobot.tilting.hamilton_backend import HamiltonTiltModuleBackend

def HamiltonTiltModule(  # pylint: disable=invalid-name
  name: str,
  com_port: str,
  write_timeout: float = 3,
  timeout: float = 3,
) -> Tilter:
  return Tilter(
    name=name,
    backend=HamiltonTiltModuleBackend(com_port=com_port, write_timeout=write_timeout, timeout=timeout),
    size_x=132,
    size_y=92.57,
    size_z=85.81,
    sites=create_homogeneous_carrier_sites(
      klass=CarrierSite,
      locations=[Coordinate(1.0, 3.0, 83.55)],
      site_size_x=127.0,
      site_size_y=86.0,
    ),
    hinge_coordinate=Coordinate(6.18, 0, 72.85),
    category="tilter",
    model=HamiltonTiltModule.__name__,
  )
