import asyncio
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

import usb.core

from pylabrobot.io.usb import USB

from .enums import VENDOR_ID, DEVICE_ENDPOINTS, SparkDevice, SparkEndpoint
from .spark_packet_parser import parse_single_spark_packet, PACKET_TYPE


class SparkReaderAsync:
  def __init__(self, vid: int = VENDOR_ID) -> None:
    self.vid: int = vid
    self.devices: Dict[SparkDevice, USB] = {}
    self.seq_num: int = 0
    self.lock: asyncio.Lock = asyncio.Lock()
    self.msgs: List[Any] = []

  async def connect(self) -> None:
    logging.info(f"Scanning for devices with VID={hex(self.vid)}...")

    for device_type in SparkDevice:
      if device_type in self.devices:
        continue

      try:

        def configure(dev: usb.core.Device) -> None:
          try:
            if dev.is_kernel_driver_active(0):
              logging.debug(f"Detaching kernel driver from {device_type.name} interface 0")
              dev.detach_kernel_driver(0)
          except usb.core.USBError as e:
            logging.error(f"Error detaching kernel driver from {device_type.name}: {e}")

          # Note: calling set_configuration(0) twice before switching to configuration 1
          # is intentional. Some Tecan Spark devices require a double reset to config 0
          # to reliably accept configuration 1 after startup.
          dev.set_configuration(0)
          dev.set_configuration(0)
          dev.set_configuration(1)

        endpoints: Optional[Dict[str, SparkEndpoint]] = DEVICE_ENDPOINTS.get(device_type)
        if endpoints is None:
          logging.warning(f"No endpoints defined for {device_type.name}, skipping.")
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
        logging.info(f"Successfully configured {device_type.name}")

      except Exception as e:
        logging.error(f"Error configuring {device_type.name}: {e}")

    if not self.devices:
      raise ValueError(f"Failed to connect to any known Spark devices for VID={hex(self.vid)}")

    logging.info(f"Successfully connected to {len(self.devices)} devices.")

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
        logging.warning(f"Packet too short ({len(data)}), ignoring: {data.hex()}")
        continue

      # Check indicator
      if data[0] not in PACKET_TYPE:
        logging.warning(f"Invalid packet indicator {data[0]}, ignoring: {data.hex()}")
        continue

      # Check length
      # bytes 2-3 are payload length (Big Endian)
      payload_len = (data[2] << 8) | data[3]
      expected_len = 4 + payload_len + 1  # Header + Payload + Checksum
      if len(data) < expected_len:
        logging.warning(
          f"Packet data shorter than payload length (got {len(data)}, expected {expected_len}), ignoring: {data.hex()}"
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
        logging.debug(f"Sending to {device_type.name}: {command_str}")
        payload = command_str.encode("ascii")
        payload_len = len(payload)

        header = bytes([0x01, self.seq_num, 0x00, payload_len])
        message = header + payload + bytes([self._calculate_checksum(header + payload)])
        self.seq_num = (self.seq_num + 1) % 256

        await reader.write(message)
        logging.debug(f"Sent message to {device_type.name}: {message.hex()}")

        # Wait for response
        if not response_task.done():
          await response_task

        response = response_task.result()
        logging.debug(f"Response: {response}")
        return (
          response["payload"]["message"]
          if response and "payload" in response and "message" in response["payload"]
          else None
        )
      except Exception as e:
        logging.error(f"Error in send_command to {device_type.name}: {e}", exc_info=True)
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
        logging.warning("Read task returned None")
        return None

      data_bytes = bytes(data)
      logging.debug(f"Read task completed ({len(data_bytes)} bytes): {data_bytes.hex()}")

      parsed = {}
      if len(data_bytes) > 0:
        try:
          parsed = parse_single_spark_packet(data_bytes)
        except ValueError as e:
          logging.warning(f"Failed to parse packet: {e}")
          # Treat as not ready/retry

      if parsed.get("type") == "RespMessage":
        self.msgs.append(parsed["payload"])
      elif parsed.get("type") == "RespError":
        raise Exception(parsed)

      deadline = time.monotonic() + timeout
      while parsed.get("type") != "RespReady" and time.monotonic() < deadline:
        try:
          await asyncio.sleep(0.01)
          logging.debug(f"Still busy, retrying... time left: {deadline - time.monotonic():.1f}s")

          resp = await self._read_packet_in_executor(
            reader=reader, endpoint=None, size=512, timeout=0.02
          )

          if resp:
            logging.debug(f"Read task completed ({len(resp)} bytes): {bytes(resp).hex()}")
            parsed = parse_single_spark_packet(bytes(resp))
            logging.debug(f"Parsed: {parsed}")
            if parsed.get("type") == "RespMessage":
              self.msgs.append(parsed["payload"])
            elif parsed.get("type") == "RespError":
              raise Exception(parsed)
        except Exception as e:
          logging.error(f"Error in get_response retry: {e}")
      if parsed.get("type") != "RespReady":
        logging.warning('Timeout waiting for "RespReady" response')
      return parsed

    except asyncio.CancelledError:
      logging.warning("Read task was cancelled")
      return None
    except Exception as e:
      logging.error(f"Error in get_response: {e}", exc_info=True)
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
      logging.error(f"Device type {device_type} not connected.")
      return None, None, None

    reader = self.devices[device_type]
    stop_event = asyncio.Event()
    results: List[bytes] = []
    endpoints = DEVICE_ENDPOINTS.get(device_type)
    if endpoints is None:
      logging.error(f"No endpoints for {device_type.name}")
      return None, None, None
    endpoint = endpoints["read_data"]

    async def background_reader() -> None:
      logging.info(
        f"Starting background reader for {device_type.name} {endpoint.name} (0x{endpoint.value:02x})"
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
            logging.debug(f"Background read {len(data)} bytes: {bytes(data).hex()}")
        except asyncio.CancelledError:
          logging.info("Background reader cancelled.")
          break
        except Exception as e:
          logging.error(f"Error in background reader: {e}", exc_info=True)
          await asyncio.sleep(0.1)
      logging.info(f"Stopping background reader for {device_type.name} {endpoint.name}")

    task = asyncio.create_task(background_reader())
    return task, stop_event, results

  async def close(self) -> None:
    for device_type, reader in self.devices.items():
      try:
        await reader.stop()  # type: ignore[no-untyped-call]
        logging.info(f"{device_type.name} resources released.")
      except Exception as e:
        logging.error(f"Error closing {device_type.name}: {e}")
    self.devices = {}
