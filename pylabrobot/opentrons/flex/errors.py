"""Flex guard errors and shared command failures."""

from typing import Optional

from pylabrobot.opentrons.errors import OpentronsCommandError  # noqa: F401
from pylabrobot.opentrons.errors import OpentronsError as _OpentronsError


class OpentronsError(_OpentronsError):
  """A Flex guard or refusal, with a title and optional detail."""

  def __init__(self, title: str, message: Optional[str] = None) -> None:
    self.title, self.message = title, message
    super().__init__(f"{title}: {message}" if message else title)
