"""Protocol-facing frontend for awaiting manual operator actions."""

from typing import Any, Dict, Optional

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
    result = await self.provider.request(request)

    if not isinstance(result, OperatorActionResult):
      raise TypeError("OperatorActionProvider.request() must return OperatorActionResult")
    if result.status == OperatorActionStatus.CANCELLED:
      raise OperatorActionCancelledError(request, result)
    if result.status == OperatorActionStatus.FAILED:
      raise OperatorActionFailedError(request, result)
    if result.status != OperatorActionStatus.COMPLETED:
      raise ValueError(f"Unsupported operator action status: {result.status!r}")
    return result
