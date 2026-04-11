from typing import Awaitable, Callable, Optional

SendCommandFunc = Callable[..., Awaitable[Optional[str]]]


class BaseControl:
  def __init__(self, send_command: SendCommandFunc) -> None:
    self.send_command = send_command
