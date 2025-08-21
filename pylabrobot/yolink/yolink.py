"""YoLink backend implementation for PyLabRobot devices."""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import aiohttp
from yolink.auth_mgr import YoLinkAuthMgr
from yolink.device import YoLinkDevice
from yolink.home_manager import YoLinkHome
from yolink.message_listener import MessageListener
from yolink.outlet_request_builder import OutletRequestBuilder

logger = logging.getLogger(__name__)


class YoLinkTokenManager:
  """Manages YoLink API token lifecycle."""

  def __init__(self, api_host: str = "https://api.yosmart.com"):
    self.api_host = api_host
    self._access_token: Optional[str] = None
    self._refresh_token: Optional[str] = None
    self._token_expires_at: Optional[datetime] = None

  async def get_access_token_from_credentials(
    self, session: aiohttp.ClientSession, client_id: str, client_secret: str
  ) -> Dict[str, Any]:
    """Get access token using UAC credentials."""
    token_url = f"{self.api_host}/open/yolink/token"

    payload = {
      "grant_type": "client_credentials",
      "client_id": client_id,
      "client_secret": client_secret,
    }

    try:
      async with session.post(token_url, data=payload) as response:
        response.raise_for_status()
        token_data = await response.json()

        self._access_token = token_data.get("access_token")
        self._refresh_token = token_data.get("refresh_token")

        # Calculate expiration time
        expires_in = token_data.get("expires_in", 3600)  # Default 1 hour
        self._token_expires_at = datetime.now() + timedelta(
          seconds=expires_in - 300
        )  # 5 min buffer

        logger.info("Successfully obtained access token from credentials")
        return token_data

    except aiohttp.ClientError as e:
      logger.error(f"Failed to get access token from credentials: {e}")
      raise

  async def refresh_access_token(
    self, session: aiohttp.ClientSession, client_id: str
  ) -> Dict[str, Any]:
    """Refresh access token using refresh token."""
    if not self._refresh_token:
      raise ValueError("No refresh token available")

    token_url = f"{self.api_host}/open/yolink/token"

    payload = {
      "grant_type": "refresh_token",
      "client_id": client_id,
      "refresh_token": self._refresh_token,
    }

    try:
      async with session.post(token_url, data=payload) as response:
        response.raise_for_status()
        token_data = await response.json()

        self._access_token = token_data.get("access_token")
        # Refresh token might be updated
        if "refresh_token" in token_data:
          self._refresh_token = token_data["refresh_token"]

        # Calculate expiration time
        expires_in = token_data.get("expires_in", 3600)
        self._token_expires_at = datetime.now() + timedelta(seconds=expires_in - 300)

        logger.info("Successfully refreshed access token")
        return token_data

    except aiohttp.ClientError as e:
      logger.error(f"Failed to refresh access token: {e}")
      raise

  def is_token_expired(self) -> bool:
    """Check if the current token is expired or about to expire."""
    if not self._token_expires_at:
      return True
    return datetime.now() >= self._token_expires_at

  @property
  def access_token(self) -> Optional[str]:
    """Get the current access token."""
    return self._access_token

  @property
  def refresh_token(self) -> Optional[str]:
    """Get the current refresh token."""
    return self._refresh_token


