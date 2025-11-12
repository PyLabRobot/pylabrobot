from typing import List

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.resource import Resource

MIN_SPACING_BETWEEN_CHANNELS = 9
# minimum spacing between the edge of the container and the center of channel
MIN_SPACING_EDGE = 1


def _get_centers_with_margin(dim_size: float, n: int, margin: float, min_spacing: float):
  """Get the centers of the channels with a minimum margin on the edges."""
  if dim_size < margin * 2 + (n - 1) * min_spacing:
    raise ValueError("Resource is too small to space channels.")
  if dim_size - (n - 1) * min_spacing <= min_spacing * 2:
    remaining_space = dim_size - (n - 1) * min_spacing - margin * 2
    return [margin + remaining_space / 2 + i * min_spacing for i in range(n)]
  return [(i + 1) * dim_size / (n + 1) for i in range(n)]


def get_wide_single_resource_liquid_op_offsets(
  resource: Resource,
  num_channels: int,
) -> List[Coordinate]:
  resource_size = resource.get_absolute_size_y()
  centers = list(
    reversed(
      _get_centers_with_margin(
        dim_size=resource_size,
        n=num_channels,
        margin=MIN_SPACING_EDGE,
        min_spacing=MIN_SPACING_BETWEEN_CHANNELS,
      )
    )
  )  # reverse because channels are from back to front

  # offsets are relative to the center of the resource, but above we computed them wrt lfb
  # so we need to subtract the center of the resource
  # also, offsets are in absolute space, so we need to rotate the center
  return [
    Coordinate(
      x=0,
      y=c - resource.center().rotated(resource.get_absolute_rotation()).y,
      z=0,
    )
    for c in centers
  ]


def get_tight_single_resource_liquid_op_offsets(
  resource: Resource, num_channels: int
) -> List[Coordinate]:
  channel_space = (num_channels - 1) * MIN_SPACING_BETWEEN_CHANNELS

  min_y = (resource.get_absolute_size_y() - channel_space) / 2
  if min_y < MIN_SPACING_EDGE:
    raise ValueError("Resource is too small to space channels.")

  centers = [min_y + i * MIN_SPACING_BETWEEN_CHANNELS for i in range(num_channels)][::-1]

  # offsets are relative to the center of the resource, but above we computed them wrt lfb
  # so we need to subtract the center of the resource
  # also, offsets are in absolute space, so we need to rotate the center
  return [
    Coordinate(
      x=0,
      y=c - resource.center().rotated(resource.get_absolute_rotation()).y,
      z=0,
    )
    for c in centers
  ]
