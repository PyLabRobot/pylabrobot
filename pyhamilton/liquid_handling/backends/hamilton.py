"""
This file defines interfaces for all supported Hamilton liquid handling robots.
"""

from abc import ABCMeta, abstractmethod
import datetime
import enum
import re

import usb.core

# TODO: from .backend import LiquidHanderBackend


# TODO: move to util.
def _assert_clamp(v, min, max, name):
  assert min <= v <= max, "{name} must be between {min} and {max}, but is {v}" \
                          .format(name=name, min=min, max=max, v=v)


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
    self.setup()

  @staticmethod
  def generate_id():
    """ continuously generate unique ids 0 <= x < 10000. """
    id = 0
    while True:
      yield id % 10000
      id += 1
 
  def send_command(module, command, **kwargs):
    # assemble command
    cmd = module + command
    id = generate_id()
    cmd += 'id{}'.format(id) # has to be first param

    for k, v in kwoargs.items():
      if type(v) is datetime.datetime:
        v = v.strftime('%Y-%m-%d %h:%M')
      cmd += '{}{}'.format(k, v)

    # write command to endpoint
    self.dev.write(self.write_endpoint)

    # block by default
    res = None
    while res is None:
      res = self.dev.read(
        self.read_endpoint,
        self.read_endpoint.wMaxPacketSize
      )
      time.sleep(self.read_poll_interval)
    return res
  
  def parse_response(resp: str, fmt: str):
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

    This order of parameters in the format and response string do not
    have to (and often do not) match.

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
    """

    # Verify format and resp match.
    resp = resp[2:] # remove device identifier from response.
    assert resp[:2] == fmt[:2], "cmds in resp and fmt do not match"
    resp = resp[2:]; fmt = fmt[2:] # remove command identifier from both.

    # Parse the parameters in the fmt string.
    info = {}

    def find_param(param):
      name, data = param[0:2], param[2:]
      type_ = {
        "#": 'int',
        "*": 'hex',
        "&": 'str'
      }[param[2]]
      len_ = len(data)

      # Build a regex to match this parameter.
      exp = {
        'int': '[0-9]',
        'hex': '[0-9a-fA-F]',
        'str': '.',
      }[type_]
      regex = f"{name}({exp}{ {len_} })"

      # Match response against regex, save results in right datatype.
      m = re.search(regex, resp).groups()[0]

      if type_ == 'str':
        info[name] = m
      elif type_ == 'int':
        info[name] = int(m)
      elif type_ == 'hex':
        info[name] = int(m, base=16)

    param = ''
    reading = True
    for char in fmt:
      if char.islower():
        if reading and len(param) > 2:
          reading = False
        if not reading:
          find_param(param)
          param = ''
          reading = True
      param += char
    find_param(param) # last parameter is not closed by loop.

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

    self.dev = usb.core.find(idVendor=0x08af)
    if self.dev is None:
      raise ValueError("Hamilton STAR device not found.")

    # TODO: can we find endpoints dynamically?
    self.write_endpoint = 0x3
    self.read_endpoint = 0x83

  def _read():
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
    return self.send_command(module="0", command="VI")
 
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

    _assert_clamp(tip_needle_index, 0, 99, "tip_needle_index")
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

    resp = self.send_command(module="C0", command="QB")
    try:
      return BoardType(resp['qb'])
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
    return resp['qw'] == 1

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

    return self.send_command(module="C0", command="SR")['sr']
  
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
    serial_number: str = '0000'
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

    return self.send_command(device="C0", command="AT")
  
  def set_x_offset_x_axis_iswap(self, x_offset: int):
    """ Set X-offset X-axis <-> iSWAP
    
    Args:
      x_offset: X-offset [0.1mm]
    """

    return self.send_command(
      device="C0",
      command="AG",
      x_offset=kf 
    )
  
  def set_x_offset_x_axis_core_96_head(self, x_offset: int):
    """ Set X-offset X-axis <-> CoRe 96 head
    
    Args:
      x_offset: X-offset [0.1mm]
    """

    return self.send_command(
      device="C0",
      command="AF",
      x_offset=kd 
    )
  
  def set_x_offset_x_axis_core_nano_pipettor_head(self, x_offset: int):
    """ Set X-offset X-axis <-> CoRe 96 head
    
    Args:
      x_offset: X-offset [0.1mm]
    """

    return self.send_command(
      device="C0",
      command="AF",
      x_offset=kn 
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
      qt=processor_board + ' ' + power_supply,
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
      tip_waste_x-position: tip waste X-position. Must be between 1000 and
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
                                              see Fig. 4. Must be between 0 and 30000. Default 11400.
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
      pip_maximal_y_position: PIP maximal Y position [0.1 mm]. Must be between 0 and 9999. Default 6065.
      left_arm_minimal_y_position: left arm minimal Y position [0.1 mm]. Must be between 0 and 9999.
                                    Default 60.
      right_arm_minimal_y_position: right arm minimal Y position [0.1 mm]. Must be between 0 and 9999.
                                    Default 60.
    """

    _assert_clamp(instrument_size_in_slots_(x_range), 10, 99, "instrument_size_in_slots_(x_range)")
    _assert_clamp(auto_load_size_in_slots, 10, 54, "auto_load_size_in_slots")
    _assert_clamp(tip_waste_x-position, 1000, 25000, "tip_waste_x-position")
    _assert_clamp(right_x_drive_configuration_byte_1, 0, 1, "right_x_drive_configuration_byte_1")
    _assert_clamp(right_x_drive_configuration_byte_2, 0, 1, "right_x_drive_configuration_byte_2")
    _assert_clamp(minimal_iswap_collision_free_position, 0, 30000, "minimal_iswap_collision_free_position")
    _assert_clamp(maximal_iswap_collision_free_position, 0, 30000, "maximal_iswap_collision_free_position")
    _assert_clamp(left_x_arm_width, 0, 9999, "left_x_arm_width")
    _assert_clamp(right_x_arm_width, 0, 9999, "right_x_arm_width")
    _assert_clamp(num_pip_channels, 0, 16, "num_pip_channels")
    _assert_clamp(num_xl_channels, 0, 8, "num_xl_channels")
    _assert_clamp(num_robotic_channels, 0, 8, "num_robotic_channels")
    _assert_clamp(minimal_raster_pitch_of_pip_channels, 0, 999, "minimal_raster_pitch_of_pip_channels")
    _assert_clamp(minimal_raster_pitch_of_xl_channels, 0, 999, "minimal_raster_pitch_of_xl_channels")
    _assert_clamp(minimal_raster_pitch_of_robotic_channels, 0, 999, "minimal_raster_pitch_of_robotic_channels")
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
        xw=tip_waste_x-position,
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

    return self.send_command(device="C0", command="AJ")
  
  def set_deck_data(
    self,
    data_index: int = 0,
    data_stream: str = '0'
  ):
    """ set deck data

    Args:
      data_index: data index. Must be between 0 and 9. Default 0.
      data_stream: data stream (12 characters). Default <class 'str'>.
    """

    assert_clamp(data_index, 0, 9, "data_index")
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
    return self.send_command(device="C0", command="QT")

  def request_installation_data(self):
    """ Request installation data """

    # TODO: parse res
    return self.send_command(device="C0", command="RI")

  def request_download_date(self):
    """ Request download date """

    # TODO: parse res
    return self.send_command(device="C0", command="RO")
  
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
      device="C0",
      command="RO",
      vo = verification_subject
    )

  def request_additional_timestamp_data(self):
    """ Request additional timestamp data """

    # TODO: parse res
    return self.send_command(device="C0", command="RS")

  def request_pip_channel_validation_status(self):
    """ Request PIP channel validation status """

    # TODO: parse res
    return self.send_command(device="C0", command="RJ")

  def request_xl_channel_validation_status(self):
    """ Request XL channel validation status """

    # TODO: parse res
    return self.send_command(device="C0", command="UJ")

  def request_machine_configuration(self):
    """ Request machine configuration """

    # TODO: parse res
    return self.send_command(device="C0", command="RM")

  def request_extended_configuration(self):
    """ Request extended configuration """

    resp = self.send_command(device="C0", command="QM")
    return self.parse_response(resp, fmt="QMid####ka******ke********xt##xa##xw#####xl**" + \
            "xn**xr**xo**xm#####xx#####xu####xv####kc#kr#ys###kl###km###ym####yu####yx####")

  def request_node_names(self):
    """ Request node names """

    # TODO: parse res
    return self.send_command(device="C0", command="RK")

  def request_deck_data(self):
    """ Request deck data """

    # TODO: parse res
    return self.send_command(device="C0", command="VD")

  # -------------- 3.4 X-Axis control --------------
  # -------------- 3.4.1 Movements --------------


# TODO: temp test
if __name__ == "__main__":
  v = STAR()
  print(v.request_master_status())
