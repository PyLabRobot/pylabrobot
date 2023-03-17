"""
This file defines interfaces for all supported Tecan liquid handling robots.
"""

# pylint: disable=invalid-name

from abc import ABCMeta, abstractmethod
from typing import Dict, List, Optional, Tuple, Sequence, TypeVar, Union

from pylabrobot.liquid_handling.liquid_classes.tecan import TecanLiquidClass, get_liquid_class
from pylabrobot.liquid_handling.backends.USBBackend import USBBackend
from pylabrobot.liquid_handling.standard import (
  Pickup,
  PickupTipRack,
  Drop,
  DropTipRack,
  Aspiration,
  AspirationPlate,
  Dispense,
  DispensePlate,
  Move
)
from pylabrobot.resources import (
  TecanPlate,
  TecanTipRack,
  TecanTip
)

T = TypeVar("T")

class TecanLiquidHandler(USBBackend, metaclass=ABCMeta):
  """
  Abstract base class for Tecan liquid handling robot backends.
  """

  @abstractmethod
  def __init__(
    self,
    packet_read_timeout: int = 3,
    read_timeout: int = 30,
    write_timeout: int = 30,
  ):
    """

    Args:
      packet_read_timeout: The timeout for reading packets from the Tecan machine in seconds.
      read_timeout: The timeout for reading from the Tecan machine in seconds.
    """

    super().__init__(
      packet_read_timeout=packet_read_timeout,
      read_timeout=read_timeout,
      write_timeout=write_timeout,
      id_vendor=0x0C47,
      id_product=0x4000)

    self._cache: Dict[str, List[Optional[int]]] = {}

  def _assemble_command(
    self,
    module: str,
    command: str,
    params: List[Optional[int]]) -> str:
    """ Assemble a firmware command to the Tecan machine.

    Args:
      module: 2 character module identifier (C5 for LiHa, ...)
      command: 3 character command identifier
      params: list of integer parameters

    Returns:
      A string containing the assembled command.
    """

    cmd = module + command + ",".join(str(a) if a is not None else "" for a in params)
    return f"\02{cmd}\00"

  def parse_response(self, resp: str) -> Dict[str, Union[str, List[int]]]:
    """ Parse a machine response string

    Args:
      resp: The response string to parse.

    Returns:
      A dictionary containing the parsed values.
    """

    data: List[int] = [int(x) for x in resp[3:-1].split(",")]
    return {
      "module": resp[1:3],
      "ret": [0], # data[0] TODO: get error return code
      "data": data
    }

  async def send_command(
    self,
    module: str,
    command: str,
    params: List[Optional[int]],
    write_timeout: Optional[int] = None,
    read_timeout: Optional[int] = None,
    wait = True
  ):
    """ Send a firmware command to the Tecan machine. Caches `set` commands and ignores if
    redundant.

    Args:
      module: 2 character module identifier (C5 for LiHa, ...)
      command: 3 character command identifier
      params: list of integer parameters
      write_timeout: write timeout in seconds. If None, `self.write_timeout` is used.
      read_timeout: read timeout in seconds. If None, `self.read_timeout` is used.
      wait: If True, wait for a response. If False, return `None` immediately after sending.

    Returns:
      A dictionary containing the parsed response, or None if no response was read within `timeout`.
    """

    if command[0] == "S":
      k = module + command
      if k in self._cache and self._cache[k] == params:
        return
      self._cache[k] = params

    cmd = self._assemble_command(module, command, params)

    self.write(cmd, timeout=write_timeout)
    if not wait:
      return None

    resp = self.read(timeout=read_timeout).decode("utf-8", "ignore")
    return self.parse_response(resp)