class YoLinkAuthManager(YoLinkAuthMgr):
  """Authentication manager for YoLink API."""

  def __init__(self, session: aiohttp.ClientSession, token_manager: YoLinkTokenManager):
    super().__init__(session)
    self._token_manager = token_manager

  def access_token(self) -> str:
    return self._token_manager.access_token or ""

  async def check_and_refresh_token(self) -> str:
    """Check token validity and refresh if needed."""
    if self._token_manager.is_token_expired():
      logger.info("Token expired, attempting refresh")
      # This would need additional client_id parameter - handle in YoLink class
      pass
    return self._token_manager.access_token or ""


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

  def __init__(
    self,
    api_key: Optional[str] = None,
    client_id: Optional[str] = None,
    client_secret: Optional[str] = None,
    refresh_token: Optional[str] = None,
    api_host: str = "https://api.yosmart.com",
  ):
    """Initialize YoLink backend.

    Args:
        api_key: Direct API access token (if already obtained)
        client_id: Your UAID (required if using credentials or refresh token)
        client_secret: Your UAC Secret Key (for getting initial token)
        refresh_token: Your refresh token (for token refresh)
        api_host: API host URL (default: https://api.yosmart.com)

    Note:
        You must provide either:
        - api_key (for direct token usage)
        - client_id + client_secret (for credential-based auth)
    """
    self.client_id = client_id
    self.client_secret = client_secret
    self.api_host = api_host

    # Initialize token manager
    self._token_manager = YoLinkTokenManager(api_host)

    # If direct API key provided, use it
    if api_key:
      self._token_manager._access_token = api_key

    # If refresh token provided, store it
    if refresh_token:
      self._token_manager._refresh_token = refresh_token

    self._session: Optional[aiohttp.ClientSession] = None
    self._auth_mgr: Optional[YoLinkAuthManager] = None
    self._home: Optional[YoLinkHome] = None
    self._listener: Optional[YoLinkMessageListener] = None
    self._is_setup = False

  @classmethod
  def from_credentials(
    cls, client_id: str, client_secret: str, api_host: str = "https://api.yosmart.com"
  ):
    """Create YoLink instance using UAC credentials.

    Args:
        client_id: Your UAID
        client_secret: Your UAC Secret Key
        api_host: API host URL

    Returns:
        YoLink instance configured for credential-based authentication
    """
    return cls(client_id=client_id, client_secret=client_secret, api_host=api_host)

  @classmethod
  def from_access_token(cls, access_token: str, api_host: str = "https://api.yosmart.com"):
    """Create YoLink instance using direct access token.

    Args:
        access_token: Direct API access token
        api_host: API host URL

    Returns:
        YoLink instance configured for direct token usage
    """
    return cls(api_key=access_token, api_host=api_host)

  async def setup(self) -> None:
    """Set up the YoLink backend connection."""
    if self._is_setup:
      logger.warning("YoLink backend already set up")
      return

    try:
      # Create HTTP session
      self._session = aiohttp.ClientSession()

      # Ensure we have a valid access token
      await self._ensure_access_token()

      # Initialize authentication manager
      self._auth_mgr = YoLinkAuthManager(self._session, self._token_manager)

      # Initialize message listener
      self._listener = YoLinkMessageListener()

      # Initialize home manager
      self._home = YoLinkHome()
      await self._home.async_setup(self._auth_mgr, self._listener)

      self._is_setup = True
      logger.info("YoLink backend set up successfully")

    except Exception as e:
      logger.error(f"Failed to set up YoLink backend: {e}")
      await self.stop()
      raise

  async def _ensure_access_token(self) -> None:
    """Ensure we have a valid access token."""
    if not self.client_id:
      if not self._token_manager.access_token:
        raise ValueError("No access token or client credentials provided")
      return

    # If token is expired or missing, get/refresh it
    if self._token_manager.is_token_expired() or not self._token_manager.access_token:
      if self.client_secret:
        # Get token using credentials
        await self._token_manager.get_access_token_from_credentials(
          self._session, self.client_id, self.client_secret
        )
      elif self._token_manager.refresh_token:
        # Refresh using refresh token
        await self._token_manager.refresh_access_token(self._session, self.client_id)
      else:
        raise ValueError("No valid authentication method available")

  async def refresh_token_if_needed(self) -> None:
    """Check and refresh token if needed (can be called externally)."""
    if self._token_manager.is_token_expired() and self._session:
      await self._ensure_access_token()

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
    if not self._home:
      return []
    devices = list(self._home.get_devices())
    return devices

  def _ensure_setup(self) -> None:
    """Ensure the backend is set up before operations."""
    if not self._is_setup:
      raise RuntimeError("YoLink backend not set up. Call setup() first.")

  @property
  def is_setup(self) -> bool:
    """Check if the backend is set up."""
    return self._is_setup

  @property
  def current_access_token(self) -> Optional[str]:
    """Get the current access token."""
    return self._token_manager.access_token

  @property
  def current_refresh_token(self) -> Optional[str]:
    """Get the current refresh token."""
    return self._token_manager.refresh_token


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
      temperature = getattr(state, "data", {}).get("state", {}).get("temperature")

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
      humidity = getattr(state, "data", {}).get("state", {}).get("humidity")

      if humidity is None:
        raise ValueError("Humidity data not available from device")

      logger.debug(f"Humidity reading: {humidity}%")
      return float(humidity)

    except Exception as e:
      logger.error(f"Failed to get humidity: {e}")
      raise

  async def get_battery_level(self) -> Optional[int]:
    """Get battery level if available.

    Returns:
        Battery level percentage (0-100) or None if not available
    """
    self._ensure_device_ready()

    try:
      state = await self._device.get_state()
      battery = getattr(state, "data", {}).get("state", {}).get("battery")

      if battery is not None:
        logger.debug(f"Battery level: {battery}%")
        return int(battery)
      return None

    except Exception as e:
      logger.error(f"Failed to get battery level: {e}")
      return None

  async def get_all_readings(self) -> Dict[str, Any]:
    """Get all available sensor readings.

    Returns:
        Dictionary with all available sensor data
    """
    self._ensure_device_ready()

    try:
      state = await self._device.get_state()
      sensor_data = getattr(state, "data", {})
      logger.debug(f"All sensor readings: {sensor_data}")
      return sensor_data

    except Exception as e:
      logger.error(f"Failed to get sensor readings: {e}")
      raise

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


