"""
This file defines interfaces for all supported Hamilton liquid handling robots.
"""

from abc import ABCMeta, abstractmethod
import datetime
import enum
import logging
import re
import time
import typing

import usb.core
import usb.util

# TODO: from .backend import LiquidHanderBackend


# TODO: move to util.
def _assert_clamp(v, min_, max_, name):
  assert min_ <= v <= max_, f"{name} must be between {min} and {max}, but is {v}"


class HamiltonLiquidHandler(object, metaclass=ABCMeta): # TODO: object->LiquidHanderBackend
  """
  Abstract base class for Hamilton liquid handling robot backends.
  """

  @abstractmethod
  def __init__(self, read_poll_interval=10):
    """

    Args:
      read_poll_interval: The sleep after each check for device responses, in ms.
    """

    self.read_poll_interval = read_poll_interval # ms

  def generate_id(self):
    """ continuously generate unique ids 0 <= x < 10000. """
    id_ = 0
    while True:
      yield id_ % 10000
      id_ += 1

  # TODO: add response format param, and parse response here.
  # If None, return raw response.

  def send_command(self, module, command, **kwargs):
    """ Send a firmware command to the Hamilton machine.

    Args:
      module: 2 character module identifier (C0 for master, ...)
      command: 2 character command identifier (QM for request status)
      kwargs: any named parameters. the parameter name should also be
              2 characters long. The value can be any size.
    """

    # pylint: disable=redefined-builtin

    # assemble command
    cmd = module + command
    id = self.generate_id()
    cmd += f"id{id}" # has to be first param

    for k, v in kwargs.items(): # pylint: disable=unused-variable
      if type(v) is datetime.datetime:
        v = v.strftime("%Y-%m-%d %h:%M")
      elif type(v) is bool:
        v = 1 if v else 0
      if k.endswith("_"): # workaround for kwargs named in, as, ...
        k = k[:-1]
      cmd += "{k}{v}"

    logging.info("Sent command: %s", cmd)

    # write command to endpoint
    self.dev.write(self.write_endpoint)

    # TODO: this code should be somewhere else.
    # block by default
    res = None
    while res is None:
      res = self.dev.read(
        self.read_endpoint,
        self.read_endpoint.wMaxPacketSize
      )
      time.sleep(self.read_poll_interval)

    logging.info("Received response: %s", res)

    return res

  def parse_response(self, resp: str, fmt: str):
    """ Parse a machine response according to a format string.

    The format contains names of parameters (always length 2),
    followed by an arbitrary number of the following, but always
    the same:
    - '&': char
    - '#': decimal
    - '*': hex

    Example:
    - fmt : "aa####bb&&cc***
    - resp: "aa1111bbrwccB0B"

    The order of parameters in the format and response string do not
    have to (and often do not) match.

    The identifier parameter (id####) is added automatically.

    TODO: string parsing
    The firmware docs mention strings in the following format: '...'
    However, the length of these is always known (except when reading
    barcodes), so it is easier to convert strings to the right number
    of '&'. With barcode reading the length of the barcode is included
    with the response string. We'll probably do a custom implementation
    for that.

    TODO: block parsing
    When a parameter is built up of several identical blocks, the
    redundant blocks are not shown; that is to say, only the first
    Block and the number of blocks are given. It is always the case
    that the first value refers to channel 1, and so on. The
    individual blocks are separated by ' ' (Space).

    # TODO: spaces
    We should also parse responses where integers are separated by spaces,
    like this: ua#### #### ###### ###### ###### ######
    """

    # Verify format and resp match.
    resp = resp[4:] # remove device and cmd identifier from response.

    # Parse the parameters in the fmt string.
    info = {}

    def find_param(param):
      name, data = param[0:2], param[2:]
      type_ = {
        "#": "int",
        "*": "hex",
        "&": "str"
      }[data[0]]
      len_ = len(data)

      # Build a regex to match this parameter.
      exp = {
        "int": "-?[0-9]",
        "hex": "[0-9a-fA-F]",
        "str": ".",
      }[type_]
      regex = f"{name}({exp}{ {len_} })"

      # Match response against regex, save results in right datatype.
      r = re.search(regex, resp)
      if r is None:
        raise ValueError(f"could not find matches for parameter {name}")
      g = r.groups()
      if len(g) == 0:
        raise ValueError(f"could not find value for parameter {name}")
      m = g[0]

      if type_ == "str":
        info[name] = m
      elif type_ == "int":
        info[name] = int(m)
      elif type_ == "hex":
        info[name] = int(m, base=16)

    param = ""
    for char in fmt:
      if char.islower():
        if len(param) > 2:
          find_param(param)
          param = ""
      param += char
    if param != "":
      find_param(param) # last parameter is not closed by loop.
    if "id" not in info: # auto add id if we don't have it yet.
      find_param("id####")

    return info


