from typing import Tuple

from pylabrobot.centrifuge.centrifuge import Centrifuge, Loader
from pylabrobot.centrifuge.vspin_backend import Access2Backend, VSpinBackend
from pylabrobot.resources import Coordinate


def Access2(name: str, device_id: str, vspin: VSpinBackend) -> Tuple[Centrifuge, Loader]:
  centrifuge = Centrifuge(
    backend=vspin,
    size_x=0,  # TODO
    size_y=0,  # TODO
    size_z=0,  # TODO
    name=name + "_centrifuge",
  )
  # Use `python -m pylibftdi.examples.list_devices` to find the device id for each
  loader = Loader(
    name=name,
    size_x=0,  # TODO
    size_y=0,  # TODO
    size_z=0,  # TODO
    backend=Access2Backend(device_id=device_id),
    centrifuge=centrifuge,
    child_location=Coordinate.zero(),
  )
  return centrifuge, loader
