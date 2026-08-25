from pylabrobot.resources import Coordinate, PlateCarrier, PlateHolder


_STACKER_SIZE_X = 112.3
_STACKER_SIZE_Y = 146.6
_SITE_SIZE_X = 85.48
_SITE_SIZE_Y = 127.76


def high_res_stacker(
  name: str,
  *,
  zero_offset: float,
  slot_height: float,
  slot_count: int,
) -> PlateCarrier:
  """Create a HighRes sample-store stacker from its configured dimensions.

  The values correspond to one line returned by the device's
  ``getstackerdimensions`` command. Carrier spots are zero-based, while site
  names use the device's one-based slot numbers.

  Args:
    name: Resource name for the stacker.
    zero_offset: Vertical position of the first slot.
    slot_height: Height and vertical pitch of each slot.
    slot_count: Number of slots in the stacker.

  Returns:
    A plate carrier containing one holder for each stacker slot.
  """
  if zero_offset < 0:
    raise ValueError(f"zero_offset must be non-negative; got {zero_offset}.")
  if slot_height <= 0:
    raise ValueError(f"slot_height must be positive; got {slot_height}.")
  if slot_count < 0:
    raise ValueError(f"slot_count must be non-negative; got {slot_count}.")

  return PlateCarrier(
    name=name,
    size_x=_STACKER_SIZE_X,
    size_y=_STACKER_SIZE_Y,
    size_z=zero_offset + slot_height * slot_count,
    sites={
      spot: PlateHolder(
        name=f"{name}_slot_{spot + 1}",
        size_x=_SITE_SIZE_X,
        size_y=_SITE_SIZE_Y,
        size_z=slot_height,
        pedestal_size_z=0,
      ).at(
        Coordinate(
          x=(_STACKER_SIZE_X - _SITE_SIZE_X) / 2,
          y=(_STACKER_SIZE_Y - _SITE_SIZE_Y) / 2,
          z=zero_offset + slot_height * spot,
        )
      )
      for spot in range(slot_count)
    },
    model="high_res_stacker",
    metadata={
      "zero_offset": zero_offset,
      "slot_height": slot_height,
      "slot_count": slot_count,
    },
  )
