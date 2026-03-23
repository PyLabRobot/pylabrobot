from typing import Callable, Dict, List

from pylabrobot.legacy.liquid_handling.utils import MIN_SPACING_BETWEEN_CHANNELS
from pylabrobot.resources import Coordinate


def _default_min_spacing_between(channel1: int, channel2: int) -> float:
  """Default minimum spacing between two channels, based on their indices."""
  return MIN_SPACING_BETWEEN_CHANNELS * abs(channel1 - channel2)


def group_by_x_batch_by_xy(
  locations: List[Coordinate],
  use_channels: List[int],
  min_spacing_between_channels: Callable[[int, int], float] = _default_min_spacing_between,
) -> Dict[float, List[List[int]]]:
  if len(use_channels) == 0:
    raise ValueError("use_channels must not be empty.")
  if len(locations) == 0:
    raise ValueError("locations must not be empty.")
  if len(locations) != len(use_channels):
    raise ValueError("locations and use_channels must have the same length.")

  # Move channels to traverse height
  x_pos, y_pos = zip(*[(loc.x, loc.y) for loc in locations])

  # Start with a list of indices for each operation. The order is the order of operations as given in the input parameters.
  # We will then turn this list of indices into batches of indices that can be executed together, based on their X and Y positions and channel numbers.
  indices = list(range(len(locations)))

  # Sort indices by x position.
  indices = sorted(indices, key=lambda i: x_pos[i])

  # Group indices by x position (rounding to 0.1mm to avoid floating point splitting of same-position containers)
  # Note that since the indices were already sorted by x position, the groups will also be sorted by x position.
  x_groups: Dict[float, List[int]] = {}
  for i in indices:
    x_rounded = round(x_pos[i], 1)
    x_groups.setdefault(x_rounded, []).append(i)

  # Within each x group, sort channels from back (lowest channel index) to front (highest channel index)
  for x_group_indices in x_groups.values():
    x_group_indices.sort(key=lambda i: use_channels[i])

  # Within each x group, batch by y position while respecting minimum y spacing constraint
  y_batches: dict[float, List[List[int]]] = {}  # x position (group) -> list of batches of indices
  for x_group, x_group_indices in x_groups.items():
    y_batches_for_this_x: List[List[int]] = []  # batches of indices for this x group
    for i in x_group_indices:
      y = y_pos[i]

      # find the first batch that this index can be added to without violating the minimum y spacing constraint
      # if no batch is found, create a new batch with this index
      for batch in y_batches_for_this_x:
        index_min_y = min(batch, key=lambda i: y_pos[i])
        # check min spacing
        if y_pos[index_min_y] - y < min_spacing_between_channels(
          use_channels[i], use_channels[index_min_y]
        ):
          continue
        # check if channel is already used in this batch
        if use_channels[i] in [use_channels[j] for j in batch]:
          continue
        batch.append(i)
        break
      else:
        y_batches_for_this_x.append([i])

    y_batches[x_group] = y_batches_for_this_x

  return y_batches
