"""Shared request, result, and error types for operator actions."""

from dataclasses import dataclass, field
from typing import Any, Dict, Literal, Optional, Sequence

from pylabrobot.resources import Resource

OperatorActionStatus = Literal["completed", "cancelled", "failed"]
"""Outcome reported by an operator-action provider."""


@dataclass
class OperatorActionRequest:
  """A transport-independent request for a person to perform one action.

  ``action`` identifies the kind of work. A provider may add its own transport-specific
  correlation identifier when publishing the request through a GUI, API, or message broker.
  """

  operator_name: str
  action: str
  title: str
  instructions: str
  confirmation_text: str = "Confirm action completed"
  details: Dict[str, Any] = field(default_factory=dict)
  resources: Sequence[Resource] = field(default_factory=tuple)
  source: Optional[Resource] = None
  destination: Optional[Resource] = None

  def __post_init__(self) -> None:
    if not self.operator_name.strip():
      raise ValueError("operator_name must not be empty")
    if not self.action.strip():
      raise ValueError("action must not be empty")
    if not self.title.strip():
      raise ValueError("title must not be empty")
    if not self.instructions.strip():
      raise ValueError("instructions must not be empty")
    if not self.confirmation_text.strip():
      raise ValueError("confirmation_text must not be empty")
    self.details = self.details.copy()
    self.resources = tuple(self.resources)


@dataclass(frozen=True)
class OperatorActionResult:
  """The outcome reported by an operator-action provider."""

  status: OperatorActionStatus
  message: Optional[str] = None
  confirmed_by: Optional[str] = None

  def __post_init__(self) -> None:
    if self.status not in ("completed", "cancelled", "failed"):
      raise ValueError(f"Unsupported operator action status: {self.status!r}")

  @classmethod
  def completed(
    cls, *, message: Optional[str] = None, confirmed_by: Optional[str] = None
  ) -> "OperatorActionResult":
    return cls(status="completed", message=message, confirmed_by=confirmed_by)

  @classmethod
  def cancelled(
    cls, *, message: Optional[str] = None, confirmed_by: Optional[str] = None
  ) -> "OperatorActionResult":
    return cls(status="cancelled", message=message, confirmed_by=confirmed_by)

  @classmethod
  def failed(
    cls, *, message: Optional[str] = None, confirmed_by: Optional[str] = None
  ) -> "OperatorActionResult":
    return cls(status="failed", message=message, confirmed_by=confirmed_by)


class OperatorActionError(RuntimeError):
  """Base exception raised when a manual operator action does not complete."""

  def __init__(self, request: OperatorActionRequest, result: OperatorActionResult):
    self.request = request
    self.result = result
    message = result.message or f"Operator action {request.action!r} did not complete."
    super().__init__(message)


class OperatorActionCancelledError(OperatorActionError):
  """Raised when an operator cancels a requested action."""


class OperatorActionFailedError(OperatorActionError):
  """Raised when an operator reports that a requested action could not be completed."""
