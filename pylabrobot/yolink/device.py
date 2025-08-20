"""YoLink Device."""

from __future__ import annotations

import abc
from typing import Optional

from pydantic import BaseModel, Field, field_validator
from tenacity import RetryError

from .client import YoLinkClient
from .client_request import ClientRequest
from .const import (
  ATTR_DEVICE_ID,
  ATTR_DEVICE_MODEL_NAME,
  ATTR_DEVICE_NAME,
  ATTR_DEVICE_PARENT_ID,
  ATTR_DEVICE_SERVICE_ZONE,
  ATTR_DEVICE_TOKEN,
  ATTR_DEVICE_TYPE,
  DEVICE_MODELS_SUPPORT_MODE_SWITCHING,
)
from .device_helper import get_device_net_mode
from .endpoint import Endpoint, Endpoints
from .message_resolver import resolve_message
from .model import BRDP, BSDPHelper


class YoLinkDeviceMode(BaseModel):
  """YoLink Device Mode."""

  device_id: str = Field(alias=ATTR_DEVICE_ID)
  device_name: str = Field(alias=ATTR_DEVICE_NAME)
  device_token: str = Field(alias=ATTR_DEVICE_TOKEN)
  device_type: str = Field(alias=ATTR_DEVICE_TYPE)
  device_model_name: str = Field(alias=ATTR_DEVICE_MODEL_NAME)
  device_parent_id: Optional[str] = Field(alias=ATTR_DEVICE_PARENT_ID)
  device_service_zone: Optional[str] = Field(alias=ATTR_DEVICE_SERVICE_ZONE)

  @field_validator("device_parent_id")
  @classmethod
  def check_parent_id(cls, val: Optional[str]) -> Optional[str]:
    """Checking and replace parent id."""
    if val == "null":
      val = None
    return val


class YoLinkDevice(metaclass=abc.ABCMeta):
  """YoLink device."""

  def __init__(self, device: YoLinkDeviceMode, client: YoLinkClient) -> None:
    self.device_id: str = device.device_id
    self.device_name: str = device.device_name
    self.device_token: str = device.device_token
    self.device_type: str = device.device_type
    self.device_model_name: str = device.device_model_name
    self.device_attrs: dict | None = None
    self.parent_id: str = device.device_parent_id
    self._client: YoLinkClient = client
    self.class_mode: str = get_device_net_mode(device)
    self._state: dict | None = {}
    if device.device_service_zone is not None:
      self.device_endpoint: Endpoint = (
        Endpoints.EU.value if device.device_service_zone.startswith("eu_") else Endpoints.US.value
      )
    else:
      self.device_endpoint: Endpoint = (
        Endpoints.EU.value if device.device_model_name.endswith("-EC") else Endpoints.US.value
      )

  async def __invoke(self, method: str, params: dict | None) -> BRDP:
    """Invoke device."""
    try:
      bsdp_helper = BSDPHelper(
        self.device_id,
        self.device_token,
        f"{self.device_type}.{method}",
      )
      if params is not None:
        bsdp_helper.add_params(params)
      return await self._client.execute(url=self.device_endpoint.url, bsdp=bsdp_helper.build())
    except RetryError as err:
      raise err.last_attempt.result()

  async def get_state(self) -> BRDP:
    """Call *.getState with device to request realtime state data."""
    return await self.__invoke("getState", None)

  async def fetch_state(self) -> BRDP:
    """Call *.fetchState with device to fetch state data."""
    if self.device_type in ["Hub", "SpeakerHub"]:
      return BRDP(
        code="000000",
        desc="success",
        method="fetchState",
        data={},
      )
    state_brdp: BRDP = await self.__invoke("fetchState", None)
    resolve_message(self, state_brdp.data.get("state"), None)
    return state_brdp

  async def get_external_data(self) -> BRDP:
    """Call *.getExternalData to get device settings."""
    return await self.__invoke("getExternalData", None)

  async def call_device(self, request: ClientRequest) -> BRDP:
    """Device invoke."""
    return await self.__invoke(request.method, request.params)

  def get_paired_device_id(self) -> str | None:
    """Get device paired device id."""
    if self.parent_id is None or self.parent_id == "null":
      return None
    return self.parent_id

  def is_support_mode_switching(self) -> bool:
    """Check if the device supports mode switching."""
    return self.device_model_name in DEVICE_MODELS_SUPPORT_MODE_SWITCHING
