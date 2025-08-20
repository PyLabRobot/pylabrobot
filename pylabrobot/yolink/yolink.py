"""YoLink backend implementation for PyLabRobot devices."""

import asyncio
import logging
from typing import Any, Dict, List, Optional

import aiohttp

from .auth_mgr import YoLinkAuthMgr
from .device import YoLinkDevice
from .home_manager import YoLinkHome
from .message_listener import MessageListener
from .outlet_request_builder import OutletRequestBuilder

logger = logging.getLogger(__name__)


class YoLinkAuthMgr(YoLinkAuthMgr):
  """Authentication manager for YoLink API."""

  def __init__(self, session: aiohttp.ClientSession, access_token: str):
    super().__init__(session)
    self._access_token = access_token

  def access_token(self) -> str:
    return self._access_token

  async def check_and_refresh_token(self) -> str:
    # TODO: Implement token refresh logic here
    return self._access_token


class YoLinkMessageListener(MessageListener):
  """Message listener for YoLink device updates."""

  def __init__(self):
    self._callbacks: Dict[str, callable] = {}

  def register_callback(self, device_id: str, callback: callable):
    """Register a callback for device updates."""
    self._callbacks[device_id] = callback

  def on_message(self, device: YoLinkDevice, msg_data: Dict[str, Any]) -> None:
    logger.debug(f"Device {device.device_name} ({device.device_id}): {msg_data}")
    if device.device_id in self._callbacks:
      self._callbacks[device.device_id](msg_data)


class YoLink:
  """YoLink backend for PyLabRobot devices."""

  def __init__(self, api_key: str):
    """Initialize YoLink backend.

    Args:
        api_key: YoLink API access token
    """
    self.api_key = api_key
    self._session: Optional[aiohttp.ClientSession] = None
    self._auth_mgr: Optional[YoLinkAuthMgr] = None
    self._home: Optional[YoLinkHome] = None
    self._listener: Optional[YoLinkMessageListener] = None
    self._is_setup = False

  async def setup(self) -> None:
    """Set up the YoLink backend connection."""
    if self._is_setup:
      logger.warning("YoLink backend already set up")
      return

    try:
      # Create HTTP session
      self._session = aiohttp.ClientSession()

      # Initialize authentication manager
      self._auth_mgr = YoLinkAuthMgr(self._session, self.api_key)

      # Initialize message listener
      self._listener = YoLinkMessageListener()

      # Initialize home manager
      self._home = YoLinkHome()
      await self._home.async_setup(self._auth_mgr, self._listener)

      self._is_setup = True
      logger.info(f"YoLink backend set up")

    except Exception as e:
      logger.error(f"Failed to set up YoLink backend: {e}")
      await self.stop()
      raise

  async def stop(self) -> None:
    """Stop the YoLink backend and clean up resources."""
    if not self._is_setup:
      return

    try:
      if self._home:
        await self._home.async_unload()
        self._home = None

      if self._session:
        await self._session.close()
        self._session = None

      self._auth_mgr = None
      self._listener = None
      self._is_setup = False

      logger.info("YoLink backend stopped")

    except Exception as e:
      logger.error(f"Error stopping YoLink backend: {e}")

  def _get_all_devices(self) -> List[YoLinkDevice]:
    """Get all devices in the home to call later."""
    devices = list(self._home.get_devices())
    return devices

  def _ensure_setup(self) -> None:
    """Ensure the backend is set up before operations."""
    if not self._is_setup or self._device is None:
      raise RuntimeError("YoLink backend not set up. Call setup() first.")

  @property
  def is_setup(self) -> bool:
    """Check if the backend is set up."""
    return self._is_setup


