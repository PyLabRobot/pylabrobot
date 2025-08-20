"""Client request"""

from typing import Any


class ClientRequest:
  """Client request"""

  def __init__(self, method: str, params: dict[str, Any]) -> None:
    self._method = method
    self._params = params

  @property
  def method(self) -> str:
    """Return call device method"""
    return self._method

  @property
  def params(self) -> dict[str, Any]:
    """Return call params"""
    return self._params