class Outlet:
  """YoLink outlet device wrapper for PyLabRobot."""

  def __init__(self, backend: YoLink, outlet_name: str):
    """Initialize YoLink outlet.

    Args:
        backend: YoLink backend instance
        outlet_name: Name of the specific outlet device
    """
    self.backend = backend
    self.outlet_name = outlet_name
    self._device: Optional[YoLinkDevice] = None

  async def setup(self) -> None:
    """Set up the outlet device."""
    # Ensure backend is set up
    if not self.backend.is_setup:
      await self.backend.setup()

    # Find the specific outlet device
    devices = self.backend._get_all_devices()
    self._device = None

    for device in devices:
      if device.device_name == self.outlet_name:
        self._device = device
        break

    if self._device is None:
      available_devices = [d.device_name for d in devices]
      raise ValueError(
        f"Outlet '{self.outlet_name}' not found. " f"Available devices: {available_devices}"
      )

  async def turn_on(self, outlet_index: int = 0) -> None:
    """Turn on a specific outlet.

    Args:
        outlet_index: Index of the outlet to turn on (0-based)
    """
    self._ensure_device_ready()

    try:
      request = OutletRequestBuilder.set_state_request("open", outlet_index)
      await self._device.call_device(request)

      logger.info(f"Turned on outlet {outlet_index}")

    except Exception as e:
      logger.error(f"Failed to turn on outlet {outlet_index}: {e}")
      raise

  async def turn_off(self, outlet_index: int = 0) -> None:
    """Turn off a specific outlet.

    Args:
        outlet_index: Index of the outlet to turn off (0-based)
    """
    self._ensure_device_ready()

    try:
      request = OutletRequestBuilder.set_state_request("close", outlet_index)
      await self._device.call_device(request)

      logger.info(f"Turned off outlet {outlet_index}")

    except Exception as e:
      logger.error(f"Failed to turn off outlet {outlet_index}: {e}")
      raise

  async def get_status(self, outlet_index: int = -1) -> bool:
    """Get the status of a specific outlet.

    Args:
        outlet_index: Index of the outlet to check. -1 returns all outlet states

    Returns:
        True if outlet is on, False if off
    """
    self._ensure_device_ready()

    try:
      state = await self._device.get_state()
      outlet_data = getattr(state, "data", {}).get("state", {})

      if outlet_index > 7 or outlet_index < -1:
        raise ValueError("Invalid outlet index")

      if outlet_index == -1:
        return outlet_data
      return outlet_data[outlet_index]

    except Exception as e:
      logger.error(f"Failed to get outlet {outlet_index} status: {e}")
      raise

  async def stop(self) -> None:
    """Stop the outlet and clean up resources."""
    logger.info(f"Stopping outlet '{self.outlet_name}'")
    await self.backend.stop()
    self._device = None

  def _ensure_device_ready(self) -> None:
    """Ensure the device is ready for operations."""
    if self._device is None:
      raise RuntimeError("Outlet not set up. Call setup() first.")
    if not self.backend.is_setup:
      raise RuntimeError("Backend not set up. Call setup() first.")

  @property
  def device_name(self) -> str:
    """Get the device name."""
    return self.outlet_name

  @property
  def device_id(self) -> Optional[str]:
    """Get the device ID if available."""
    return self._device.device_id if self._device else None