class STAR(HamiltonLiquidHandler):
  """
  Interface for the Hamilton STAR.
  """

  def __init__(self, **kwargs):
    """ Create a new STAR interface.

    Args:
      read_poll_interval: The sleep after each check for device responses, in ms.
    """

    super().__init__(**kwargs)

  def setup(self):
    """ setup

    Creates a USB connection and finds read/write interfaces.
    """

    logging.info("Finding Hamilton USB device...")

    self.dev = usb.core.find(idVendor=0x08af)
    if self.dev is None:
      raise ValueError("Hamilton STAR device not found.")

    logging.info("Found Hamilton USB device.")

    # set the active configuration. With no arguments, the first
    # configuration will be the active one
    self.dev.set_configuration()

    cfg = self.dev.get_active_configuration()
    intf = cfg[(0,0)]

    self.write_endpoint = usb.util.find_descriptor(
      intf,
      custom_match = \
      lambda e: \
          usb.util.endpoint_direction(e.bEndpointAddress) == \
          usb.util.ENDPOINT_OUT) # 0x3?

    self.read_endpoint = usb.util.find_descriptor(
      intf,
      custom_match = \
      lambda e: \
          usb.util.endpoint_direction(e.bEndpointAddress) == \
          usb.util.ENDPOINT_IN) # 0x83?

    logging.info("Found endpoints. Write: %x Read %x", self.write_endpoint, self.read_endpoint)

  def _read(self):
    """
    continuously read data sent by Hamilton device and store each entry
    with an id in the responses cache.
    """

    while True:
      time.sleep(self.read_poll_interval)
      self.dev.read(self.read_endpoint, 100) # TODO: instead of 100 we want to read until new line.
      # TODO: what happens when we write 2 commands without reading in between?

  # -------------- 3.2 System general commands --------------

  def pre_initialize_instrument(self):
    """ Pre-initialize instrument """
    return self.send_command(module="C0", command="VI")

  class TipType(enum.Enum):
    """ Tip type """
    UNDEFINED=0
    LOW_VOLUME=1
    STANDARD_VOLUME=2
    HIGH_VOLUME=3
    CORE_384_HEAD_TIP=4
    XL=5

  class PickUpMethod(enum.Enum):
    """ Tip pick up method """
    OUT_OF_RACK=0
    OUT_OF_WASH_LIQUID=1

  def define_tip_needle(
    self,
    tip_type_table_index: int = 4,
    filter: bool = False,
    tip_length: int = 1950,
    maximum_tip_volume: int = 3500,
    tip_type: TipType = TipType.STANDARD_VOLUME,
    pick_up_method: PickUpMethod = PickUpMethod.OUT_OF_RACK
  ):
    """ Tip/needle definition.

    TODO: Define default values for type/application/filter.

    Args:
      tip_type_table_index: tip_table_index
      filter: with(out) filter
      tip_length: Tip length [0.1mm]
      maximum_tip_volume: Maximum volume of tip [0.1ul]
                          Note! it's automatically limited to max. channel capacity
      tip_type: Type of tip collar (Tip type identification)
      pick_up_method: pick up method.
                      Attention! The values set here are temporary and apply only until
                      power OFF or RESET. After power ON the default val- ues apply. (see Table 3)
    """

    # pylint: disable=redefined-builtin

    _assert_clamp(tip_type_table_index, 0, 99, "tip_type_table_index")
    filter = 1 if filter else 0
    _assert_clamp(tip_length, 1, 1999, "tip_length")
    _assert_clamp(maximum_tip_volume, 1, 56000, "maximum_tip_volume")

    return self.send_command(
      module="C0",
      command="TT",
      tt=tip_type_table_index,
      tf=filter,
      tl=tip_length,
      tv=maximum_tip_volume,
      tg=tip_type,
      tu=pick_up_method
    )

  # -------------- 3.2.1 System query --------------

  def request_error_code(self):
    """ Request error code

    Here the last saved error messages can be retrieved. The error buffer
    is automatically voided when a new command is started.
    All configured nodes are displayed.

    Returns:
      TODO:
      X0##/##: X0 slave
      ..##/## see node definitions ( chapter 5)
    """

    resp = self.send_command(module="RE", command="RF")
    return resp

  def request_firmware_version(self):
    """ Request firmware version

    Returns: TODO: Rfid0001rf1.0S 2009-06-24 A
    """
    return self.send_command(module="C0", command="RF")

  def request_parameter_value(self):
    """ Request parameter value

    Returns: TODO: Raid1111er00/00yg1200
    """

    return self.send_command(module="C0", command="RA")

  class BoardType(enum.Enum):
    C167CR_SINGLE_PROCESSOR_BOARD = 0
    C167CR_DUAL_PROCESSOR_BOARD = 1
    LPC2468_XE167_DUAL_PROCESSOR_BOARD = 2
    LPC2468_SINGLE_PROCESSOR_BOARD = 5
    UNKNOWN = -1

  def request_electronic_board_type(self):
    """ Request electronic board type

    Returns:
      The board type.
    """

    # pylint: disable=undefined-variable

    resp = self.send_command(module="C0", command="QB")
    try:
      return BoardType(resp["qb"])
    except ValueError:
      return BoardType.UNKNOWN

  # TODO: parse response.
  def request_supply_voltage(self):
    """ Request supply voltage

    Request supply voltage (for LDPB only)
    """

    return self.send_command(module="C0", command="MU")

  def request_instrument_initialization_status(self):
    """ Request instrument initialization status """

    resp = self.send_command(module="C0", command="QW")
    return resp["qw"] == 1

  def request_name_of_last_faulty_parameter(self):
    """ Request name of last faulty parameter

    Returns: TODO:
      Name of last parameter with syntax error
      (optional) received value separated with blank
      (optional) minimal permitted value separated with blank (optional)
      maximal permitted value separated with blank example with min max data:
      Vpid2233er00/00vpth 00000 03500 example without min max data: Vpid2233er00/00vpcd
    """

    return self.send_command(module="C0", command="VP")

  def request_master_status(self):
    """ Request master status

    Returns: TODO: see page 19 (SFCO.0036)
    """

    return self.send_command(module="C0", command="RQ")

  def request_number_of_presence_sensors_installed(self):
    """ Request number of presence sensors installed

    Returns:
      number of sensors installed (1...103)
    """

    return self.send_command(module="C0", command="SR")["sr"]

  def request_eeprom_data_correctness(self):
    """ Request EEPROM data correctness

    Returns: TODO: (SFCO.0149)
    """

    return self.send_command(module="C0", command="QV")

  # -------------- 3.3 Settings --------------

  # -------------- 3.3.1 Volatile Settings --------------


  def set_single_step_mode(
    self,
    single_step_mode: bool = False
  ):
    """ Set Single step mode

    Args:
      single_step_mode: Single Step Mode. Default False.
    """

    return self.send_command(
      module="C0",
      command="AM",
      am=single_step_mode,
    )

  def trigger_next_step(self):
    """ Trigger next step (Single step mode) """

    # TODO: this command has no reply!!!!
    return self.send_command(module="C0", command="NS")

  def halt(self):
    """ Halt

    Intermediate sequences not yet carried out and the commands in
    the command stack are discarded. Sequence already in process is
    completed.
    """

    return self.send_command(module="C0", command="HD")

  def save_all_cycle_counters(self):
    """ Save all cycle counters

    Save all cycle counters of the instrument
    """

    return self.send_command(module="C0", command="AZ")

  def set_not_stop(self, non_stop):
    """ Set not stop mode

    Args:
      non_stop: True if non stop mode should be turned on after command is sent.
    """

    if non_stop:
      # TODO: this command has no reply!!!!
      return self.send_command(module="C0", command="AB")
    else:
      return self.send_command(module="C0", command="AW")

  # -------------- 3.3.2 Non volatile settings (stored in EEPROM) --------------

  def store_installation_data(
    self,
    date: datetime.datetime = datetime.datetime.now(),
    serial_number: str = "0000"
  ):
    """ Store installation data

    Args:
      date: installation date.
    """

    assert len(serial_number) == 4, "serial number must be 4 chars long"

    return self.send_command(
      module="C0",
      command="SI",
      si=date,
      sn=serial_number
    )

  def store_verification_data(
    self,
    verification_subject: int = 0,
    date: datetime.datetime = datetime.datetime.now(),
    verification_status: bool = None
  ):
    """ Store verification data

    Args:
      verification_subject: verification subject. Default 0. Must be between 0 and 24.
      date: verification date.
      verification_status: verification status.
    """

    _assert_clamp(verification_subject, 0, 24, "verification_subject")

    return self.send_command(
      module="C0",
      command="AV",
      vo=verification_subject,
      vd=date,
      vs=verification_status,
    )

  def additional_time_stamp(self):
    """ Additional time stamp """

    return self.send_command(module="C0", command="AT")

  def set_x_offset_x_axis_iswap(self, x_offset: int):
    """ Set X-offset X-axis <-> iSWAP

    Args:
      x_offset: X-offset [0.1mm]
    """

    return self.send_command(
      module="C0",
      command="AG",
      x_offset=x_offset
    )

  def set_x_offset_x_axis_core_96_head(self, x_offset: int):
    """ Set X-offset X-axis <-> CoRe 96 head

    Args:
      x_offset: X-offset [0.1mm]
    """

    return self.send_command(
      module="C0",
      command="AF",
      x_offset=x_offset
    )

  def set_x_offset_x_axis_core_nano_pipettor_head(self, x_offset: int):
    """ Set X-offset X-axis <-> CoRe 96 head

    Args:
      x_offset: X-offset [0.1mm]
    """

    return self.send_command(
      module="C0",
      command="AF",
      x_offset=x_offset
    )

  def save_download_date(
    self,
    date: datetime.datetime = datetime.datetime.now()
  ):
    """ Save Download date

    Args:
      date: download date. Default now.
    """

    return self.send_command(
      module="C0",
      command="AO",
      ao=date,
    )

  def save_technical_status_of_assemblies(
    self,
    processor_board: str,
    power_supply: str
  ):
    """ Save technical status of assemblies

    Args:
      processor_board: Processor board. Art.Nr./Rev./Ser.No. (000000/00/0000)
      power_supply: Power supply. Art.Nr./Rev./Ser.No. (000000/00/0000)
    """

    return self.send_command(
      module="C0",
      command="BT",
      qt=processor_board + " " + power_supply,
    )

  def set_instrument_configuration(
    self,
    configuration_data_1: str = None, # TODO: configuration byte
    configuration_data_2: str = None, # TODO: configuration byte
    configuration_data_3: str = None, # TODO: configuration byte
    instrument_size_in_slots_x_range: int = 54,
    auto_load_size_in_slots: int = 54,
    tip_waste_x_position: int = 13400,
    right_x_drive_configuration_byte_1: int = 0,
    right_x_drive_configuration_byte_2: int = 0,
    minimal_iswap_collision_free_position: int = 3500,
    maximal_iswap_collision_free_position: int = 11400,
    left_x_arm_width: int = 3700,
    right_x_arm_width: int = 3700,
    num_pip_channels: int = 0,
    num_xl_channels: int = 0,
    num_robotic_channels: int = 0,
    minimal_raster_pitch_of_pip_channels: int = 90,
    minimal_raster_pitch_of_xl_channels: int = 360,
    minimal_raster_pitch_of_robotic_channels: int = 360,
    pip_maximal_y_position: int = 6065,
    left_arm_minimal_y_position: int = 60,
    right_arm_minimal_y_position: int = 60
  ):
    """ Set instrument configuration

    Args:
      configuration_data_1: configuration data 1.
      configuration_data_2: configuration data 2.
      configuration_data_3: configuration data 3.
      instrument_size_in_slots_x_range: instrument size in slots (X range).
                                          Must be between 10 and 99. Default 54.
      auto_load_size_in_slots: auto load size in slots. Must be between 10
                                and 54. Default 54.
      tip_waste_x_position: tip waste X-position. Must be between 1000 and
                            25000. Default 13400.
      right_x_drive_configuration_byte_1: right X drive configuration byte 1 (see
                                          xl parameter bits). Must be between 0 and 1.
                                          Default 0. # TODO: this.
      right_x_drive_configuration_byte_2: right X drive configuration byte 2 (see
                                          xn parameter bits). Must be between 0 and 1.
                                          Default 0. # TODO: this.
      minimal_iswap_collision_free_position: minimal iSWAP collision free position for
                                            direct X access. For explanation of calculation
                                            see Fig. 4. Must be between 0 and 30000. Default 3500.
      maximal_iswap_collision_free_position: maximal iSWAP collision free position for
                                              direct X access. For explanation of calculation
                                              see Fig. 4. Must be between 0 and 30000. Default 11400
      left_x_arm_width: width of left X arm [0.1 mm]. Must be between 0 and 9999. Default 3700.
      right_x_arm_width: width of right X arm [0.1 mm]. Must be between 0 and 9999. Default 3700.
      num_pip_channels: number of PIP channels. Must be between 0 and 16. Default 0.
      num_xl_channels: number of XL channels. Must be between 0 and 8. Default 0.
      num_robotic_channels: number of Robotic channels. Must be between 0 and 8. Default 0.
      minimal_raster_pitch_of_pip_channels: minimal raster pitch of PIP channels [0.1 mm]. Must
                                            be between 0 and 999. Default 90.
      minimal_raster_pitch_of_xl_channels: minimal raster pitch of XL channels [0.1 mm]. Must be
                                            between 0 and 999. Default 360.
      minimal_raster_pitch_of_robotic_channels: minimal raster pitch of Robotic channels [0.1 mm].
                                                Must be between 0 and 999. Default 360.
      pip_maximal_y_position: PIP maximal Y position [0.1 mm]. Must be between 0 and 9999.
                              Default 6065.
      left_arm_minimal_y_position: left arm minimal Y position [0.1 mm]. Must be between 0 and 9999.
                                    Default 60.
      right_arm_minimal_y_position: right arm minimal Y position [0.1 mm]. Must be between 0
                                    and 9999. Default 60.
    """

    _assert_clamp(instrument_size_in_slots_x_range, 10, 99, "instrument_size_in_slots_(x_range)")
    _assert_clamp(auto_load_size_in_slots, 10, 54, "auto_load_size_in_slots")
    _assert_clamp(tip_waste_x_position, 1000, 25000, "tip_waste_x_position")
    _assert_clamp(right_x_drive_configuration_byte_1, 0, 1, "right_x_drive_configuration_byte_1")
    _assert_clamp(right_x_drive_configuration_byte_2, 0, 1, "right_x_drive_configuration_byte_2")
    _assert_clamp(minimal_iswap_collision_free_position, 0, 30000, \
                  "minimal_iswap_collision_free_position")
    _assert_clamp(maximal_iswap_collision_free_position, 0, 30000, \
                  "maximal_iswap_collision_free_position")
    _assert_clamp(left_x_arm_width, 0, 9999, "left_x_arm_width")
    _assert_clamp(right_x_arm_width, 0, 9999, "right_x_arm_width")
    _assert_clamp(num_pip_channels, 0, 16, "num_pip_channels")
    _assert_clamp(num_xl_channels, 0, 8, "num_xl_channels")
    _assert_clamp(num_robotic_channels, 0, 8, "num_robotic_channels")
    _assert_clamp(minimal_raster_pitch_of_pip_channels, 0, 999, \
                  "minimal_raster_pitch_of_pip_channels")
    _assert_clamp(minimal_raster_pitch_of_xl_channels, 0, 999, \
                  "minimal_raster_pitch_of_xl_channels")
    _assert_clamp(minimal_raster_pitch_of_robotic_channels, 0, 999, \
                  "minimal_raster_pitch_of_robotic_channels")
    _assert_clamp(pip_maximal_y_position, 0, 9999, "pip_maximal_y_position")
    _assert_clamp(left_arm_minimal_y_position, 0, 9999, "left_arm_minimal_y_position")
    _assert_clamp(right_arm_minimal_y_position, 0, 9999, "right_arm_minimal_y_position")

    return self.send_command(
        module="C0",
        command="AK",
        kb=configuration_data_1,
        ka=configuration_data_2,
        ke=configuration_data_3,
        xt=instrument_size_in_slots_x_range,
        xa=auto_load_size_in_slots,
        xw=tip_waste_x_position,
        xr=right_x_drive_configuration_byte_1,
        xo=right_x_drive_configuration_byte_2,
        xm=minimal_iswap_collision_free_position,
        xx=maximal_iswap_collision_free_position,
        xu=left_x_arm_width,
        xv=right_x_arm_width,
        kp=num_pip_channels,
        kc=num_xl_channels,
        kr=num_robotic_channels,
        ys=minimal_raster_pitch_of_pip_channels,
        kl=minimal_raster_pitch_of_xl_channels,
        km=minimal_raster_pitch_of_robotic_channels,
        ym=pip_maximal_y_position,
        yu=left_arm_minimal_y_position,
        yx=right_arm_minimal_y_position,
      )

  def save_pip_channel_validation_status(
    self,
    validation_status: bool = False
  ):
    """ Save PIP channel validation status

    Args:
      validation_status: PIP channel validation status. Default False.
    """

    return self.send_command(
      module="C0",
      command="AJ",
      tq=validation_status,
    )

  def save_xl_channel_validation_status(
    self,
    validation_status: bool = False
  ):
    """ Save XL channel validation status

    Args:
      validation_status: XL channel validation status. Default False.
    """

    return self.send_command(
      module="C0",
      command="AE",
      tx=validation_status,
    )

  # TODO: response
  def configure_node_names(self):
    """ Configure node names """

    return self.send_command(module="C0", command="AJ")

  def set_deck_data(
    self,
    data_index: int = 0,
    data_stream: str = "0"
  ):
    """ set deck data

    Args:
      data_index: data index. Must be between 0 and 9. Default 0.
      data_stream: data stream (12 characters). Default <class 'str'>.
    """

    _assert_clamp(data_index, 0, 9, "data_index")
    assert len(data_stream) == 12, "data_stream must be 12 chars"

    return self.send_command(
      module="C0",
      command="DD",
      vi=data_index,
      vj=data_stream,
    )

  # -------------- 3.3.3 Settings query (stored in EEPROM) --------------

  def request_technical_status_of_assemblies(self):
    """ Request Technical status of assemblies """

    # TODO: parse res
    return self.send_command(module="C0", command="QT")

  def request_installation_data(self):
    """ Request installation data """

    # TODO: parse res
    return self.send_command(module="C0", command="RI")

  def request_download_date(self):
    """ Request download date """

    # TODO: parse res
    return self.send_command(module="C0", command="RO")

  def request_verification_data(
    self,
    verification_subject: int = 0
  ):
    """ Request download date

    Args:
      verification_subject: verification subject. Must be between 0 and 24. Default 0.
    """

    _assert_clamp(verification_subject, 0, 24, "verification_subject")

    # TODO: parse results.
    return self.send_command(
      module="C0",
      command="RO",
      vo = verification_subject
    )

  def request_additional_timestamp_data(self):
    """ Request additional timestamp data """

    # TODO: parse res
    return self.send_command(module="C0", command="RS")

  def request_pip_channel_validation_status(self):
    """ Request PIP channel validation status """

    # TODO: parse res
    return self.send_command(module="C0", command="RJ")

  def request_xl_channel_validation_status(self):
    """ Request XL channel validation status """

    # TODO: parse res
    return self.send_command(module="C0", command="UJ")

  def request_machine_configuration(self):
    """ Request machine configuration """

    # TODO: parse res
    return self.send_command(module="C0", command="RM")

  def request_extended_configuration(self):
    """ Request extended configuration """

    resp = self.send_command(module="C0", command="QM")
    return self.parse_response(resp, fmt="QMid####ka******ke********xt##xa##xw#####xl**" + \
            "xn**xr**xo**xm#####xx#####xu####xv####kc#kr#ys###kl###km###ym####yu####yx####")

  def request_node_names(self):
    """ Request node names """

    # TODO: parse res
    return self.send_command(module="C0", command="RK")

  def request_deck_data(self):
    """ Request deck data """

    # TODO: parse res
    return self.send_command(module="C0", command="VD")

  # -------------- 3.4 X-Axis control --------------

  # -------------- 3.4.1 Movements --------------

  def position_left_x_arm_(
    self,
    x_position: int = 0
  ):
    """ Position left X-Arm

    Collision risk!

    Args:
      x_position: X-Position [0.1mm]. Must be between 0 and 30000. Default 0.
    """

    _assert_clamp(x_position, 0, 30000, "x_position_[0.1mm]")

    resp = self.send_command(
      module="C0",
      command="JX",
      xs=x_position,
    )
    return self.parse_response(resp, "")

  def position_right_x_arm_(
    self,
    x_position: int = 0
  ):
    """ Position right X-Arm

    Collision risk!

    Args:
      x_position: X-Position [0.1mm]. Must be between 0 and 30000. Default 0.
    """

    _assert_clamp(x_position, 0, 30000, "x_position_[0.1mm]")

    resp = self.send_command(
      module="C0",
      command="JX",
      xs=x_position,
    )
    return self.parse_response(resp, "")

  def move_left_x_arm_to_position_with_all_attached_components_in_z_safety_position(
    self,
    x_position: int = 0
  ):
    """ Move left X-arm to position with all attached components in Z-safety position

    Args:
      x_position: X-Position [0.1mm]. Must be between 0 and 30000. Default 0.
    """

    _assert_clamp(x_position, 0, 30000, "x_position")

    resp = self.send_command(
      module="C0",
      command="KX",
      xs=x_position,
    )
    return self.parse_response(resp, "")

  def move_right_x_arm_to_position_with_all_attached_components_in_z_safety_position(
    self,
    x_position: int = 0
  ):
    """ Move right X-arm to position with all attached components in Z-safety position

    Args:
      x_position: X-Position [0.1mm]. Must be between 0 and 30000. Default 0.
    """

    _assert_clamp(x_position, 0, 30000, "x_position")

    resp = self.send_command(
      module="C0",
      command="KR",
      xs=x_position,
    )
    return self.parse_response(resp, "")

  # -------------- 3.4.2 X-Area reservation for external access --------------

  def occupy_and_provide_area_for_external_access(
    self,
    taken_area_identification_number: int = 0,
    taken_area_left_margin: int = 0,
    taken_area_left_margin_direction: int = 0,
    taken_area_size: int = 0,
    arm_preposition_mode_related_to_taken_areas: int = 0
  ):
    """ Occupy and provide area for external access

    Args:
      taken_area_identification_number: taken area identification number. Must be between 0 and
                                        9999. Default 0.
    taken_area_left_margin: taken area left margin. Must be between 0 and 99. Default 0.
    taken_area_left_margin_direction: taken area left margin direction. 1 = negative. Must be
                                      between 0 and 1. Default 0.
    taken_area_size: taken area size. Must be between 0 and 50000. Default 0.
    arm_preposition_mode_related_to_taken_areas: 0) left arm to left & right arm to right.
                                                 1) all arms left.
                                                 2) all arms right.
    """

    _assert_clamp(taken_area_identification_number, 0, 9999, \
                  "taken_area_identification_number")
    _assert_clamp(taken_area_left_margin, 0, 99, "taken_area_left_margin")
    _assert_clamp(taken_area_left_margin_direction, 0, 1, "taken_area_left_margin_direction")
    _assert_clamp(taken_area_size, 0, 50000, "taken_area_size")
    _assert_clamp(arm_preposition_mode_related_to_taken_areas, 0, 2, \
                  "arm_preposition_mode_(related_to_taken_area)s")

    resp = self.send_command(
      module="C0",
      command="BA",
      aq=taken_area_identification_number,
      al=taken_area_left_margin,
      ad=taken_area_left_margin_direction,
      ar=taken_area_size,
      ap=arm_preposition_mode_related_to_taken_areas,
    )
    return self.parse_response(resp, "")

  def release_occupied_area(
    self,
    taken_area_identification_number: int = 0
  ):
    """ Release occupied area

    Args:
      taken_area_identification_number: taken area identification number.
                                        Must be between 0 and 9999. Default 0.
    """

    _assert_clamp(taken_area_identification_number, 0, 9999, "taken_area_identification_number")

    resp = self.send_command(
      module="C0",
      command="BB",
      aq=taken_area_identification_number,
    )
    return self.parse_response(resp, "")

  def release_all_occupied_areas(self):
    """ Release all occupied areas """

    resp = self.send_command(module="C0", command="BC")
    return resp

  # -------------- 3.4.3 X-query --------------

  def request_left_x_arm_position(self):
    """ Request left X-Arm position """

    resp = self.send_command(module="C0", command="RX")
    return self.parse_response(resp, "rx#####")

  def request_right_x_arm_position(self):
    """ Request right X-Arm position """

    resp = self.send_command(module="C0", command="QX")
    return self.parse_response(resp, "rx#####")

  def request_maximal_ranges_of_x_drives(self):
    """ Request maximal ranges of X drives """

    resp = self.send_command(module="C0", command="RU")
    return self.parse_response(resp, "")

  def request_present_wrap_size_of_installed_arms(self):
    """ Request present wrap size of installed arms """

    resp = self.send_command(module="C0", command="UA")
    return self.parse_response(resp, "")

  def request_left_x_arm_last_collision_type(self):
    """ Request left X-Arm last collision type (after error 27)

    Returns:
      False if present positions collide (not reachable),
      True if position is never reachable.
    """

    resp = self.send_command(module="C0", command="XX")
    parsed = self.parse_response(resp, "xq#")
    return parsed["xq"] == 1

  def request_right_x_arm_last_collision_type(self) -> bool:
    """ Request right X-Arm last collision type (after error 27)

    Returns:
      False if present positions collide (not reachable),
      True if position is never reachable.
    """

    resp = self.send_command(module="C0", command="XR")
    parsed = self.parse_response(resp, "xq#")
    return parsed["xq"] == 1

  # -------------- 3.5 Pipetting channel commands --------------

  # -------------- 3.5.1 Initialization --------------

  # TODO:(command) Initialize pipetting channels (discard tips)

  # -------------- 3.5.2 Tip handling commands using PIP --------------

  def pick_up_tip(
    self,
    x_positions: int = 0, # TODO: these are probably lists.
    y_positions: int = 0, # TODO: these are probably lists.
    tip_pattern: bool = True,
    tip_type: TipType = TipType.STANDARD_VOLUME,
    begin_tip_pick_up_process: int = 0,
    end_tip_pick_up_process: int = 0,
    minimum_traverse_height_at_beginning_of_a_command: int = 3600,
    pick_up_method: PickUpMethod = PickUpMethod.OUT_OF_RACK
  ):
    """ Tip Pick-up

    Args:
      x_positions: x positions [0.1mm]. Must be between 0 and 25000. Default 0.
      y_positions: y positions [0.1mm]. Must be between 0 and 6500. Default 0.
      tip_pattern: Tip pattern (channels involved).
      tip_type: Tip type.
      begin_tip_pick_up_process: Begin of tip picking up process (Z- range) [0.1mm]. Must be
          between 0 and 2. Default 0.
      end_tip_pick_up_process: End of tip picking up process (Z- range) [0.1mm]. Must be
          between 0 and 3600. Default 0.
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning
          of a command 0.1mm] (refers to all channels independent of tip pattern parameter 'tm').
          Must be between 0 and 3600. Default 3600.
      pick_up_method: Pick up method.
    """

    _assert_clamp(x_positions, 0, 25000, "x_positions")
    _assert_clamp(y_positions, 0, 6500, "y_positions")
    _assert_clamp(begin_tip_pick_up_process, 0, 3600, "begin_tip_pick_up_process")
    _assert_clamp(end_tip_pick_up_process, 0, 3600, "end_tip_pick_up_process")
    _assert_clamp(minimum_traverse_height_at_beginning_of_a_command, 0, 3600, \
                  "minimum_traverse_height_at_beginning_of_a_command")

    return self.send_command(
      module="C0",
      command="TP",
      xp=x_positions,
      yp=y_positions,
      tm=tip_pattern,
      tt=tip_type, # .rawValue?
      tp=begin_tip_pick_up_process,
      tz=end_tip_pick_up_process,
      th=minimum_traverse_height_at_beginning_of_a_command,
      td=pick_up_method,
    )

  class DiscardingMethod(enum.Enum):
    """ Tip discarding method """
    PLACE_SHIFT = 0
    DROP = 1

  def discard_tip(
    self,
    x_positions: int = 0, # TODO: these are probably lists.
    y_positions: int = 0, # TODO: these are probably lists.
    tip_pattern: bool = True,
    tip_type: TipType = TipType.STANDARD_VOLUME,
    begin_tip_deposit_process: int = 0,
    end_tip_deposit_process: int = None,
    minimum_traverse_height_at_beginning_of_a_command: int = None,
    discarding_method: DiscardingMethod = DiscardingMethod.DROP
  ):
    """ discard tip

    Args:
      x_positions: x positions [0.1mm]. Must be between 0 and 25000. Default 0.
      y_positions: y positions [0.1mm]. Must be between 0 and 6500. Default 0.
      tip_pattern: Tip pattern (channels involved). Must be between 0 and 1. Default 1.
      tip_type: Tip type. Must be between 0 and 99. Default 4.
      begin_tip_deposit_process: Begin of tip deposit process (Z- range) [0.1mm]. Must be between
          0 and 3600. Default 0.
      end_tip_deposit_process: End of tip deposit process (Z- range) [0.1mm]. Must be between 0
          and 3600.
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning of a
          command 0.1mm] (refers to all channels independent of tip pattern parameter 'tm'). Must
          be between 0 and 3600.
      discarding_method: Pick up method Pick up method. 0 = auto selection (see command TT
          parameter tu) 1 = pick up out of rack. 2 = pick up out of wash liquid (slowly). Must be
          between 0 and 2.

    If discarding is PLACE_SHIFT (0), tp/ tz = tip cone end height.
    Otherwise, tp/ tz = stop disk height.
    """

    _assert_clamp(x_positions, 0, 25000, "x_positions")
    _assert_clamp(y_positions, 0, 6500, "y_positions")
    _assert_clamp(begin_tip_deposit_process, 0, 3600, "begin_tip_deposit_process")
    _assert_clamp(end_tip_deposit_process, 0, 3600, "end_tip_deposit_process")
    _assert_clamp(minimum_traverse_height_at_beginning_of_a_command, 0, 3600, \
                  "minimum_traverse_height_at_beginning_of_a_command")

    return self.send_command(
      module="C0",
      command="TR",
      xp=x_positions,
      yp=y_positions,
      tm=tip_pattern,
      tt=tip_type,
      tp=begin_tip_deposit_process,
      tz=end_tip_deposit_process,
      th=minimum_traverse_height_at_beginning_of_a_command,
      td=discarding_method,
    )

  # TODO:(command:TW) Tip Pick-up for DC wash procedure

  # -------------- 3.5.3 Liquid handling commands using PIP --------------

  # TODO:(command:DC) Set multiple dispense values using PIP

  def aspirate_pip(
    self,
    aspiration_type: int = 0,
    tip_pattern: bool = True,
    x_positions: int = 0,
    y_positions: int = 0,
    minimum_traverse_height_at_beginning_of_a_command: int = 3600,
    min_z_endpos: int = 3600,
    lld_search_height: int = 0,
    clot_detection_height: int = 4,
    liquid_surface_no_lld: int = 3600,
    pull_out_distance_transport_air: int = 50,
    second_section_height: int = 0,
    second_section_ratio: int = 0,
    minimum_height: int = 3600,
    immersion_depth: int = 0,
    immersion_depth_direction: int = 0,
    surface_following_distance: int = 0,
    aspiration_volume: int = 0,
    aspiration_speed: int = 500,
    transport_air_volume: int = 0,
    blow_out_air_volume: int = 200,
    pre_wetting_volume: int = 0,
    lld_mode: int = 1,
    gamma_lld_sensitivity: int = 1,
    dp_lld_sensitivity: int = 1,
    aspirate_position_above_z_touch_off: int = 5,
    detection_height_difference_for_dual_lld: int = 0,
    swap_speed: int = 100,
    settling_time: int = 5,
    homogenization_volume: int = 0,
    homogenization_cycles: int = 0,
    homogenization_position_from_liquid_surface: int = 250,
    homogenization_speed: int = 500,
    homogenization_surface_following_distance: int = 0,
    limit_curve_index: int = 0,
    tadm_algorithm: bool = False,
    recording_mode: int = 0,

    # For second section aspiration only
    use_2nd_section_aspiration: bool = False,
    retract_height_over_2nd_section_to_empty_tip: int = 60,
    dispensation_speed_during_emptying_tip: int = 468,
    dosing_drive_speed_during_2nd_section_search: int = 468,
    z_drive_speed_during_2nd_section_search: int = 215,
    cup_upper_edge: int = 3600,
    ratio_liquid_rise_to_tip_deep_in: int = 16246,
    immersion_depth_2nd_section: int = 30
  ):
    """ aspirate pip

    Aspiration of liquid using PIP.

    It's not really clear what second section aspiration is, but it does not seem to be used
    very often. Probably safe to ignore it.

    LLD restrictions!
    - "dP and Dual LLD" are used in aspiration only. During dispensation LLD is set to OFF.
    - "side touch off" turns LLD & "Z touch off" to OFF , is not available for simultaneous
      Asp/Disp. command

    Args:
      aspiration_type: Type of aspiration (0 = simple;1 = sequence; 2 = cup emptied).
                        Must be between 0 and 2. Default 0.
      tip_pattern: Tip pattern (channels involved). Default True.
      x_positions: x positions [0.1mm]. Must be between 0 and 25000. Default 0.
      y_positions: y positions [0.1mm]. Must be between 0 and 6500. Default 0.
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning of
          a command 0.1mm] (refers to all channels independent of tip pattern parameter 'tm').
          Must be between 0 and 3600. Default 3600.
      min_z_endpos: Minimum z-Position at end of a command [0.1 mm] (refers to all channels
          independent of tip pattern parameter 'tm'). Must be between 0 and 3600. Default 3600.
      lld_search_height: LLD search height [0.1 mm]. Must be between 0 and 3600. Default 0.
      clot_detection_height: Check height of clot detection above current surface (as computed)
          of the liquid [0.1mm]. Must be between 60 and 500. Default 4.
      liquid_surface_no_lld: Liquid surface at function without LLD [0.1mm]. Must be between 0
          and 3600. Default 3600.
      pull_out_distance_transport_air: pull out distance to take transport air in function
          without LLD [0.1mm]. Must be between 0 and 3600. Default 50.
      second_section_height: Tube 2nd section height measured from "zx" [0.1mm]. Must be
          between 0 and 3600. Default 0.
      second_section_ratio: Tube 2nd section ratio (see Fig. 2 in fw guide). Must be between
          0 and 10000. Default 0.
      minimum_height: Minimum height (maximum immersion depth) [0.1 mm]. Must be between 0 and
          3600. Default 3600.
      immersion_depth: Immersion depth [0.1mm]. Must be between 0 and 3600. Default 0.
      immersion_depth_direction: Direction of immersion depth (0 = go deeper, 1 = go up out
          of liquid). Must be between 0 and 1. Default 0.
      surface_following_distance: Surface following distance during aspiration [0.1mm]. Must
          be between 0 and 3600. Default 0.
      aspiration_volume: Aspiration volume [0.1ul]. Must be between 0 and 12500. Default 0.
      aspiration_speed: Aspiration speed [0.1ul/s]. Must be between 4 and 5000. Default 500.
      transport_air_volume: Transport air volume [0.1ul]. Must be between 0 and 500. Default 0.
      blow_out_air_volume: Blow-out air volume [0.1ul]. Must be between 0 and 9999. Default 200.
      pre_wetting_volume: Pre-wetting volume. Must be between 0 and 999. Default 0.
      lld_mode: LLD mode (0 = off, 1 = gamma, 2 = dP, 3 = dual, 4 = Z touch off). Must be
            between 0 and 4. Default 1.
      gamma_lld_sensitivity: gamma LLD sensitivity (1= high, 4=low). Must be between 1 and
            4. Default 1.
      dp_lld_sensitivity: delta p LLD sensitivity (1= high, 4=low). Must be between 1 and
            4. Default 1.
      aspirate_position_above_z_touch_off: aspirate position above Z touch off [0.1mm]. Must
            be between 0 and 100. Default 5.
      detection_height_difference_for_dual_lld: Difference in detection height for dual
            LLD [0.1 mm]. Must be between 0 and 99. Default 0.
      swap_speed: Swap speed (on leaving liquid) [0.1mm/s]. Must be between 3 and 1600.
            Default 100.
      settling_time: Settling time [0.1s]. Must be between 0 and 99. Default 5.
      homogenization_volume: Homogenization volume [0.1ul]. Must be between 0 and 12500. Default 0
      homogenization_cycles: Number of homogenization cycles. Must be between 0 and 99. Default 0.
      homogenization_position_from_liquid_surface: Homogenization position in Z- direction from
          liquid surface (LLD or absolute terms) [0.1mm]. Must be between 0 and 900. Default 250.
      homogenization_speed: Speed of homogenization [0.1ul/s]. Must be between 4 and 5000.
          Default 500.
      homogenization_surface_following_distance: Surface following distance during
          homogenization [0.1mm]. Must be between 4 and 3600. Default 0.
      limit_curve_index: limit curve index. Must be between 0 and 999. Default 0.
      tadm_algorithm: TADM algorithm. Default False.
      recording_mode: Recording mode 0 : no 1 : TADM errors only 2 : all TADM measurement. Must
          be between 0 and 2. Default 0.
      use_2nd_section_aspiration: 2nd section aspiration. Default False.
      retract_height_over_2nd_section_to_empty_tip: Retract height over 2nd section to empty
          tip [0.1mm]. Must be between 0 and 3600. Default 60.
      dispensation_speed_during_emptying_tip: Dispensation speed during emptying tip [0.1ul/s]
            Must be between 4 and 5000. Default 468.
      dosing_drive_speed_during_2nd_section_search: Dosing drive speed during 2nd section
          search [0.1ul/s]. Must be between 4 and 5000. Default 468.
      z_drive_speed_during_2nd_section_search: Z drive speed during 2nd section search [0.1mm/s].
          Must be between 3 and 1600. Default 215.
      cup_upper_edge: Cup upper edge [0.1mm]. Must be between 0 and 3600. Default 3600.
      ratio_liquid_rise_to_tip_deep_in: Ratio liquid rise to tip deep in [1/100000]. Must be
          between 0 and 50000. Default 16246.
      immersion_depth_2nd_section: Immersion depth 2nd section [0.1mm]. Must be between 0 and
          3600. Default 30.
    """

    _assert_clamp(aspiration_type, 0, 2, "aspiration_type")
    _assert_clamp(x_positions, 0, 25000, "x_positions")
    _assert_clamp(y_positions, 0, 6500, "y_positions")
    _assert_clamp(minimum_traverse_height_at_beginning_of_a_command, 0, 3600, \
                  "minimum_traverse_height_at_beginning_of_a_command")
    _assert_clamp(min_z_endpos, 0, 3600, "min_z_endpos")
    _assert_clamp(lld_search_height, 0, 3600, "lld_search_height")
    _assert_clamp(clot_detection_height, 60, 500, "clot_detection_height")
    _assert_clamp(liquid_surface_no_lld, 0, 3600, "liquid_surface_no_lld")
    _assert_clamp(pull_out_distance_transport_air, 0, 3600, "pull_out_distance_transport_air")
    _assert_clamp(second_section_height, 0, 3600, "second_section_height")
    _assert_clamp(second_section_ratio, 0, 10000, "second_section_ratio")
    _assert_clamp(minimum_height, 0, 3600, "minimum_height")
    _assert_clamp(immersion_depth, 0, 3600, "immersion_depth")
    _assert_clamp(immersion_depth_direction, 0, 1, "immersion_depth_direction")
    _assert_clamp(surface_following_distance, 0, 3600, "surface_following_distance")
    _assert_clamp(aspiration_volume, 0, 12500, "aspiration_volume")
    _assert_clamp(aspiration_speed, 4, 5000, "aspiration_speed")
    _assert_clamp(transport_air_volume, 0, 500, "transport_air_volume")
    _assert_clamp(blow_out_air_volume, 0, 9999, "blow_out_air_volume")
    _assert_clamp(pre_wetting_volume, 0, 999, "pre_wetting_volume")
    _assert_clamp(lld_mode, 0, 4, "lld_mode")
    _assert_clamp(gamma_lld_sensitivity, 1, 4, "gamma_lld_sensitivity")
    _assert_clamp(dp_lld_sensitivity, 1, 4, "dp_lld_sensitivity")
    _assert_clamp(aspirate_position_above_z_touch_off, 0, 100, \
                  "aspirate_position_above_z_touch_off")
    _assert_clamp(detection_height_difference_for_dual_lld, 0, 99, \
                  "detection_height_difference_for_dual_lld")
    _assert_clamp(swap_speed, 3, 1600, "swap_speed")
    _assert_clamp(settling_time, 0, 99, "settling_time")
    _assert_clamp(homogenization_volume, 0, 12500, "homogenization_volume")
    _assert_clamp(homogenization_cycles, 0, 99, "homogenization_cycles")
    _assert_clamp(homogenization_position_from_liquid_surface, 0, 900, \
                  "homogenization_position_from_liquid_surface")
    _assert_clamp(homogenization_speed, 4, 5000, "homogenization_speed")
    _assert_clamp(homogenization_surface_following_distance, 4, 3600, \
                  "homogenization_surface_following_distance")
    _assert_clamp(limit_curve_index, 0, 999, "limit_curve_index")
    _assert_clamp(recording_mode, 0, 2, "recording_mode")
    _assert_clamp(retract_height_over_2nd_section_to_empty_tip, 0, 3600, \
                  "retract_height_over_2nd_section_to_empty_tip")
    _assert_clamp(dispensation_speed_during_emptying_tip, 4, 5000, \
                  "dispensation_speed_during_emptying_tip")
    _assert_clamp(dosing_drive_speed_during_2nd_section_search, 4, 5000, \
                  "dosing_drive_speed_during_2nd_section_search")
    _assert_clamp(z_drive_speed_during_2nd_section_search, 3, 1600, \
                  "z_drive_speed_during_2nd_section_search")
    _assert_clamp(cup_upper_edge, 0, 3600, "cup_upper_edge")
    _assert_clamp(ratio_liquid_rise_to_tip_deep_in, 0, 50000, "ratio_liquid_rise_to_tip_deep_in")
    _assert_clamp(immersion_depth_2nd_section, 0, 3600, "immersion_depth_2nd_section")

    resp = self.send_command(
      module="C0",
      command="AS",
      at=aspiration_type,
      tm=tip_pattern,
      xp=x_positions,
      yp=y_positions,
      th=minimum_traverse_height_at_beginning_of_a_command,
      te=min_z_endpos,
      lp=lld_search_height,
      ch=clot_detection_height,
      zl=liquid_surface_no_lld,
      po=pull_out_distance_transport_air,
      zu=second_section_height,
      zr=second_section_ratio,
      zx=minimum_height,
      ip=immersion_depth,
      it=immersion_depth_direction,
      fp=surface_following_distance,
      av=aspiration_volume,
      as_=aspiration_speed,
      ta=transport_air_volume,
      ba=blow_out_air_volume,
      oa=pre_wetting_volume,
      lm=lld_mode,
      ll=gamma_lld_sensitivity,
      lv=dp_lld_sensitivity,
      zo=aspirate_position_above_z_touch_off,
      ld=detection_height_difference_for_dual_lld,
      de=swap_speed,
      wt=settling_time,
      mv=homogenization_volume,
      mc=homogenization_cycles,
      mp=homogenization_position_from_liquid_surface,
      ms=homogenization_speed,
      mh=homogenization_surface_following_distance,
      gi=limit_curve_index,
      gj=tadm_algorithm,
      gk=recording_mode,
      lk=use_2nd_section_aspiration,
      ik=retract_height_over_2nd_section_to_empty_tip,
      sd=dispensation_speed_during_emptying_tip,
      se=dosing_drive_speed_during_2nd_section_search,
      sz=z_drive_speed_during_2nd_section_search,
      io=cup_upper_edge,
      il=ratio_liquid_rise_to_tip_deep_in,
      in_=immersion_depth_2nd_section,
    )
    return resp

  # TODO: a lot of these probably need to be lists.
  def dispense_pip(
    self,
    dispensing_mode: int = 0,
    tip_pattern: bool = True,
    x_positions: int = 0,
    y_positions: int = 0,
    minimum_height: int = 3600,
    lld_search_height: int = 0,
    liquid_surface_no_lld: int = 3600,
    pull_out_distance_transport_air: int = 50,
    immersion_depth: int = 0,
    immersion_depth_direction: int = 0,
    surface_following_distance: int = 0,
    second_section_height: int = 0,
    second_section_ratio: int = 0,
    minimum_traverse_height_at_beginning_of_a_command: int = 3600,
    min_z_endpos: int = 3600,
    dispense_volume: int = 0,
    dispense_speed: int = 500,
    cut_off_speed: int = 250,
    stop_back_volume: int = 0,
    transport_air_volume: int = 0,
    blow_out_air_volume: int = 200,
    lld_mode: int = 1,
    side_touch_off_distance: int = 1,
    dispense_position_above_z_touch_off: int = 5,
    gamma_lld_sensitivity: int = 1,
    dp_lld_sensitivity: int = 1,
    swap_speed: int = 100,
    settling_time: int = 5,
    mix_volume: int = 0,
    mix_cycles: int = 0,
    mix_position_from_liquid_surface: int = 250,
    mix_speed: int = 500,
    mix_surface_following_distance: int = 0,
    limit_curve_index: int = 0,
    tadm_algorithm: bool = False,
    recording_mode: int = 0
  ):
    """ dispense pip

    Dispensing of liquid using PIP.

    LLD restrictions!
    - "dP and Dual LLD" are used in aspiration only. During dispensation LLD is set to OFF.
    - "side touch off" turns LLD & "Z touch off" to OFF , is not available for simultaneous
      Asp/Disp. command

    Args:
      dispensing_mode: Type of dispensing mode 0 = Partial volume in jet mode
                       1 = Blow out in jet mode 2 = Partial volume at surface
                       3 = Blow out at surface 4 = Empty tip at fix position.
      tip_pattern: Tip pattern (channels involved). Default True.
      x_positions: x positions [0.1mm]. Must be between 0 and 25000. Default 0.
      y_positions: y positions [0.1mm]. Must be between 0 and 6500. Default 0.
      minimum_height: Minimum height (maximum immersion depth) [0.1 mm]. Must be between 0 and
                      3600. Default 3600.
      lld_search_height: LLD search height [0.1 mm]. Must be between 0 and 3600. Default 0.
      liquid_surface_no_lld: Liquid surface at function without LLD [0.1mm]. Must be between 0 and
                             3600. Default 3600.
      pull_out_distance_transport_air: pull out distance to take transport air in function without
                                       LLD [0.1mm]. Must be between 0 and 3600. Default 50.
      immersion_depth: Immersion depth [0.1mm]. Must be between 0 and 3600. Default 0.
      immersion_depth_direction: Direction of immersion depth (0 = go deeper, 1 = go up out of
                                 liquid). Must be between 0 and 1. Default 0.
      surface_following_distance: Surface following distance during aspiration [0.1mm]. Must be
                                  between 0 and 3600. Default 0.
      second_section_height: Tube 2nd section height measured from "zx" [0.1mm]. Must be between
                             0 and 3600. Default 0.
      second_section_ratio: Tube 2nd section ratio (see Fig. 2 in fw guide). Must be between 0 and
                            10000. Default 0.
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning of a
                                                         command 0.1mm] (refers to all channels
                                                         independent of tip pattern parameter 'tm').
                                                         Must be between 0 and 3600. Default 3600.
      min_z_endpos: Minimum z-Position at end of a command [0.1 mm] (refers to all channels
                    independent of tip pattern parameter 'tm'). Must be between 0 and 3600.
                    Default 3600.
      dispense_volume: Dispense volume [0.1ul]. Must be between 0 and 12500. Default 0.
      dispense_speed: Dispense speed [0.1ul/s]. Must be between 4 and 5000. Default 500.
      cut_off_speed: Cut-off speed [0.1ul/s]. Must be between 4 and 5000. Default 250.
      stop_back_volume: Stop back volume [0.1ul]. Must be between 0 and 180. Default 0.
      transport_air_volume: Transport air volume [0.1ul]. Must be between 0 and 500. Default 0.
      blow_out_air_volume: Blow-out air volume [0.1ul]. Must be between 0 and 9999. Default 200.
      lld_mode: LLD mode (0 = off, 1 = gamma, 2 = dP, 3 = dual, 4 = Z touch off). Must be between 0
                and 4. Default 1.
      side_touch_off_distance: side touch off distance [0.1 mm] (0 = OFF). Must be between 0 and 45.
                               Default 1.
      dispense_position_above_z_touch_off: dispense position above Z touch off [0.1 s] (0 = OFF)
                                           Turns LLD & Z touch off to OFF if ON!. Must be between
                                           0 and 100. Default 5.
      gamma_lld_sensitivity: gamma LLD sensitivity (1= high, 4=low). Must be between 1 and 4.
                             Default 1.
      dp_lld_sensitivity: delta p LLD sensitivity (1= high, 4=low). Must be between 1 and 4.
                          Default 1.
      swap_speed: Swap speed (on leaving liquid) [0.1mm/s]. Must be between 3 and 1600.
                  Default 100.
      settling_time: Settling time [0.1s]. Must be between 0 and 99. Default 5.
      mix_volume: Mix volume [0.1ul]. Must be between 0 and 12500. Default 0.
      mix_cycles: Number of mix cycles. Must be between 0 and 99. Default 0.
      mix_position_from_liquid_surface: Mix position in Z- direction from liquid surface (LLD or
                                        absolute terms) [0.1mm]. Must be between 0 and 900.
                                        Default 250.
      mix_speed: Speed of mixing [0.1ul/s]. Must be between 4 and 5000. Default 500.
      mix_surface_following_distance: Surface following distance during mixing [0.1mm]. Must be
                                      between 4 and 3600. Default 0.
      limit_curve_index: limit curve index. Must be between 0 and 999. Default 0.
      tadm_algorithm: TADM algorithm. Default False.
      recording_mode: Recording mode 0 : no 1 : TADM errors only 2 : all TADM measurement. Must
                      be between 0 and 2. Default 0.
    """

    _assert_clamp(dispensing_mode, 0, 4, "dispensing_mode")
    _assert_clamp(x_positions, 0, 25000, "x_positions")
    _assert_clamp(y_positions, 0, 6500, "y_positions")
    _assert_clamp(minimum_height, 0, 3600, "minimum_height")
    _assert_clamp(lld_search_height, 0, 3600, "lld_search_height")
    _assert_clamp(liquid_surface_no_lld, 0, 3600, "liquid_surface_no_lld")
    _assert_clamp(pull_out_distance_transport_air, 0, 3600, "pull_out_distance_transport_air")
    _assert_clamp(immersion_depth, 0, 3600, "immersion_depth")
    _assert_clamp(immersion_depth_direction, 0, 1, "immersion_depth_direction")
    _assert_clamp(surface_following_distance, 0, 3600, "surface_following_distance")
    _assert_clamp(second_section_height, 0, 3600, "second_section_height")
    _assert_clamp(second_section_ratio, 0, 10000, "second_section_ratio")
    _assert_clamp(minimum_traverse_height_at_beginning_of_a_command, 0, 3600, \
                  "minimum_traverse_height_at_beginning_of_a_command")
    _assert_clamp(min_z_endpos, 0, 3600, "min_z_endpos")
    _assert_clamp(dispense_volume, 0, 12500, "dispense_volume")
    _assert_clamp(dispense_speed, 4, 5000, "dispense_speed")
    _assert_clamp(cut_off_speed, 4, 5000, "cut_off_speed")
    _assert_clamp(stop_back_volume, 0, 180, "stop_back_volume")
    _assert_clamp(transport_air_volume, 0, 500, "transport_air_volume")
    _assert_clamp(blow_out_air_volume, 0, 9999, "blow_out_air_volume")
    _assert_clamp(lld_mode, 0, 4, "lld_mode")
    _assert_clamp(side_touch_off_distance, 0, 45, "side_touch_off_distance")
    _assert_clamp(dispense_position_above_z_touch_off, 0, 100, \
                  "dispense_position_above_z_touch_off")
    _assert_clamp(gamma_lld_sensitivity, 1, 4, "gamma_lld_sensitivity")
    _assert_clamp(dp_lld_sensitivity, 1, 4, "dp_lld_sensitivity")
    _assert_clamp(swap_speed, 3, 1600, "swap_speed")
    _assert_clamp(settling_time, 0, 99, "settling_time")
    _assert_clamp(mix_volume, 0, 12500, "mix_volume")
    _assert_clamp(mix_cycles, 0, 99, "mix_cycles")
    _assert_clamp(mix_position_from_liquid_surface, 0, 900, "mix_position_from_liquid_surface")
    _assert_clamp(mix_speed, 4, 5000, "mix_speed")
    _assert_clamp(mix_surface_following_distance, 4, 3600, "mix_surface_following_distance")
    _assert_clamp(limit_curve_index, 0, 999, "limit_curve_index")
    _assert_clamp(recording_mode, 0, 2, "recording_mode")

    return self.send_command(
      module="C0",
      command="DS",
      dm=dispensing_mode,
      tm=tip_pattern,
      xp=x_positions,
      yp=y_positions,
      zx=minimum_height,
      lp=lld_search_height,
      zl=liquid_surface_no_lld,
      po=pull_out_distance_transport_air,
      ip=immersion_depth,
      it=immersion_depth_direction,
      fp=surface_following_distance,
      zu=second_section_height,
      zr=second_section_ratio,
      th=minimum_traverse_height_at_beginning_of_a_command,
      te=min_z_endpos,
      av=dispense_volume,
      as_=dispense_speed,
      ss=cut_off_speed,
      rv=stop_back_volume,
      ta=transport_air_volume,
      ba=blow_out_air_volume,
      lm=lld_mode,
      dj=side_touch_off_distance,
      zo=dispense_position_above_z_touch_off,
      ll=gamma_lld_sensitivity,
      lv=dp_lld_sensitivity,
      de=swap_speed,
      wt=settling_time,
      mv=mix_volume,
      mc=mix_cycles,
      mp=mix_position_from_liquid_surface,
      ms=mix_speed,
      mh=mix_surface_following_distance,
      gi=limit_curve_index,
      gj=tadm_algorithm,
      gk=recording_mode,
    )

  # TODO:(command:DA) Simultaneous aspiration & dispensation of liquid

  # TODO:(command:DF) Dispense on fly using PIP (Partial volume in jet mode)

  # TODO:(command:LW) DC Wash procedure using PIP

  # -------------- 3.5.5 CoRe gripper commands --------------

  # TODO:(command) All CoRe gripper commands
  # TODO:(command:ZT)
  # TODO:(command:ZS)
  # TODO:(command:ZP)
  # TODO:(command:ZR)
  # TODO:(command:ZM)
  # TODO:(command:ZO)
  # TODO:(command:ZB)

  # -------------- 3.5.6 Adjustment & movement commands --------------

  # TODO:(command:JY) Position all pipetting channels in Y-direction

  # TODO:(command:JZ) Position all pipetting channels in Z-direction

  def position_single_pipetting_channel_in_y_direction(
    self,
    pipetting_channel_index: int = 1,
    y_position: int = 0
  ):
    """ Position single pipetting channel in Y-direction

    Args:
      pipetting_channel_index: Index of pipetting channel. Must be between 1 and 16. Default 1.
      y_position: y position [0.1mm]. Must be between 0 and 6500. Default 0.
    """

    _assert_clamp(pipetting_channel_index, 1, 16, "pipetting_channel_index")
    _assert_clamp(y_position, 0, 6500, "y_position")

    return self.send_command(
      module="C0",
      command="KY",
      pn=pipetting_channel_index,
      yj=y_position,
    )

  def position_single_pipetting_channel_in_z_direction(
    self,
    pipetting_channel_index: int = 1,
    z_position: int = 0
  ):
    """ Position single pipetting channel in Z-direction

    Args:
      pipetting_channel_index: Index of pipetting channel. Must be between 1 and 16. Default 1.
      z_position: y position [0.1mm]. Must be between 0 and 6500. Default 0.
    """

    _assert_clamp(pipetting_channel_index, 1, 16, "pipetting_channel_index")
    _assert_clamp(z_position, 0, 6500, "z_position")

    return self.send_command(
      module="C0",
      command="KZ",
      pn=pipetting_channel_index,
      zp=z_position,
    )

  # TODO:(command:XL) Search for Teach in signal using pipetting channel n in X-direction

  def spread_pip_channels(self):
    """ Spread PIP channels """

    resp = self.send_command(module="C0", command="JE")
    return self.parse_response(resp, "") # TODO: what does `( Pn##/##)` response mean?

  def move_all_pipetting_channels_to_defined_position(
    self,
    tip_pattern: bool = True,
    x_positions: int = 0,
    y_positions: int = 0,
    minimum_traverse_height_at_beginning_of_command: int = 3600,
    z_endpos: int = 0
  ):
    """ Move all pipetting channels to defined position

    Args:
      tip_pattern: Tip pattern (channels involved). Default True.
      x_positions: x positions [0.1mm]. Must be between 0 and 25000. Default 0.
      y_positions: y positions [0.1mm]. Must be between 0 and 6500. Default 0.
      minimum_traverse_height_at_beginning_of_command: Minimum traverse height at beginning of a
                                                       command 0.1mm] (refers to all channels
                                                       independent of tip pattern parameter 'tm').
                                                       Must be between 0 and 3600. Default 3600.
      z_endpos: Z-Position at end of a command [0.1 mm] (refers to all channels independent of tip
                pattern parameter 'tm'). Must be between 0 and 3600. Default 0.
    """

    _assert_clamp(x_positions, 0, 25000, "x_positions")
    _assert_clamp(y_positions, 0, 6500, "y_positions")
    _assert_clamp(minimum_traverse_height_at_beginning_of_command, 0, 3600, \
                  "minimum_traverse_height_at_beginning_of_command")
    _assert_clamp(z_endpos, 0, 3600, "z_endpos")

    return self.send_command(
      module="C0",
      command="JM",
      tm=tip_pattern,
      xp=x_positions,
      yp=y_positions,
      th=minimum_traverse_height_at_beginning_of_command,
      zp=z_endpos,
    )

  # TODO:(command:JR): teach rack using pipetting channel n

  def position_max_free_y_for_n(
    self,
    pipetting_channel_index: int = 1
  ):
    """ Position all pipetting channels so that there is maximum free Y range for channel n

    Args:
      pipetting_channel_index: Index of pipetting channel. Must be between 1 and 16. Default 1.
    """

    _assert_clamp(pipetting_channel_index, 1, 16, "pipetting_channel_index")

    return self.send_command(
      module="C0",
      command="KZ",
      pn=pipetting_channel_index,
    )

  def move_all_channels_in_z_safety(self):
    """ Move all pipetting channels in Z-safety position """

    resp = self.send_command(module="C0", command="ZA")
    return self.parse_response(resp, "")

  # -------------- 3.5.7 PIP query --------------

  # TODO:(command:RY): Request Y-Positions of all pipetting channels

  def request_y_pos_channel_n(
    self,
    pipetting_channel_index: int = 1
  ):
    """ Request Y-Position of Pipetting channel n

    Args:
      pipetting_channel_index: Index of pipetting channel. Must be between 1 and 16. Default 1.
    """

    _assert_clamp(pipetting_channel_index, 1, 16, "pipetting_channel_index")

    resp = self.send_command(
      module="C0",
      command="RB",
      pn=pipetting_channel_index,
    )
    return self.parse_response(resp, "rb####")

  # TODO:(command:RZ): Request Z-Positions of all pipetting channels

  def request_z_pos_channel_n(
    self,
    pipetting_channel_index: int = 1
  ):
    """ Request Z-Position of Pipetting channel n

    Args:
      pipetting_channel_index: Index of pipetting channel. Must be between 1 and 16. Default 1.

    Returns:
      Z-Position of channel n [0.1mm]. Taking into account tip presence and length.
    """

    _assert_clamp(pipetting_channel_index, 1, 16, "pipetting_channel_index")

    resp = self.send_command(
      module="C0",
      command="RD",
      pn=pipetting_channel_index,
    )
    return self.parse_response(resp, "rd####")

  def request_query_tip_presence(self):
    """ Request query tip presence on each channel

    Returns:
      0 = no tip, 1 = Tip in gripper (for each channel)
    """

    resp = self.send_command(module="C0", command="RT")
    return self.parse_response(resp, "rt# (n)")

  def request_pip_height_last_lld(self):
    """ Request PIP height of last LLD

    Returns:
      LLD height of all channels
    """

    resp = self.send_command(module="C0", command="RL")
    return self.parse_response(resp, "lh# (n)")

  def request_tadm_status(self):
    """ Request PIP height of last LLD

    Returns:
      TADM channel status 0 = off, 1 = on
    """

    resp = self.send_command(module="C0", command="QS")
    return self.parse_response(resp, "qs# (n)")

  # TODO:(command:FS) Request PIP channel dispense on fly status
  # TODO:(command:VE) Request PIP channel 2nd section aspiration data

  # -------------- 3.6 XL channel commands --------------

  # TODO: all XL channel commands

  # -------------- 3.6.1 Initialization XL --------------

  # TODO:(command:LI)

  # -------------- 3.6.2 Tip handling commands using XL --------------

  # TODO:(command:LP)
  # TODO:(command:LR)

  # -------------- 3.6.3 Liquid handling commands using XL --------------

  # TODO:(command:LA)
  # TODO:(command:LD)
  # TODO:(command:LB)
  # TODO:(command:LC)

  # -------------- 3.6.4 Wash commands using XL channel --------------

  # TODO:(command:LE)
  # TODO:(command:LF)

  # -------------- 3.6.5 XL CoRe gripper commands --------------

  # TODO:(command:LT)
  # TODO:(command:LS)
  # TODO:(command:LU)
  # TODO:(command:LV)
  # TODO:(command:LM)
  # TODO:(command:LO)
  # TODO:(command:LG)

  # -------------- 3.6.6 Adjustment & movement commands CP --------------

  # TODO:(command:LY)
  # TODO:(command:LZ)
  # TODO:(command:LH)
  # TODO:(command:LJ)
  # TODO:(command:XM)
  # TODO:(command:LL)
  # TODO:(command:LQ)
  # TODO:(command:LK)
  # TODO:(command:UE)

  # -------------- 3.6.7 XL channel query --------------

  # TODO:(command:UY)
  # TODO:(command:UB)
  # TODO:(command:UZ)
  # TODO:(command:UD)
  # TODO:(command:UT)
  # TODO:(command:UL)
  # TODO:(command:US)
  # TODO:(command:UF)

  # -------------- 3.7 Tube gripper commands --------------

  # TODO: all tube gripper commands

  # -------------- 3.7.1 Movements --------------

  # TODO:(command:FC)
  # TODO:(command:FD)
  # TODO:(command:FO)
  # TODO:(command:FT)
  # TODO:(command:FU)
  # TODO:(command:FJ)
  # TODO:(command:FM)
  # TODO:(command:FW)

  # -------------- 3.7.2 Tube gripper query --------------

  # TODO:(command:FQ)
  # TODO:(command:FN)

  # -------------- 3.8 Imaging channel commands --------------

  # TODO: all imaging commands

  # -------------- 3.8.1 Movements --------------

  # TODO:(command:IC)
  # TODO:(command:ID)
  # TODO:(command:IM)
  # TODO:(command:IJ)

  # -------------- 3.8.2 Imaging channel query --------------

  # TODO:(command:IN)

  # -------------- 3.9 Robotic channel commands --------------

  # -------------- 3.9.1 Initialization --------------

  # TODO:(command:OI)

  # -------------- 3.9.2 Cap handling commands --------------

  # TODO:(command:OP)
  # TODO:(command:OQ)

  # -------------- 3.9.3 Adjustment & movement commands --------------

  # TODO:(command:OY)
  # TODO:(command:OZ)
  # TODO:(command:OH)
  # TODO:(command:OJ)
  # TODO:(command:OX)
  # TODO:(command:OM)
  # TODO:(command:OF)
  # TODO:(command:OG)

  # -------------- 3.9.4 Robotic channel query --------------

  # TODO:(command:OA)
  # TODO:(command:OB)
  # TODO:(command:OC)
  # TODO:(command:OD)
  # TODO:(command:OT)

  # -------------- 3.10 CoRe 96 Head commands --------------

  # -------------- 3.10.1 Initialization --------------

  def initialize_core_96_head(
    self,
    x_position: int = 0,
    x_direction: int = 0,
    y_position: int = 5743,
    z_deposit_position: int = 3425,
    z_position_at_the_command_end: int = 3425
  ):
    """ Initialize CoRe 96 Head

    Initialize CoRe 96 Head. Dependent to configuration initialization change.

    Args:
      x_position: X-Position [0.1mm] (discard position of tip A1 ). Must be between 0 and 30000.
                  Default 0.
      x_direction: X-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      y_position: Y-Position [0.1mm] (discard position of tip A1 ). Must be between 1054 and 5743.
                  Default 5743.
      z-_deposit_position_[0.1mm]: Z- deposit position [0.1mm] (collar bearing position). Must be
                                   between 0 and 3425. Default 3425.
      z_position_at_the_command_end: Z-Position at the command end [0.1mm]. Must be between 0 and
                                     3425. Default 3425.
    """

    _assert_clamp(x_position, 0, 30000, "x_position")
    _assert_clamp(x_direction, 0, 1, "x_direction")
    _assert_clamp(y_position, 1054, 5743, "y_position")
    _assert_clamp(z_deposit_position, 0, 3425, "z_deposit_position")
    _assert_clamp(z_position_at_the_command_end, 0, 3425, "z_position_at_the_command_end")

    return self.send_command(
      module="C0",
      command="EI",
      xs=x_position,
      xd=x_direction,
      yh=y_position,
      za=z_deposit_position,
      ze=z_position_at_the_command_end,
    )

  def move_core_96_to_safe_position(self):
    """ Move CoRe 96 Head to Z save position """

    resp = self.send_command(module="C0", command="EV")
    return self.parse_response(resp, "")

  # -------------- 3.10.2 Tip handling using CoRe 96 Head --------------

  def pick_up_tip_core96(
    self,
    x_position: int = 0,
    x_direction: int = 0,
    y_position: int = 5600,
    tip_type: TipType = TipType.STANDARD_VOLUME,
    tip_pick_up_method: int = 2,
    z_deposit_position: int = 3425,
    minimum_traverse_height_at_beginning_of_a_command: int = 3425,
    minimum_height_command_end: int = 3425
  ):
    """ Pick up tips with CoRe 96 head

    Args:
      x_position: x position [0.1mm]. Must be between 0 and 30000. Default 0.
      x_direction: X-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      y_position: y position [0.1mm]. Must be between 1080 and 5600. Default 5600.
      tip_type: Tip type.
      tip_pick_up_method: Tip pick up method. 0 = pick up from rack. 1 = pick up from C0Re 96 tip
                          wash station. 2 = pick up with " full volume blow out"
      z_deposit_position: Z- deposit position [0.1mm] (collar bearing position) Must bet between
                          0 and 3425. Default 3425.
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning
          of a command [0.1mm]. Must be between 0 and 3425.
      minimum_height_command_end: Minimal height at command end [0.1 mm] Must be between 0 and 3425.
    """

    _assert_clamp(x_position, 0, 30000, "x_position")
    _assert_clamp(x_position, 0, 1, "x_direction")
    _assert_clamp(y_position, 1080, 5600, "y_position")
    _assert_clamp(z_deposit_position, 0, 3425, "z_deposit_position")
    _assert_clamp(minimum_traverse_height_at_beginning_of_a_command, 0, 3425, \
                  "minimum_traverse_height_at_beginning_of_a_command")
    _assert_clamp(minimum_height_command_end, 0, 3425, "minimum_height_command_end")

    return self.send_command(
      module="C0",
      command="EP",
      xs=x_position,
      xd=x_direction,
      yh=y_position,
      tt=tip_type, # .rawValue?
      wu=tip_pick_up_method,
      za=z_deposit_position,
      zh=minimum_traverse_height_at_beginning_of_a_command,
      ze=minimum_height_command_end
    )

  def discard_tip_core96(
    self,
    x_position: int = 0,
    x_direction: int = 0,
    y_position: int = 5600,
    z_deposit_position: int = 3425,
    minimum_traverse_height_at_beginning_of_a_command: int = 3425,
    minimum_height_command_end: int = 3425
  ):
    """ Discard tips with CoRe 96 head

    Args:
      x_position: x position [0.1mm]. Must be between 0 and 30000. Default 0.
      x_direction: X-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      y_position: y position [0.1mm]. Must be between 1080 and 5600. Default 5600.
      tip_type: Tip type.
      tip_pick_up_method: Tip pick up method. 0 = pick up from rack. 1 = pick up from C0Re 96
                          tip wash station. 2 = pick up with " full volume blow out"
      z_deposit_position: Z- deposit position [0.1mm] (collar bearing position) Must bet between
                          0 and 3425. Default 3425.
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning
          of a command [0.1mm]. Must be between 0 and 3425.
      minimum_height_command_end: Minimal height at command end [0.1 mm] Must be between 0 and 3425
    """

    _assert_clamp(x_position, 0, 30000, "x_position")
    _assert_clamp(x_position, 0, 1, "x_direction")
    _assert_clamp(y_position, 1080, 5600, "y_position")
    _assert_clamp(z_deposit_position, 0, 3425, "z_deposit_position")
    _assert_clamp(minimum_traverse_height_at_beginning_of_a_command, 0, 3425, \
                  "minimum_traverse_height_at_beginning_of_a_command")
    _assert_clamp(minimum_height_command_end, 0, 3425, "minimum_height_command_end")

    return self.send_command(
      module="C0",
      command="ER",
      xs=x_position,
      xd=x_direction,
      yh=y_position,
      za=z_deposit_position,
      zh=minimum_traverse_height_at_beginning_of_a_command,
      ze=minimum_height_command_end
    )

  # -------------- 3.10.3 Liquid handling using CoRe 96 Head --------------

  def aspirate_core_96(
    self,
    aspiration_type: int = 0,
    x_position: int = 0,
    x_direction: int = 0,
    y_positions: int = 0,
    minimum_traverse_height_at_beginning_of_a_command: int = 3425,
    minimal_end_height: int = 3425,
    lld_search_height: int = 3425,
    liquid_surface_at_function_without_lld: int = 3425,
    pull_out_distance_to_take_transport_air_in_function_without_lld: int = 50,
    maximum_immersion_depth: int = 3425,
    tube_2nd_section_height_measured_from_zm: int = 0,
    tube_2nd_section_ratio: int = 3425,
    immersion_depth: int = 0,
    immersion_depth_direction: int = 0,
    liquid_surface_sink_distance_at_the_end_of_aspiration: int = 0,
    aspiration_volume: int = 0,
    aspiration_speed: int = 1000,
    transport_air_volume: int = 0,
    blow_out_air_volume: int = 200,
    pre_wetting_volume: int = 0,
    lld_mode: int = 1,
    gamma_lld_sensitivity: int = 1,
    swap_speed: int = 100,
    settling_time: int = 5,
    homogenization_volume: int = 0,
    homogenization_cycles: int = 0,
    homogenization_position_from_liquid_surface: int = 250,
    surface_following_distance_during_homogenization: int = 0,
    speed_of_homogenization: int = 1000,
    todo: int = None,
    limit_curve_index: int = 0,
    tadm_algorithm: bool = False,
    recording_mode: int = 0
  ):
    """ aspirate CoRe 96

    Aspiration of liquid using CoRe 96

    Args:
      aspiration_type: Type of aspiration (0 = simple; 1 = sequence; 2 = cup emptied). Must be
          between 0 and 2. Default 0.
      x_position: X-Position [0.1mm] of well A1. Must be between 0 and 30000. Default 0.
      x_direction: X-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      y_positions: Y-Position [0.1mm] of well A1. Must be between 1080 and 5600. Default 0.
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning of
          a command 0.1mm] (refers to all channels independent of tip pattern parameter 'tm').
          Must be between 0 and 3425. Default 3425.
      minimal_end_height: Minimal height at command end [0.1mm]. Must be between 0 and 3425.
          Default 3425.
      lld_search_height: LLD search height [0.1mm]. Must be between 0 and 3425. Default 3425.
      liquid_surface_at_function_without_lld: Liquid surface at function without LLD [0.1mm].
          Must be between 0 and 3425. Default 3425.
      pull_out_distance_to_take_transport_air_in_function_without_lld: pull out distance to take
          transport air in function without LLD [0.1mm]. Must be between 0 and 3425. Default 50.
      maximum_immersion_depth: Minimum height (maximum immersion depth) [0.1mm]. Must be between
          0 and 3425. Default 3425.
      tube_2nd_section_height_measured_from_zm: Tube 2nd section height measured from "zm" [0.1mm]
           Must be between 0 and 3425. Default 0.
      tube_2nd_section_ratio: Tube 2nd section ratio (See Fig 2.). Must be between 0 and 10000.
          Default 3425.
      immersion_depth: Immersion depth [0.1mm]. Must be between 0 and 3600. Default 0.
      immersion_depth_direction: Direction of immersion depth (0 = go deeper, 1 = go up out of
          liquid). Must be between 0 and 1. Default 0.
      liquid_surface_sink_distance_at_the_end_of_aspiration: Liquid surface sink distance at
          the end of aspiration [0.1mm]. Must be between 0 and 990. Default 0.
      aspiration_volume: Aspiration volume [0.1ul]. Must be between 0 and 11500. Default 0.
      aspiration_speed: Aspiration speed [0.1ul/s]. Must be between 3 and 5000. Default 1000.
      transport_air_volume: Transport air volume [0.1ul]. Must be between 0 and 500. Default 0.
      blow_out_air_volume: Blow-out air volume [0.1ul]. Must be between 0 and 11500. Default 200.
      pre_wetting_volume: Pre-wetting volume. Must be between 0 and 11500. Default 0.
      lld_mode: LLD mode (0 = off, 1 = gamma, 2 = dP, 3 = dual, 4 = Z touch off). Must be between
          0 and 4. Default 1.
      gamma_lld_sensitivity: gamma LLD sensitivity (1= high, 4=low). Must be between 1 and 4.
          Default 1.
      swap_speed: Swap speed (on leaving liquid) [0.1mm/s]. Must be between 3 and 1000. Default 100.
      settling_time: Settling time [0.1s]. Must be between 0 and 99. Default 5.
      homogenization_volume: Homogenization volume [0.1ul]. Must be between 0 and 11500. Default 0.
      homogenization_cycles: Number of homogenization cycles. Must be between 0 and 99. Default 0.
      homogenization_position_from_liquid_surface: Homogenization position in Z- direction from
          liquid surface (LLD or absolute terms) [0.1mm]. Must be between 0 and 990. Default 250.
      surface_following_distance_during_homogenization: surface following distance during
          homogenization [0.1mm]. Must be between 0 and 990. Default 0.
      speed_of_homogenization: Speed of homogenization [0.1ul/s]. Must be between 3 and 5000.
          Default 1000.
      todo: TODO: 24 hex chars. Must be between 4 and 5000.
      limit_curve_index: limit curve index. Must be between 0 and 999. Default 0.
      tadm_algorithm: TADM algorithm. Default False.
      recording_mode: Recording mode 0 : no 1 : TADM errors only 2 : all TADM measurement.
          Must be between 0 and 2. Default 0.
    """

    _assert_clamp(aspiration_type, 0, 2, "aspiration_type")
    _assert_clamp(x_position, 0, 30000, "x_position")
    _assert_clamp(x_direction, 0, 1, "x_direction")
    _assert_clamp(y_positions, 1080, 5600, "y_positions")
    _assert_clamp(minimum_traverse_height_at_beginning_of_a_command, 0, 3425, \
                  "minimum_traverse_height_at_beginning_of_a_command")
    _assert_clamp(minimal_end_height, 0, 3425, "minimal_end_height")
    _assert_clamp(lld_search_height, 0, 3425, "lld_search_height")
    _assert_clamp(liquid_surface_at_function_without_lld, 0, 3425, \
                  "liquid_surface_at_function_without_lld")
    _assert_clamp(pull_out_distance_to_take_transport_air_in_function_without_lld, 0, 3425, \
                  "pull_out_distance_to_take_transport_air_in_function_without_lld")
    _assert_clamp(maximum_immersion_depth, 0, 3425, "maximum_immersion_depth")
    _assert_clamp(tube_2nd_section_height_measured_from_zm, 0, 3425, \
                  "tube_2nd_section_height_measured_from_zm")
    _assert_clamp(tube_2nd_section_ratio, 0, 10000, "tube_2nd_section_ratio")
    _assert_clamp(immersion_depth, 0, 3600, "immersion_depth")
    _assert_clamp(immersion_depth_direction, 0, 1, "immersion_depth_direction")
    _assert_clamp(liquid_surface_sink_distance_at_the_end_of_aspiration, 0, 990, \
                  "liquid_surface_sink_distance_at_the_end_of_aspiration")
    _assert_clamp(aspiration_volume, 0, 11500, "aspiration_volume")
    _assert_clamp(aspiration_speed, 3, 5000, "aspiration_speed")
    _assert_clamp(transport_air_volume, 0, 500, "transport_air_volume")
    _assert_clamp(blow_out_air_volume, 0, 11500, "blow_out_air_volume")
    _assert_clamp(pre_wetting_volume, 0, 11500, "pre_wetting_volume")
    _assert_clamp(lld_mode, 0, 4, "lld_mode")
    _assert_clamp(gamma_lld_sensitivity, 1, 4, "gamma_lld_sensitivity")
    _assert_clamp(swap_speed, 3, 1000, "swap_speed")
    _assert_clamp(settling_time, 0, 99, "settling_time")
    _assert_clamp(homogenization_volume, 0, 11500, "homogenization_volume")
    _assert_clamp(homogenization_cycles, 0, 99, "homogenization_cycles")
    _assert_clamp(homogenization_position_from_liquid_surface, 0, 990, \
                  "homogenization_position_from_liquid_surface")
    _assert_clamp(surface_following_distance_during_homogenization, 0, 990, \
                  "surface_following_distance_during_homogenization")
    _assert_clamp(speed_of_homogenization, 3, 5000, "speed_of_homogenization")
    _assert_clamp(todo, 4, 5000, "todo")
    _assert_clamp(limit_curve_index, 0, 999, "limit_curve_index")

    _assert_clamp(recording_mode, 0, 2, "recording_mode")

    return self.send_command(
      module="C0",
      command="EA",
      aa=aspiration_type,
      xs=x_position,
      xd=x_direction,
      yh=y_positions,
      zh=minimum_traverse_height_at_beginning_of_a_command,
      ze=minimal_end_height,
      lz=lld_search_height,
      zt=liquid_surface_at_function_without_lld,
      pp=pull_out_distance_to_take_transport_air_in_function_without_lld,
      zm=maximum_immersion_depth,
      zv=tube_2nd_section_height_measured_from_zm,
      zq=tube_2nd_section_ratio,
      iw=immersion_depth,
      ix=immersion_depth_direction,
      fh=liquid_surface_sink_distance_at_the_end_of_aspiration,
      af=aspiration_volume,
      ag=aspiration_speed,
      vt=transport_air_volume,
      bv=blow_out_air_volume,
      wv=pre_wetting_volume,
      cm=lld_mode,
      cs=gamma_lld_sensitivity,
      bs=swap_speed,
      wh=settling_time,
      hv=homogenization_volume,
      hc=homogenization_cycles,
      hp=homogenization_position_from_liquid_surface,
      mj=surface_following_distance_during_homogenization,
      hs=speed_of_homogenization,
      cw=todo,
      cr=limit_curve_index,
      cj=tadm_algorithm,
      cx=recording_mode,
    )

  def dispense_core_96(
    self,
    dispensing_mode: int = 0,
    x_position: int = 0,
    x_direction: int = 0,
    y_position: int = 0,
    tube_2nd_section_height_measured_from_zm: int = 0,
    tube_2nd_section_ratio: int = 3425,
    lld_search_height: int = 3425,
    liquid_surface_at_function_without_lld: int = 3425,
    pull_out_distance_to_take_transport_air_in_function_without_lld: int = 50,
    maximum_immersion_depth: int = 3425,
    immersion_depth: int = 0,
    immersion_depth_direction: int = 0,
    liquid_surface_sink_elevation_at_the_end_of_aspiration: int = 0,
    minimal_traverse_height_at_begin_of_command: int = 3425,
    minimal_end_height: int = 3425,
    dispense_volume: int = 0,
    dispense_speed: int = 5000,
    cut_off_speed: int = 250,
    stop_back_volume: int = 0,
    transport_air_volume: int = 0,
    blow_out_air_volume: int = 200,
    lld_mode: int = 1,
    gamma_lld_sensitivity: int = 1,
    swap_speed: int = 100,
    settling_time: int = 5,
    mixing_volume: int = 0,
    mixing_cycles: int = 0,
    mixing_position_from_liquid_surface: int = 250,
    surface_following_distance_during_mixing: int = 0,
    speed_of_mixing: int = 1000,
    todo: int = None,
    limit_curve_index: int = 0,
    tadm_algorithm: bool = False,
    recording_mode: int = 0
  ):
    """ dispense CoRe 96

    Dispensing of liquid using CoRe 96

    Args:
      dispensing_mode: Type of dispsensing mode 0 = Partial volume in jet mode 1 = Blow out
          in jet mode 2 = Partial volume at surface 3 = Blow out at surface 4 = Empty tip at fix
          position. Must be between 0 and 4. Default 0.
      x_position: X-Position [0.1mm] of well A1. Must be between 0 and 30000. Default 0.
      x_direction: X-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      y_position: Y-Position [0.1mm] of well A1. Must be between 1080 and 5600. Default 0.
      maximum_immersion_depth: Minimum height (maximum immersion depth) [0.1mm]. Must be between
          0 and 3425. Default 3425.
      tube_2nd_section_height_measured_from_zm: Tube 2nd section height measured from
          "zm" [0.1mm]. Must be between 0 and 3425. Default 0.
      tube_2nd_section_ratio: Tube 2nd section ratio (See Fig 2.). Must be between 0 and 10000.
          Default 3425.
      lld_search_height: LLD search height [0.1mm]. Must be between 0 and 3425. Default 3425.
      liquid_surface_at_function_without_lld: Liquid surface at function without LLD [0.1mm].
          Must be between 0 and 3425. Default 3425.
      pull_out_distance_to_take_transport_air_in_function_without_lld: pull out distance to take
          transport air in function without LLD [0.1mm]. Must be between 0 and 3425. Default 50.
      immersion_depth: Immersion depth [0.1mm]. Must be between 0 and 3600. Default 0.
      immersion_depth_direction: Direction of immersion depth (0 = go deeper, 1 = go up out of
          liquid). Must be between 0 and 1. Default 0.
      liquid_surface_sink_elevation_at_the_end_of_aspiration: Liquid surface sink elevation at
          the end of aspiration [0.1mm]. Must be between 0 and 990. Default 0.
      minimal_traverse_height_at_begin_of_command: Minimal traverse height at begin of
          command [0.1mm]. Must be between 0 and 3425. Default 3425.
      minimal_end_height: Minimal height at command end [0.1mm]. Must be between 0 and 3425.
          Default 3425.
      dispense_volume: Dispense volume [0.1ul]. Must be between 0 and 11500. Default 0.
      dispense_speed: Dispense speed [0.1ul/s]. Must be between 3 and 5000. Default 5000.
      cut_off_speed: Cut-off speed [0.1ul/s]. Must be between 3 and 5000. Default 250.
      stop_back_volume: Stop back volume [0.1ul/s]. Must be between 0 and 999. Default 0.
      transport_air_volume: Transport air volume [0.1ul]. Must be between 0 and 500. Default 0.
      blow_out_air_volume: Blow-out air volume [0.1ul]. Must be between 0 and 11500. Default 200.
      lld_mode: LLD mode (0 = off, 1 = gamma, 2 = dP, 3 = dual, 4 = Z touch off). Must be
          between 0 and 4. Default 1.
      gamma_lld_sensitivity: gamma LLD sensitivity (1= high, 4=low). Must be between 1 and 4.
          Default 1.
      swap_speed: Swap speed (on leaving liquid) [0.1mm/s]. Must be between 3 and 1000. Default 100.
      settling_time: Settling time [0.1s]. Must be between 0 and 99. Default 5.
      mixing_volume: Homogenization volume [0.1ul]. Must be between 0 and 11500. Default 0.
      mixing_cycles: Number of mixing cycles. Must be between 0 and 99. Default 0.
      mixing_position_from_liquid_surface: Homogenization position in Z- direction from liquid
          surface (LLD or absolute terms) [0.1mm]. Must be between 0 and 990. Default 250.
      surface_following_distance_during_mixing: surface following distance during mixing [0.1mm].
          Must be between 0 and 990. Default 0.
      speed_of_mixing: Speed of mixing [0.1ul/s]. Must be between 3 and 5000. Default 1000.
      todo: TODO: 24 hex chars. Must be between 4 and 5000.
      limit_curve_index: limit curve index. Must be between 0 and 999. Default 0.
      tadm_algorithm: TADM algorithm. Default False.
      recording_mode: Recording mode 0 : no 1 : TADM errors only 2 : all TADM measurement. Must
          be between 0 and 2. Default 0.
    """

    _assert_clamp(dispensing_mode, 0, 4, "dispensing_mode")
    _assert_clamp(x_position, 0, 30000, "x_position")
    _assert_clamp(x_direction, 0, 1, "x_direction")
    _assert_clamp(y_position, 1080, 5600, "y_position")
    _assert_clamp(maximum_immersion_depth, 0, 3425, "maximum_immersion_depth")
    _assert_clamp(tube_2nd_section_height_measured_from_zm, 0, 3425, \
                  "tube_2nd_section_height_measured_from_zm")
    _assert_clamp(tube_2nd_section_ratio, 0, 10000, "tube_2nd_section_ratio")
    _assert_clamp(lld_search_height, 0, 3425, "lld_search_height")
    _assert_clamp(liquid_surface_at_function_without_lld, 0, 3425, \
                  "liquid_surface_at_function_without_lld")
    _assert_clamp(pull_out_distance_to_take_transport_air_in_function_without_lld, 0, 3425, \
                  "pull_out_distance_to_take_transport_air_in_function_without_lld")
    _assert_clamp(immersion_depth, 0, 3600, "immersion_depth")
    _assert_clamp(immersion_depth_direction, 0, 1, "immersion_depth_direction")
    _assert_clamp(liquid_surface_sink_elevation_at_the_end_of_aspiration, 0, 990, \
                  "liquid_surface_sink_elevation_at_the_end_of_aspiration")
    _assert_clamp(minimal_traverse_height_at_begin_of_command, 0, 3425, \
                  "minimal_traverse_height_at_begin_of_command")
    _assert_clamp(minimal_end_height, 0, 3425, "minimal_end_height")
    _assert_clamp(dispense_volume, 0, 11500, "dispense_volume")
    _assert_clamp(dispense_speed, 3, 5000, "dispense_speed")
    _assert_clamp(cut_off_speed, 3, 5000, "cut_off_speed")
    _assert_clamp(stop_back_volume, 0, 999, "stop_back_volume")
    _assert_clamp(transport_air_volume, 0, 500, "transport_air_volume")
    _assert_clamp(blow_out_air_volume, 0, 11500, "blow_out_air_volume")
    _assert_clamp(lld_mode, 0, 4, "lld_mode")
    _assert_clamp(gamma_lld_sensitivity, 1, 4, "gamma_lld_sensitivity")
    _assert_clamp(swap_speed, 3, 1000, "swap_speed")
    _assert_clamp(settling_time, 0, 99, "settling_time")
    _assert_clamp(mixing_volume, 0, 11500, "mixing_volume")
    _assert_clamp(mixing_cycles, 0, 99, "mixing_cycles")
    _assert_clamp(mixing_position_from_liquid_surface, 0, 990, \
                  "mixing_position_from_liquid_surface")
    _assert_clamp(surface_following_distance_during_mixing, 0, 990, \
                  "surface_following_distance_during_mixing")
    _assert_clamp(speed_of_mixing, 3, 5000, "speed_of_mixing")
    _assert_clamp(todo, 4, 5000, "todo")
    _assert_clamp(limit_curve_index, 0, 999, "limit_curve_index")
    _assert_clamp(recording_mode, 0, 2, "recording_mode")

    return self.send_command(
      module="C0",
      command="ED",
      dm=dispensing_mode,
      xs=x_position,
      xd=x_direction,
      yh=y_position,
      zm=maximum_immersion_depth,
      zv=tube_2nd_section_height_measured_from_zm,
      zq=tube_2nd_section_ratio,
      lz=lld_search_height,
      zt=liquid_surface_at_function_without_lld,
      pp=pull_out_distance_to_take_transport_air_in_function_without_lld,
      iw=immersion_depth,
      ix=immersion_depth_direction,
      fh=liquid_surface_sink_elevation_at_the_end_of_aspiration,
      zh=minimal_traverse_height_at_begin_of_command,
      ze=minimal_end_height,
      df=dispense_volume,
      dg=dispense_speed,
      es=cut_off_speed,
      ev=stop_back_volume,
      vt=transport_air_volume,
      bv=blow_out_air_volume,
      cm=lld_mode,
      cs=gamma_lld_sensitivity,
      bs=swap_speed,
      wh=settling_time,
      hv=mixing_volume,
      hc=mixing_cycles,
      hp=mixing_position_from_liquid_surface,
      mj=surface_following_distance_during_mixing,
      hs=speed_of_mixing,
      cw=todo,
      cr=limit_curve_index,
      cj=tadm_algorithm,
      cx=recording_mode,
    )

  # -------------- 3.10.4 Adjustment & movement commands --------------

  def move_core_96_head_to_defined_position(
    self,
    dispsensing_mode: int = 0,
    x_position: int = 0,
    x_direction: int = 0,
    y_position: int = 0,
    z_position: int = 0,
    minimum_height_at_beginning_of_a_command: int = 3425
  ):
    """ Move CoRe 96 Head to defined position

    Args:
      dispsensing_mode: Type of dispsensing mode 0 = Partial volume in jet mode 1 = Blow out
                        in jet mode 2 = Partial volume at surface 3 = Blow out at surface 4 = Empty
                        tip at fix position. Must be between 0 and 4. Default 0.
      x_position: X-Position [0.1mm] of well A1. Must be between 0 and 30000. Default 0.
      x_direction: X-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      y_position: Y-Position [0.1mm]. Must be between 1080 and 5600. Default 0.
      z_position: Z-Position [0.1mm]. Must be between 0 and 5600. Default 0.
      minimum_height_at_beginning_of_a_command: Minimum height at beginning of a command 0.1mm]
                        (refers to all channels independent of tip pattern parameter 'tm'). Must be
                        between 0 and 3425. Default 3425.
    """

    _assert_clamp(dispsensing_mode, 0, 4, "dispsensing_mode")
    _assert_clamp(x_position, 0, 30000, "x_position")
    _assert_clamp(x_direction, 0, 1, "x_direction")
    _assert_clamp(y_position, 1080, 5600, "y_position")
    _assert_clamp(y_position, 0, 5600, "z_position")
    _assert_clamp(minimum_height_at_beginning_of_a_command, 0, 3425, \
                  "minimum_height_at_beginning_of_a_command")

    return self.send_command(
      module="C0",
      command="EM",
      dm=dispsensing_mode,
      xs=x_position,
      xd=x_direction,
      yh=y_position,
      za=z_position,
      zh=minimum_height_at_beginning_of_a_command,
    )

  # -------------- 3.10.5 Wash procedure commands using CoRe 96 Head --------------

  # TODO:(command:EG) Washing tips using CoRe 96 Head
  # TODO:(command:EU) Empty washed tips (end of wash procedure only)

  # -------------- 3.10.6 Query CoRe 96 Head --------------

  def request_tip_presence_in_core_96_head(self):
    """ Request Tip presence in CoRe 96 Head

    Returns:
      qh: 0 = no tips, 1 = Tips are picked up
    """

    resp = self.send_command(module="C0", command="QH")
    return self.parse_response(resp, "qh#")

  def request_position_of_core_96_head(self):
    """ Request position of CoRe 96 Head (A1 considered to tip length)

    Returns:
      xs: A1 X direction [0.1mm]
      xd: X direction 0 = positive 1 = negative
      yh: A1 Y direction [0.1mm]
      za: Z height [0.1mm]
    """

    resp = self.send_command(module="C0", command="QI")
    return self.parse_response(resp, "xs#####xd#hy####za####")

  def request_core_96_head_channel_tadm_status(self):
    """ Request CoRe 96 Head channel TADM Status

    Returns:
      qx: TADM channel status 0 = off 1 = on
    """

    resp = self.send_command(module="C0", command="VC")
    return self.parse_response(resp, "qx#")

  def request_core_96_head_channel_tadm_error_status(self):
    """ Request CoRe 96 Head channel TADM error status

    Returns:
      vb: error pattern 0 = no error
    """

    resp = self.send_command(module="C0", command="VB")
    return self.parse_response(resp, "vb" + "&" * 24)

  # -------------- 3.11 384 Head commands --------------

  # -------------- 3.11.1 Initialization --------------

  # -------------- 3.11.2 Tip handling using 384 Head --------------

  # -------------- 3.11.3 Liquid handling using 384 Head --------------

  # -------------- 3.11.4 Adjustment & movement commands --------------

  # -------------- 3.11.5 Wash procedure commands using 384 Head --------------

  # -------------- 3.11.6 Query 384 Head --------------

  # -------------- 3.12 Nano pipettor commands --------------

  # TODO: all nano pipettor commands

  # -------------- 3.12.1 Initialization --------------

  # TODO:(commandL:NI)
  # TODO:(commandL:NV)
  # TODO:(commandL:NP)

  # -------------- 3.12.2 Nano pipettor liquid handling commands --------------

  # TODO:(commandL:NA)
  # TODO:(commandL:ND)
  # TODO:(commandL:NF)

  # -------------- 3.12.3 Nano pipettor wash & clean commands --------------

  # TODO:(commandL:NW)
  # TODO:(commandL:NU)

  # -------------- 3.12.4 Nano pipettor adjustment & movements --------------

  # TODO:(commandL:NM)
  # TODO:(commandL:NT)

  # -------------- 3.12.5 Nano pipettor query --------------

  # TODO:(commandL:QL)
  # TODO:(commandL:QN)
  # TODO:(commandL:RN)
  # TODO:(commandL:QQ)
  # TODO:(commandL:QR)
  # TODO:(commandL:QO)
  # TODO:(commandL:RR)
  # TODO:(commandL:QU)

  # -------------- 3.13 Auto load commands --------------

  # -------------- 3.13.1 Initialization --------------

  def initialize_auto_load(self):
    """ Initialize Auto load module """

    resp = self.send_command(module="C0", command="II")
    return self.parse_response(resp, "")

  def move_auto_load_to_z_save_position(self):
    """ Move auto load to Z save position """

    resp = self.send_command(module="C0", command="IV")
    return self.parse_response(resp, "")

  # -------------- 3.13.2 Carrier handling --------------

  # TODO:(command:CI) Identify carrier (determine carrier type)

  def request_single_carrier_presence(
    self,
    carrier_position: int
  ):
    """ Request single carrier presence

    Args:
      carrier_position: Carrier position (slot number)

    Returns:
      True if present, False otherwise
    """

    _assert_clamp(carrier_position, 1, 54, "carrier_position")

    resp = self.send_command(
      module="C0",
      command="CT",
      cp=carrier_position
    )
    return self.parse_response(resp, "ct#")["ct"] == 1

  # TODO:(command:CA) Push out carrier to loading tray (after identification CI)

  # TODO:(command:CR) Unload carrier

  # TODO:(command:CL) Load carrier

  def set_loading_indicators(
    self,
    bit_pattern: typing.List[bool],
    blink_pattern: typing.List[bool]
  ):
    """ Set loading indicators (LEDs)

    The docs here are a little weird because 2^54 < 7FFFFFFFFFFFFF.

    Args:
      bit_pattern: On if True, off otherwise
      blink_pattern: Blinking if True, steady otherwise
    """

    assert len(bit_pattern) == 54, "bit pattern must be length 54"
    assert len(blink_pattern) == 54, "bit pattern must be length 54"

    bit_pattern   = hex(int("".join(["1" if x else "0" for x in bit_pattern]), base=2))
    blink_pattern = hex(int("".join(["1" if x else "0" for x in blink_pattern]), base=2))

    resp = self.send_command(
      module="C0",
      command="CP",
      cl=bit_pattern,
      cb=blink_pattern
    )
    return self.parse_response(resp, "")

  # TODO:(command:CS) Check for presence of carriers on loading tray

  def set_barcode_type(
    self,
    ISBT_Standard: bool = True,
    code128: bool = True,
    code39: bool = True,
    codebar: bool = True,
    code2_5: bool = True,
    UPC_AE: bool = True,
    EAN8: bool = True
  ):
    """ Set bar code type: which types of barcodes will be scanned for.

    Args:
      ISBT_Standard: ISBT_Standard. Default True.
      code128: Code128. Default True.
      code39: Code39. Default True.
      codebar: Codebar. Default True.
      code2_5: Code2_5. Default True.
      UPC_AE: UPC_AE. Default True.
      EAN8: EAN8. Default True.
    """

    # pylint: disable=invalid-name

    # Encode values into bit pattern. Last bit is always one.
    bt = ""
    for t in [ISBT_Standard, code128, code39, codebar, code2_5, UPC_AE, EAN8, True]:
      bt += "1" if t else "0"

    # Convert bit pattern to hex.
    bt = hex(int(bt), base=2)

    resp = self.send_command(
      module="C0",
      command="CB",
      bt=bt
    )
    return self.parse_response(resp, "")

  # TODO:(command:CW) Unload carrier finally

  def set_carrier_monitoring(
    self,
    should_monitor: bool = False
  ):
    """ Set carrier monitoring

    Args:
      should_monitor: whether carrier should be monitored.

    Returns:
      True if present, False otherwise
    """

    resp = self.send_command(
      module="C0",
      command="CU",
      cu=should_monitor
    )
    return self.parse_response(resp, "")

  # TODO:(command:CN) Take out the carrier to identification position

  # -------------- 3.13.3 Auto load query --------------

  # TODO:(command:RC) Query presence of carrier on deck

  def request_auto_load_slot_position(self):
    """ Request auto load slot position.

    Returns:
      slot position (0..54)
    """

    resp = self.send_command(module="C0", command="QA")
    return self.parse_response(resp, "qa##")

  # TODO:(command:CQ) Request auto load module type

  # -------------- 3.14 G1-3/ CR Needle Washer commands --------------

  # TODO: All needle washer commands

  # TODO:(command:WI)
  # TODO:(command:WI)
  # TODO:(command:WS)
  # TODO:(command:WW)
  # TODO:(command:WR)
  # TODO:(command:WC)
  # TODO:(command:QF)

  # -------------- 3.15 Pump unit commands --------------

  def request_pump_settings(
    self,
    pump_station: int = 1
  ):
    """ Set carrier monitoring

    Args:
      carrier_position: pump station number (1..3)

    Returns:
      0 = CoRe 96 wash station (single chamber)
      1 = DC wash station (single chamber rev 02 ) 2 = ReReRe (single chamber)
      3 = CoRe 96 wash station (dual chamber)
      4 = DC wash station (dual chamber)
      5 = ReReRe (dual chamber)
    """

    _assert_clamp(pump_station, 1, 3, "pump_station")

    resp = self.send_command(
      module="C0",
      command="ET",
      ep=pump_station
    )
    return self.parse_response(resp, "et#")

  # -------------- 3.15.1 DC Wash commands (only for revision up to 01) --------------

  # TODO:(command:FA) Start DC wash procedure
  # TODO:(command:FB) Stop DC wash procedure
  # TODO:(command:FP) Prime DC wash station

  # -------------- 3.15.2 Single chamber pump unit only --------------

  # TODO:(command:EW) Start circulation (single chamber only)
  # TODO:(command:EC) Check circulation (single chamber only)
  # TODO:(command:ES) Stop circulation (single chamber only)
  # TODO:(command:EF) Prime (single chamber only)
  # TODO:(command:EE) Drain & refill (single chamber only)
  # TODO:(command:EB) Fill (single chamber only)
  # TODO:(command:QE) Request single chamber pump station prime status

  # -------------- 3.15.3 Dual chamber pump unit only --------------

  def initialize_dual_pump_station_valves(
    self,
    pump_station: int = 1
  ):
    """ Initialize pump station valves (dual chamber only)

    Args:
      carrier_position: pump station number (1..3)
    """

    _assert_clamp(pump_station, 1, 3, "pump_station")

    resp = self.send_command(
      module="C0",
      command="EJ",
      ep=pump_station
    )
    return self.parse_response(resp, "")

  def fill_selected_dual_chamber(
    self,
    pump_station: int = 1,
    drain_before_refill: bool = False,
    wash_fluid: int = 1,
    chamber: int = 2,
    waste_chamber_suck_time_after_sensor_change: int = 0
  ):
    """ Initialize pump station valves (dual chamber only)

    Args:
      carrier_position: pump station number (1..3)
      drain_before_refill: drain chamber before refill. Default False.
      wash_fluid: wash fluid (1 or 2)
      chamber: chamber (1 or 2)
      drain_before_refill: waste chamber suck time after sensor change [s] (for error handling only)
    """

    _assert_clamp(pump_station, 1, 3, "pump_station")
    _assert_clamp(wash_fluid, 1, 2, "wash_fluid")
    _assert_clamp(chamber, 1, 2, "chamber")

    # wash fluid <-> chamber connection
    # 0 = wash fluid 1 <-> chamber 2
    # 1 = wash fluid 1 <-> chamber 1
    # 2 = wash fluid 2 <-> chamber 1
    # 3 = wash fluid 2 <-> chamber 2
    connection = None
    if wash_fluid == 1:
      connection = 0 if chamber == 2 else 1
    if wash_fluid == 2:
      connection = 2 if chamber == 1 else 3

    resp = self.send_command(
      module="C0",
      command="EJ",
      ep=pump_station,
      ed=drain_before_refill,
      ek=connection,
      eu=waste_chamber_suck_time_after_sensor_change
    )
    return self.parse_response(resp, "")

  # TODO:(command:EK) Drain selected chamber

  def drain_dual_chamber_system(
    self,
    pump_station: int = 1
  ):
    """ Drain system (dual chamber only)

    Args:
      carrier_position: pump station number (1..3)
    """

    _assert_clamp(pump_station, 1, 3, "pump_station")

    resp = self.send_command(
      module="C0",
      command="EL",
      ep=pump_station
    )
    return self.parse_response(resp, "")

  # TODO:(command:QD) Request dual chamber pump station prime status

  # -------------- 3.16 Incubator commands --------------

  # TODO: all incubator commands
  # TODO:(command:HC)
  # TODO:(command:HI)
  # TODO:(command:HF)
  # TODO:(command:RP)

  # -------------- 3.17 iSWAP commands --------------

  # -------------- 3.17.1 Pre & Initialization commands --------------

  def initialize_iswap(self):
    """ Initialize iSWAP (for standalone configuration only) """

    resp = self.send_command(module="C0", command="FI")
    return self.parse_response(resp, "")

  def position_components_for_free_iswap_y_range(self):
    """ Position all components so that there is maximum free Y range for iSWAP """

    resp = self.send_command(module="C0", command="FY")
    return self.parse_response(resp, "")

  def move_iswap_x_direction(
    self,
    step_size: int = 0,
    direction: int = 0
  ):
    """ Move iSWAP in X-direction

    Args:
      step_size: X Step size [0.1mm] Between 0 and 999. Default 0.
      direction: X direction. 0 = positive 1 = negative
    """

    resp = self.send_command(
      module="C0",
      command="GX",
      gx=step_size,
      xd=direction
    )
    return self.parse_response(resp, "")

  def move_iswap_y_direction(
    self,
    step_size: int = 0,
    direction: int = 0
  ):
    """ Move iSWAP in Y-direction

    Args:
      step_size: Y Step size [0.1mm] Between 0 and 999. Default 0.
      direction: Y direction. 0 = positive 1 = negative
    """

    resp = self.send_command(
      module="C0",
      command="GY",
      gx=step_size,
      xd=direction
    )
    return self.parse_response(resp, "")

  def move_iswap_z_direction(
    self,
    step_size: int = 0,
    direction: int = 0
  ):
    """ Move iSWAP in Z-direction

    Args:
      step_size: Z Step size [0.1mm] Between 0 and 999. Default 0.
      direction: Z direction. 0 = positive 1 = negative
    """

    resp = self.send_command(
      module="C0",
      command="GZ",
      gx=step_size,
      xd=direction
    )
    return self.parse_response(resp, "")

  def open_not_initialized_gripper(self):
    """ Open not initialized gripper """

    resp = self.send_command(module="C0", command="GI")
    return self.parse_response(resp, "")

  def open_gripper(
    self,
    open_position: int = 860
  ):
    """ Open gripper

    Args:
      open_position: Open position [0.1mm] (0.1 mm = 16 increments) The gripper moves to pos + 20.
                     Must be between 0 and 9999. Default 860.
    """

    _assert_clamp(open_position, 0, 9999, "open_position")

    resp = self.send_command(
      module="C0",
      command="GC",
      go=open_position
    )
    return self.parse_response(resp, "")

  def close_gripper(
    self,
    grip_strength: int = 5,
    plate_width: int = 0,
    plate_width_tolerance: int = 0
  ):
    """ Close gripper

    The gripper should be at the position gb+gt+20 before sending this command.

    Args:
      grip_strength: Grip strength. 0 = low . 9 = high. Default 5.
      plate_width: Plate width [0.1mm]
                   (gb should be > min. Pos. + stop ramp + gt -> gb > 760 + 5 + g )
      plate_width_tolerance: Plate width tolerance [0.1mm]. Must be between 0 and 99. Default 20.
    """

    resp = self.send_command(
      module="C0",
      command="GC",
      gw=grip_strength,
      gb=plate_width,
      gt=plate_width_tolerance
    )
    return self.parse_response(resp, "")

  # -------------- 3.17.2 Stack handling commands CP --------------

  def park_iswap(
    self,
    minimum_traverse_height_at_beginning_of_a_command: int = 3600
  ):
    """ Close gripper

    The gripper should be at the position gb+gt+20 before sending this command.

    Args:
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning
                of a command [0.1mm]. Must be between 0 and 3600. Default 3600.
    """

    _assert_clamp(minimum_traverse_height_at_beginning_of_a_command, 0, 3600, \
                  "minimum_traverse_height_at_beginning_of_a_command")

    resp = self.send_command(
      module="C0",
      command="GC",
      th=minimum_traverse_height_at_beginning_of_a_command
    )
    return self.parse_response(resp, "")

  def get_plate(
    self,
    x_position: int = 0,
    x_direction: int = 0,
    y_position: int = 0,
    y_direction: int = 0,
    z_position: int = 0,
    z_direction: int = 0,
    grip_direction: int = 1,
    minimum_traverse_height_at_beginning_of_a_command: int = 3600,
    z_position_at_the_command_end: int = 3600,
    grip_strength: int = 5,
    open_gripper_position: int = 860,
    plate_width: int = 860,
    plate_width_tolerance: int = 860,
    collision_control_level: int = 1,
    acceleration_index_high_acc: int = 4,
    acceleration_index_low_acc: int = 1,
    fold_up_sequence_at_the_end_of_process: bool = True
  ):
    """ get plate

    Args:
      x_position: Plate center in X direction  [0.1mm]. Must be between 0 and 30000. Default 0.
      x_direction: X-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      y_position: Plate center in Y direction [0.1mm]. Must be between 0 and 6500. Default 0.
      y_direction: Y-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      z_position: Plate gripping height in Z direction. Must be between 0 and 3600. Default 0.
      z_direction: Z-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      grip_direction: Grip direction. 1 = negative Y, 2 = positive X, 3 = positive Y,
            4 =negative X. Must be between 1 and 4. Default 1.
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning of
            a command 0.1mm]. Must be between 0 and 3600. Default 3600.
      z_position_at_the_command_end: Z-Position at the command end [0.1mm]. Must be between 0
            and 3600. Default 3600.
      grip_strength: Grip strength 0 = low .. 9 = high. Must be between 1 and 9. Default 5.
      open_gripper_position: Open gripper position [0.1mm]. Must be between 0 and 9999.
            Default 860.
      plate_width: plate width [0.1mm]. Must be between 0 and 9999. Default 860.
      plate_width_tolerance: plate width tolerance [0.1mm]. Must be between 0 and 99. Default 860.
      collision_control_level: collision control level 1 = high 0 = low. Must be between 0 and 1.
                               Default 1.
      acceleration_index_high_acc: acceleration index high acc. Must be between 0 and 4. Default 4.
      acceleration_index_low_acc: acceleration index high acc. Must be between 0 and 4. Default 1.
      fold_up_sequence_at_the_end_of_process: fold up sequence at the end of process. Default True.
    """

    _assert_clamp(x_position, 0, 30000, "x_position")
    _assert_clamp(x_direction, 0, 1, "x_direction")
    _assert_clamp(y_position, 0, 6500, "y_position")
    _assert_clamp(y_direction, 0, 1, "y_direction")
    _assert_clamp(z_position, 0, 3600, "z_position")
    _assert_clamp(z_direction, 0, 1, "z_direction")
    _assert_clamp(grip_direction, 1, 4, "grip_direction")
    _assert_clamp(minimum_traverse_height_at_beginning_of_a_command, 0, 3600, \
                  "minimum_traverse_height_at_beginning_of_a_command")
    _assert_clamp(z_position_at_the_command_end, 0, 3600, "z_position_at_the_command_end")
    _assert_clamp(grip_strength, 1, 9, "grip_strength")
    _assert_clamp(open_gripper_position, 0, 9999, "open_gripper_position")
    _assert_clamp(plate_width, 0, 9999, "plate_width")
    _assert_clamp(plate_width_tolerance, 0, 99, "plate_width_tolerance")
    _assert_clamp(collision_control_level, 0, 1, "collision_control_level")
    _assert_clamp(acceleration_index_high_acc, 0, 4, "acceleration_index_high_acc")
    _assert_clamp(acceleration_index_low_acc, 0, 4, "acceleration_index_low_acc")

    return self.send_command(
      module="C0",
      command="PP",
      xs=x_position,
      xd=x_direction,
      yj=y_position,
      yd=y_direction,
      zj=z_position,
      zd=z_direction,
      gr=grip_direction,
      th=minimum_traverse_height_at_beginning_of_a_command,
      te=z_position_at_the_command_end,
      gw=grip_strength,
      go=open_gripper_position,
      gb=plate_width,
      gt=plate_width_tolerance,
      ga=collision_control_level,
      xe=f"{acceleration_index_high_acc} {acceleration_index_low_acc}",
      gc=fold_up_sequence_at_the_end_of_process,
    )

  def put_plate(
    self,
    x_position: int = 0,
    x_direction: int = 0,
    y_position: int = 0,
    y_direction: int = 0,
    z_position: int = 0,
    z_direction: int = 0,
    grip_direction: int = 1,
    minimum_traverse_height_at_beginning_of_a_command: int = 3600,
    z_position_at_the_command_end: int = 3600,
    open_gripper_position: int = 860,
    collision_control_level: int = 1,
    acceleration_index_high_acc: int = 4,
    acceleration_index_low_acc: int = 1
  ):
    """ put plate

    Args:
      x_position: Plate center in X direction  [0.1mm]. Must be between 0 and 30000. Default 0.
      x_direction: X-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      y_position: Plate center in Y direction [0.1mm]. Must be between 0 and 6500. Default 0.
      y_direction: Y-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      z_position: Plate gripping height in Z direction. Must be between 0 and 3600. Default 0.
      z_direction: Z-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      grip_direction: Grip direction. 1 = negative Y, 2 = positive X, 3 = positive Y, 4 = negative
            X. Must be between 1 and 4. Default 1.
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning of a
            command 0.1mm]. Must be between 0 and 3600. Default 3600.
      z_position_at_the_command_end: Z-Position at the command end [0.1mm]. Must be between 0 and
            3600. Default 3600.
      open_gripper_position: Open gripper position [0.1mm]. Must be between 0 and 9999. Default
            860.
      collision_control_level: collision control level 1 = high 0 = low. Must be between 0 and 1.
            Default 1.
      acceleration_index_high_acc: acceleration index high acc. Must be between 0 and 4.
            Default 4.
      acceleration_index_low_acc: acceleration index high acc. Must be between 0 and 4.
            Default 1.
    """

    _assert_clamp(x_position, 0, 30000, "x_position")
    _assert_clamp(x_direction, 0, 1, "x_direction")
    _assert_clamp(y_position, 0, 6500, "y_position")
    _assert_clamp(y_direction, 0, 1, "y_direction")
    _assert_clamp(z_position, 0, 3600, "z_position")
    _assert_clamp(z_direction, 0, 1, "z_direction")
    _assert_clamp(grip_direction, 1, 4, "grip_direction")
    _assert_clamp(minimum_traverse_height_at_beginning_of_a_command, 0, 3600, \
                  "minimum_traverse_height_at_beginning_of_a_command")
    _assert_clamp(z_position_at_the_command_end, 0, 3600, "z_position_at_the_command_end")
    _assert_clamp(open_gripper_position, 0, 9999, "open_gripper_position")
    _assert_clamp(collision_control_level, 0, 1, "collision_control_level")
    _assert_clamp(acceleration_index_high_acc, 0, 4, "acceleration_index_high_acc")
    _assert_clamp(acceleration_index_low_acc, 0, 4, "acceleration_index_low_acc")

    return self.send_command(
      module="C0",
      command="PR",
      xs=x_position,
      xd=x_direction,
      yj=y_position,
      yd=y_direction,
      zj=z_position,
      zd=z_direction,
      gr=grip_direction,
      th=minimum_traverse_height_at_beginning_of_a_command,
      te=z_position_at_the_command_end,
      go=open_gripper_position,
      ga=collision_control_level,
      xe=f"{acceleration_index_high_acc} {acceleration_index_low_acc}"
    )

  def move_plate_to_position(
    self,
    x_position: int = 0,
    x_direction: int = 0,
    y_position: int = 0,
    y_direction: int = 0,
    z_position: int = 0,
    z_direction: int = 0,
    grip_direction: int = 1,
    minimum_traverse_height_at_beginning_of_a_command: int = 3600,
    collision_control_level: int = 1,
    acceleration_index_high_acc: int = 4,
    acceleration_index_low_acc: int = 1
  ):
    """ Move plate to position.

    Args:
      x_position: Plate center in X direction  [0.1mm]. Must be between 0 and 30000. Default 0.
      x_direction: X-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      y_position: Plate center in Y direction [0.1mm]. Must be between 0 and 6500. Default 0.
      y_direction: Y-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      z_position: Plate gripping height in Z direction. Must be between 0 and 3600. Default 0.
      z_direction: Z-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      grip_direction: Grip direction. 1 = negative Y, 2 = positive X, 3 = positive Y, 4 = negative
            X. Must be between 1 and 4. Default 1.
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning of a
            command 0.1mm]. Must be between 0 and 3600. Default 3600.
      collision_control_level: collision control level 1 = high 0 = low. Must be between 0 and 1.
            Default 1.
      acceleration_index_high_acc: acceleration index high acc. Must be between 0 and 4. Default 4.
      acceleration_index_low_acc: acceleration index high acc. Must be between 0 and 4. Default 1.
    """

    _assert_clamp(x_position, 0, 30000, "x_position")
    _assert_clamp(x_direction, 0, 1, "x_direction")
    _assert_clamp(y_position, 0, 6500, "y_position")
    _assert_clamp(y_direction, 0, 1, "y_direction")
    _assert_clamp(z_position, 0, 3600, "z_position")
    _assert_clamp(z_direction, 0, 1, "z_direction")
    _assert_clamp(grip_direction, 1, 4, "grip_direction")
    _assert_clamp(minimum_traverse_height_at_beginning_of_a_command, 0, 3600, \
                  "minimum_traverse_height_at_beginning_of_a_command")
    _assert_clamp(collision_control_level, 0, 1, "collision_control_level")
    _assert_clamp(acceleration_index_high_acc, 0, 4, "acceleration_index_high_acc")
    _assert_clamp(acceleration_index_low_acc, 0, 4, "acceleration_index_low_acc")

    return self.send_command(
      module="C0",
      command="PM",
      xs=x_position,
      xd=x_direction,
      yj=y_position,
      yd=y_direction,
      zj=z_position,
      zd=z_direction,
      gr=grip_direction,
      th=minimum_traverse_height_at_beginning_of_a_command,
      ga=collision_control_level,
      xe=f"{acceleration_index_high_acc} {acceleration_index_low_acc}"
    )

  def collapse_gripper_arm(
    self,
    minimum_traverse_height_at_beginning_of_a_command: int = 3600,
    fold_up_sequence_at_the_end_of_process: bool = True
  ):
    """ Collapse gripper arm

    Args:
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning of a
                                                         command 0.1mm]. Must be between 0 and 3600.
                                                         Default 3600.
      fold_up_sequence_at_the_end_of_process: fold up sequence at the end of process. Default True.
    """

    _assert_clamp(minimum_traverse_height_at_beginning_of_a_command, 0, 3600, \
                  "minimum_traverse_height_at_beginning_of_a_command")

    return self.send_command(
      module="C0",
      command="PN",
      th=minimum_traverse_height_at_beginning_of_a_command,
      gc=fold_up_sequence_at_the_end_of_process,
    )

  # -------------- 3.17.3 Hotel handling commands --------------

  # TODO:(command:PO) Get plate from hotel
  # TODO:(command:PI) Put plate to hotel

  # -------------- 3.17.4 Barcode commands --------------

  # TODO:(command:PB) Read barcode using iSWAP

  # -------------- 3.17.5 Teach in commands --------------

  def prepare_iswap_teaching(
  self,
  x_position: int = 0,
  x_direction: int = 0,
  y_position: int = 0,
  y_direction: int = 0,
  z_position: int = 0,
  z_direction: int = 0,
  location: int = 0,
  hotel_depth: int = 0,
  minimum_traverse_height_at_beginning_of_a_command: int = 3600,
  collision_control_level: int = 1,
  acceleration_index_high_acc: int = 4,
  acceleration_index_low_acc: int = 1
):
    """ Prepare iSWAP teaching

    Prepare for teaching with iSWAP

    Args:
      x_position: Plate center in X direction  [0.1mm]. Must be between 0 and 30000. Default 0.
      x_direction: X-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      y_position: Plate center in Y direction [0.1mm]. Must be between 0 and 6500. Default 0.
      y_direction: Y-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      z_position: Plate gripping height in Z direction. Must be between 0 and 3600. Default 0.
      z_direction: Z-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      location: location. 0 = Stack 1 = Hotel. Must be between 0 and 1. Default 0.
      hotel_depth: Hotel depth [0.1mm]. Must be between 0 and 3000. Default 13000.
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning of
                                                         a command 0.1mm]. Must be between 0 and
                                                         3600. Default 3600.
      collision_control_level: collision control level 1 = high 0 = low. Must be between 0 and 1.
                               Default 1.
      acceleration_index_high_acc: acceleration index high acc. Must be between 0 and 4.
                                   Default 4.
      acceleration_index_low_acc: acceleration index high acc. Must be between 0 and 4.
                                  Default 1.
    """

    _assert_clamp(x_position, 0, 30000, "x_position")
    _assert_clamp(x_direction, 0, 1, "x_direction")
    _assert_clamp(y_position, 0, 6500, "y_position")
    _assert_clamp(y_direction, 0, 1, "y_direction")
    _assert_clamp(z_position, 0, 3600, "z_position")
    _assert_clamp(z_direction, 0, 1, "z_direction")
    _assert_clamp(location, 0, 1, "location")
    _assert_clamp(hotel_depth, 0, 3000, "hotel_depth")
    _assert_clamp(minimum_traverse_height_at_beginning_of_a_command, 0, 3600, \
                  "minimum_traverse_height_at_beginning_of_a_command")
    _assert_clamp(collision_control_level, 0, 1, "collision_control_level")
    _assert_clamp(acceleration_index_high_acc, 0, 4, "acceleration_index_high_acc")
    _assert_clamp(acceleration_index_low_acc, 0, 4, "acceleration_index_low_acc")

    return self.send_command(
      module="C0",
      command="PT",
      xs=x_position,
      xd=x_direction,
      yj=y_position,
      yd=y_direction,
      zj=z_position,
      zd=z_direction,
      hh=location,
      hd=hotel_depth,
      th=minimum_traverse_height_at_beginning_of_a_command,
      ga=collision_control_level,
      xe=f"{acceleration_index_high_acc} {acceleration_index_low_acc}"
    )

  def get_logic_iswap_position(
    self,
    x_position: int = 0,
    x_direction: int = 0,
    y_position: int = 0,
    y_direction: int = 0,
    z_position: int = 0,
    z_direction: int = 0,
    location: int = 0,
    hotel_depth: int = 1300,
    grip_direction: int = 1,
    collision_control_level: int = 1
  ):
    """ Get logic iSWAP position

    Args:
      x_position: Plate center in X direction  [0.1mm]. Must be between 0 and 30000. Default 0.
      x_direction: X-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      y_position: Plate center in Y direction [0.1mm]. Must be between 0 and 6500. Default 0.
      y_direction: Y-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      z_position: Plate gripping height in Z direction. Must be between 0 and 3600. Default 0.
      z_direction: Z-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      location: location. 0 = Stack 1 = Hotel. Must be between 0 and 1. Default 0.
      hotel_depth: Hotel depth [0.1mm]. Must be between 0 and 3000. Default 1300.
      grip_direction: Grip direction. 1 = negative Y, 2 = positive X, 3 = positive Y,
                      4 = negative X. Must be between 1 and 4. Default 1.
      collision_control_level: collision control level 1 = high 0 = low. Must be between 0 and 1.
                               Default 1.
    """

    _assert_clamp(x_position, 0, 30000, "x_position")
    _assert_clamp(x_direction, 0, 1, "x_direction")
    _assert_clamp(y_position, 0, 6500, "y_position")
    _assert_clamp(y_direction, 0, 1, "y_direction")
    _assert_clamp(z_position, 0, 3600, "z_position")
    _assert_clamp(z_direction, 0, 1, "z_direction")
    _assert_clamp(location, 0, 1, "location")
    _assert_clamp(hotel_depth, 0, 3000, "hotel_depth")
    _assert_clamp(grip_direction, 1, 4, "grip_direction")
    _assert_clamp(collision_control_level, 0, 1, "collision_control_level")

    return self.send_command(
      module="C0",
      command="PC",
      xs=x_position,
      xd=x_direction,
      yj=y_position,
      yd=y_direction,
      zj=z_position,
      zd=z_direction,
      hh=location,
      hd=hotel_depth,
      gr=grip_direction,
      ga=collision_control_level,
    )

  # -------------- 3.17.6 iSWAP query --------------

  def request_iswap_in_parking_position(self):
    """ Request iSWAP in parking position

    Returns:
      0 = gripper is not in parking position
      1 = gripper is in parking position
    """

    resp = self.send_command(module="C0", command="RG")
    return self.parse_response(resp, "rg#")

  def request_plate_in_iswap(self):
    """ Request plate in iSWAP

    Returns:
      0 = plate not holding
      1 = plate holding
    """

    resp = self.send_command(module="C0", command="RG")
    return self.parse_response(resp, "rg#")

  def request_iswap_position(self):
    """ Request iSWAP position ( grip center )

    Returns:
      xs: Hotel center in X direction [0.1mm]
      xd: X direction 0 = positive 1 = negative
      yj: Gripper center in Y direction [0.1mm]
      yd: Y direction 0 = positive 1 = negative
      zj: Gripper Z height (gripping height) [0.1mm]
      zd: Z direction 0 = positive 1 = negative
    """

    resp = self.send_command(module="C0", command="RG")
    return self.parse_response(resp, "xs#####xd#yj####yd#zj####zd#")


# TODO: temp test
if __name__ == "__main__":
  dev = STAR()
  print(dev.request_master_status())
