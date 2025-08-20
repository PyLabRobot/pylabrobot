"""Thermostat request builder"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from .client_request import ClientRequest


class ThermostatState(BaseModel):
  """Thermostat State."""

  lowTemp: Optional[float] = None
  highTemp: Optional[float] = None
  mode: Optional[str] = None
  fan: Optional[str] = None
  sche: Optional[str] = None


class ThermostatRequestBuilder:  # pylint: disable=too-few-public-methods
  """Thermostat request builder"""

  @classmethod
  def set_state_request(cls, state: ThermostatState) -> ClientRequest:
    """Set device state."""
    return ClientRequest("setState", state.dict(exclude_none=True))

  @classmethod
  def set_eco_request(cls, state: str) -> ClientRequest:
    """Enable/Disable eco mode."""
    return ClientRequest("setECO", {"mode": state})
