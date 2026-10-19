"""Image limits and durable acquisition specifications."""

from __future__ import annotations

from typing import Protocol

from google.protobuf.message import Message

MAX_PIXELS = 4096 * 2048
MAX_FRAME_BYTES = 32 * 1024 * 1024
MAX_MESSAGE_BYTES = 40 * 1024 * 1024


class Specification(Protocol):
  @property
  def frame_count(self) -> int: ...

  @property
  def interval_ms(self) -> int: ...

  def reservation(self) -> int: ...

  def to_proto(self) -> Message: ...
