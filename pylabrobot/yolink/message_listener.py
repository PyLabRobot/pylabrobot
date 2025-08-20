"""YoLink  cloud message listener."""
from abc import ABCMeta, abstractmethod
from typing import Any

from .device import YoLinkDevice


class MessageListener(metaclass=ABCMeta):
  """Home message listener."""

  @abstractmethod
  def on_message(self, device: YoLinkDevice, msg_data: dict[str, Any]) -> None:
    """On device message receive."""
