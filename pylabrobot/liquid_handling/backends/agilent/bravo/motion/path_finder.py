"""Safe Z calculation and obstacle avoidance.

Determines the minimum Z retraction needed to move the head safely between
deck locations without colliding with labware or other obstacles.
"""

from __future__ import annotations

from pylabrobot.liquid_handling.backends.agilent.bravo.types import (
    Axis,
    Z_CLEARANCE,
    Z_CLEARANCE_NOT_PICKANDPLACE,
    COLLISION_BUFFER,
    MAX_LOCATIONS,
    MIN_LOCATION,
    location_to_row_col,
)


class PathFinder:
    """Computes safe Z positions for head travel across the deck.

    Parameters:
        teachpoints: Mapping of location -> axis -> position (mm).
        labware_map: Mapping of location -> total labware stack height (mm).
        head_height: Height of the pipette head assembly (mm).
    """

    def __init__(
        self,
        teachpoints: dict[int, dict[Axis, float]],
        labware_map: dict[int, float],
        head_height: float,
    ) -> None:
        self.teachpoints = teachpoints
        self.labware_map = labware_map
        self.head_height = head_height

    def get_tallest_obstacle(self, current_location: int) -> float:
        """Return the height of the tallest obstacle on the deck.

        Iterates all 9 deck locations and computes each obstacle's effective
        height relative to the head's current Z reference.
        """
        tallest = 0.0
        for loc in range(MIN_LOCATION, MAX_LOCATIONS + 1):
            if loc == current_location:
                continue
            obstacle_height = self.labware_map.get(loc, 0.0)
            if obstacle_height > tallest:
                tallest = obstacle_height
        return tallest

    def get_safe_z_position(
        self,
        from_loc: int,
        to_loc: int,
        labware_heights: dict[int, float] | None = None,
    ) -> float:
        """Return the minimum Z position (most retracted) to clear obstacles.

        The safe Z is computed so the head clears the tallest labware on any
        location between *from_loc* and *to_loc* (inclusive of the bounding
        rectangle), plus appropriate clearance buffers.

        Parameters:
            from_loc: Starting deck location (1-9).
            to_loc:   Destination deck location (1-9).
            labware_heights: Optional override for labware heights; defaults
                to ``self.labware_map``.
        """
        heights = labware_heights if labware_heights is not None else self.labware_map

        from_row, from_col = location_to_row_col(from_loc)
        to_row, to_col = location_to_row_col(to_loc)

        min_row = min(from_row, to_row)
        max_row = max(from_row, to_row)
        min_col = min(from_col, to_col)
        max_col = max(from_col, to_col)

        tallest = 0.0
        is_adjacent = abs(from_row - to_row) <= 1 and abs(from_col - to_col) <= 1

        for row in range(min_row, max_row + 1):
            for col in range(min_col, max_col + 1):
                loc = row * 3 + col + 1
                obstacle = heights.get(loc, 0.0)
                if obstacle > tallest:
                    tallest = obstacle

        clearance = Z_CLEARANCE_NOT_PICKANDPLACE if is_adjacent else Z_CLEARANCE
        safe_z = tallest + clearance + COLLISION_BUFFER + self.head_height

        return safe_z
