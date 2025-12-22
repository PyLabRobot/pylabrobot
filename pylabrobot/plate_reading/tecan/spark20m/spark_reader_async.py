import asyncio
import contextlib
import logging
from enum import Enum

import usb.core
import usb.util

from .spark_packet_parser import parse_single_spark_packet

# Tecan Spark
VENDOR_ID = 0x0C47


class SparkDevice(Enum):
  FLUORESCENCE = 0x8027
  ABSORPTION = 0x8026
  LUMINESCENCE = 0x8022
  PLATE_TRANSPORT = 0x8028


class SparkEndpoint(Enum):
  BULK_IN = 0x82
  BULK_IN1 = 0x81
  BULK_OUT = 0x01
  INTERRUPT_IN = 0x83


class SparkReaderAsync:
  def __init__(self, vid=VENDOR_ID):
    self.vid = vid
    self.devices = {}
    self.endpoints = {}
    self.seq_num = 0
    self.lock = asyncio.Lock()
    self.msgs = []

  def connect(self):
    found_devices = list(usb.core.find(find_all=True, idVendor=self.vid))
    if not found_devices:
      raise ValueError(f"No devices found for VID={hex(self.vid)}")

    logging.info(f"Found {len(found_devices)} devices with VID={hex(self.vid)}.")

    for d in found_devices:
      device_type = None
      try:
        device_type = SparkDevice(d.idProduct)
      except ValueError:
        logging.warning(f"Unknown device type with PID={hex(d.idProduct)}")
        continue

      if device_type in self.devices:
        logging.warning(f"Duplicate device type {device_type} found. Skipping.")
        continue

      try:
        if d.is_kernel_driver_active(0):
          logging.debug(f"Detaching kernel driver from {device_type.name} interface 0")
          d.detach_kernel_driver(0)
      except usb.core.USBError as e:
        logging.error(f"Error detaching kernel driver from {device_type.name}: {e}")

      try:
        d.set_configuration()
        cfg = d.get_active_configuration()
        intf = cfg[(0, 0)]

        ep_bulk_out = usb.util.find_descriptor(intf, bEndpointAddress=SparkEndpoint.BULK_OUT.value)
        ep_bulk_in = usb.util.find_descriptor(intf, bEndpointAddress=SparkEndpoint.BULK_IN.value)
        ep_bulk_in1 = usb.util.find_descriptor(intf, bEndpointAddress=SparkEndpoint.BULK_IN1.value)
        ep_interrupt_in = usb.util.find_descriptor(
          intf, bEndpointAddress=SparkEndpoint.INTERRUPT_IN.value
        )
        self.devices[device_type] = d
        self.endpoints[device_type] = {
          SparkEndpoint.BULK_OUT: ep_bulk_out,
          SparkEndpoint.BULK_IN: ep_bulk_in,
          SparkEndpoint.BULK_IN1: ep_bulk_in1,
          SparkEndpoint.INTERRUPT_IN: ep_interrupt_in,
        }
        # Note: calling set_configuration(0) twice before switching to configuration 1
        # is intentional. Some Tecan Spark devices require a double reset to config 0
        # to reliably accept configuration 1 after startup.
        d.set_configuration(0)
        d.set_configuration(0)
        d.set_configuration(1)

        logging.info(
          f"Successfully configured {device_type.name} (PID: {hex(d.idProduct)} SN: {d.serial_number})"
        )

      except usb.core.USBError as e:
        logging.error(f"USBError configuring {device_type.name}: {e}")
      except Exception as e:
        logging.error(f"Error configuring {device_type.name}: {e}")

    if not self.devices:
      raise ValueError(f"Failed to connect to any known Spark devices for VID={hex(self.vid)}")

    logging.info(f"Successfully connected to {len(self.devices)} devices.")

  def _calculate_checksum(self, data):
    checksum = 0
    for byte in data:
      checksum ^= byte
    return checksum

  async def _usb_read(self, endpoint, timeout, count=None):
    if count is None:
      count = endpoint.wMaxPacketSize
    return await asyncio.to_thread(endpoint.read, count, timeout=timeout)

  async def _usb_write(self, endpoint, data):
    return await asyncio.to_thread(endpoint.write, data)

  async def send_command(self, command_str, device_type=SparkDevice.PLATE_TRANSPORT):
    if device_type not in self.devices:
      logging.error(f"Device type {device_type} not connected.")
      return False

    endpoints = self.endpoints[device_type]
    ep_bulk_out = endpoints[SparkEndpoint.BULK_OUT]

    async with self.lock:
      logging.debug(f"Sending to {device_type.name}: {command_str}")
      payload = command_str.encode("ascii")
      payload_len = len(payload)

      header = bytes([0x01, self.seq_num, 0x00, payload_len])
      message = header + payload + bytes([self._calculate_checksum(header + payload)])
      self.seq_num = (self.seq_num + 1) % 256

      try:
        await self._usb_write(ep_bulk_out, message)
        logging.debug(f"Sent message to {device_type.name}: {message.hex()}")
        return True
      except usb.core.USBError as e:
        logging.error(f"USB error sending command to {device_type.name}: {e}")
        return False
      except Exception as e:
        logging.error(f"Error sending command to {device_type.name}: {e}", exc_info=True)
        return False

  def init_read(self, in_endpoint, count=512, read_timeout=2000):
    logging.debug(f"Initiating read task on {hex(in_endpoint.bEndpointAddress)}...")
    self.cur_in_endpoint = in_endpoint
    return asyncio.create_task(self._usb_read(in_endpoint, read_timeout, count))

  async def get_response(self, read_task, timeout=2000, attempts=10000):
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
          resp = await self._usb_read(self.cur_in_endpoint, 20, 512)
          if resp:
            logging.debug(f"Read task completed ({len(resp)} bytes): {bytes(resp).hex()}")
            parsed = parse_single_spark_packet(bytes(resp))
            logging.debug(f"Parsed: {parsed}")
            if parsed.get("type") == "RespMessage":
              self.msgs.append(parsed["payload"])
            elif parsed.get("type") == "RespError":
              raise Exception(parsed)
        except usb.core.USBError as e:
          if e.errno == 110:  # Timeout
            await asyncio.sleep(0.1)
          else:
            logging.error(f"USB error in get_response: {e}")

      return parsed

    except asyncio.CancelledError:
      logging.warning("Read task was cancelled")
      return None
    except Exception as e:
      logging.error(f"Error in get_response: {e}", exc_info=True)
      return None

  def clear_messages(self):
    """Clear the list of recorded RespMessage payloads."""
    self.msgs = []

  @contextlib.asynccontextmanager
  async def reading(
    self,
    device_type=SparkDevice.PLATE_TRANSPORT,
    endpoint=SparkEndpoint.INTERRUPT_IN,
    count=512,
    read_timeout=2000,
  ):
    if device_type not in self.devices:
      raise ValueError(f"Device type {device_type} not connected.")

    ep = self.endpoints[device_type].get(endpoint)
    if not ep:
      raise ValueError(f"Endpoint {endpoint} not found for {device_type.name}.")

    read_task = self.init_read(ep, count, read_timeout)
    await asyncio.sleep(0.01)  # Short delay to ensure the read task starts

    response_task = asyncio.create_task(self.get_response(read_task))

    try:
      yield response_task
    finally:
      logging.debug(
        f"Context manager exiting, awaiting read task for {device_type.name} {endpoint.name}"
      )
      if not response_task.done():
        await response_task

      try:
        response = response_task.result()
        logging.debug(f"Response from context manager read: {response}")
      except Exception as e:
        logging.debug(f"Response task exception: {e}")

  async def start_background_read(
    self, device_type, endpoint=SparkEndpoint.INTERRUPT_IN, read_timeout=100
  ):
    if device_type not in self.devices:
      logging.error(f"Device type {device_type} not connected.")
      return None, None, None

    ep = self.endpoints[device_type].get(endpoint)
    if not ep:
      logging.error(f"Endpoint {endpoint} not found for {device_type.name}.")
      return None, None, None

    stop_event = asyncio.Event()
    results = []

    async def background_reader():
      logging.info(
        f"Starting background reader for {device_type.name} {endpoint.name} (0x{ep.bEndpointAddress:02x})"
      )
      while not stop_event.is_set():
        await asyncio.sleep(0.2)  # Avoid tight loop
        try:
          data = await self._usb_read(ep, read_timeout, 1024)
          if data:
            results.append(bytes(data))
            logging.debug(f"Background read {len(data)} bytes: {bytes(data).hex()}")
        except usb.core.USBError as e:
          if e.errno == 110:  # Timeout
            pass
          else:
            logging.error(f"USB error in background reader: {e}")
            await asyncio.sleep(0.1)  # Avoid tight loop on other errors
        except asyncio.CancelledError:
          logging.info("Background reader cancelled.")
          break
        except Exception as e:
          logging.error(f"Error in background reader: {e}", exc_info=True)
          await asyncio.sleep(0.1)
      logging.info(f"Stopping background reader for {device_type.name} {endpoint.name}")

    task = asyncio.create_task(background_reader())
    return task, stop_event, results

  async def close(self):
    for device_type, device in self.devices.items():
      try:
        await asyncio.to_thread(usb.util.dispose_resources, device)
        logging.info(f"{device_type.name} resources released.")
      except Exception as e:
        logging.error(f"Error closing {device_type.name}: {e}")
    self.devices = {}
    self.endpoints = {}
