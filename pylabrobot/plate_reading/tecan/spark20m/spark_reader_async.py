import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple

import usb.core

from pylabrobot.io.usb import USB

from .enums import VENDOR_ID, SparkDevice, SparkEndpoint
from .spark_packet_parser import parse_single_spark_packet


class SparkReaderAsync:
  def __init__(self, vid: int = VENDOR_ID) -> None:
    self.vid: int = vid
    self.devices: Dict[SparkDevice, USB] = {}
    self.seq_num: int = 0
    self.lock: asyncio.Lock = asyncio.Lock()
    self.msgs: List[Any] = []
    self.cur_reader: Optional[USB] = None
    self.cur_endpoint_addr: Optional[int] = None

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

        reader = USB(
          id_vendor=self.vid,
          id_product=device_type.value,
          configuration_callback=configure,
          max_workers=16,
        )

        await reader.setup()  # type: ignore[no-untyped-call]
        self.devices[device_type] = reader
        logging.info(f"Successfully configured {device_type.name}")

      except RuntimeError:
        # Device not found
        pass
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

  async def send_command(
    self,
    command_str: str,
    device_type: SparkDevice = SparkDevice.PLATE_TRANSPORT,
    attempts: int = 10000,
  ) -> Optional[str]:
    if device_type not in self.devices:
      raise RuntimeError(f"Device type {device_type} not connected.")

    reader = self.devices[device_type]
    endpoint_addr = SparkEndpoint.BULK_OUT.value

    async with self.lock:
      # Set up read task before sending command
      read_task = self._init_read(device_type, SparkEndpoint.INTERRUPT_IN)
      await asyncio.sleep(0.01)
      response_task = asyncio.create_task(self._get_response(read_task, attempts=attempts))

      logging.debug(f"Sending to {device_type.name}: {command_str}")
      payload = command_str.encode("ascii")
      payload_len = len(payload)

      header = bytes([0x01, self.seq_num, 0x00, payload_len])
      message = header + payload + bytes([self._calculate_checksum(header + payload)])
      self.seq_num = (self.seq_num + 1) % 256

      try:
        await reader.write_to_endpoint(endpoint_addr, message)
        logging.debug(f"Sent message to {device_type.name}: {message.hex()}")
      except Exception as e:
        logging.error(f"Error sending command to {device_type.name}: {e}", exc_info=True)
        raise e

      # Wait for response
      if not response_task.done():
        await response_task

      try:
        response = response_task.result()
        logging.debug(f"Response: {response}")
        return (
          response["payload"]["message"]
          if response and "payload" in response and "message" in response["payload"]
          else None
        )
      except Exception as e:
        logging.error(f"Response task exception: {e}")
        raise e

  def _init_read(
    self,
    device_type: SparkDevice,
    endpoint: SparkEndpoint,
    count: int = 512,
    read_timeout: int = 2000,
  ) -> "asyncio.Task[Any]":
    logging.debug(f"Initiating read task on {device_type.name} ep {hex(endpoint.value)}...")
    self.cur_reader = self.devices[device_type]
    self.cur_endpoint_addr = endpoint.value
    # Convert read_timeout from milliseconds to seconds for USB class.
    return asyncio.create_task(
      self.cur_reader.read_from_endpoint(
        self.cur_endpoint_addr, size=count, timeout=read_timeout / 1000.0
      )
    )

  async def _get_response(
    self, read_task: "asyncio.Task[Any]", timeout: int = 2000, attempts: int = 10000
  ) -> Optional[Dict[str, Any]]:
    try:
      data = await read_task

      if data is None:
        logging.warning("Read task returned None")
        return None

      data_bytes = bytes(data)
      logging.debug(f"Read task completed ({len(data_bytes)} bytes): {data_bytes.hex()}")
      parsed = parse_single_spark_packet(data_bytes)

      if parsed.get("type") == "RespMessage":
        self.msgs.append(parsed["payload"])
      elif parsed.get("type") == "RespError":
        raise Exception(parsed)

      while parsed.get("type") != "RespReady" and attempts > 0:
        attempts -= 1
        try:
          await asyncio.sleep(0.01)
          logging.debug(f"Still busy, retrying... attempts left: {attempts}")
          if self.cur_reader is None or self.cur_endpoint_addr is None:
            raise RuntimeError("Current reader or endpoint not set")

          resp = await self.cur_reader.read_from_endpoint(
            self.cur_endpoint_addr, size=512, timeout=0.02
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
    endpoint: SparkEndpoint = SparkEndpoint.INTERRUPT_IN,
    read_timeout: int = 100,
  ) -> Tuple[Optional["asyncio.Task[None]"], Optional[asyncio.Event], Optional[List[bytes]]]:
    if device_type not in self.devices:
      logging.error(f"Device type {device_type} not connected.")
      return None, None, None

    reader = self.devices[device_type]
    stop_event = asyncio.Event()
    results: List[bytes] = []

    async def background_reader() -> None:
      logging.info(
        f"Starting background reader for {device_type.name} {endpoint.name} (0x{endpoint.value:02x})"
      )
      while not stop_event.is_set():
        await asyncio.sleep(0.2)  # Avoid tight loop
        try:
          # timeout in seconds
          data = await reader.read_from_endpoint(
            endpoint.value, size=1024, timeout=read_timeout / 1000.0
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
