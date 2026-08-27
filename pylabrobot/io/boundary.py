"""Lossless byte cursor with labelled transport-boundary marks."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BoundaryRead:
  """Bytes returned up to a selected boundary or caller limit."""

  data: bytes
  started_at_boundary: bool
  ended_at_boundary: bool
  limit_reached: bool


@dataclass(frozen=True)
class BoundaryDiscard:
  """Count and position of deliberately discarded bytes."""

  discarded_bytes: int | None
  started_at_boundary: bool
  ended_at_boundary: bool
  limit_reached: bool


@dataclass(frozen=True)
class BoundaryMark:
  """A mark's offset relative to the last successful operation."""

  offset: int
  kind: str
  pending: bool


class HIDReportTooLargeError(RuntimeError):
  """An HID report exceeded the configured maximum and was quarantined."""

  def __init__(self, maximum: int) -> None:
    super().__init__(f"HID input report exceeds configured maximum of {maximum} bytes")


class BoundaryCursor:
  """Buffer bytes and marks without acquiring native input or blocking."""

  def __init__(self, initial_marks: frozenset[str]) -> None:
    self._data = bytearray()
    self._position = 0
    self._marks: list[tuple[int, frozenset[str]]] = [(0, initial_marks)] if initial_marks else []
    self.last_operation_marks: tuple[BoundaryMark, ...] = ()

  def append(self, data: bytes, end_marks: frozenset[str]) -> None:
    """Append one native unit, retaining marks even for an empty unit."""
    self._data.extend(data)
    if end_marks:
      self._marks.append((len(self._data), end_marks))

  def _commit(self, end: int, consumed_immediate: int | None = None, kind: str = "") -> None:
    """Move the cursor and preserve marks at the new position."""
    self._marks = [
      (position, kinds - {kind} if index == consumed_immediate else kinds)
      for index, (position, kinds) in enumerate(self._marks)
      if position >= end
    ]
    self._marks = [(position, kinds) for position, kinds in self._marks if kinds]
    del self._data[:end]
    self._marks = [(position - end, kinds) for position, kinds in self._marks]
    self._position = 0

  def read_bytes(self, size: int) -> bytes:
    """Take at most ``size`` bytes, transparently crossing marks."""
    if size <= 0:
      raise ValueError("read size must be positive")
    end = min(len(self._data), self._position + size)
    if end == self._position:
      return b""
    start = self._position
    result = bytes(self._data[start:end])
    self.last_operation_marks = tuple(
      BoundaryMark(position - start, kind, position == end)
      for position, kinds in self._marks
      if start <= position <= end
      for kind in sorted(kinds)
    )
    self._commit(end)
    return result

  def take_to_boundary(
    self, max_bytes: int | None, *, boundary: str, consume_immediate_boundary: bool, discard: bool
  ) -> BoundaryRead | BoundaryDiscard | None:
    """Take through a selected mark; return ``None`` if more input is required."""
    if max_bytes is not None and max_bytes <= 0:
      raise ValueError("max_bytes must be positive")
    start = self._position
    immediate = next(
      (
        index
        for index, (position, kinds) in enumerate(self._marks)
        if position == start and boundary in kinds
      ),
      None,
    )
    started = immediate is not None
    consumed = immediate if started and consume_immediate_boundary else None
    candidates = [
      position
      for index, (position, kinds) in enumerate(self._marks)
      if boundary in kinds and position >= start and index != consumed
    ]
    next_boundary = min(candidates) if candidates else None
    limit = start + max_bytes if max_bytes is not None else None
    if next_boundary is not None and (limit is None or next_boundary <= limit):
      end = next_boundary
      ended = True
    elif limit is not None and limit <= len(self._data):
      end = limit
      ended = False
    else:
      return None
    size = end - start
    self.last_operation_marks = tuple(
      BoundaryMark(
        position - start,
        mark_kind,
        position == end and not (index == consumed and mark_kind == boundary),
      )
      for index, (position, kinds) in enumerate(self._marks)
      if start <= position <= end
      for mark_kind in sorted(kinds)
    )
    result: BoundaryRead | BoundaryDiscard
    if discard:
      result = BoundaryDiscard(size, started, ended, limit is not None and size == max_bytes)
    else:
      result = BoundaryRead(
        bytes(self._data[start:end]), started, ended, limit is not None and size == max_bytes
      )
    self._commit(end, consumed, boundary)
    return result
