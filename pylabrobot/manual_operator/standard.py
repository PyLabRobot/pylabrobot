"""Shared request, result, and error types for operator actions."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class OperatorActionStatus(str, Enum):
  """Outcome reported by an operator-action provider."""

  COMPLETED = "completed"
  CANCELLED = "cancelled"
  FAILED = "failed"


@dataclass(frozen=True)
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

  def __post_init__(self) -> None:
    for field_name in (
      "operator_name",
      "action",
      "title",
      "instructions",
      "confirmation_text",
    ):
      if not getattr(self, field_name).strip():
        raise ValueError(f"{field_name} must not be empty")
    object.__setattr__(self, "details", self.details.copy())


@dataclass(frozen=True)
class OperatorActionResult:
  """The outcome reported by an operator-action provider."""

  status: OperatorActionStatus
  message: Optional[str] = None
  confirmed_by: Optional[str] = None

  @classmethod
  def completed(
    cls, *, message: Optional[str] = None, confirmed_by: Optional[str] = None
  ) -> "OperatorActionResult":
    return cls(status=OperatorActionStatus.COMPLETED, message=message, confirmed_by=confirmed_by)

  @classmethod
  def cancelled(
    cls, *, message: Optional[str] = None, confirmed_by: Optional[str] = None
  ) -> "OperatorActionResult":
    return cls(status=OperatorActionStatus.CANCELLED, message=message, confirmed_by=confirmed_by)

  @classmethod
  def failed(
    cls, *, message: Optional[str] = None, confirmed_by: Optional[str] = None
  ) -> "OperatorActionResult":
    return cls(status=OperatorActionStatus.FAILED, message=message, confirmed_by=confirmed_by)


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
