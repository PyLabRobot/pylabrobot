from typing import Optional, Dict

from pylabrobot.resources import Coordinate, Rotation
from pylabrobot.thermocycling.thermocycler import Thermocycler
from .odtc_backend import ODTCBackend


class ODTC(Thermocycler):
  """Inheco ODTC (On Deck Thermal Cycler)."""

  def __init__(
    self,
    name: str,
    ip: str,
    client_ip: Optional[str] = None,
    child_location: Coordinate = Coordinate(0, 0, 0),
    rotation: Optional[Rotation] = None
  ):
    """
    Initialize the Inheco ODTC.

    Args:
      name: The name of the resource.
      ip: The IP address of the ODTC.
      client_ip: The IP address of the client (this computer). If None, it will be automatically
                 determined.
      child_location: The location of the child resource (plate) relative to the ODTC.
      rotation: The rotation of the ODTC.
    """
    backend = ODTCBackend(ip=ip, client_ip=client_ip)
    super().__init__(
      name=name,
      size_x=159.0,  # Approximate dimensions, verify with spec
      size_y=245.0,
      size_z=228.0,
      backend=backend,
      child_location=child_location,
      category="thermocycler",
      model="ODTC",
    )
    if rotation is not None:
      self.rotation = rotation

  async def get_sensor_data(self) -> Dict[str, float]:
    """Get all sensor data from the device."""
    return await self.backend.get_sensor_data()  # type: ignore
