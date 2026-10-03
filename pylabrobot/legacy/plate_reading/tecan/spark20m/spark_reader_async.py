import logging
import sys
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

import anyio

if sys.version_info >= (3, 11):
  from builtins import BaseExceptionGroup
else:
  from exceptiongroup import BaseExceptionGroup

try:
  import usb.core
  import usb.util
except ImportError:
  pass

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.io.usb import USB

from .enums import DEVICE_ENDPOINTS, VENDOR_ID, SparkDevice, SparkEndpoint
from .spark_packet_parser import PACKET_TYPE, parse_single_spark_packet


class SparkError(Exception):
  """Error returned by the Spark device in a RespError packet."""


class SparkReaderAsync(HasLifespan):
  def __init__(self, vid: int = VENDOR_ID) -> None:
    super().__init__()
    self.vid: int = vid
    self.devices: Dict[SparkDevice, USB] = {}
    # Per-device discovered endpoints, overriding DEVICE_ENDPOINTS from enums.py
    self.device_endpoints: Dict[SparkDevice, Dict[str, SparkEndpoint]] = {}
    self.seq_num: int = 0
    self.lock = anyio.Lock()
    self.msgs: List[Any] = []
    self._packet_buffers: Dict[tuple[USB, Optional[int]], bytearray] = {}

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    """Connect each available Spark USB module for this lifespan."""
    await super()._enter_lifespan(stack)

    logging.info(f"Scanning for devices with VID={hex(self.vid)}...")
    usb_stack = await stack.enter_async_context(AsyncExitStackWithShielding())
    stack.callback(self._clear_connections)

    for device_type in SparkDevice:
      try:
        await self._connect_device(usb_stack, device_type)
      except Exception as e:
        logging.error(f"Error configuring {device_type.name}: {e}")

    if not self.devices:
      raise ValueError(f"Failed to connect to any known Spark devices for VID={hex(self.vid)}")

    logging.info(f"Successfully connected to {len(self.devices)} devices.")

  async def _connect_device(
    self, stack: AsyncExitStackWithShielding, device_type: SparkDevice
  ) -> None:
    """Connect and publish one Spark USB module when it is available."""

    def configure(dev: usb.core.Device) -> None:
      try:
        if dev.is_kernel_driver_active(0):
          logging.debug(f"Detaching kernel driver from {device_type.name} interface 0")
          dev.detach_kernel_driver(0)
      except usb.core.USBError as e:
        logging.error(f"Error detaching kernel driver from {device_type.name}: {e}")

      # Some Tecan Spark devices require two resets to configuration zero.
      try:
        dev.set_configuration(0)
      except usb.core.USBError:
        pass
      try:
        dev.set_configuration(0)
      except usb.core.USBError:
        pass
      dev.set_configuration(1)

    endpoints: Optional[Dict[str, SparkEndpoint]] = DEVICE_ENDPOINTS.get(device_type)
    if endpoints is None:
      logging.warning(f"No endpoints defined for {device_type.name}, skipping.")
      return

    reader = USB(
      id_vendor=self.vid,
      id_product=device_type.value,
      human_readable_device_name=f"Tecan Spark {device_type.name}",
      configuration_callback=configure,
      read_endpoint_address=endpoints["read_status"].value,
      write_endpoint_address=endpoints["write"].value,
    )
    await stack.enter_async_context(reader.lifespan(empty_buffer=False))
    self.devices[device_type] = reader
    self.device_endpoints[device_type] = self._discover_endpoints(reader, device_type)
    logging.info(f"Successfully configured {device_type.name}")

  def _clear_connections(self) -> None:
    """Unpublish every USB module before the transports close."""

    self.devices = {}
    self.device_endpoints = {}
    self._packet_buffers.clear()

  def _discover_endpoints(self, reader: USB, device_type: SparkDevice) -> Dict[str, SparkEndpoint]:
    """Discover endpoints from the USB device descriptor.

    Finds the first bulk-in, bulk-out, and interrupt-in endpoints and builds
    a mapping compatible with DEVICE_ENDPOINTS. Falls back to the hardcoded
    DEVICE_ENDPOINTS if discovery fails.
    """
    assert reader.dev is not None, "Device not connected."

    try:
      cfg = reader.dev.get_active_configuration()
      intf = cfg[(0, 0)]

      bulk_in = None
      bulk_out = None
      interrupt_in = None

      for ep in intf:
        direction = usb.util.endpoint_direction(ep.bEndpointAddress)
        transfer_type = usb.util.endpoint_type(ep.bmAttributes)

        if transfer_type == usb.util.ENDPOINT_TYPE_BULK:
          if direction == usb.util.ENDPOINT_IN and bulk_in is None:
            bulk_in = ep.bEndpointAddress
          elif direction == usb.util.ENDPOINT_OUT and bulk_out is None:
            bulk_out = ep.bEndpointAddress
        elif transfer_type == usb.util.ENDPOINT_TYPE_INTR:
          if direction == usb.util.ENDPOINT_IN and interrupt_in is None:
            interrupt_in = ep.bEndpointAddress

      if bulk_in is None or bulk_out is None or interrupt_in is None:
        logging.warning(
          f"Incomplete endpoint discovery for {device_type.name}: "
          f"bulk_in={bulk_in}, bulk_out={bulk_out}, interrupt_in={interrupt_in}. "
          f"Falling back to hardcoded endpoints."
        )
        fallback = DEVICE_ENDPOINTS.get(device_type, {})
        return dict(fallback)

      logging.info(
        f"{device_type.name} endpoints: bulk_in=0x{bulk_in:02x}, "
        f"bulk_out=0x{bulk_out:02x}, interrupt_in=0x{interrupt_in:02x}"
      )

      # Build endpoint mapping using SparkEndpoint values or raw ints.
      # We create new SparkEndpoint-like values for the discovered addresses.
      return {
        "write": SparkEndpoint(bulk_out),
        "read_status": SparkEndpoint(interrupt_in),
        "read_data": SparkEndpoint(bulk_in),
      }
    except Exception as e:
      logging.warning(f"Endpoint discovery failed for {device_type.name}: {e}. Using hardcoded.")
      fallback = DEVICE_ENDPOINTS.get(device_type, {})
      return dict(fallback)

  def get_endpoints(self, device_type: SparkDevice) -> Dict[str, SparkEndpoint]:
    """Get endpoints for a device, preferring discovered over hardcoded."""
    if device_type in self.device_endpoints:
      return self.device_endpoints[device_type]
    fallback = DEVICE_ENDPOINTS.get(device_type)
    if fallback is None:
      raise ValueError(f"No endpoints for {device_type.name}")
    return dict(fallback)

  def _calculate_checksum(self, data: bytes) -> int:
    checksum = 0
    for byte in data:
      checksum ^= byte
    return checksum

  async def _read_packet(
    self,
    reader: USB,
    endpoint: Optional[int] = None,
    size: Optional[int] = None,
    timeout: Optional[float] = None,
  ) -> Optional[bytes]:
    key = reader, endpoint
    packet = self._packet_buffers.setdefault(key, bytearray())
    with anyio.move_on_after(timeout):
      while True:
        if packet and packet[0] not in PACKET_TYPE:
          logging.warning(f"Invalid packet indicator {packet[0]}, ignoring: {packet.hex()}")
          packet.clear()
        if len(packet) >= 5:
          payload_len = (packet[2] << 8) | packet[3]
          expected_len = 4 + payload_len + 1  # Header + Payload + Checksum
          if len(packet) >= expected_len:
            result = bytes(packet[:expected_len])
            del packet[:expected_len]
            return result

        data = await reader.read(size=size, endpoint=endpoint)
        if not packet and len(data) < 5:
          logging.warning(f"Packet too short ({len(data)}), ignoring: {data.hex()}")
          await anyio.lowlevel.checkpoint()
          continue
        if not packet and data[0] not in PACKET_TYPE:
          logging.warning(f"Invalid packet indicator {data[0]}, ignoring: {data.hex()}")
          await anyio.lowlevel.checkpoint()
          continue
        if packet and len(data) >= 5 and data[0] in PACKET_TYPE:
          new_payload_len = (data[2] << 8) | data[3]
          if len(data) >= 4 + new_payload_len + 1:
            logging.warning(f"Discarding incomplete packet before new header: {packet.hex()}")
            packet.clear()
        packet.extend(data)
    return None

  async def send_command(
    self,
    command_str: str,
    device_type: SparkDevice = SparkDevice.PLATE_TRANSPORT,
    timeout: float = 60.0,
  ) -> Optional[str]:
    return await self._send_command(command_str, device_type=device_type, timeout=timeout)

  async def _send_command(
    self,
    command_str: str,
    device_type: SparkDevice = SparkDevice.PLATE_TRANSPORT,
    timeout: float = 60.0,
    *,
    _on_commit: Optional[Callable[[], None]] = None,
  ) -> Optional[str]:
    if device_type not in self.devices:
      raise RuntimeError(f"Device type {device_type} not connected.")

    reader = self.devices[device_type]

    async with self.lock:
      await anyio.lowlevel.checkpoint()
      response: Optional[Dict[str, Any]] = None
      response_failure: Optional[BaseException] = None
      response_done = anyio.Event()
      transaction_scope = anyio.CancelScope()
      with transaction_scope:
        async with anyio.create_task_group() as task_group:

          async def read_response() -> None:
            nonlocal response, response_failure
            try:
              response = await self._get_response(reader, timeout=timeout)
            except anyio.get_cancelled_exc_class():
              raise
            except BaseException as error:
              response_failure = error
              if not transaction_scope.shield:
                task_group.cancel_scope.cancel()
            finally:
              response_done.set()

          task_group.start_soon(read_response)
          await anyio.sleep(0.01)

          if response_failure is None:
            logging.debug(f"Sending to {device_type.name}: {command_str}")
            payload = command_str.encode("ascii")
            payload_len = len(payload)

            header = bytes([0x01, self.seq_num, 0x00, payload_len])
            message = header + payload + bytes([self._calculate_checksum(header + payload)])

            transaction_scope.shield = True
            self.seq_num = (self.seq_num + 1) % 256
            await reader.write(message)
            if _on_commit is not None:
              _on_commit()
            logging.debug(f"Sent message to {device_type.name}: {message.hex()}")
            await response_done.wait()

      if response_failure is not None:
        raise response_failure
      await anyio.lowlevel.checkpoint()
      logging.debug(f"Response: {response}")
      return (
        response["payload"]["message"]
        if response and "payload" in response and "message" in response["payload"]
        else None
      )

  async def _get_response(
    self,
    reader: USB,
    timeout: float = 60.0,
  ) -> Optional[Dict[str, Any]]:
    parsed: Dict[str, Any] = {}
    with anyio.move_on_after(timeout):
      while parsed.get("type") != "RespReady":
        data = await self._read_packet(reader=reader, endpoint=None, size=512)
        if data is None:
          continue
        logging.debug(f"Read task completed ({len(data)} bytes): {bytes(data).hex()}")
        try:
          parsed = parse_single_spark_packet(bytes(data))
        except ValueError as error:
          logging.warning(f"Failed to parse packet: {error}")
          continue
        if parsed.get("type") == "RespMessage":
          self.msgs.append(parsed["payload"])
        elif parsed.get("type") == "RespError":
          raise SparkError(parsed)
    if parsed.get("type") != "RespReady":
      logging.warning('Timeout waiting for "RespReady" response')
    return parsed

  def clear_messages(self) -> None:
    """Clear the list of recorded RespMessage payloads."""
    self.msgs = []

  @asynccontextmanager
  async def background_read(
    self,
    device_type: SparkDevice,
    read_timeout: int = 100,
  ) -> AsyncIterator[List[bytes]]:
    if device_type not in self.devices:
      raise RuntimeError(f"Device type {device_type} not connected.")

    reader = self.devices[device_type]
    stop_event = anyio.Event()
    results: List[bytes] = []
    endpoints = self.get_endpoints(device_type)
    endpoint = endpoints["read_data"]

    async def background_reader() -> None:
      logging.info(
        f"Starting background reader for {device_type.name} {endpoint.name} (0x{endpoint.value:02x})"
      )
      try:
        while not stop_event.is_set():
          await anyio.sleep(0.05)  # 50ms polling to capture fast spectrum data bursts
          # timeout in seconds
          data = await self._read_packet(
            reader=reader,
            endpoint=endpoint.value,
            size=1024,
            timeout=read_timeout / 1000.0,
          )
          if data:
            results.append(bytes(data))
            logging.debug(f"Background read {len(data)} bytes: {bytes(data).hex()}")
      finally:
        logging.info(f"Stopping background reader for {device_type.name} {endpoint.name}")

    try:
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(background_reader)
        try:
          yield results
        finally:
          stop_event.set()
          task_group.cancel_scope.cancel()
    except BaseExceptionGroup as error:
      if len(error.exceptions) == 1:
        raise error.exceptions[0]
      raise
