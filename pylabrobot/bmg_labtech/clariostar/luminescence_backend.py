import struct
import time
from typing import List, Optional

from pylabrobot.capabilities.plate_reading.luminescence import (
  LuminescenceBackend,
  LuminescenceResult,
)
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.well import Well
from pylabrobot.serializer import SerializableMixin
from pylabrobot.utils.list import reshape_2d

from .driver import CLARIOstarDriver


class CLARIOstarLuminescenceBackend(LuminescenceBackend):
  """Translates LuminescenceBackend interface into CLARIOstar driver commands."""

  def __init__(self, driver: CLARIOstarDriver):
    self.driver = driver

  async def read_luminescence(
    self,
    plate: Plate,
    wells: List[Well],
    focal_height: float = 13,
    backend_params: Optional[SerializableMixin] = None,
  ) -> List[LuminescenceResult]:
    if wells != plate.get_all_items():
      raise NotImplementedError("Only full plate reads are supported for now.")

    await self.driver.mp_and_focus_height_value()

    assert 0 <= focal_height <= 25, "focal height must be between 0 and 25 mm"
    focal_height_data = int(focal_height * 100).to_bytes(2, byteorder="big")
    plate_bytes = self.driver.plate_bytes(plate)
    payload = (
      b"\x04" + plate_bytes + b"\x02\x01\x00\x00\x00\x00\x00\x00\x00\x20\x04\x00\x1e\x27"
      b"\x0f\x27\x0f\x01" + focal_height_data + b"\x00\x00\x01\x00\x00\x0e\x10\x00\x01\x00\x01"
      b"\x00\x01\x00\x01\x00\x01\x00\x06\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02\x00\x00\x00"
      b"\x00\x01\x00\x00\x00\x01\x00\x64\x00\x20\x00\x00"
    )
    await self.driver.run_measurement(payload)
    await self.driver.read_order_values()
    await self.driver.status_hw()

    vals = await self.driver.request_measurement_values()
    num_wells = plate.num_items
    start_idx = vals.index(b"\x00\x00\x00\x00\x00\x00") + len(b"\x00\x00\x00\x00\x00\x00")
    data = list(vals)[start_idx : start_idx + num_wells * 4]
    int_bytes = [data[i : i + 4] for i in range(0, len(data), 4)]
    ints = [struct.unpack(">i", bytes(int_data))[0] for int_data in int_bytes]
    floats: List[List[Optional[float]]] = reshape_2d(
      [float(i) for i in ints], (plate.num_items_y, plate.num_items_x)
    )

    return [
      LuminescenceResult(
        data=floats,
        temperature=None,
        timestamp=time.time(),
      )
    ]
