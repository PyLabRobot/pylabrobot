"""YoLink Basic Model."""

from typing import Any, Dict, Optional

from pydantic import BaseModel

from .exception import (
  YoLinkAuthFailError,
  YoLinkClientError,
  YoLinkDeviceConnectionFailed,
  YoLinkUnSupportedMethodError,
)


class BRDP(BaseModel):
  """BRDP of YoLink API."""

  code: Optional[str] = None
  desc: Optional[str] = None
  method: Optional[str] = None
  data: Dict[str, Any] = None
  event: Optional[str] = None

  def check_response(self):
    """Check API Response."""
    if self.code != "000000":
      if self.code == "000103":
        raise YoLinkAuthFailError(self.code, self.desc)
      if self.code == "000201":
        raise YoLinkDeviceConnectionFailed(self.code, self.desc)
      if self.code == "010203":
        raise YoLinkUnSupportedMethodError(self.code, self.desc)
      raise YoLinkClientError(self.code, self.desc)


class BSDPHelper:
  """YoLink API -> BSDP Builder."""

  _bsdp: Dict

  def __init__(self, device_id: str, device_token: str, method: str):
    """Constanst."""
    self._bsdp = {"method": method, "params": {}}
    if device_id is not None:
      self._bsdp["targetDevice"] = device_id
      self._bsdp["token"] = device_token

  def add_params(self, params: Dict):
    """Build params of BSDP."""
    self._bsdp["params"].update(params)
    return self

  def build(self) -> Dict:
    """Generate BSDP."""
    return self._bsdp
