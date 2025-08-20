"""YoLink home manager."""

from __future__ import annotations

import logging
from typing import Any

from .auth_mgr import YoLinkAuthMgr
from .client import YoLinkClient
from .const import ATTR_DEVICE_WATER_DEPTH_SENSOR
from .device import YoLinkDevice, YoLinkDeviceMode
from .endpoint import Endpoint, Endpoints
from .exception import YoLinkClientError, YoLinkUnSupportedMethodError
from .message_listener import MessageListener
from .model import BRDP
from .mqtt_client import YoLinkMqttClient

_LOGGER = logging.getLogger(__name__)

has_external_data_devices = [ATTR_DEVICE_WATER_DEPTH_SENSOR]


class YoLinkHome:
  """YoLink home manager."""

  def __init__(self) -> None:
    """Init YoLink Home Manager."""
    self._home_devices: dict[str, YoLinkDevice] = {}
    self._http_client: YoLinkClient = None
    self._endpoints: dict[str, Endpoint] = {}
    self._mqtt_clients: dict[str, YoLinkMqttClient] = {}
    self._message_listener: MessageListener = None

  async def async_setup(self, auth_mgr: YoLinkAuthMgr, listener: MessageListener) -> None:
    """Init YoLink home."""
    if not auth_mgr:
      raise YoLinkClientError("-1001", "setup failed, auth_mgr is required!")
    if not listener:
      raise YoLinkClientError("-1002", "setup failed, message listener is required!")
    self._http_client = YoLinkClient(auth_mgr)
    home_info: BRDP = await self.async_get_home_info()
    # load home devices
    await self.async_load_home_devices()
    # setup yolink mqtt connection
    self._message_listener = listener
    # setup yolink mqtt clients
    for endpoint in self._endpoints.values():
      endpoint_mqtt_client = YoLinkMqttClient(
        auth_manager=auth_mgr,
        endpoint=endpoint.name,
        broker_host=endpoint.mqtt_broker_host,
        broker_port=endpoint.mqtt_broker_port,
        home_devices=self._home_devices,
      )
      await endpoint_mqtt_client.connect(home_info.data["id"], self._message_listener)
      self._mqtt_clients[endpoint.name] = endpoint_mqtt_client

  async def async_unload(self) -> None:
    """Unload YoLink home."""
    self._home_devices = {}
    self._http_client = None
    for endpoint, client in self._mqtt_clients.items():
      _LOGGER.info(
        "[%s] shutting down yolink mqtt client.",
        endpoint,
      )
      await client.disconnect()
      _LOGGER.info(
        "[%s] yolink mqtt client disconnected.",
        endpoint,
      )
    self._message_listener = None
    self._mqtt_clients = {}

  async def async_get_home_info(self, **kwargs: Any) -> BRDP:
    """Get home general information."""
    return await self._http_client.execute(
      url=Endpoints.US.value.url, bsdp={"method": "Home.getGeneralInfo"}, **kwargs
    )

  async def async_load_home_devices(self, **kwargs: Any) -> dict[str, YoLinkDevice]:
    """Get home devices."""
    # sync eu devices, will remove in future
    eu_response: BRDP = await self._http_client.execute(
      url=Endpoints.EU.value.url, bsdp={"method": "Home.getDeviceList"}, **kwargs
    )
    response: BRDP = await self._http_client.execute(
      url=Endpoints.US.value.url, bsdp={"method": "Home.getDeviceList"}, **kwargs
    )
    eu_dev_tokens = {}
    for eu_device in eu_response.data["devices"]:
      eu_dev_tokens[eu_device["deviceId"]] = eu_device["token"]
    for _device in response.data["devices"]:
      _yl_device = YoLinkDevice(YoLinkDeviceMode(**_device), self._http_client)
      if _yl_device.device_endpoint == Endpoints.EU.value:
        # sync eu device token
        _yl_device.device_token = eu_dev_tokens.get(_yl_device.device_id)
      self._endpoints[_yl_device.device_endpoint.name] = _yl_device.device_endpoint
      if _yl_device.device_type in has_external_data_devices:
        try:
          dev_external_data_resp = await _yl_device.get_external_data()
          _yl_device.device_attrs = dev_external_data_resp.data.get("extData")
        except YoLinkUnSupportedMethodError:
          _LOGGER.debug(
            "getExternalData is not supported for: %s",
            _yl_device.device_type,
          )
      self._home_devices[_device["deviceId"]] = _yl_device

    return self._home_devices

  def get_devices(self) -> list[YoLinkDevice]:
    """Get home devices."""
    return self._home_devices.values()

  def get_device(self, device_id: str) -> YoLinkDevice | None:
    """Get home device via device id."""
    return self._home_devices.get(device_id)
