"""Where a volume of liquid stands in a container, from the container's own model."""

from pylabrobot.resources import Container


def get_liquid_height_from_volume(container: Container, volume: float) -> float:
  """How high `volume` stands above the container's cavity bottom, in mm, by its own model.

  A container that knows only volume from height is inverted by bisection over its depth.

  Args:
    container: the container holding the liquid.
    volume: in uL.

  Returns:
    The height in mm, 0.0 for nothing.

  Raises:
    RuntimeError: If the container has no height-volume functions.
  """
  if volume <= 0:
    return 0.0
  try:
    return round(container.compute_height_from_volume(volume), 2)
  except NotImplementedError:
    pass
  try:
    container.compute_volume_from_height(0.0)
  except NotImplementedError:
    raise RuntimeError(
      f"where {volume} uL stands in {container.name} is not known: the container has no "
      "height-volume functions. Generate a height_volume_data dictionary for it and consider "
      "contributing it back to PyLabRobot :)"
    ) from None
  low, high = 0.0, container.get_size_z()
  for _ in range(40):
    mid = (low + high) / 2
    low, high = (mid, high) if container.compute_volume_from_height(mid) < volume else (low, mid)
  return round(low, 2)
