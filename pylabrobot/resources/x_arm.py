from typing import Any, Dict, Literal

from pylabrobot.resources.resource import Resource
from pylabrobot.resources.x_arm_tracker import XArmTracker


class XArm(Resource):
  """A model of an X-arm carriage, owned by the deck.

  Like a :class:`~pylabrobot.resources.tip_rack.TipSpot` owns a ``TipTracker``, an
  ``XArm`` owns an :class:`XArmTracker`: the tracker's x is the resource's state, so
  it reaches the Visualizer through the standard state channel. The backend drives
  the tracker (from firmware); the Visualizer reads its x and draws it there.

  ``reference_point`` says where along the arm's width the tracked x refers to (the
  arm centre for a dual-rail arm, the right edge for a single-rail arm), so the
  Visualizer positions the X-arm without re-deriving the convention.
  """

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    reference_point: Literal["center", "right"] = "center",
    category: str = "x_arm",
    model: str = "hamilton_legacy_star_dual_rail_arm",
  ):
    super().__init__(
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      category=category,
      model=model,
    )
    self.reference_point = reference_point
    self.tracker = XArmTracker(thing=name)
    self.tracker.register_callback(self._state_updated)

  def serialize(self) -> Dict[str, Any]:
    return {**super().serialize(), "reference_point": self.reference_point}

  def serialize_state(self) -> Dict[str, Any]:
    return {**super().serialize_state(), "tracker": self.tracker.serialize()}

  def load_state(self, state: Dict[str, Any]) -> None:
    super().load_state(state)
    if "tracker" in state:
      self.tracker.load_state(state["tracker"])
