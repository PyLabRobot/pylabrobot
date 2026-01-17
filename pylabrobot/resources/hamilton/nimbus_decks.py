"""Nimbus deck class and utilities for Hamilton Nimbus instruments.

This module provides the NimbusDeck class and factory function for creating
Nimbus deck instances with either explicit parameters or by parsing config files.
"""

from __future__ import annotations

import logging
import re
import warnings
from typing import Any, Dict, List, Literal, Optional

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.hamilton.hamilton_decks import HamiltonDeck
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.trash import Trash
from pylabrobot.serializer import serialize

logger = logging.getLogger("pylabrobot")


class NimbusDeck(HamiltonDeck):
  """Hamilton Nimbus deck.

  Supports track-based positioning (called "rails" in the API for consistency
  with other Hamilton decks). The deck is defined in PyLabRobot coordinates,
  but can convert to/from Hamilton coordinates when interfacing with hardware.
  """

  def __init__(
    self,
    num_rails: int = 30,
    size_x: float = 831.85,
    size_y: float = 424.18,
    size_z: float = 300.0,
    hamilton_origin: Coordinate = Coordinate(x=-151.51, y=-363.83, z=0.0),
    y_min: float = -310.0,
    y_max: float = 20.0,
    z_max: float = 146.0,
    rail_start_x: float = -125.7,
    rail_width: float = 22.454,
    rail_y: float = -360.487,
    name: str = "deck",
    category: str = "deck",
    origin: Coordinate = Coordinate.zero(),
    waste_type: Optional[Literal["default_long"]] = "default_long",
  ) -> None:
    """Create a new Nimbus deck.

    Default values are from Nimbus8.dck layout 8 and Nimbus8.cfg.

    Args:
        num_rails: Number of rails (maps to hardware tracks, default: 30)
        size_x: Deck size in X dimension (mm, default: 831.85)
        size_y: Deck size in Y dimension (mm, default: 424.18)
        size_z: Deck size in Z dimension (mm, default: 300.0)
        hamilton_origin: Hamilton origin coordinate for coordinate conversion
            (default: Coordinate(x=-151.51, y=-363.83, z=0.0))
        y_min: Hamilton Y minimum coordinate bound (mm, default: -310.0)
        y_max: Hamilton Y maximum coordinate bound (mm, default: 20.0)
        z_max: Maximum Z height (mm, default: 146.0)
        rail_start_x: Hamilton X coordinate of first rail start (mm, default: -125.7)
        rail_width: Width between rails (mm, default: 22.454)
        rail_y: Hamilton Y coordinate of all rails (mm, default: -360.487)
        name: Deck name (default: "deck")
        category: Deck category (default: "deck")
        origin: PyLabRobot origin coordinate (default: Coordinate.zero())
        waste_type: Waste configuration type (default: "default_long"). If "default_long",
            creates a waste block with 8 channel positions. If None, no waste is created.
    """
    super().__init__(
      num_rails=num_rails,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      name=name,
      category=category,
      origin=origin,
    )

    # Store Hamilton origin for coordinate conversion
    self._hamilton_origin = hamilton_origin

    # Store coordinate bounds for validation
    self._y_min = y_min
    self._y_max = y_max
    self._z_max = z_max

    # Store rail/track parameters for rails_to_location()
    self._rail_start_x = rail_start_x
    self._rail_width = rail_width
    self._rail_y = rail_y

    # Store waste type for waste position lookup
    self.waste_type = waste_type

    # Create waste resources if specified
    if waste_type == "default_long":
      self._create_default_long_waste()

  def _create_default_long_waste(self) -> None:
    """Create default_long waste block with 8 channel positions.

    Creates a waste block resource (ChannelLongWasteBlock) and 8 Container
    resources for waste positions (DefaultLongWaste_0001). All coordinates
    are converted from Hamilton to PyLabRobot coordinate system.
    """
    # Waste block dimensions and location
    # ChannelLongWasteBlock: X=546.225, Y=-313.64, Z=0, dX=19.05, dY=366.25, dZ=160
    waste_block_location_hamilton = Coordinate(x=546.225, y=-313.64, z=0.0)

    # Convert waste block location to PyLabRobot coordinates
    waste_block_location_plr = self.from_hamilton_coordinate(waste_block_location_hamilton)

    # Create waste block resource
    waste_block = Resource(
      name="default_long_block",
      size_x=19.05,
      size_y=366.25,
      size_z=160.0,
      category="waste_block",
    )

    # Assign waste block to deck
    self.assign_child_resource(waste_block, location=waste_block_location_plr)

    # Waste positions (from CSV sequences)
    # DefaultLongWaste_0001 positions 1-8
    waste_positions_hamilton = [
      Coordinate(x=553.746, y=19.863, z=131.389),  # Position 1
      Coordinate(x=553.746, y=1.880, z=131.389),  # Position 2
      Coordinate(x=553.746, y=-76.149, z=131.389),  # Position 3
      Coordinate(x=553.746, y=-94.132, z=131.389),  # Position 4
      Coordinate(x=553.746, y=-152.349, z=131.389),  # Position 5
      Coordinate(x=553.746, y=-170.332, z=131.389),  # Position 6
      Coordinate(x=553.746, y=-219.549, z=131.389),  # Position 7
      Coordinate(x=553.746, y=-237.532, z=131.389),  # Position 8
    ]

    # Create container resources for each waste position
    for i, pos_hamilton in enumerate(waste_positions_hamilton, start=1):
      # Convert position to PyLabRobot coordinates (relative to deck origin)
      pos_plr = self.from_hamilton_coordinate(pos_hamilton)

      # Position relative to waste block location
      # Both waste_block_location_plr and pos_plr are relative to deck origin,
      # so subtract to get position relative to waste block
      pos_plr_rel = Coordinate(
        x=pos_plr.x - waste_block_location_plr.x,
        y=pos_plr.y - waste_block_location_plr.y,
        z=pos_plr.z - waste_block_location_plr.z,
      )

      # Create Trash resource for this position (compatible with LiquidHandler.drop_tips)
      waste_position = Trash(
        name=f"default_long_{i}",
        size_x=0.0,
        size_y=0.0,
        size_z=0.0,
        category="waste_position",
      )

      # Assign waste position to waste block
      waste_block.assign_child_resource(waste_position, location=pos_plr_rel)

  def rails_to_location(self, rails: int) -> Coordinate:
    """Convert a rail identifier to an absolute (x, y, z) coordinate.

    Converts rail number (1-30) to PyLabRobot coordinates. Internally maps
    hardware tracks to API rails for consistency with other Hamilton decks.
    Uses instance attributes for rail positions, which can be set from config files.

    Args:
        rails: Rail number (1-30, maps to hardware tracks)

    Returns:
        PyLabRobot coordinate relative to deck origin
    """
    # Calculate X position in Hamilton coordinates using instance attributes
    x_hamilton = self._rail_start_x + (rails - 1) * self._rail_width
    y_hamilton = self._rail_y
    z_hamilton = 0.0

    # Convert to PyLabRobot coordinates (absolute, relative to PLR world origin)
    rail_coord_hamilton = Coordinate(x=x_hamilton, y=y_hamilton, z=z_hamilton)

    # X and Z remain the same relative to their origins
    x_plr = rail_coord_hamilton.x - self._hamilton_origin.x
    z_plr = rail_coord_hamilton.z - self._hamilton_origin.z

    # Y conversion: Hamilton Y is negative, with less negative = back, more negative = front
    # PyLabRobot Y is positive, with larger = back, smaller = front
    # Hamilton origin (Origin.Y) is at front-left (most negative Y), maps to PyLabRobot Y=0
    # Formula: y_plr = y_hamilton - hamilton_origin.y
    y_plr = rail_coord_hamilton.y - self._hamilton_origin.y

    rail_coord_plr_abs = Coordinate(x=x_plr, y=y_plr, z=z_plr)

    # Return coordinates relative to deck origin
    # Deck always sets location during initialization, so it's never None
    assert self.location is not None
    return Coordinate(
      x=rail_coord_plr_abs.x - self.location.x,
      y=rail_coord_plr_abs.y - self.location.y,
      z=rail_coord_plr_abs.z - self.location.z,
    )

  def to_hamilton_coordinate(self, coord: Coordinate) -> Coordinate:
    """Convert PyLabRobot coordinate to Hamilton coordinate.

    Useful when sending commands to hardware that expects Hamilton coordinates.

    Args:
      coord: PyLabRobot coordinate (relative to deck origin)

    Returns:
      Hamilton coordinate
    """
    # Convert to absolute coordinate (relative to deck's PyLabRobot origin)
    # Deck always sets location during initialization, so it's never None
    assert self.location is not None
    abs_coord = Coordinate(
      x=coord.x + self.location.x,
      y=coord.y + self.location.y,
      z=coord.z + self.location.z,
    )

    # Convert to Hamilton coordinate system
    # X and Z: add back the origin offset
    x_hamilton = abs_coord.x + self._hamilton_origin.x
    z_hamilton = abs_coord.z + self._hamilton_origin.z

    # Y conversion: inverse of from_hamilton_coordinate
    # y_plr = y_hamilton - hamilton_origin.y
    # Solving for y_hamilton: y_hamilton = y_plr + hamilton_origin.y
    y_hamilton = abs_coord.y + self._hamilton_origin.y

    return Coordinate(x=x_hamilton, y=y_hamilton, z=z_hamilton)

  def from_hamilton_coordinate(self, coord: Coordinate) -> Coordinate:
    """Convert Hamilton coordinate to PyLabRobot coordinate.

    Useful when reading config files or parsing hardware responses.

    Args:
      coord: Hamilton coordinate

    Returns:
      PyLabRobot coordinate (relative to deck origin)
    """
    # Convert to PyLabRobot coordinate system (absolute)
    # X and Z remain the same relative to their origins
    x_plr = coord.x - self._hamilton_origin.x
    z_plr = coord.z - self._hamilton_origin.z

    # Y conversion: Hamilton Y is negative, with less negative = back, more negative = front
    # PyLabRobot Y is positive, with larger = back, smaller = front
    # Hamilton origin (Origin.Y) is at front-left (most negative Y), maps to PyLabRobot Y=0
    # Formula: y_plr = y_hamilton - hamilton_origin.y
    y_plr = coord.y - self._hamilton_origin.y

    plr_coord_abs = Coordinate(x=x_plr, y=y_plr, z=z_plr)

    # Adjust to deck origin (make relative to deck origin)
    # Deck always sets location during initialization, so it's never None
    assert self.location is not None
    return Coordinate(
      x=plr_coord_abs.x - self.location.x,
      y=plr_coord_abs.y - self.location.y,
      z=plr_coord_abs.z - self.location.z,
    )

  # TODO: There is probably a better way to not hardcode waste positions AND make serialization/de not terrible
  def serialize(self) -> dict:
    """Serialize this deck."""
    serialized = super().serialize()
    # Remove with_trash and with_trash96 since NimbusDeck.__init__() doesn't accept them
    # NimbusDeck uses waste_type instead to control waste block creation
    serialized.pop("with_trash", None)
    serialized.pop("with_trash96", None)
    return {
      **serialized,
      "hamilton_origin": serialize(self._hamilton_origin),
      "y_min": self._y_min,
      "y_max": self._y_max,
      "z_max": self._z_max,
      "rail_start_x": self._rail_start_x,
      "rail_width": self._rail_width,
      "rail_y": self._rail_y,
      "waste_type": self.waste_type,
    }

  @classmethod
  def deserialize(cls, data: dict, allow_marshal: bool = False) -> "NimbusDeck":
    """Deserialize a NimbusDeck from a dictionary.

    Overrides parent deserialize to prevent waste block creation conflict.
    Sets waste_type=None before calling parent deserialize to prevent __init__()
    from creating the waste block, then restores waste_type from serialized data.

    Args:
        data: Serialized deck data dictionary
        allow_marshal: If True, allow marshal module for function deserialization

    Returns:
        Deserialized NimbusDeck instance
    """
    data_copy = data.copy()
    original_waste_type = data_copy.get("waste_type")
    # Set waste_type=None to prevent __init__() from creating waste block
    # The waste block will come from children data (already serialized)
    data_copy["waste_type"] = None

    # Call parent deserialize (waste block won't be created in __init__)
    deck = super().deserialize(data_copy, allow_marshal=allow_marshal)

    # Restore waste_type attribute from serialized data to keep instance consistent
    deck.waste_type = original_waste_type

    return deck

  @classmethod
  def from_files(
    cls,
    cfg_path: str,
    dck_path: str,
    origin: Coordinate = Coordinate.zero(),
    num_rails: Optional[int] = None,
    size_x: Optional[float] = None,
    size_y: Optional[float] = None,
    size_z: Optional[float] = None,
    hamilton_origin: Optional[Coordinate] = None,
    y_min: Optional[float] = None,
    y_max: Optional[float] = None,
    z_max: Optional[float] = None,
    rail_start_x: Optional[float] = None,
    rail_width: Optional[float] = None,
    rail_y: Optional[float] = None,
    waste_type: Optional[Literal["default_long"]] = None,
  ) -> NimbusDeck:
    """Create a Nimbus deck by parsing config files.

    Parses .cfg and .dck files to extract deck definition. The layout number
    is extracted from the "Layout" field in the .cfg file. Explicit parameters
    can be provided to override values parsed from the files.

    Args:
        cfg_path: Path to Nimbus .cfg file
        dck_path: Path to Nimbus .dck file
        origin: PyLabRobot origin coordinate (default: Coordinate.zero())
        num_rails: Override number of rails from parsed config
        size_x: Override deck size in X dimension from parsed config
        size_y: Override deck size in Y dimension from parsed config
        size_z: Override deck size in Z dimension from parsed config
        hamilton_origin: Override Hamilton origin coordinate from parsed config
        y_min: Override Hamilton Y minimum coordinate bound from parsed config
        y_max: Override Hamilton Y maximum coordinate bound from parsed config
        z_max: Override maximum Z height from parsed config
        rail_start_x: Override Hamilton X coordinate of first rail start from parsed config
        rail_width: Override width between rails from parsed config
        rail_y: Override Hamilton Y coordinate of all rails from parsed config
        waste_type: Waste configuration type (default: None, auto-detected from config).
            If "default_long", creates a waste block with 8 channel positions.
            If None, attempts to detect from config (WasteLong ExSite), otherwise warns.

    Returns:
        NimbusDeck instance with parsed or overridden dimensions

    Raises:
        FileNotFoundError: If config files are not found
        ValueError: If required values are not found in config files
    """

    # Helper function to parse config files
    def _parse_config_files(cfg_path: str, dck_path: str) -> Dict[str, Any]:
      """Parse Nimbus config files to extract deck definition.

      The layout number is extracted from the "Layout" field in the .cfg file.
      Returns a dictionary with float values for dimensions and a list of strings for exsite_ids.
      """
      # Read .cfg file
      with open(cfg_path, "r", encoding="utf-8") as f:
        cfg_content = f.read()

      # Read .dck file
      with open(dck_path, "r", encoding="utf-8") as f:
        dck_content = f.read()

      # Extract from .cfg file
      def extract_cfg_value(key: str) -> Optional[float]:
        """Extract a value from .cfg file."""
        pattern = rf'{key},\s*"([^"]+)"'
        match = re.search(pattern, cfg_content)
        if match:
          try:
            return float(match.group(1))
          except ValueError:
            return None
        return None

      def extract_cfg_string(key: str) -> Optional[str]:
        """Extract a string value from .cfg file."""
        pattern = rf'{key},\s*"([^"]+)"'
        match = re.search(pattern, cfg_content)
        if match:
          return match.group(1)
        return None

      # Extract from .dck file (layout-specific section)
      def extract_dck_value(key: str, layout_num: int) -> Optional[float]:
        """Extract a value from .dck file for specific layout."""
        # Find the layout section: DataDef,DECK,2,{layout_num}
        layout_pattern = rf"DataDef,DECK,2,{layout_num},"
        layout_match = re.search(layout_pattern, dck_content)
        if not layout_match:
          return None

        # Find the section end
        start_pos = layout_match.end()
        # Look for next DataDef or end of file
        next_datadef = re.search(r"DataDef,", dck_content[start_pos:])
        if next_datadef:
          section_end = start_pos + next_datadef.start()
        else:
          section_end = len(dck_content)

        section_content = dck_content[start_pos:section_end]

        # Extract value from this section
        pattern = rf'{key},\s*"([^"]+)"'
        match = re.search(pattern, section_content)
        if match:
          try:
            return float(match.group(1))
          except ValueError:
            return None
        return None

      def extract_dck_exsite_ids(layout_num: int) -> List[str]:
        """Extract ExSite.Id values from .dck file for specific layout."""
        # Find the layout section: DataDef,DECK,2,{layout_num}
        layout_pattern = rf"DataDef,DECK,2,{layout_num},"
        layout_match = re.search(layout_pattern, dck_content)
        if not layout_match:
          return []

        # Find the section end
        start_pos = layout_match.end()
        # Look for next DataDef or end of file
        next_datadef = re.search(r"DataDef,", dck_content[start_pos:])
        if next_datadef:
          section_end = start_pos + next_datadef.start()
        else:
          section_end = len(dck_content)

        section_content = dck_content[start_pos:section_end]

        # Extract all ExSite.Id values
        pattern = r'ExSite\.\d+\.Id,\s*"([^"]+)"'
        matches = re.findall(pattern, section_content)
        return matches

      # Extract layout from .cfg file (required)
      layout_str = extract_cfg_string("Layout")
      if layout_str is None:
        raise ValueError(
          "Could not extract Layout from .cfg file. "
          "The Layout field is required to determine which deck layout to use from the .dck file."
        )
      try:
        layout = int(layout_str)
      except ValueError:
        raise ValueError(
          f"Could not parse Layout value '{layout_str}' from .cfg file as an integer."
        )

      # Extract ExSite.Id values to check for waste configuration
      exsite_ids = extract_dck_exsite_ids(layout)

      # Extract from .cfg file
      y_min_val = extract_cfg_value("Y_MIN")
      y_max_val = extract_cfg_value("Y_MAX")
      z_max_val = extract_cfg_value("probeMaxZPosition")

      # Extract from .dck file (layout-specific)
      dim_dx = extract_dck_value("Dim\\.Dx", layout)
      dim_dy = extract_dck_value("Dim\\.Dy", layout)
      dim_dz = extract_dck_value("Dim\\.Dz", layout)
      origin_x = extract_dck_value("Origin\\.X", layout)
      origin_y = extract_dck_value("Origin\\.Y", layout)
      origin_z = extract_dck_value("Origin\\.Z", layout)
      track_count = extract_dck_value("Track\\.Cnt", layout)
      track_width = extract_dck_value("Track\\.Dx", layout)
      track_start_x_val = extract_dck_value("Track\\.Start\\.X", layout)
      track_y_val = extract_dck_value("Track\\.Y", layout)

      # Validate required values
      if dim_dx is None or dim_dy is None or dim_dz is None:
        raise ValueError(
          f"Could not extract deck dimensions from config files. "
          f"Found: Dx={dim_dx}, Dy={dim_dy}, Dz={dim_dz}"
        )

      if origin_x is None or origin_y is None or origin_z is None:
        raise ValueError(
          f"Could not extract deck origin from config files. "
          f"Found: Origin.X={origin_x}, Origin.Y={origin_y}, Origin.Z={origin_z}"
        )

      result: Dict[str, Any] = {
        "size_x": dim_dx,
        "size_y": dim_dy,
        "size_z": dim_dz,
        "origin_x": origin_x,
        "origin_y": origin_y,
        "origin_z": origin_z,
      }

      # Add optional values if found
      if y_min_val is not None:
        result["y_min"] = y_min_val
      if y_max_val is not None:
        result["y_max"] = y_max_val
      if z_max_val is not None:
        result["z_max"] = z_max_val
      if track_count is not None:
        result["track_count"] = track_count
      if track_width is not None:
        result["track_width"] = track_width
      if track_start_x_val is not None:
        result["track_start_x"] = track_start_x_val
      if track_y_val is not None:
        result["track_y"] = track_y_val

      # Store ExSite.Id values for waste detection
      result["exsite_ids"] = exsite_ids

      return result

    # Parse config files
    parsed_config = _parse_config_files(cfg_path, dck_path)

    # Determine waste_type from config if not explicitly provided
    if waste_type is None:
      exsite_ids = parsed_config.get("exsite_ids", [])
      if "WasteLong" in exsite_ids:
        waste_type = "default_long"
      else:
        # Find waste-related ExSite names
        waste_sites = [site_id for site_id in exsite_ids if "Waste" in site_id]
        if waste_sites:
          warnings.warn(
            f"Waste site(s) found in config ({', '.join(waste_sites)}), but no PyLabRobot "
            f"resource is defined for them. To add waste support, either:\n"
            f"  1. Specify waste_type='default_long' when calling from_files() if using WasteLong, or\n"
            f"  2. Add support for the waste site type(s) in PyLabRobot by implementing "
            f"the corresponding resource definition.",
            UserWarning,
            stacklevel=2,
          )
        else:
          warnings.warn(
            "No waste block found in config files. "
            "Waste will not be created. To add waste, specify waste_type='default_long' "
            "when calling from_files().",
            UserWarning,
            stacklevel=2,
          )

    # Extract Hamilton origin from parsed config if not overridden
    if hamilton_origin is None:
      hamilton_origin = Coordinate(
        x=parsed_config["origin_x"],
        y=parsed_config["origin_y"],
        z=parsed_config["origin_z"],
      )

    # Use parsed values, but allow explicit parameters to override
    num_rails_val = num_rails if num_rails is not None else int(parsed_config.get("track_count", 0))
    size_x_val = size_x if size_x is not None else parsed_config["size_x"]
    size_y_val = size_y if size_y is not None else parsed_config["size_y"]
    size_z_val = size_z if size_z is not None else parsed_config["size_z"]
    y_min_val = y_min if y_min is not None else parsed_config.get("y_min")
    y_max_val = y_max if y_max is not None else parsed_config.get("y_max")
    z_max_val = z_max if z_max is not None else parsed_config.get("z_max")
    rail_start_x_val = (
      rail_start_x if rail_start_x is not None else parsed_config.get("track_start_x")
    )
    rail_width_val = rail_width if rail_width is not None else parsed_config.get("track_width")
    rail_y_val = rail_y if rail_y is not None else parsed_config.get("track_y")

    # Validate that we have all required values
    if num_rails_val is None:
      raise ValueError("Could not extract track_count from config files and num_rails not provided")
    if size_x_val is None:
      raise ValueError("Could not extract size_x from config files and size_x not provided")
    if size_y_val is None:
      raise ValueError("Could not extract size_y from config files and size_y not provided")
    if size_z_val is None:
      raise ValueError("Could not extract size_z from config files and size_z not provided")
    if y_min_val is None:
      raise ValueError("Could not extract y_min from config files and y_min not provided")
    if y_max_val is None:
      raise ValueError("Could not extract y_max from config files and y_max not provided")
    if z_max_val is None:
      raise ValueError("Could not extract z_max from config files and z_max not provided")
    if rail_start_x_val is None:
      raise ValueError(
        "Could not extract track_start_x from config files and rail_start_x not provided"
      )
    if rail_width_val is None:
      raise ValueError(
        "Could not extract track_width from config files and rail_width not provided"
      )
    if rail_y_val is None:
      raise ValueError("Could not extract track_y from config files and rail_y not provided")

    return cls(
      num_rails=num_rails_val,
      size_x=size_x_val,
      size_y=size_y_val,
      size_z=size_z_val,
      hamilton_origin=hamilton_origin,
      y_min=y_min_val,
      y_max=y_max_val,
      z_max=z_max_val,
      rail_start_x=rail_start_x_val,
      rail_width=rail_width_val,
      rail_y=rail_y_val,
      origin=origin,
      waste_type=waste_type,
    )
