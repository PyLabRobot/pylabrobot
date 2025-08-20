"""Outlet request builder"""
from __future__ import annotations

from .client_request import ClientRequest


class OutletRequestBuilder:  # pylint: disable=too-few-public-methods
  """Outlet request builder"""

  @classmethod
  def set_state_request(cls, state: str, plug_indx: int | None) -> ClientRequest:
    """Set device state."""
    params: dict[str, str | int] = {"state": state}
    if plug_indx is not None:
      params["chs"] = 1 << plug_indx
    return ClientRequest("setState", params)
