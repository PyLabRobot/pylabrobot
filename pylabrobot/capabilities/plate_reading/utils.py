from typing import List, Optional

from pylabrobot.resources.plate import Plate
from pylabrobot.resources.well import Well


def mask_wells(
  result: List[List[Optional[float]]], wells: List[Well], plate: Plate
) -> List[List[Optional[float]]]:
  """Return a copy of *result* with only the requested wells; others become ``None``."""
  masked: List[List[Optional[float]]] = [
    [None for _ in range(plate.num_items_x)] for _ in range(plate.num_items_y)
  ]
  for well in wells:
    r, c = well.get_row(), well.get_column()
    if r < plate.num_items_y and c < plate.num_items_x:
      masked[r][c] = result[r][c]
  return masked
