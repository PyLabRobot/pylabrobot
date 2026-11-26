"""Reading what an ACE module says about itself: identity, its boards, and its inputs.

The order follows the Master Controller's cold start: the module, then each of its boards from
the highest index down (a motion board's serial number and firmware image, any other board's
application version), then the names of its inputs and outputs. Nothing here moves or writes.
"""

from pylabrobot.beckman_coulter.biomek_i7.driver import protocol as p
from pylabrobot.beckman_coulter.biomek_i7.driver.board import AceBoard
from pylabrobot.beckman_coulter.biomek_i7.driver.configuration import BoardInfo, ModuleConfiguration

# Board kinds whose identity is a serial number and firmware image; every other kind reports an
# application version. AccuFrame boards are left at their hardware version.
_IMAGE_BOARDS = {0x00}
_ACCUFRAME = 0xE3


async def read_module(board: AceBoard) -> ModuleConfiguration:
  """Read a module's identity, board inventory and input/output names."""
  module_id = p.parse_module_id((await board.send(p.get_module_id())).payload)
  hardware = p.parse_hardware_version((await board.send(p.get_module_hardware_version())).payload)
  serial = p.parse_string((await board.send(p.get_module_serial_number())).payload)
  crc = p.parse_u32((await board.send(p.get_module_application_image_crc())).payload)
  length = p.parse_u32((await board.send(p.get_module_application_image_length())).payload)
  counts = p.parse_device_count((await board.send(p.get_module_device_count())).payload)
  usb_serial = board.usb_serial_number
  config = ModuleConfiguration(module_id, usb_serial, serial, hardware, crc, length, counts)

  for index in range(counts.get(p.Device.BOARD, 0), 0, -1):
    info = BoardInfo(
      index,
      p.parse_hardware_version((await board.send(p.get_board_hardware_version(index))).payload),
    )
    if info.hardware.type_code in _IMAGE_BOARDS:
      info.serial_number = p.parse_string(
        (await board.send(p.get_board_serial_number(index))).payload
      )
      info.application_crc = p.parse_u32(
        (await board.send(p.get_board_application_image_crc(index))).payload
      )
      info.application_length = p.parse_u32(
        (await board.send(p.get_board_application_image_length(index))).payload
      )
    elif info.hardware.type_code != _ACCUFRAME:
      info.application_version = p.parse_string(
        (await board.send(p.get_board_application_version(index))).payload
      )
    config.boards.append(info)

  io_counts = p.parse_device_count((await board.send(p.get_io_service_device_count())).payload)
  for index in range(1, io_counts.get(p.Device.ANALOG_INPUT, 0) + 1):
    name = await board.send(p.get_input_output_name(p.Device.ANALOG_INPUT, index))
    units = await board.send(p.get_analog_input_units(index))
    config.analog_inputs[index] = p.parse_string(name.payload)
    config.analog_units[index] = p.parse_string(units.payload)
  for index in range(1, io_counts.get(p.Device.DIGITAL_INPUT, 0) + 1):
    name = await board.send(p.get_input_output_name(p.Device.DIGITAL_INPUT, index))
    config.digital_inputs[index] = p.parse_string(name.payload)
  for index in range(1, io_counts.get(p.Device.DIGITAL_OUTPUT, 0) + 1):
    name = await board.send(p.get_input_output_name(p.Device.DIGITAL_OUTPUT, index))
    config.digital_outputs[index] = p.parse_string(name.payload)
  return config


async def read_analog_inputs(board: AceBoard, config: ModuleConfiguration) -> dict:
  """Every analog input's value, by name, in its units."""
  replies = await board.send_many(
    [p.read_input_output(p.Device.ANALOG_INPUT, i) for i in config.analog_inputs]
  )
  return {
    config.analog_inputs[i]: p.parse_float(r.payload) for i, r in zip(config.analog_inputs, replies)
  }
