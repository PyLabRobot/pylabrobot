import asyncio
import logging
import struct
import time
from typing import Any, Dict, List, Optional, Tuple

import usb.core

from pylabrobot.io.usb import USB

from .enums import DEVICE_ENDPOINTS, VENDOR_ID, SparkDevice, SparkEndpoint
from .spark_packet_parser import PACKET_TYPE, parse_single_spark_packet

logger = logging.getLogger(__name__)


class SparkReaderAsync:
  def __init__(self, vid: int = VENDOR_ID) -> None:
    self.vid: int = vid
    self.devices: Dict[SparkDevice, USB] = {}
    self.seq_num: int = 0
    self.lock: asyncio.Lock = asyncio.Lock()
    self.msgs: List[Any] = []

  async def connect(self) -> None:
    logger.info("Scanning for devices with VID=%s...", hex(self.vid))

    for device_type in SparkDevice:
      if device_type in self.devices:
        continue

      try:

        def configure(dev: usb.core.Device) -> None:
          try:
            if dev.is_kernel_driver_active(0):
              logger.debug("Detaching kernel driver from %s interface 0", device_type.name)
              dev.detach_kernel_driver(0)
          except usb.core.USBError as e:
            logger.error("Error detaching kernel driver from %s: %s", device_type.name, e)

          # Note: calling set_configuration(0) twice before switching to configuration 1
          # is intentional. Some Tecan Spark devices require a double reset to config 0
          # to reliably accept configuration 1 after startup.
          dev.set_configuration(0)
          dev.set_configuration(0)
          dev.set_configuration(1)

        endpoints: Optional[Dict[str, SparkEndpoint]] = DEVICE_ENDPOINTS.get(device_type)
        if endpoints is None:
          logger.warning("No endpoints defined for %s, skipping.", device_type.name)
          continue

        reader = USB(
          id_vendor=self.vid,
          id_product=device_type.value,
          configuration_callback=configure,
          max_workers=16,
          read_endpoint_address=endpoints["read_status"].value,
          write_endpoint_address=endpoints["write"].value,
        )

        await reader.setup(empty_buffer=False)  # type: ignore[no-untyped-call]
        self.devices[device_type] = reader
        logger.info("Successfully configured %s", device_type.name)

      except Exception as e:
        logger.error("Error configuring %s: %s", device_type.name, e)

    if not self.devices:
      raise ValueError(f"Failed to connect to any known Spark devices for VID={hex(self.vid)}")

    logger.info("Successfully connected to %d devices.", len(self.devices))

  def _calculate_checksum(self, data: bytes) -> int:
    checksum = 0
    for byte in data:
      checksum ^= byte
    return checksum

  async def _read_packet_in_executor(
    self,
    reader: USB,
    endpoint: Optional[int] = None,
    size: Optional[int] = None,
    timeout: Optional[float] = None,
  ) -> Optional[bytes]:
    loop = asyncio.get_running_loop()
    if reader._executor is None:
      raise RuntimeError("Call setup() first.")

    start_time = time.monotonic()

    while True:
      # Calculate remaining timeout if a timeout is set
      current_timeout = timeout
      if timeout is not None:
        elapsed = time.monotonic() - start_time
        if elapsed > timeout:
          return None  # Timeout
        current_timeout = timeout - elapsed

      data = await loop.run_in_executor(
        reader._executor,
        lambda: reader._read_packet(size=size, timeout=current_timeout, endpoint=endpoint),
      )

      if data is None:
        return None

      # Validation Logic
      if len(data) < 5:  # Header(4) + Checksum(1) min
        logger.warning("Packet too short (%d), ignoring: %s", len(data), data.hex())
        continue

      # Check indicator
      if data[0] not in PACKET_TYPE:
        logger.warning("Invalid packet indicator %d, ignoring: %s", data[0], data.hex())
        continue

      # Check length
      # bytes 2-3 are payload length (Big Endian)
      payload_len = (data[2] << 8) | data[3]
      expected_len = 4 + payload_len + 1  # Header + Payload + Checksum
      if len(data) < expected_len:
        logger.warning(
          "Packet data shorter than payload length (got %d, expected %d), ignoring: %s",
          len(data),
          expected_len,
          data.hex(),
        )
        continue

      return data

  async def send_command(
    self,
    command_str: str,
    device_type: SparkDevice = SparkDevice.PLATE_TRANSPORT,
    timeout: float = 60.0,
  ) -> Optional[str]:
    if device_type not in self.devices:
      raise RuntimeError(f"Device type {device_type} not connected.")

    reader = self.devices[device_type]
    endpoints = DEVICE_ENDPOINTS.get(device_type)
    if not endpoints:
      raise ValueError(f"No endpoints defined for {device_type}")

    async with self.lock:
      # Set up read task before sending command
      read_task = self._init_read(reader)
      await asyncio.sleep(0.01)

      response_task = asyncio.create_task(self._get_response(read_task, reader, timeout=timeout))

      try:
        logger.debug("Sending to %s: %s", device_type.name, command_str)
        payload = command_str.encode("ascii")
        payload_len = len(payload)

        header = bytes([0x01, self.seq_num]) + struct.pack(">H", payload_len)
        message = header + payload + bytes([self._calculate_checksum(header + payload)])
        self.seq_num = (self.seq_num + 1) % 256

        await reader.write(message)
        logger.debug("Sent message to %s: %s", device_type.name, message.hex())

        # Wait for response
        if not response_task.done():
          await response_task

        response = response_task.result()
        logger.debug("Response: %s", response)
        return (
          response["payload"]["message"]
          if response and "payload" in response and "message" in response["payload"]
          else None
        )
      except Exception as e:
        logger.error("Error in send_command to %s: %s", device_type.name, e, exc_info=True)
        raise
      finally:
        if not response_task.done():
          response_task.cancel()
          try:
            await response_task
          except asyncio.CancelledError:
            pass

  def _init_read(
    self,
    reader: USB,
    count: int = 512,
    read_timeout: int = 2000,
  ) -> "asyncio.Future[Any]":
    # Convert read_timeout from milliseconds to seconds for USB class.
    return asyncio.ensure_future(
      self._read_packet_in_executor(
        reader=reader,
        endpoint=None,
        size=count,
        timeout=read_timeout / 1000.0,
      )
    )

  async def _get_response(
    self,
    read_task: "asyncio.Future[Any]",
    reader: USB,
    timeout: float = 60.0,
  ) -> Optional[Dict[str, Any]]:
    try:
      data = await read_task

      if data is None:
        logger.warning("Read task returned None")
        return None

      data_bytes = bytes(data)
      logger.debug("Read task completed (%d bytes): %s", len(data_bytes), data_bytes.hex())

      parsed = {}
      if len(data_bytes) > 0:
        try:
          parsed = parse_single_spark_packet(data_bytes)
        except ValueError as e:
          logger.warning("Failed to parse packet: %s", e)
          # Treat as not ready/retry

      if parsed.get("type") == "RespMessage":
        self.msgs.append(parsed["payload"])
      elif parsed.get("type") == "RespError":
        raise Exception(parsed)

      deadline = time.monotonic() + timeout
      while parsed.get("type") != "RespReady" and time.monotonic() < deadline:
        try:
          await asyncio.sleep(0.01)
          logger.debug("Still busy, retrying... time left: %.1fs", deadline - time.monotonic())

          resp = await self._read_packet_in_executor(
            reader=reader, endpoint=None, size=512, timeout=0.02
          )

          if resp:
            logger.debug("Read task completed (%d bytes): %s", len(resp), bytes(resp).hex())
            parsed = parse_single_spark_packet(bytes(resp))
            logger.debug("Parsed: %s", parsed)
            if parsed.get("type") == "RespMessage":
              self.msgs.append(parsed["payload"])
            elif parsed.get("type") == "RespError":
              raise Exception(parsed)
        except Exception as e:
          logger.error("Error in get_response retry: %s", e, exc_info=True)
      if parsed.get("type") != "RespReady":
        logger.warning('Timeout waiting for "RespReady" response')
      return parsed

    except asyncio.CancelledError:
      logger.warning("Read task was cancelled")
      return None
    except Exception as e:
      logger.error("Error in get_response: %s", e, exc_info=True)
      return None

  def clear_messages(self) -> None:
    """Clear the list of recorded RespMessage payloads."""
    self.msgs = []

  async def start_background_read(
    self,
    device_type: SparkDevice,
    read_timeout: int = 100,
  ) -> Tuple[Optional["asyncio.Task[None]"], Optional[asyncio.Event], Optional[List[bytes]]]:
    if device_type not in self.devices:
      logger.error("Device type %s not connected.", device_type)
      return None, None, None

    reader = self.devices[device_type]
    stop_event = asyncio.Event()
    results: List[bytes] = []
    endpoints = DEVICE_ENDPOINTS.get(device_type)
    if endpoints is None:
      logger.error("No endpoints for %s", device_type.name)
      return None, None, None
    endpoint = endpoints["read_data"]

    async def background_reader() -> None:
      logger.info(
        "Starting background reader for %s %s (0x%02x)",
        device_type.name,
        endpoint.name,
        endpoint.value,
      )
      while not stop_event.is_set():
        await asyncio.sleep(0.2)  # Avoid tight loop
        try:
          # timeout in seconds
          data = await self._read_packet_in_executor(
            reader=reader,
            endpoint=endpoint.value,
            size=1024,
            timeout=read_timeout / 1000.0,
          )
          if data:
            results.append(bytes(data))
            logger.debug("Background read %d bytes: %s", len(data), bytes(data).hex())
        except asyncio.CancelledError:
          logger.info("Background reader cancelled.")
          break
        except Exception as e:
          logger.error("Error in background reader: %s", e, exc_info=True)
          await asyncio.sleep(0.1)
      logger.info("Stopping background reader for %s %s", device_type.name, endpoint.name)

    task = asyncio.create_task(background_reader())
    return task, stop_event, results

  async def close(self) -> None:
    for device_type, reader in self.devices.items():
      try:
        await reader.stop()  # type: ignore[no-untyped-call]
        logger.info("%s resources released.", device_type.name)
      except Exception as e:
        logger.error("Error closing %s: %s", device_type.name, e)
    self.devices = {}
