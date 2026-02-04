import logging
from typing import Optional

from .base_control import BaseControl
from .spark_enums import InstrumentMessageType, ModuleType


class DataControl(BaseControl):
  async def get_programmable_memory_scope(
    self,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    """Gets the programmable memory scope."""
    command = "#DOWNLOAD TYPE"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    if subcomponent:
      command += f" SUB={subcomponent}"
    response = await self.send_command(command)
    return response

  async def get_ranges_for_memory_scope(
    self,
    memory_scope: str,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    """Gets the ranges for a memory scope."""
    command = "#DOWNLOAD"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    if subcomponent:
      command += f" SUB={subcomponent}"
    command += f" TYPE={memory_scope}"
    return await self.send_command(command)

  async def prepare_download(
    self,
    memory_scope: str,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    """Prepares the device for download."""
    command = "DOWNLOAD PREPARE"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    if subcomponent:
      command += f" SUB={subcomponent}"
    command += f" TYPE={memory_scope}"
    return await self.send_command(command)

  async def start_download_block(
    self,
    offset: int,
    size: int,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    """Starts downloading a block of data."""
    command = "DOWNLOAD BLOCK START"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    if subcomponent:
      command += f" SUB={subcomponent}"
    command += f" OFFSET={offset} SIZE={size}"
    return await self.send_command(command)

  async def end_download_block(
    self,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    """Ends the download of a block of data."""
    command = "DOWNLOAD BLOCK END"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    if subcomponent:
      command += f" SUB={subcomponent}"
    return await self.send_command(command)

  async def get_download_sections(
    self,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    """Gets the available download sections."""
    command = "#DOWNLOAD SECTION NAME"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    if subcomponent:
      command += f" SUB={subcomponent}"
    response = await self.send_command(command)
    return response

  async def start_download_section(
    self,
    section_name: str,
    size: int,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    """Starts downloading a specific section."""
    command = "DOWNLOAD SECTION START"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    if subcomponent:
      command += f" SUB={subcomponent}"
    command += f" NAME={section_name} SIZE={size}"
    return await self.send_command(command)

  async def end_download_section(
    self,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    """Ends the download of a section."""
    command = "DOWNLOAD SECTION END"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    if subcomponent:
      command += f" SUB={subcomponent}"
    return await self.send_command(command)

  async def finalize_download(
    self,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    """Finalizes the download process."""
    command = "DOWNLOAD FINISH"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    if subcomponent:
      command += f" SUB={subcomponent}"
    return await self.send_command(command)

  async def send_download_data(self, data: bytes) -> None:
    """Sends data to the device during download."""
    raise NotImplementedError
    # return await self._usb_write(self.ep_bulk_out, data)

  async def get_command_buffer_size(self) -> Optional[str]:
    """Gets the command buffer size."""
    return await self.send_command("?BUFFER COMMAND SIZE")

  async def get_command_overhead(self) -> Optional[str]:
    """Gets the command overhead."""
    return await self.send_command("?BUFFER COMMAND OVERHEAD")

  async def clear_error_stack(
    self,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    """Clears the error stack."""
    command = "LASTERROR CLEAR"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    if subcomponent:
      command += f" SUB={subcomponent}"
    return await self.send_command(command)

  async def get_error_index_range(
    self,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    """Gets the error index range."""
    command = "#LASTERROR INDEX"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    if subcomponent:
      command += f" SUB={subcomponent}"
    return await self.send_command(command)

  async def get_current_max_error_index(
    self,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    """Gets the current maximum error index."""
    command = "?LASTERROR MAX"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    if subcomponent:
      command += f" SUB={subcomponent}"
    return await self.send_command(command)

  async def get_error(
    self,
    index: int,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    """Gets the error at the given index."""
    command = f"?LASTERROR INDEX={index}"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    if subcomponent:
      command += f" SUB={subcomponent}"
    return await self.send_command(command)

  async def get_module_sap_number(self) -> Optional[str]:
    """Gets the module SAP number."""
    return await self.send_command("?INFO SAP_NR_MODULE")

  async def get_instrument_sap_number(self) -> Optional[str]:
    """Gets the instrument SAP number."""
    return await self.send_command("?INFO SAP_NR_INSTRUMENT")

  async def get_module_sap_serial_number(self) -> Optional[str]:
    """Gets the module SAP serial number."""
    return await self.send_command("?SAP_SERIAL_INSTRUMENT SAP_NR_INSTRUMENT")

  async def get_instrument_type(self) -> Optional[str]:
    """Gets the instrument type."""
    return await self.send_command("?INFO INSTRUMENT_TYPE")

  async def get_hardware_version(
    self,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    """Gets the hardware version."""
    command = "?INFO HARDWARE_VERSION"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    if subcomponent:
      command += f" SUB={subcomponent}"
    return await self.send_command(command)

  async def get_user_defined(self) -> Optional[str]:
    """Gets the user-defined information."""
    return await self.send_command("?INFO USERDEFINED")

  async def get_available_modules(self) -> Optional[str]:
    """Gets the list of available modules."""
    return await self.send_command("#MODULE")

  async def get_expected_modules(self) -> Optional[str]:
    """Gets the list of expected modules."""
    return await self.send_command("#MODULE EXPECTED")

  async def get_expected_usb_modules(self) -> Optional[str]:
    """Gets the list of expected USB modules."""
    return await self.send_command("#MODULE EXPECTED_USB")

  async def get_available_sub_modules(
    self,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    """Gets the list of available sub-modules."""
    command = "#MODULE SUB"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    if subcomponent:
      command += f" SUB={subcomponent}"
    return await self.send_command(command)

  async def get_expected_sub_modules(
    self,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    """Gets the list of expected sub-modules."""
    command = "#MODULE EXPECTED_SUB"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    if subcomponent:
      command += f" SUB={subcomponent}"
    return await self.send_command(command)

  async def get_optional_modules(self) -> Optional[str]:
    """Gets the list of optional modules."""
    return await self.send_command("#MODULE DYNAMIC")

  async def get_available_functions(
    self,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    """Gets the list of available functions."""
    command = "#FUNCTION"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    if subcomponent:
      command += f" SUB={subcomponent}"
    return await self.send_command(command)

  async def get_limit_value(
    self,
    name: str,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    """Gets the integer limit value for a given name."""
    command = "?LIMIT"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    if subcomponent:
      command += f" SUB={subcomponent}"
    command += f" {name.upper()}"
    return await self.send_command(command)

  async def get_double_limit_value(
    self,
    name: str,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    """Gets the double limit value for a given name."""
    command = "?LIMIT"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    if subcomponent:
      command += f" SUB={subcomponent}"
    command += f" {name.upper()}"
    return await self.send_command(command)

  async def get_available_message_types(self) -> Optional[str]:
    """Gets the available message types."""
    response = await self.send_command("#MESSAGE TYPE")
    return response

  async def get_interval_range(self, message_type: InstrumentMessageType) -> Optional[str]:
    """Gets the interval range for a message type."""
    return await self.send_command(f"#MESSAGE TYPE={message_type.value.upper()} TIME_INTERVAL")

  async def get_current_interval(self, message_type: InstrumentMessageType) -> Optional[str]:
    """Gets the current interval for a message type."""
    return await self.send_command(f"?MESSAGE TYPE={message_type.value.upper()} TIME_INTERVAL")

  async def set_interval(self, message_type: InstrumentMessageType, interval: int) -> Optional[str]:
    """Sets the interval for a message type."""
    return await self.send_command(
      f"MESSAGE TYPE={message_type.value.upper()} TIME_INTERVAL={interval}"
    )

  async def turn_all_interval_messages_off(self) -> Optional[str]:
    """Turns off all interval messages."""
    return await self.send_command("MESSAGE TYPE=ALL TIME_INTERVAL=0")

  def _create_target_string(
    self,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> str:
    target_string = ""
    if hw_module:
      target_string += f" MODULE={hw_module.value}"
    if number is not None:
      target_string += f" NUMBER={number}"
    if subcomponent:
      target_string += f" SUB={subcomponent}"
    return target_string

  async def get_upload_memory_scope(
    self,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    """Gets the upload memory scope."""
    target_string = self._create_target_string(hw_module, number, subcomponent)
    response = await self.send_command(f"#UPLOAD TYPE{target_string}")
    return response

  async def get_ranges_for_upload_memory_scope(
    self,
    memory_scope: str,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    """Gets the ranges for an upload memory scope."""
    target_string = self._create_target_string(hw_module, number, subcomponent)
    return await self.send_command(f"#UPLOAD{target_string} TYPE={memory_scope}")

  async def prepare_upload(
    self,
    memory_scope: str,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    """Prepares the device for upload."""
    target_string = self._create_target_string(hw_module, number, subcomponent)
    # This command uses data channel, which is not fully implemented yet.
    logging.warning("Prepare upload uses data channel, not fully implemented.")
    return await self.send_command(f"UPLOAD PREPARE{target_string} TYPE={memory_scope}")

  async def upload_block(
    self,
    offset: int,
    size: int,
    timeout: int,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    """Uploads a block of data."""
    target_string = self._create_target_string(hw_module, number, subcomponent)
    # This command uses data channel, which is not fully implemented yet.
    logging.warning("Upload block uses data channel, not fully implemented.")
    return await self.send_command(f"UPLOAD BLOCK{target_string} OFFSET={offset} SIZE={size}")

  async def get_upload_sections(
    self,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    """Gets the available upload sections."""
    target_string = self._create_target_string(hw_module, number, subcomponent)
    response = await self.send_command(f"#UPLOAD SECTION{target_string} NAME")
    return response

  async def upload_section(
    self,
    section_name: str,
    size: int,
    timeout: int,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    """Uploads a specific section."""
    target_string = self._create_target_string(hw_module, number, subcomponent)
    # This command uses data channel, which is not fully implemented yet.
    logging.warning("Upload section uses data channel, not fully implemented.")
    return await self.send_command(f"UPLOAD SECTION{target_string} NAME={section_name} SIZE={size}")

  async def get_upload_section_size(
    self,
    section_name: str,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    """Gets the size of a specific upload section."""
    target_string = self._create_target_string(hw_module, number, subcomponent)
    return await self.send_command(f"?UPLOAD SECTION{target_string} NAME={section_name} SIZE")
