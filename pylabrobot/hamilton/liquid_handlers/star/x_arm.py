"""STARXArm: X-arm positioning control for Hamilton STAR liquid handlers."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Literal, Optional

if TYPE_CHECKING:
  from .driver import STARDriver

logger = logging.getLogger(__name__)


class STARXArm:
  """Controls one X-arm (left or right) on a Hamilton STAR.

  This is a plain helper class (not a CapabilityBackend). It encapsulates the
  firmware protocol for X-arm positioning and delegates I/O to the driver.

  Args:
    driver: The STARDriver instance to send commands through.
    side: Which X-arm to control — ``"left"`` or ``"right"``.
  """

  def __init__(self, driver: "STARDriver", side: Literal["left", "right"]):
    self.driver = driver
    self._side = side

  # -- lifecycle -------------------------------------------------------------

  async def _on_setup(self, backend_params=None):
    pass

  async def _on_stop(self):
    pass

  # -- positioning (collision risk) ------------------------------------------

  async def move_to(self, x_position: float = 0.0):
    """Position X-arm (C0:JX for left, C0:JS for right).

    Collision risk! This moves the arm without raising components to Z-safety.

    Args:
      x_position: X-position in mm. Must be between 0 and 3000. Default 0.
    """

    if not 0 <= x_position <= 3000.0:
      raise ValueError("x_position must be between 0 and 3000 mm")

    if (
      self._side == "left"
      and self.driver.left_side_panel_installed
      and x_position < self.driver.PIP_X_MIN_WITH_LEFT_SIDE_PANEL
    ):
      raise ValueError(
        f"PIP channel x={x_position}mm is below the minimum "
        f"{self.driver.PIP_X_MIN_WITH_LEFT_SIDE_PANEL}mm (left side panel is installed)"
      )

    cmd = "JX" if self._side == "left" else "JS"
    return await self.driver.send_command(
      module="C0",
      command=cmd,
      xs=f"{round(x_position * 10):05}",
    )

  # -- safe positioning (Z-safety) -------------------------------------------

  async def move_to_safe(self, x_position: float = 0.0):
    """Move X-arm to position with all attached components in Z-safety position
    (C0:KX for left, C0:KR for right).

    Args:
      x_position: X-position in mm. Must be between 0 and 3000. Default 0.
    """

    if not 0 <= x_position <= 3000.0:
      raise ValueError("x_position must be between 0 and 3000 mm")

    if (
      self._side == "left"
      and self.driver.left_side_panel_installed
      and x_position < self.driver.PIP_X_MIN_WITH_LEFT_SIDE_PANEL
    ):
      raise ValueError(
        f"PIP channel x={x_position}mm is below the minimum "
        f"{self.driver.PIP_X_MIN_WITH_LEFT_SIDE_PANEL}mm (left side panel is installed)"
      )

    cmd = "KX" if self._side == "left" else "KR"
    return await self.driver.send_command(
      module="C0",
      command=cmd,
      xs=round(x_position * 10),
    )

  # -- position query --------------------------------------------------------

  async def request_position(self) -> float:
    """Request current X-arm position (C0:RX for left, C0:QX for right).

    Returns:
      X-position in mm (firmware value divided by 10).
    """

    cmd = "RX" if self._side == "left" else "QX"
    resp = await self.driver.send_command(module="C0", command=cmd, fmt="rx#####")
    return float(resp["rx"]) / 10

  # -- collision type query --------------------------------------------------

  async def last_collision_type(self) -> bool:
    """Request last collision type after error 27 (C0:XX for left, C0:XR for right).

    Returns:
      False if present positions collide (not reachable),
      True if position is never reachable.
    """

    cmd = "XX" if self._side == "left" else "XR"
    resp = await self.driver.send_command(module="C0", command=cmd, fmt="xq#")
    return bool(resp["xq"] == 1)

  # -- cLLD X-probing ----------------------------------------------------------

  async def clld_probe_x_position(
    self,
    channel_idx: int,
    probing_direction: Literal["right", "left"],
    end_pos_search: Optional[float] = None,
    post_detection_dist: float = 2.0,
    tip_bottom_diameter: float = 1.2,
    read_timeout: float = 240.0,
  ) -> float:
    """Probe the x-position of a conductive material using cLLD via a lateral X scan.

    Starting from the current X position, the arm is moved laterally in the specified
    direction using the XL command until cLLD triggers or the end position is reached.
    After the scan, the arm is retracted by ``post_detection_dist``.

    The returned value is a geometric estimate of the material boundary, corrected by
    half the tip bottom diameter assuming cylindrical tip contact.

    Preconditions:
      - A channel must already be at a Z height safe for lateral X motion.
      - The current X position must be consistent with ``probing_direction``.

    Args:
      channel_idx: 0-indexed channel performing the probe.
      probing_direction: ``"right"`` or ``"left"``.
      end_pos_search: End position in mm. Defaults to max safe range for the direction.
      post_detection_dist: Distance to retract after detection in mm.
      tip_bottom_diameter: Effective diameter of the tip bottom in mm.
      read_timeout: Timeout in seconds for the XL command.

    Returns:
      Estimated x-position of the detected material boundary in mm.
    """
    if probing_direction not in ("right", "left"):
      raise ValueError(f"probing_direction must be 'right' or 'left', got {probing_direction!r}")
    if post_detection_dist < 0.0:
      raise ValueError(f"post_detection_dist must be non-negative, got {post_detection_dist}")

    current_x = await self.request_position()

    assert self.driver.extended_conf is not None
    num_rails = self.driver.extended_conf.instrument_size_slots
    track_width = 22.5  # mm
    reachable_dist_to_last_rail = 125.0
    max_safe_upper = num_rails * track_width + reachable_dist_to_last_rail
    max_safe_lower = 95.0  # mm

    if end_pos_search is None:
      end_pos_search = max_safe_upper if probing_direction == "right" else max_safe_lower
    elif not (max_safe_lower <= end_pos_search <= max_safe_upper):
      raise ValueError(
        f"end_pos_search must be between {max_safe_lower} and {max_safe_upper} mm, "
        f"got {end_pos_search}"
      )

    if probing_direction == "right" and current_x >= end_pos_search:
      raise ValueError(
        f"Current position ({current_x} mm) must be < end position ({end_pos_search} mm) "
        "when probing right."
      )
    if probing_direction == "left" and current_x <= end_pos_search:
      raise ValueError(
        f"Current position ({current_x} mm) must be > end position ({end_pos_search} mm) "
        "when probing left."
      )

    # C0:XL — move arm in X until cLLD triggers on the specified channel
    # Note: pn (channel index) is NOT sent here. The firmware uses whichever
    # channel is at the correct Z height for cLLD sensing. The channel_idx is
    # used only to query the post-probe position.
    await self.driver.send_command(
      module="C0",
      command="XL",
      xs=f"{int(round(end_pos_search * 10)):05}",
      read_timeout=int(read_timeout),
    )

    sensor_triggered_x = await self.request_position()

    if probing_direction == "left":
      final_x = sensor_triggered_x + post_detection_dist
      material_x = sensor_triggered_x - tip_bottom_diameter / 2
    else:
      final_x = sensor_triggered_x - post_detection_dist
      material_x = sensor_triggered_x + tip_bottom_diameter / 2

    await self.move_to(final_x)

    return round(material_x, 1)
