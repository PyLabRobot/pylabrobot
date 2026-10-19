from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.head_tool import HeadTool
from pylabrobot.resources.volume_tracker import VolumeTracker


class Tip(HeadTool):
  """A single tip.

  Attributes:
    has_filter: whether the tip type has a filter
    size_z: total length of the tip, in mm
    nominal_volume: rated working volume of the tip (what it is sold and named as), in uL.
      Defaults to maximal_volume when not given.
    maximal_volume: physical brim-full capacity of the tip, in uL
    collar_height: the height of the collar, in mm
    name: identifier for this tip
  """

  def __init__(
    self,
    name: str,
    diameter: float,
    size_z: float,
    has_filter: bool,
    maximal_volume: float,
    fitting_depth: float,
    nominal_volume: Optional[float] = None,
    collar_height: Optional[float] = None,
    category: str = "tip",
    model: Optional[str] = None,
    pick_up_location: Optional[Coordinate] = None,
    is_conductive: Optional[bool] = None,
    *,
    identifier: Optional[str] = None,
    origin_identifier: Optional[str] = None,
    metadata: Optional[dict] = None,
  ):
    """Initialize a tip with its resource dimensions and liquid handling properties."""
    super().__init__(
      name=name,
      size_x=diameter,
      size_y=diameter,
      size_z=size_z,
      fitting_depth=fitting_depth,
      category=category,
      model=model,
      pick_up_location=pick_up_location,
      is_conductive=is_conductive,
      identifier=identifier,
      metadata=metadata,
    )
    self.origin_identifier = origin_identifier
    self.has_filter = has_filter
    self.maximal_volume = maximal_volume
    self._collar_height = collar_height
    self.nominal_volume = nominal_volume if nominal_volume is not None else maximal_volume
    self.tracker = VolumeTracker(thing=name, max_volume=maximal_volume)
    # What the tip holds is state a viewer shows, so a change in the tracker is a state update.
    self.tracker.register_callback(self._state_updated)

  def serialize_state(self) -> Dict[str, Any]:
    return {**super().serialize_state(), **self.tracker.serialize()}

  def load_state(self, state: Dict[str, Any]) -> None:
    super().load_state(state)
    self.tracker.load_state({k: v for k, v in state.items() if k != "rotation"})

  def serialize(self) -> dict:
    """Serialize the tip's resource fields and liquid handling properties."""
    data = super().serialize()
    diameter = data.pop("size_x")
    data.pop("size_y")
    return {
      **data,
      "diameter": diameter,
      "origin_identifier": self.origin_identifier,
      "has_filter": self.has_filter,
      "nominal_volume": self.nominal_volume,
      "maximal_volume": self.maximal_volume,
      "collar_height": self._collar_height,
    }

  @property
  def collar_height(self) -> float:
    """Return collar_height, raising if it is None."""
    if self._collar_height is None:
      raise ValueError(f"collar_height is not defined for this tip: {self!r}")
    return self._collar_height

  @property
  def has_collar_height(self) -> bool:
    """Whether this tip specifies a collar height."""
    return self._collar_height is not None


TipCreator = Callable[[str], Tip]