class EVO(TecanLiquidHandler):
  """
  Interface for the Tecan Freedom EVO 200
  """

  def __init__(
    self,
    packet_read_timeout: int = 3,
    read_timeout: int = 30,
    write_timeout: int = 30,
  ):
    """ Create a new STAR interface.

    Args:
      packet_read_timeout: timeout in seconds for reading a single packet.
      read_timeout: timeout in seconds for reading a full response.
      write_timeout: timeout in seconds for writing a command.
    """

    super().__init__(
      packet_read_timeout=packet_read_timeout,
      read_timeout=read_timeout,
      write_timeout=write_timeout)

  @property
  def num_channels(self) -> int:
    """ The number of pipette channels present on the robot. """

    if self._num_channels is None:
      raise RuntimeError("has not loaded num_channels, forgot to call `setup`?")
    return self._num_channels

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "packet_read_timeout": self.packet_read_timeout,
      "read_timeout": self.read_timeout,
      "write_timeout": self.write_timeout,
    }

  async def setup(self):
    """ setup

    Creates a USB connection and finds read/write interfaces.
    """

    await super().setup()

    self._num_channels = await self.report_number_tips()
    self._x_range = await self.report_x_param(5)
    self._y_range = (await self.report_y_param(5))[0]
    self._z_range = (await self.report_z_param(5))[0] # TODO: assert all are same?

  # ============== LiquidHandlerBackend methods ==============

  async def aspirate(
    self,
    ops: List[Aspiration],
    use_channels: List[int]
  ):
    """ Aspirate liquid from the specified channels.

    Args:
      ops: The aspiration operations to perform.
      use_channels: The channels to use for the operations.
    """

    x_positions, y_positions, z_positions = self._ops_to_fw_positions(ops, use_channels)

    tecan_liquid_classes = [
      get_liquid_class(
        target_volume=op.volume,
        liquid_class=op.liquid_class,
        tip_type=op.tip.tip_type
      ) if isinstance(op.tip, TecanTip) else None for op in ops]

    for op, tlc in zip(ops, tecan_liquid_classes):
      op.volume = tlc.compute_corrected_volume(op.volume) if tlc is not None else op.volume

    ys = int(ops[0].resource.get_size_y() * 10)
    zadd: List[Optional[int]] = [0] * self._num_channels
    for i, channel in enumerate(use_channels):
      par = ops[i].resource.parent
      if par is None:
        continue
      if not isinstance(par, TecanPlate):
        raise ValueError(f"Operation is not supported by resource {par}.")
      # TODO: calculate defaults when area is not specified
      zadd[channel] = round(ops[i].volume / par.area * 10)

    # moves such that first channel is over first location
    x, _ = self._first_valid(x_positions)
    y, yi = self._first_valid(y_positions)
    assert x is not None and y is not None
    await self.set_z_travel_height([self._z_range] * self._num_channels)
    await self.position_absolute_all_axis(
      x, y - yi * ys, ys,
      [z if z else self._z_range for z in z_positions["travel"]])
    # TODO check channel positions match resource positions

    # aspirate airgap
    pvl, sep, ppr = self._aspirate_airgap(use_channels, tecan_liquid_classes, "lag")
    if any(ppr):
      await self.position_valve_logical(pvl)
      await self.set_end_speed_plunger(sep)
      await self.move_plunger_relative(ppr)

    # perform liquid level detection
    # TODO: only supports Detect twice with separate tips with retract (SDM7,1)
    if any(tlc.aspirate_lld if tlc is not None else None for tlc in tecan_liquid_classes):
      tlc, _ = self._first_valid(tecan_liquid_classes)
      assert tlc is not None
      detproc = tlc.lld_mode # must be same for all channels?
      sense = tlc.lld_conductivity
      await self.set_detection_mode(detproc, sense)
      ssl, sdl, sbl = self._liquid_detection(use_channels, tecan_liquid_classes)
      await self.set_search_speed(ssl)
      await self.set_search_retract_distance(sdl)
      await self.set_search_z_start(z_positions["start"])
      await self.set_search_z_max(list(z if z else self._z_range for z in z_positions["max"]))
      await self.set_search_submerge(sbl)
      shz = [min(z for z in z_positions["travel"] if z)] * self._num_channels
      await self.set_z_travel_height(shz)
      await self.move_detect_liquid(self._bin_use_channels(use_channels), zadd)
      await self.set_z_travel_height([self._z_range] * self._num_channels)

    # aspirate + retract
    # SSZ: z_add / (vol / asp_speed)
    zadd = [min(z, 32) if z else None for z in zadd]
    ssz, sep, stz, mtr, ssz_r = self._aspirate(ops, use_channels, tecan_liquid_classes, zadd)
    await self.set_slow_speed_z(ssz)
    await self.set_end_speed_plunger(sep)
    await self.set_tracking_distance_z(stz)
    await self.move_tracking_relative(mtr)
    await self.set_slow_speed_z(ssz_r)
    await self.move_absolute_z(z_positions["start"]) # TODO: use retract_position and offset

    # aspirate airgap
    pvl, sep, ppr = self._aspirate_airgap(use_channels, tecan_liquid_classes, "tag")
    await self.position_valve_logical(pvl)
    await self.set_end_speed_plunger(sep)
    await self.move_plunger_relative(ppr)

  async def dispense(
    self,
    ops: List[Dispense],
    use_channels: List[int]
  ):
    """ Dispense liquid from the specified channels.

    Args:
      ops: The dispense operations to perform.
      use_channels: The channels to use for the dispense operations.
    """

    x_positions, y_positions, z_positions = self._ops_to_fw_positions(ops, use_channels)
    ys = int(ops[0].resource.get_size_y() * 10)

    tecan_liquid_classes = [
      get_liquid_class(
        target_volume=op.volume,
        liquid_class=op.liquid_class,
        tip_type=op.tip.tip_type
      ) if isinstance(op.tip, TecanTip) else None for op in ops]

    for op, tlc in zip(ops, tecan_liquid_classes):
      op.volume = tlc.compute_corrected_volume(op.volume) + \
        tlc.aspirate_lag_volume + tlc.aspirate_tag_volume \
        if tlc is not None else op.volume

    x, _ = self._first_valid(x_positions)
    y, yi = self._first_valid(y_positions)
    assert x is not None and y is not None
    await self.set_z_travel_height(z if z else self._z_range for z in z_positions["travel"])
    await self.position_absolute_all_axis(
      x, y - yi * ys, ys,
      [z if z else self._z_range for z in z_positions["dispense"]])

    sep, spp, stz, mtr = self._dispense(ops, use_channels, tecan_liquid_classes)
    await self.set_end_speed_plunger(sep)
    await self.set_stop_speed_plunger(spp)
    await self.set_tracking_distance_z(stz)
    await self.move_tracking_relative(mtr)

  async def pick_up_tips(self, ops: List[Pickup], use_channels: List[int]):
    raise NotImplementedError()

  async def drop_tips(self, ops: List[Drop], use_channels: List[int]):
    raise NotImplementedError()

  async def pick_up_tips96(self, pickup: PickupTipRack):
    raise NotImplementedError()

  async def drop_tips96(self, drop: DropTipRack):
    raise NotImplementedError()

  async def aspirate96(self, aspiration: AspirationPlate):
    raise NotImplementedError()

  async def dispense96(self, dispense: DispensePlate):
    raise NotImplementedError()

  async def move_resource(self, move: Move):
    raise NotImplementedError()

  def _first_valid(
    self,
    l: List[Optional[T]]
  ) -> Tuple[Optional[T], int]:
    """ Returns first item in list that is not None """

    for i, v in enumerate(l):
      if v is not None:
        return v, i
    return None, -1

  def _bin_use_channels(
    self,
    use_channels: List[int]
  ) -> int:
    """ Converts use_channels to a binary coded tip representation. """

    b = 0
    for channel in use_channels:
      b += (1 << channel)
    return b

  def _ops_to_fw_positions(
    self,
    ops: Sequence[Union[Aspiration, Dispense]],
    use_channels: List[int]
  ) -> Tuple[List[Optional[int]], List[Optional[int]], Dict[str, List[Optional[int]]]]:
    """ Creates lists of x, y, and z positions used by ops. """

    x_positions: List[Optional[int]] = [None] * self._num_channels
    y_positions: List[Optional[int]] = [None] * self._num_channels
    z_positions: Dict[str, List[Optional[int]]] = {
      "travel": [None] * self._num_channels,
      "start": [None] * self._num_channels,
      "dispense": [None] * self._num_channels,
      "max": [None] * self._num_channels
    }
    def get_z_position(z, z_off, tip_length):
      return int(self._z_range - z + z_off * 10 + tip_length)  # TODO: verify z formula

    for i, channel in enumerate(use_channels):
      location = ops[i].resource.get_absolute_location() + ops[i].resource.center()
      x_positions[channel] = int((location.x - 100) * 10)
      y_positions[channel] = int((345 - location.y) * 10) # TODO: verify

      par = ops[i].resource.parent
      if not isinstance(par, (TecanPlate, TecanTipRack)):
        raise ValueError(f"Operation is not supported by resource {par}.")
      # TODO: calculate defaults when z-attribs are not specified
      tip_length = ops[i].tip.total_tip_length
      z_positions["travel"][channel] = get_z_position(
        par.z_travel, par.get_absolute_location().z, tip_length)
      z_positions["start"][channel] = get_z_position(
        par.z_start, par.get_absolute_location().z, tip_length)
      z_positions["dispense"][channel] = get_z_position(
        par.z_dispense, par.get_absolute_location().z, tip_length)
      z_positions["max"][channel] = get_z_position(
        par.z_max, par.get_absolute_location().z, tip_length)

    return x_positions, y_positions, z_positions

  def _aspirate_airgap(
    self,
    use_channels: List[int],
    tecan_liquid_classes: List[Optional[TecanLiquidClass]],
    airgap: str
  ) -> Tuple[List[Optional[int]], List[Optional[int]], List[Optional[int]]]:
    """ Creates parameters used to aspirate airgaps.

    Args:
      airgap: `lag` for leading airgap, `tag` for trailing airgap.

    Returns:
      pvl: position_valve_logial
      sep: set_end_speed_plunger
      ppr: move_plunger_relative
    """

    pvl: List[Optional[int]] = [None] * self._num_channels
    sep: List[Optional[int]] = [None] * self._num_channels
    ppr: List[Optional[int]] = [None] * self._num_channels

    for i, channel in enumerate(use_channels):
      tlc = tecan_liquid_classes[i]
      assert tlc is not None
      pvl[channel] = 0
      if airgap == "lag":
        sep[channel] = int(tlc.aspirate_lag_speed * 12) # 6? TODO: verify step unit
        ppr[channel] = int(tlc.aspirate_lag_volume * 6) # 3?
      elif airgap == "tag":
        sep[channel] = int(tlc.aspirate_tag_speed * 12) # 6?
        ppr[channel] = int(tlc.aspirate_tag_volume * 6) # 3?

    return pvl, sep, ppr

  def _liquid_detection(
    self,
    use_channels: List[int],
    tecan_liquid_classes: List[Optional[TecanLiquidClass]],
  ) -> Tuple[List[Optional[int]], List[Optional[int]], List[Optional[int]]]:
    """ Creates parameters use for liquid detection.

    Returns:
      ssl: set_search_speed
      sdl: set_search_retract_distance
      sbl: set_search_submerge
    """

    ssl: List[Optional[int]] = [None] * self._num_channels
    sdl: List[Optional[int]] = [None] * self._num_channels
    sbl: List[Optional[int]] = [None] * self._num_channels

    for i, channel in enumerate(use_channels):
      tlc = tecan_liquid_classes[i]
      assert tlc is not None
      ssl[channel] = int(tlc.lld_speed * 10)
      sdl[channel] = int(tlc.lld_distance * 10)
      sbl[channel] = int(tlc.aspirate_lld_offset * 10)

    return ssl, sdl, sbl

  def _aspirate(
    self,
    ops: Sequence[Union[Aspiration, Dispense]],
    use_channels: List[int],
    tecan_liquid_classes: List[Optional[TecanLiquidClass]],
    zadd: List[Optional[int]]
  ) -> Tuple[
    List[Optional[int]],
    List[Optional[int]],
    List[Optional[int]],
    List[Optional[int]],
    List[Optional[int]]]:
    """ Creates parameters used for aspiration action.

    Args:
      zadd: distance moved while aspirating

    Returns:
      ssz: set_slow_speed_z
      sep: set_end_speed_plunger
      stz: set_tracking_distance_z
      mtr: move_tracking_relative
      ssz_r: set_slow_speed_z retract
    """

    ssz: List[Optional[int]] = [None] * self._num_channels
    sep: List[Optional[int]] = [None] * self._num_channels
    stz: List[Optional[int]] = [-z if z else None for z in zadd] # TODO: verify max cutoff
    mtr: List[Optional[int]] = [None] * self._num_channels
    ssz_r: List[Optional[int]] = [None] * self._num_channels

    for i, channel in enumerate(use_channels):
      tlc = tecan_liquid_classes[i]
      z = zadd[channel]
      assert tlc is not None and z is not None
      sep[channel] = int(tlc.aspirate_speed * 12) # 6?
      ssz[channel] = round(z * tlc.aspirate_speed / ops[i].volume)
      mtr[channel] = round(ops[i].volume * 6) # 3?
      ssz_r[channel] = int(tlc.aspirate_retract_speed * 10)

    return ssz, sep, stz, mtr, ssz_r

  def _dispense(
    self,
    ops: Sequence[Union[Aspiration, Dispense]],
    use_channels: List[int],
    tecan_liquid_classes: List[Optional[TecanLiquidClass]]
  ) -> Tuple[List[Optional[int]], List[Optional[int]], List[Optional[int]], List[Optional[int]]]:
    """ Creates parameters used for dispense action.

    Returns:
      sep: set_end_speed_plunger
      spp: set_stop_speed_plunger
      stz: set_tracking_distance_z
      mtr: move_tracking_relative
    """

    sep: List[Optional[int]] = [None] * self._num_channels
    spp: List[Optional[int]] = [None] * self._num_channels
    stz: List[Optional[int]] = [None] * self._num_channels
    mtr: List[Optional[int]] = [None] * self._num_channels

    for i, channel in enumerate(use_channels):
      tlc = tecan_liquid_classes[i]
      assert tlc is not None
      sep[channel] = int(tlc.dispense_speed * 12) # 6?
      spp[channel] = int(tlc.dispense_breakoff * 12) # 6?
      stz[channel] = 0
      mtr[channel] = -round(ops[i].volume * 6) # 3?

    return sep, spp, stz, mtr

  # ============== Firmware Commands ==============

  async def report_number_tips(self) -> int:
    """ Report number of tips on arm. """

    resp: List[int] = (await self.send_command(module="C5", command="RNT", params=[1]))["data"]
    return resp[0]

  async def report_x_param(self, param: int) -> int:
    """ Report current parameter for x-axis.

    Args:
      param: 0 - current position, 5 - actual machine range
    """

    resp: List[int] = (await self.send_command(module="C5", command="RPX", params=[param]))["data"]
    return resp[0]

  async def report_y_param(self, param: int) -> List[int]:
    """ Report current parameters for y-axis.

    Args:
      param: 0 - current position, 5 - actual machine range
    """

    resp: List[int] = (await self.send_command(module="C5", command="RPY", params=[param]))["data"]
    return resp

  async def report_z_param(self, param: int) -> List[int]:
    """ Report current parameters for z-axis.

    Args:
      param: 0 - current position, 5 - actual machine range
    """

    resp: List[int] = (await self.send_command(module="C5", command="RPZ", params=[param]))["data"]
    return resp

  async def position_absolute_all_axis(self, x: int, y: int, ys: int, z: List[int]):
    """ Position absolute for all axes.

    Args:
      x: aboslute x position in 1/10 mm, must be in allowed machine range
      y: absolute y position in 1/10 mm, must be in allowed machine range
      ys: absolute y spacing in 1/10 mm, must be between 90 and 380
      z: absolute z position in 1/10 mm for each channel, must be in
         allowed machine range
    """

    await self.send_command(module="C5", command="PAA", params=list([x, y, ys] + z))

  async def position_valve_logical(self, param: List[Optional[int]]):
    """ Position valve logical for each channel.

    Args:
      param: 0 - outlet, 1 - inlet, 2 - bypass
    """

    await self.send_command(module="C5", command="PVL", params=param)

  async def set_end_speed_plunger(self, speed: List[Optional[int]]):
    """ Set end speed for plungers.

    Args:
      speed: speed for each plunger in half step per second, must be between
             5 and 6000
    """

    await self.send_command(module="C5", command="SEP", params=speed)

  async def move_plunger_relative(self, rel: List[Optional[int]]):
    """ Move plunger relative upwards (dispense) or downards (aspirate).

    Args:
      rel: relative position for each plunger in full steps, must be between
           -3150 and 3150
    """

    await self.send_command(module="C5", command="PPR", params=rel)

  async def set_detection_mode(self, proc: int, sense: int):
    """ Set liquid detection mode.

    Args:
      proc: detection procedure (7 for double detection sequential with
            retract and extra submerge)
      sense: conductivity (1 for high)
    """

    await self.send_command(module="C5", command="SDM", params=[proc, sense])

  async def set_search_speed(self, speed: List[Optional[int]]):
    """ Set search speed for liquid search commands.

    Args:
      speed: speed for each channel in 1/10 mm/s, must be between 1 and 1500
    """

    await self.send_command(module="C5", command="SSL", params=speed)

  async def set_search_retract_distance(self, dist: List[Optional[int]]):
    """ Set z-axis retract distance for liquid search commands.

    Args:
      dist: retract distance for each channel in 1/10 mm, must be in allowed
            machine range
    """

    await self.send_command(module="C5", command="SDL", params=dist)

  async def set_search_submerge(self, dist: List[Optional[int]]):
    """ Set submerge for liquid search commands.

    Args:
      dist: submerge distance for each channel in 1/10 mm, must be between
            -1000 and max z range
    """

    await self.send_command(module="C5", command="SBL", params=dist)

  async def set_search_z_start(self, z: List[Optional[int]]):
    """ Set z-start for liquid search commands.

    Args:
      z: start height for each channel in 1/10 mm, must be in allowed machine range
    """

    await self.send_command(module="C5", command="STL", params=z)

  async def set_search_z_max(self, z: List[Optional[int]]):
    """ Set z-max for liquid search commands.

    Args:
      z: max for each channel in 1/10 mm, must be in allowed machine range
    """

    await self.send_command(module="C5", command="SML", params=z)

  async def set_z_travel_height(self, z):
    """ Set z-travel height.

    Args:
      z: travel heights in absolute 1/10 mm for each channel, must be in allowed
         machine range + 20
    """
    await self.send_command(module="C5", command="SHZ", params=z)

  async def move_detect_liquid(self, channels: int, zadd: List[Optional[int]]):
    """ Move tip, detect liquid, submerge.

    Args:
      channels: binary coded tip select
      zadd: required distance to travel downwards in 1/10 mm for each channel,
            must be between 0 and z-start - z-max
    """

    await self.send_command(module="C5", command="MDT",
      params=[channels] + [None] * 3 + zadd)

  async def set_slow_speed_z(self, speed: List[Optional[int]]):
    """ Set slow speed for z.

    Args:
      speed: speed in 1/10 mm/s for each channel, must be between 1 and 4000
    """

    await self.send_command(module="C5", command="SSZ", params=speed)

  async def set_tracking_distance_z(self, rel: List[Optional[int]]):
    """ Set z-axis relative tracking distance used by dispense and aspirate.

    Args:
      rel: relative value in 1/10 mm for each channel, must be between
            -2100 and 2100
    """

    await self.send_command(module="C5", command="STZ", params=rel)

  async def move_tracking_relative(self, rel: List[Optional[int]]):
    """ Move tracking relative. Starts the z-drives and dilutors simultaneously
        to achieve a synchronous tracking movement.

    Args:
      rel: relative position for each plunger in full steps, must be between
           -3150 and 3150
    """

    await self.send_command(module="C5", command="MTR", params=rel)

  async def move_absolute_z(self, z: List[Optional[int]]):
    """ Position absolute with slow speed z-axis

    Args:
      z: absolute osition in 1/10 mm for each channel, must be in
         allowed machine range
    """

    await self.send_command(module="C5", command="MAZ", params=z)

  async def set_stop_speed_plunger(self, speed: List[Optional[int]]):
    """ Set stop speed for plungers

    Args:
      speed: speed for each plunger in half step per second, must be between
             50 and 2700
    """

    await self.send_command(module="C5", command="SPP", params=speed)
