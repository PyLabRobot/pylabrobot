"""Exceptions for Hamilton HOI exception frames that are not per pipetting channel."""

from __future__ import annotations

from typing import Dict, List

from pylabrobot.hamilton.tcp.wire_types import HcResultEntry


class HamiltonStatusException(Exception):
  """Raised for ``STATUS_EXCEPTION`` / ``COMMAND_EXCEPTION`` when the command wire shape
  does not carry per-channel parameters (e.g. void MLPrep queries).

  Errors are keyed by **wire entry index**, not physical channel index — use
  :attr:`entries` for raw :class:`HcResultEntry` data.
  """

  def __init__(
    self,
    *,
    errors: Dict[int, Exception],
    entries: List[HcResultEntry],
    raw_response: bytes,
  ) -> None:
    self.errors = errors
    self.entries = entries
    self.raw_response = raw_response
    super().__init__(self._format_message())

  def _format_message(self) -> str:
    parts = [f"entry[{i}]: {self.errors[i]}" for i in sorted(self.errors)]
    return "HamiltonStatusException(" + "; ".join(parts) + ")"
