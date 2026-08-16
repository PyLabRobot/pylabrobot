"""Protocol-facing frontend for awaiting manual operator actions."""

from typing import Any, Dict, Optional, Sequence

from pylabrobot.events import device_reference, event_operation, resource_reference
from pylabrobot.resources import Coordinate, Resource

from .provider import OperatorActionProvider
from .standard import (
  OperatorActionCancelledError,
  OperatorActionFailedError,
  OperatorActionRequest,
  OperatorActionResult,
  OperatorActionStatus,
)


class ManualOperator:
  """Await manual protocol work through a pluggable acknowledgement provider."""

  def __init__(self, provider: OperatorActionProvider, name: str = "operator"):
    if not name.strip():
      raise ValueError("name must not be empty")
    self.name = name
    self.provider = provider

  async def perform(
    self,
    *,
    action: str,
    title: str,
    instructions: str,
    confirmation_text: str = "Confirm action completed",
    details: Optional[Dict[str, Any]] = None,
    resources: Optional[Sequence[Resource]] = None,
    source: Optional[Resource] = None,
    destination: Optional[Resource] = None,
  ) -> OperatorActionResult:
    """Await one operator action and return its successful acknowledgement.

    Providers report a structured outcome. Cancellation and failure become distinct exceptions;
    exceptions raised by the provider itself propagate unchanged.
    """

    request = OperatorActionRequest(
      operator_name=self.name,
      action=action,
      title=title,
      instructions=instructions,
      confirmation_text=confirmation_text,
      details={} if details is None else details,
    )
    operation_data: Dict[str, Any] = {
      "device": device_reference(self, name=self.name),
      "resources": [resource_reference(resource) for resource in resources or ()],
      "manual_action": request.action,
      "title": request.title,
      "instructions": request.instructions,
      "confirmation_text": request.confirmation_text,
      "details": request.details.copy(),
    }
    if source is not None:
      operation_data["source"] = resource_reference(source)
    if destination is not None:
      operation_data["destination"] = resource_reference(destination)

    result: Optional[OperatorActionResult] = None

    def completed_data() -> Dict[str, Any]:
      assert result is not None
      data = operation_data.copy()
      if result.message is not None:
        data["result_message"] = result.message
      if result.confirmed_by is not None:
        data["confirmed_by"] = result.confirmed_by
      return data

    with event_operation(
      f"manual_operator.{request.action}",
      completed_data_factory=completed_data,
      **operation_data,
    ):
      result = await self.provider.request(request)

      if not isinstance(result, OperatorActionResult):
        raise TypeError("OperatorActionProvider.request() must return OperatorActionResult")
      if result.status == OperatorActionStatus.CANCELLED:
        raise OperatorActionCancelledError(request, result)
      if result.status == OperatorActionStatus.FAILED:
        raise OperatorActionFailedError(request, result)
      if result.status != OperatorActionStatus.COMPLETED:
        raise ValueError(f"Unsupported operator action status: {result.status!r}")
    assert result is not None
    return result

  async def move_resource(
    self,
    *,
    resource: Resource,
    source: Resource,
    destination: Resource,
    destination_location: Optional[Coordinate] = None,
    title: Optional[str] = None,
    instructions: Optional[str] = None,
    confirmation_text: str = "Confirm resource moved",
    details: Optional[Dict[str, Any]] = None,
  ) -> OperatorActionResult:
    """Await a manual resource move, then reconcile the PLR resource model.

    The resource remains assigned to ``source`` while the operator action is pending. A completed
    acknowledgement reassigns it to ``destination`` using PLR's normal assignment machinery.
    Cancellation, reported failure, and provider exceptions leave the model unchanged.

    Args:
      resource: Resource physically moved by the operator.
      source: Resource currently containing ``resource``.
      destination: Resource that will contain ``resource`` after the move.
      destination_location: Optional resource location relative to ``destination``. Resource
        holders calculate their normal child location when this is omitted.
      title: Provider-facing title. Defaults to ``"Move <resource name>"``.
      instructions: Provider-facing instructions. Defaults to a concise source-to-destination
        instruction.
      confirmation_text: Provider-facing completion acknowledgement text.
      details: Additional transport-safe details for the provider.
    """

    self._validate_resource_move(
      resource=resource,
      source=source,
      destination=destination,
    )

    move_details = {} if details is None else details.copy()
    move_details.update(
      {
        "resource": resource.name,
        "source": source.name,
        "destination": destination.name,
      }
    )
    if destination_location is not None:
      move_details["destination_location"] = destination_location.serialize()

    result = await self.perform(
      action="resource.move",
      title=title or f"Move {resource.name}",
      instructions=instructions
      or f"Move {resource.name} from {source.name} to {destination.name}.",
      confirmation_text=confirmation_text,
      details=move_details,
      resources=[resource],
      source=source,
      destination=destination,
    )

    try:
      self._validate_resource_move(
        resource=resource,
        source=source,
        destination=destination,
      )
    except (RuntimeError, ValueError) as error:
      raise RuntimeError(
        "The PLR resource model changed while the manual move was pending; "
        "the completed physical move was not applied to the model."
      ) from error

    destination.assign_child_resource(
      resource=resource,
      location=destination_location,
      reassign=True,
    )
    return result

  @staticmethod
  def _validate_resource_move(
    *,
    resource: Resource,
    source: Resource,
    destination: Resource,
  ) -> None:
    if resource.parent is not source:
      current_parent = None if resource.parent is None else resource.parent.name
      raise ValueError(
        f"Resource {resource.name!r} is assigned to {current_parent!r}, not source {source.name!r}."
      )
    if source is destination:
      raise ValueError("source and destination must be different resources")
    destination.check_can_drop_resource_here(resource, reassign=True)
