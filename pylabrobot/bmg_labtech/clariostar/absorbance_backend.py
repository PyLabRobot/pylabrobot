import math
import struct
import sys
import time
from dataclasses import dataclass
from typing import List, Optional

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.plate_reading.absorbance import AbsorbanceBackend, AbsorbanceResult
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.well import Well
from pylabrobot.serializer import SerializableMixin
from pylabrobot.utils.list import reshape_2d

from .driver import CLARIOstarDriver

if sys.version_info >= (3, 8):
  from typing import Literal
else:
  from typing_extensions import Literal


@dataclass
class CLARIOstarAbsorbanceParams(BackendParams):
  """CLARIOstar-specific parameters for absorbance reads.

  Args:
    report: Report type. ``"OD"`` for optical density (absorbance) or
      ``"transmittance"`` for transmittance values. Default ``"OD"``.
  """

  report: Literal["OD", "transmittance"] = "OD"


class CLARIOstarAbsorbanceBackend(AbsorbanceBackend):
  """Translates AbsorbanceBackend interface into CLARIOstar driver commands."""

  def __init__(self, driver: CLARIOstarDriver):
    self.driver = driver

  # Keep the nested class for backward compat with the legacy wrapper that references
  # ``CLARIOstarBackend.AbsorbanceParams``.  The canonical name is now
  # ``CLARIOstarAbsorbanceParams`` (module-level).
  AbsorbanceParams = CLARIOstarAbsorbanceParams

  async def read_absorbance(
    self,
    plate: Plate,
    wells: List[Well],
    wavelength: int,
    backend_params: Optional[SerializableMixin] = None,
  ) -> List[AbsorbanceResult]:
    if not isinstance(backend_params, CLARIOstarAbsorbanceParams):
      backend_params = CLARIOstarAbsorbanceParams()

    if wells != plate.get_all_items():
      raise NotImplementedError("Only full plate reads are supported for now.")

    await self.driver.mp_and_focus_height_value()

    wavelength_data = int(wavelength * 10).to_bytes(2, byteorder="big")
    plate_bytes = self.driver.plate_bytes(plate)
    payload = (
      b"\x04" + plate_bytes + b"\x82\x02\x00\x00\x00\x00\x00\x00\x00\x20\x04\x00\x1e\x27\x0f\x27"
      b"\x0f\x19\x01" + wavelength_data + b"\x00\x00\x00\x64\x00\x00\x00\x00\x00\x00\x00\x64\x00"
      b"\x00\x00\x00\x00\x02\x00\x00\x00\x00\x01\x00\x00\x00\x01\x00\x16\x00\x01\x00\x00"
    )
    await self.driver.run_measurement(payload)
    await self.driver.read_order_values()
    await self.driver.status_hw()

    vals = await self.driver.request_measurement_values()
    num_wells = plate.num_items
    div = b"\x00" * 6
    start_idx = vals.index(div) + len(div)
    chromatic_data = vals[start_idx : start_idx + num_wells * 4]
    ref_data = vals[start_idx + num_wells * 4 : start_idx + (num_wells * 2) * 4]
    chromatic_bytes = [bytes(chromatic_data[i : i + 4]) for i in range(0, len(chromatic_data), 4)]
    ref_bytes = [bytes(ref_data[i : i + 4]) for i in range(0, len(ref_data), 4)]
    chromatic_reading = [struct.unpack(">i", x)[0] for x in chromatic_bytes]
    reference_reading = [struct.unpack(">i", x)[0] for x in ref_bytes]

    after_values_idx = start_idx + (num_wells * 2) * 4
    c100, c0, r100, r0 = struct.unpack(">iiii", vals[after_values_idx : after_values_idx + 4 * 4])

    real_chromatic_reading = [(cr - c0) / c100 for cr in chromatic_reading]
    real_reference_reading = [(rr - r0) / r100 for rr in reference_reading]

    transmittance: List[Optional[float]] = [
      rcr / rrr * 100 for rcr, rrr in zip(real_chromatic_reading, real_reference_reading)
    ]

    data: List[List[Optional[float]]]
    if backend_params.report == "OD":
      od: List[Optional[float]] = [
        math.log10(100 / t) if t is not None and t > 0 else None for t in transmittance
      ]
      data = reshape_2d(od, (plate.num_items_y, plate.num_items_x))
    elif backend_params.report == "transmittance":
      data = reshape_2d(transmittance, (plate.num_items_y, plate.num_items_x))
    else:
      raise ValueError(f"Invalid report type: {backend_params.report}")

    return [
      AbsorbanceResult(
        data=data,
        wavelength=wavelength,
        temperature=None,
        timestamp=time.time(),
      )
    ]