class Sensor:
  """YoLink sensor device wrapper for PyLabRobot."""

  def __init__(self, backend: YoLink, sensor_name: str):
    """Initialize YoLink sensor.

    Args:
        backend: YoLink backend instance
        sensor_name: Name of the specific sensor device
    """
    self.backend = backend
    self.sensor_name = sensor_name
    self._device: Optional[YoLinkDevice] = None

  async def setup(self) -> None:
    """Set up the sensor device."""
    # Ensure backend is set up
    if not self.backend.is_setup:
      await self.backend.setup()

    # Find the specific sensor device
    devices = self.backend._get_all_devices()
    self._device = None

    for device in devices:
      if device.device_name == self.sensor_name:
        self._device = device
        break

    if self._device is None:
      available_devices = [d.device_name for d in devices]
      raise ValueError(
        f"Sensor '{self.sensor_name}' not found. " f"Available devices: {available_devices}"
      )

  async def get_temperature(self) -> float:
    """Get temperature reading from sensor device.

    Returns:
        Temperature in degrees Celsius
    """
    self._ensure_device_ready()

    try:
      state = await self._device.get_state()
      temperature = state.data["state"]["temperature"]

      if temperature is None:
        raise ValueError("Temperature data not available from device")

      logger.debug(f"Temperature reading: {temperature}°C")
      return float(temperature)

    except Exception as e:
      logger.error(f"Failed to get temperature: {e}")
      raise

  async def get_humidity(self) -> float:
    """Get humidity reading from sensor device.

    Returns:
        Relative humidity percentage (0-100)
    """
    self._ensure_device_ready()

    try:
      state = await self._device.get_state()
      humidity = state.data["state"]["humidity"]

      if humidity is None:
        raise ValueError("Humidity data not available from device")

      logger.debug(f"Humidity reading: {humidity}%")
      return float(humidity)

    except Exception as e:
      logger.error(f"Failed to get humidity: {e}")
      raise

  # async def get_battery_level(self) -> Optional[int]:
  #     """Get battery level if available.

  #     Returns:
  #         Battery level percentage (0-100) or None if not available
  #     """
  #     self._ensure_device_ready()

  #     try:
  #         state = await self._device.get_state()
  #         battery_data = state.get('data', {}).get('state', {})
  #         battery = battery_data.get('battery')

  #         if battery is not None:
  #             logger.debug(f"Battery level: {battery}%")
  #             return int(battery)
  #         return None

  #     except Exception as e:
  #         logger.error(f"Failed to get battery level: {e}")
  #         return None

  # async def get_all_readings(self) -> Dict[str, Any]:
  #     """Get all available sensor readings.

  #     Returns:
  #         Dictionary with all available sensor data
  #     """
  #     self._ensure_device_ready()

  #     try:
  #         state = await self._device.get_state()
  #         sensor_data = state.get('data', {}).get('state', {})
  #         logger.debug(f"All sensor readings: {sensor_data}")
  #         return sensor_data

  #     except Exception as e:
  #         logger.error(f"Failed to get sensor readings: {e}")
  #         raise

  async def stop(self) -> None:
    """Stop the sensor and clean up resources."""
    logger.info(f"Stopping sensor '{self.sensor_name}'")
    await self.backend.stop()
    self._device = None

  def _ensure_device_ready(self) -> None:
    """Ensure the device is ready for operations."""
    if self._device is None:
      raise RuntimeError("Sensor not set up. Call setup() first.")
    if not self.backend.is_setup:
      raise RuntimeError("Backend not set up. Call setup() first.")

  @property
  def device_name(self) -> str:
    """Get the device name."""
    return self.sensor_name

  @property
  def device_id(self) -> Optional[str]:
    """Get the device ID if available."""
    return self._device.device_id if self._device else None

  @property
  def is_online(self) -> bool:
    """Check if the device is online."""
    if self._device is None:
      return False
    # Implement based on YoLinkDevice API
    return getattr(self._device, "is_online", True)


# class Outlet:
#     """YoLink outlet device wrapper for PyLabRobot."""

#     def __init__(self, backend: YoLink, outlet_name: str):
#         """Initialize YoLink outlet.

#         Args:
#             backend: YoLink backend instance
#             outlet_name: Name of the specific outlet device
#         """
#         self.backend = backend
#         self.outlet_name = outlet_name
#         self._device: Optional[YoLinkDevice] = None

