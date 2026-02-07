from enum import Enum

# Tecan Spark USB Vendor ID
VENDOR_ID = 0x0C47


class SparkDevice(Enum):
  """USB product IDs for Spark device modules."""

  FLUORESCENCE = 0x8027
  ABSORPTION = 0x8026
  LUMINESCENCE = 0x8022
  PLATE_TRANSPORT = 0x8028


class SparkEndpoint(Enum):
  """USB endpoint addresses for Spark communication."""

  BULK_IN = 0x82
  BULK_IN1 = 0x81
  BULK_OUT = 0x01
  INTERRUPT_IN = 0x83


DEVICE_ENDPOINTS = {
  SparkDevice.FLUORESCENCE: {
    "write": SparkEndpoint.BULK_OUT,
    "read_status": SparkEndpoint.BULK_IN1,
    "read_data": SparkEndpoint.BULK_IN1,
  },
  SparkDevice.ABSORPTION: {
    "write": SparkEndpoint.BULK_OUT,
    "read_status": SparkEndpoint.INTERRUPT_IN,
    "read_data": SparkEndpoint.BULK_IN,
  },
  SparkDevice.PLATE_TRANSPORT: {
    "write": SparkEndpoint.BULK_OUT,
    "read_status": SparkEndpoint.INTERRUPT_IN,
  },
  SparkDevice.LUMINESCENCE: {
    "write": SparkEndpoint.BULK_OUT,
    "read_status": SparkEndpoint.INTERRUPT_IN,
  },
}
