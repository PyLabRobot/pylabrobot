"""Flex-side error ergonomics over the shared Opentrons error hierarchy.

Both subclass the package :class:`~pylabrobot.opentrons.errors.OpentronsError`,
so ``except OpentronsError`` catches them everywhere, while the Flex driver
keeps the two shapes it raises against: a titled guard/refusal error, and a
command failure that carries the robot-server's raw ``ErrorOccurrence`` dict so
callers can branch on ``error_type`` (e.g. ``"liquidNotFound"``).
"""

from typing import Any, Dict, Optional, cast

from pylabrobot.opentrons.errors import OpentronsError as _OpentronsError


class OpentronsError(_OpentronsError):
  """A Flex guard or refusal, with a title and optional detail."""

  def __init__(self, title: str, message: Optional[str] = None) -> None:
    self.title, self.message = title, message
    super().__init__(f"{title}: {message}" if message else title)


class OpentronsCommandError(_OpentronsError):
  """A robot-server command completed with status "failed".

  Carries the command's error payload (the robot-server's ``ErrorOccurrence``
  dict) so callers can react to defined errors by ``error_type`` (e.g.
  ``"liquidNotFound"``) instead of parsing the message string.
  """

  def __init__(self, command_type: str, error: Dict[str, Any]) -> None:
    super().__init__(f"Opentrons command '{command_type}' failed: {error.get('detail', error)}")
    self.command_type = command_type
    self.error = error

  @property
  def error_type(self) -> Optional[str]:
    """The machine-readable error identifier, e.g. "liquidNotFound"."""
    return cast(Optional[str], self.error.get("errorType"))
