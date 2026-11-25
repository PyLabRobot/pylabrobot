"""Optional ManualOperator fulfillment and physical-Start acknowledgment for Flex actions."""

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from pylabrobot.manual_operator import ManualOperator, OperatorActionRequest, OperatorActionResult
from pylabrobot.resources import Resource

from .actions import FlexAction
from .connection import FlexError

if TYPE_CHECKING:
  from .flex import KingFisherFlex

logger = logging.getLogger(__name__)


class FlexStartAcknowledgement:
  """A ManualOperator provider awaiting one active Flex action's original PAU exchange.

  Create this adapter for the specific action being handled, and select instrument
  acknowledgment before handling it. ManualOperator publishes the request lifecycle;
  this provider reports completion only after the instrument returns PAU END 0.
  It does not simulate Start or require a console acknowledgment as well.
  """

  def __init__(self, flex: "KingFisherFlex", action: FlexAction) -> None:
    """Bind acknowledgment to this instrument and exact action, not a later pause."""
    self._flex = flex
    self._action = action

  async def request(self, action: OperatorActionRequest) -> OperatorActionResult:
    """Await firmware completion inside the caller's ManualOperator operation."""
    logger.info("Awaiting physical Start: %s. %s", action.title, action.instructions)
    await self._flex.wait_for_physical_start(self._action)
    return OperatorActionResult.completed(confirmed_by=f"{self._flex.name}: PAU completion")


class FlexManualHandler:
  """Fulfill Flex actions through a caller-configured ManualOperator.

  Loading uses the bound plate's existing parent as its source. The application
  supplies unloading destinations through ``unload_destination``; no storage or arm
  is selected by this helper. Transfers use move_resource(), while interventions
  and already-loaded plate confirmations use perform(). Custom destination poses
  or workflows can instead call ManualOperator directly in an application handler.
  """

  @staticmethod
  def _details(action: FlexAction) -> dict[str, Any]:
    """Provide transport-safe BDZ context without copying resource objects."""
    return {
      "source_sha256": action.source_sha256,
      "line_number": action.line_number,
      "command": action.command.text,
      "bdz_plate_id": action.plate_id,
      "bdz_plate_name": action.plate_name,
      "loading_slot": action.loading_slot,
      "processing_slot": action.processing_slot,
      "additions": [
        {
          "plate_id": addition.plate_id,
          "reagent_name": addition.reagent_name,
          "volume": addition.volume,
        }
        for addition in action.additions
      ],
    }

  def __init__(
    self, operator: ManualOperator, *, unload_destination: Callable[[FlexAction], Resource]
  ) -> None:
    """Configure fulfillment without selecting a provider or changing any resources."""
    self.operator = operator
    self.unload_destination = unload_destination

  async def __call__(self, action: FlexAction) -> bool:
    """Await fulfillment and its model updates before authorizing further execution."""
    details = self._details(action)
    clearance = "Complete the action and clear the carousel before acknowledging."
    if action.action == "pause":
      await self.operator.perform(
        action="kingfisher.intervention",
        title="KingFisher intervention",
        instructions=f"{action.label or 'Complete the protocol intervention.'} {clearance}",
        resources=[action.plate] if action.plate is not None else [],
        source=action.holder,
        details=details,
      )
      return True
    plate = action.plate
    if plate is None:
      raise FlexError("A Flex transfer requires a bound plate")
    if action.action == "load" and action.holder.resource is plate:
      await self.operator.perform(
        action="kingfisher.confirm_load",
        title=f"Confirm preloaded plate {plate.name}",
        instructions=f"Verify {plate.name} is in tray {action.loading_slot}. {clearance}",
        resources=[plate],
        source=action.holder,
        destination=action.holder,
        details=details,
      )
      return True
    if action.action == "load":
      source = plate.parent
      if source is None:
        raise FlexError(f"Plate {plate.name!r} needs a modeled source before manual loading")
      destination: Resource = action.holder
    else:
      source = action.holder
      destination = self.unload_destination(action)
      if not isinstance(destination, Resource):
        raise TypeError("unload_destination must return a PLR Resource")
      if source.parent is not None and destination.is_in_subtree_of(source.parent):
        raise FlexError("Unloading destination must be outside this Flex carousel")
    await self.operator.move_resource(
      resource=plate,
      source=source,
      destination=destination,
      title=f"KingFisher {action.action}: {plate.name}",
      instructions=f"Move {plate.name} from {source.name} to {destination.name}. {clearance}",
      details=details,
    )
    return True