#     async def setup(self) -> None:
#         """Set up the outlet device."""
#         # Ensure backend is set up
#         if not self.backend.is_setup:
#             await self.backend.setup()

#         # Find the specific outlet device
#         devices = self.backend._get_all_devices()
#         self._device = None

#         for device in devices:
#             if device.device_name == self.outlet_name:
#                 self._device = device
#                 break

#         if self._device is None:
#             available_devices = [d.device_name for d in devices]
#             raise ValueError(
#                 f"Outlet '{self.outlet_name}' not found. "
#                 f"Available devices: {available_devices}"
#             )

#         # Set the device in backend for compatibility
#         self.backend._device = self._device
#         logger.info(f"Outlet '{self.outlet_name}' set up successfully")

#     async def turn_on(self, outlet_index: int = 0) -> None:
#         """Turn on a specific outlet.

#         Args:
#             outlet_index: Index of the outlet to turn on (0-based)
#         """
#         self._ensure_device_ready()

#         try:
#             if hasattr(self._device, 'turn_on'):
#                 await self._device.turn_on(outlet_index)
#             else:
#                 # Use OutletRequestBuilder for custom commands
#                 request_builder = OutletRequestBuilder()
#                 command = request_builder.build_turn_on_request(outlet_index)
#                 await self._device.send_command(command)

#             logger.info(f"Turned on outlet {outlet_index}")

#         except Exception as e:
#             logger.error(f"Failed to turn on outlet {outlet_index}: {e}")
#             raise

#     async def turn_off(self, outlet_index: int = 0) -> None:
#         """Turn off a specific outlet.

#         Args:
#             outlet_index: Index of the outlet to turn off (0-based)
#         """
#         self._ensure_device_ready()

#         try:
#             if hasattr(self._device, 'turn_off'):
#                 await self._device.turn_off(outlet_index)
#             else:
#                 # Use OutletRequestBuilder for custom commands
#                 request_builder = OutletRequestBuilder()
#                 command = request_builder.build_turn_off_request(outlet_index)
#                 await self._device.send_command(command)

#             logger.info(f"Turned off outlet {outlet_index}")

#         except Exception as e:
#             logger.error(f"Failed to turn off outlet {outlet_index}: {e}")
#             raise

#     async def get_status(self, outlet_index: int = 0) -> bool:
#         """Get the status of a specific outlet.

#         Args:
#             outlet_index: Index of the outlet to check

#         Returns:
#             True if outlet is on, False if off
#         """
#         self._ensure_device_ready()

#         try:
#             state = await self._device.get_state()
#             outlet_data = state.get('data', {}).get('state', {})

#             # Handle different outlet state formats
#             if 'outlets' in outlet_data:
#                 outlets = outlet_data['outlets']
#                 if outlet_index < len(outlets):
#                     return outlets[outlet_index].get('on', False)
#             elif f'outlet_{outlet_index}' in outlet_data:
#                 return outlet_data[f'outlet_{outlet_index}'].get('on', False)
#             elif 'power' in outlet_data and outlet_index == 0:
#                 return outlet_data['power']

#             return False

#         except Exception as e:
#             logger.error(f"Failed to get outlet {outlet_index} status: {e}")
#             raise

#     async def stop(self) -> None:
#         """Stop the outlet and clean up resources."""
#         logger.info(f"Stopping outlet '{self.outlet_name}'")
#         await self.backend.stop()
#         self._device = None

#     def _ensure_device_ready(self) -> None:
#         """Ensure the device is ready for operations."""
#         if self._device is None:
#             raise RuntimeError("Outlet not set up. Call setup() first.")
#         if not self.backend.is_setup:
#             raise RuntimeError("Backend not set up. Call setup() first.")

#     @property
#     def device_name(self) -> str:
#         """Get the device name."""
#         return self.outlet_name

#     @property
#     def device_id(self) -> Optional[str]:
#         """Get the device ID if available."""
#         return self._device.device_id if self._device else None
