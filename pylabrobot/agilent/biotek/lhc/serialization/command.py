"""What a command is: a framed request and the reply it expects."""

from __future__ import annotations

from dataclasses import dataclass

from pylabrobot.agilent.biotek.lhc.serialization.frame import STATUS_LENGTH, Header, checksum


@dataclass
class Command:
  """One request and its reply.

  Attributes:
    number: Which command this is.
    payload: The payload sent with it.
    reserved: The header's reserved field.
    answer_length: How many bytes of the reply are the answer, or 0 to take all of them.
    timeout: How long to wait for the reply, in seconds, or None for the transport's default.
      Commands the instrument answers only once it has finished moving set their own.
    expects_reply: Whether a reply frame follows the acknowledgement. A command that stops the
      instrument is acknowledged and then answered by nothing at all, so waiting for a frame would
      only ever time out.
  """

  number: int
  payload: bytes = b""
  reserved: int = 0
  answer_length: int = 0
  timeout: float | None = None
  expects_reply: bool = True

  def to_bytes(self) -> bytes:
    """Frame the command for sending.

    Returns:
      The header followed by the payload.
    """
    header = Header(
      number=int(self.number), reserved=self.reserved, payload_length=len(self.payload)
    )
    header.check = checksum(header.to_bytes(), self.payload)
    return header.to_bytes() + self.payload

  def reply_is_intact(self, header: Header, payload: bytes) -> bool:
    """Whether a reply's checksum matches its contents.

    Args:
      header: The reply header.
      payload: The reply payload.

    Returns:
      True if the reply arrived intact.
    """
    return header.check == checksum(header.to_bytes(), payload)

  def parse_reply(self, payload: bytes) -> tuple[int, bytes]:
    """Split the instrument's status off a reply payload.

    Args:
      payload: The reply payload.

    Returns:
      The status and the answer. A status of 0 means the instrument is reporting success and the
      answer is meaningful; anything else is an error code and the answer is empty.
    """
    status = int.from_bytes(payload[:STATUS_LENGTH], "little")
    if status != 0:
      return status, b""
    answer = payload[STATUS_LENGTH:]
    if self.answer_length:
      answer = answer[: self.answer_length]
    return 0, answer
