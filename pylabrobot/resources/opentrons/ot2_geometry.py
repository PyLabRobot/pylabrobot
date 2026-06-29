import dataclasses
from typing import List, Tuple

from pylabrobot.resources.coordinate import Coordinate


@dataclasses.dataclass(frozen=True)
class OT2RobotGeometry:
  """Static geometry of an Opentrons OT-2, expressed in the robot frame.

  Every OT-2 Standard shares this geometry, so it is a constant rather than something probed from
  the device. This is the key difference from Hamilton STAR hardware, whose installed configuration
  varies per machine and must be read from firmware; the only OT-2 data worth discovering at runtime
  is which pipettes are mounted, not where the deck is.

  The robot frame has its origin at the front-left corner of slot 1, with +x to the right, +y to
  the back, and +z up. All distances are in mm.
  """

  # Gantry working area reachable by the reference (right) mount.
  extents: Coordinate = dataclasses.field(default_factory=lambda: Coordinate(446.75, 347.5, 0.0))

  # Partial-tip body clearance: how far a pipette's bounding box may sit beyond the deck extents at
  # each edge. These bound the pipette body in partial nozzle configurations only; they do not
  # constrain a fully-configured single or multi-channel pipette and are not used by the
  # single-channel reach check below.
  padding_front: float = 31.89
  padding_rear: float = -35.91
  padding_left: float = 0.0
  padding_right: float = 0.0

  # Nozzle offset of each mount from the gantry carriage; the right mount is the reference.
  left_mount_offset: Coordinate = dataclasses.field(
    default_factory=lambda: Coordinate(-34.0, 0.0, 0.0)
  )
  right_mount_offset: Coordinate = dataclasses.field(
    default_factory=lambda: Coordinate(0.0, 0.0, 0.0)
  )

  def mount_offset(self, mount: str) -> Coordinate:
    """Nozzle offset of ``mount`` ("left" or "right") from the gantry carriage."""
    if mount == "left":
      return self.left_mount_offset
    if mount == "right":
      return self.right_mount_offset
    raise ValueError(f"mount must be 'left' or 'right', got {mount!r}")

  def single_channel_reach(self, mount: str) -> Tuple[float, float, float, float]:
    """Reachable nozzle region for a fully-configured pipette on ``mount``.

    Returns ``(x_min, x_max, y_min, y_max)`` in the robot frame: the gantry working area
    (``extents``) translated by the mount offset. This mirrors how the OT-2 motion system bounds a
    fully-configured pipette - the deck extents per mount, running from the front-left corner to
    ``extents`` plus the mount offset. It is independent of ``padding_*``, which govern partial-tip
    nozzle configurations only. The mount offset has no y component, so both mounts share the same
    front/back reach and differ only in x.
    """
    off = self.mount_offset(mount)
    return (off.x, self.extents.x + off.x, 0.0, self.extents.y)

  @staticmethod
  def channel_y_offsets(num_channels: int = 8, channel_pitch: float = 9.0) -> List[float]:
    """Y offset of each channel from the head center, in mm, for a linear multi-channel head.

    Indexed back-to-front to match the Opentrons nozzle map: index 0 is the back-most nozzle (row
    A, the primary/critical point) at the largest +y, descending to the front-most nozzle at -y.
    Defaults describe the head8: 8 channels at 9 mm pitch spanning the head center by +-31.5 mm.
    """
    half = (num_channels - 1) / 2
    return [(half - i) * channel_pitch for i in range(num_channels)]

  def can_reach_position(
    self, mount: str, position: Coordinate, channel_offset: float = 0.0
  ) -> bool:
    """Whether a nozzle displaced ``channel_offset`` mm in y from the head center can reach
    ``position`` in x and y on ``mount``.

    Mirrors ``STARBackend.can_reach_position``: a pure predicate over the reachable region, here the
    single-channel deck extents for the mount. For a single channel use the default
    ``channel_offset=0``. For a head8, pass each channel's offset from :meth:`channel_y_offsets`; the
    center must land within the extents, so near the front/back limits only a subset of channels can
    reach, which is why an edge column is picked up partially rather than all 8. Z is not bounded by
    deck extents and is checked separately by the motion layer.
    """
    x_min, x_max, y_min, y_max = self.single_channel_reach(mount)
    center_y = position.y - channel_offset
    return x_min <= position.x <= x_max and y_min <= center_y <= y_max
