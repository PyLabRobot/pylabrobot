"""Behavioral tests for transport boundary cursors."""

import pytest

from pylabrobot.io.boundary import BoundaryCursor, BoundaryDiscard, BoundaryRead


@pytest.mark.parametrize("kind", ["report", "packet"])
def test_immediate_and_adjacent_boundaries(kind: str) -> None:
  cursor = BoundaryCursor(frozenset({kind}))
  assert cursor.take_to_boundary(
    None, boundary=kind, consume_immediate_boundary=False, discard=False
  ) == BoundaryRead(b"", True, True, False)
  cursor.append(b"abc", frozenset({kind}))
  assert cursor.take_to_boundary(
    None, boundary=kind, consume_immediate_boundary=True, discard=False
  ) == BoundaryRead(b"abc", True, True, False)
  cursor.append(b"", frozenset({kind}))
  assert cursor.take_to_boundary(
    None, boundary=kind, consume_immediate_boundary=True, discard=False
  ) == BoundaryRead(b"", True, True, False)


@pytest.mark.parametrize("kind", ["report", "packet"])
def test_limit_and_atomic_pending_read(kind: str) -> None:
  cursor = BoundaryCursor(frozenset({kind}))
  cursor.append(b"abc", frozenset({kind}))
  assert cursor.take_to_boundary(
    1, boundary=kind, consume_immediate_boundary=True, discard=False
  ) == BoundaryRead(b"a", True, False, True)
  assert cursor.take_to_boundary(
    None, boundary=kind, consume_immediate_boundary=True, discard=False
  ) == BoundaryRead(b"bc", False, True, False)
  cursor.append(b"d", frozenset())
  assert (
    cursor.take_to_boundary(None, boundary=kind, consume_immediate_boundary=True, discard=True)
    is None
  )
  assert cursor.read_bytes(1) == b"d"


def test_mixed_bytes_marks_and_short_packet_selection() -> None:
  cursor = BoundaryCursor(frozenset({"packet"}))
  cursor.append(b"ab", frozenset({"packet"}))
  cursor.append(b"cd", frozenset({"packet", "short_packet_end"}))
  assert cursor.read_bytes(1) == b"a"
  assert cursor.take_to_boundary(
    2, boundary="packet", consume_immediate_boundary=True, discard=False
  ) == BoundaryRead(b"b", False, True, False)
  assert cursor.take_to_boundary(
    None, boundary="short_packet_end", consume_immediate_boundary=True, discard=True
  ) == BoundaryDiscard(2, False, True, False)
  assert [(mark.offset, mark.kind, mark.pending) for mark in cursor.last_operation_marks] == [
    (0, "packet", False),
    (2, "packet", True),
    (2, "short_packet_end", True),
  ]


def test_empty_byte_read_preserves_initial_mark() -> None:
  cursor = BoundaryCursor(frozenset({"report"}))
  assert cursor.read_bytes(1) == b""
  assert cursor.take_to_boundary(
    None, boundary="report", consume_immediate_boundary=False, discard=False
  ) == BoundaryRead(b"", True, True, False)
