"""A STAR that answers without being plugged in.

Each capability has a small subclass here that overrides the handful of methods which would
otherwise talk to a machine, returning what one would have said. `STARSimulationDriver` swaps
those in, so everything above them - discovery, the initialization order, the configuration each
capability resolves - runs exactly as it does against hardware.

Nothing reaches the wire. `send_command` raises, which is how a command that has not been
simulated makes itself known: override the method that sends it, on the capability that owns it.
"""

import dataclasses
import datetime
import logging
from typing import Any, Dict, List, Optional, Tuple, cast

from pylabrobot.hamilton.protocol.text.framing import (
  assemble_channel_command,
  parse_firmware_version_date,
)
from pylabrobot.hamilton.star.driver.configuration import DeviceConfiguration
from pylabrobot.hamilton.star.driver.features.autoload import Autoload, AutoloadConfiguration
from pylabrobot.hamilton.star.driver.features.cover import CoverPosition, FrontCover
from pylabrobot.hamilton.star.driver.features.head import (
  HEAD_REFERENCE_SHAFT,
  Head,
  HeadConfiguration,
)
from pylabrobot.hamilton.star.driver.features.head96 import Head96, Head96Configuration
from pylabrobot.hamilton.star.driver.features.head384 import Head384, Head384Configuration
from pylabrobot.hamilton.star.driver.features.iswap import iSWAP
from pylabrobot.hamilton.star.driver.features.pipettes import (
  PipetteConfiguration,
  Pipettes,
)
from pylabrobot.hamilton.star.driver.features.x_arm import XArm, XArmConfiguration
from pylabrobot.hamilton.star.driver.master import STARDriver
from pylabrobot.io.io import IOBase
from pylabrobot.io.validation_utils import LOG_LEVEL_IO
from pylabrobot.resources.hamilton.hamilton_decks import (
  _RAILS_WIDTH,
  STAR_NUM_RAILS,
  STARLET_NUM_RAILS,
  HamiltonDeck,
)

logger = logging.getLogger(__name__)

# What each capability reports for its firmware. Read off a real instrument, so a simulated run
# resolves to a machine that exists.
SIMULATED_FIRMWARE = {
  "master": "7.6S 25 2021_11_05 (GRU C0)",
  "pipettes": "4.0S j 2022-03-16",
  "x_arm": "1.4S 2012-04-25",
  "head96": "5.0S i 2021-10-22 (H0 XE167)",
  "iswap": "4.1S 2011-12-19",
  # No 384-head has been read, so this is marked as simulated rather than given a version that
  # would read as one taken off a machine. The date is the specification's.
  "head384": "0.0S 2015-08-07 (D0 simulated)",
}

# Made up, so a simulator is never mistaken for a particular machine.
SIMULATED_SERIAL_NUMBER = "SIM0"

# What stands where a transport's identity would be in the log, so simulated and recorded runs read
# the same way.
SIMULATED_LINK = "[simulation]"

# How wide a pipette is, in mm.
PIPETTE_WIDTH = 8.98

# Where the channels rest on a simulated machine, in mm: their Z-safety height, and the Y band the
# initialization procedure spreads them across, so a simulated machine looks like one that has been
# been set up rather than one with every channel on top of the next.
SIMULATED_CHANNEL_Z_SAFETY = 334.3

# What each pipetting channel is: an ML_STAR channel on an ML_STAR head, with a CoRe II stop disc
# and a Renesas pressure ADC.
SIMULATED_PIPETTE = PipetteConfiguration(
  channel_type="ML_STAR",
  head_type="ML_STAR",
  stop_disc_type="core_ii",
  pressure_adc="Renesas_X9268",
)

# The 96-head this machine has, as the autoload beside it: what it answers about itself, read off a
# real instrument. Discovery fills the capability's own configuration from these, as on a machine.
SIMULATED_HEAD96 = Head96Configuration(
  # Resolves the windows and defaults this head derives, so it has to know its own.
  firmware_version=SIMULATED_FIRMWARE["head96"],
  firmware_date=parse_firmware_version_date(SIMULATED_FIRMWARE["head96"]),
  head_type="96 head II",
  x_offset=368.2,
  supports_clot_monitoring_clld=False,
  stop_disc_type="core_ii",
  instrument_type="legacy",
)

# Where a head parks along Y, and the nine further slots it stores beside that one. Read off a
# real 96-head; the 384-head documents the same two.
SIMULATED_HEAD_Y_PARK = 554.45
SIMULATED_HEAD_Y_PREDEFINED = 546.88

# Where its Z drive comes to rest when the firmware retracts it, in mm. Not a device fact but a
# probe result, so it stands apart from the configuration, as each drive's rest position does.
SIMULATED_HEAD96_Z_SAFETY = 336.97

# The 384-head a machine configured for one has. Unlike the 96-head above, none of this is read off
# an instrument: the offset and the drive defaults are the ones the drives document.
SIMULATED_HEAD384 = Head384Configuration(
  firmware_version=SIMULATED_FIRMWARE["head384"],
  firmware_date=parse_firmware_version_date(SIMULATED_FIRMWARE["head384"]),
  head_type="High volume head",
  x_offset=260.0,
  supports_clot_monitoring_clld=False,
  supports_lld_absolute_threshold_check=False,
)

# Where its Z drive rests after a retract. No unit has been probed, so this is the ceiling the drive
# documents rather than a measurement, as the 96-head's is.
SIMULATED_HEAD384_Z_SAFETY = 336.0

# The autoload this machine has. Its device facts are the defaults; what it answers about itself is
# here, and discovery reads it as it would off a real one.
SIMULATED_AUTOLOAD = AutoloadConfiguration(
  firmware_version="3.4S f 2017-01-09",
  autoload_type="1D barcode scanner",
)

# Where its two undriven drives report themselves, in mm. Where they actually are is not modelled:
# each answers from its zero. X is not among them - it answers from the deck.
SIMULATED_AUTOLOAD_Y_POSITION = 0.0
SIMULATED_AUTOLOAD_Z_POSITION = 0.0

# What it says about its own adjustment, and the track its X drive homes against.
SIMULATED_AUTOLOAD_ADJUSTMENT_DATE = datetime.date(2017, 1, 9)
SIMULATED_AUTOLOAD_INIT_TRACK = 1

# The two diagnostic reads that exist to show what a real unit holds. A simulated one holds nothing,
# and says so rather than inventing a block for a caller to read meaning into.
SIMULATED_AUTOLOAD_ADJUSTMENT_VALUES = "[simulation] no adjustment values"
SIMULATED_AUTOLOAD_PARAMETER_VALUE = "[simulation]"

# Whether the front cover is shut. A simulated machine is not being reached into.
SIMULATED_COVER_POSITION: CoverPosition = "closed"

# The three inputs on the cover connector: the cover input, and two whose meaning is not known.
SIMULATED_COVER_INPUTS = (True, False, False)

# What its scanner reads. A simulated deck holds no carriers, so nothing.
SIMULATED_BARCODE: Optional[str] = None

# The iSWAP's stored position tables, and where its rotation drive sits relative to the carriage.
SIMULATED_ISWAP_TABLES = {
  "pw": [13000, -29007, 156, 29068, 29500, 29068, 29068, 29068, 29068, 1378],
  "pt": [-26577, -26577, -8860, 9044, 26858, -26577, -26577, -26577, -26577, 1377],
  "py": [9855, 7000, 9000, 13550, 12600, 9855, 9855, 9855, 9855, 9855],
}
SIMULATED_ISWAP_X_OFFSET = 32.8

# An arm that carries nothing: the geometry of a STAR arm with none of its capability bits set.
# The firmware requires the two drives' bits to be disjoint, so a machine with two arms has its
# capabilities on one of them and an arm like this as the other.
BARE_X_ARM = XArmConfiguration(
  width=354.0,
  x_range=(95.0, 1340.2),
  workspace_range=(-323.2, 1517.2),
  wrap_size=595.2,
)

# What a bare STARSimulationDriver() pretends to be, copied field for field off a real instrument:
# a full-size STAR, 54 slots wide, with eight 1000uL channels, a wide-gripper iSWAP and a 96-head
# on a dual-rail left arm, autoload fitted, no right arm. A STARlet would be the same at 30 slots.
DEFAULT_STAR_CONFIGURATION = DeviceConfiguration(
  pip_type_1000ul=True,
  kb_iswap_installed=True,
  autoload_installed=True,
  num_pip_channels=8,
  left_x_drive_large=True,
  ka_head96_installed=True,
  iswap_gripper_wide=True,
  instrument_size_slots=54,
  autoload_size_slots=54,
  tip_waste_x_position=1340.0,
  left_arm=XArmConfiguration(
    pip_installed=True,
    iswap_installed=True,
    head96_installed=True,
    width=354.0,
    x_range=(95.0, 1340.2),
    workspace_range=(-323.2, 1517.2),
    wrap_size=595.2,
  ),
  right_arm=None,
  min_iswap_collision_free_position=350.0,
  max_iswap_collision_free_position=1140.0,
  left_x_arm_width=354.0,
  right_x_arm_width=370.0,
)

# How much shorter a STARlet is than the STAR above: 24 rails at the 22.5 mm track pitch, which is
# 540.0 mm. The two decks state the same fact twice, as the file that defines them says.
_STARLET_SHORTER_BY = (STAR_NUM_RAILS - STARLET_NUM_RAILS) * _RAILS_WIDTH

# A STARlet, derived from the STAR rather than captured: there is no STARlet here to read a QM, RU
# and UA reply from, and the STAR above is a recording of the machine we do have. Everything the
# right-hand end of the deck sets moves left by `_STARLET_SHORTER_BY`; everything belonging to the
# arm itself - its width, its wrap, and how far left it reaches - is the same part and does not
# move. The slot count follows the same reading: the STAR reports two fewer slots than its deck has
# rails, 54 against 56, which puts a 32-rail STARlet at 30.
#
# Replace this wholesale with a recording when there is a STARlet to take one from. It is a
# derivation, and only the slot count is independently known to be right.
_STAR_LEFT_ARM = cast(XArmConfiguration, DEFAULT_STAR_CONFIGURATION.left_arm)
_STAR_X_RANGE = cast(Tuple[float, float], _STAR_LEFT_ARM.x_range)
_STAR_WORKSPACE = cast(Tuple[float, float], _STAR_LEFT_ARM.workspace_range)
DEFAULT_STARLET_CONFIGURATION = dataclasses.replace(
  DEFAULT_STAR_CONFIGURATION,
  instrument_size_slots=STARLET_NUM_RAILS - 2,
  autoload_size_slots=STARLET_NUM_RAILS - 2,
  tip_waste_x_position=DEFAULT_STAR_CONFIGURATION.tip_waste_x_position - _STARLET_SHORTER_BY,
  max_iswap_collision_free_position=(
    DEFAULT_STAR_CONFIGURATION.max_iswap_collision_free_position - _STARLET_SHORTER_BY
  ),
  left_arm=dataclasses.replace(
    _STAR_LEFT_ARM,
    x_range=(_STAR_X_RANGE[0], _STAR_X_RANGE[1] - _STARLET_SHORTER_BY),
    workspace_range=(_STAR_WORKSPACE[0], _STAR_WORKSPACE[1] - _STARLET_SHORTER_BY),
  ),
)


class _UnusedTransport(IOBase):
  """Stands where the transport would be. Nothing should reach it."""

  async def setup(self, *args, **kwargs):
    pass

  async def stop(self):
    pass

  async def write(self, data: bytes, *args, **kwargs):
    raise RuntimeError(f"the simulator tried to write to a transport: {data!r}")

  async def read(self, *args, **kwargs) -> bytes:
    raise RuntimeError("the simulator tried to read from a transport")


class _Simulated:
  """Reaches the machine behind a capability, which for a simulated one is the simulator."""

  _driver: STARDriver

  @property
  def machine(self) -> "STARSimulationDriver":
    return cast("STARSimulationDriver", self._driver)


class SimulatedPipettes(_Simulated, Pipettes):
  """The pipetting channels, answering for themselves."""

  async def request_firmware_version(self, channel: int) -> Tuple[str, datetime.date]:
    return self.machine.reported("pipettes")

  async def request_min_pipette_width(self, channel: int) -> float:
    return PIPETTE_WIDTH

  async def request_pipette_configuration(self, channel: int) -> PipetteConfiguration:
    return PipetteConfiguration(
      channel_type=SIMULATED_PIPETTE.channel_type,
      head_type=SIMULATED_PIPETTE.head_type,
      stop_disc_type=SIMULATED_PIPETTE.stop_disc_type,
      pressure_adc=SIMULATED_PIPETTE.pressure_adc,
    )

  async def initialize(self, *args, **kwargs):
    """Whatever was mounted on the channels comes off."""
    self.machine.tips_mounted = [False] * len(self.machine.tips_mounted)

  async def request_y_positions(self) -> List[float]:
    # Where initialization spread them, which is where a machine that has been set up leaves
    # them and nothing here has since moved them. Answered from the procedure rather than from the
    # resources, so the model can be checked against this rather than derived from it.
    positions = self.default_initialize_y_positions()
    for channel, y in enumerate(positions):
      self.update_location_by_reference_point(channel, y=y)
    return positions

  async def request_stop_disk_z(self, channel: int) -> float:
    # Recorded as the real read records it, so a simulated channel is modelled at the height it
    # reports rather than at whatever the arm's own is.
    self.update_location_by_reference_point(channel, z=SIMULATED_CHANNEL_Z_SAFETY)
    return SIMULATED_CHANNEL_Z_SAFETY


# Where the left arm has come to rest when a simulated machine is switched on, in mm: far enough
# along the rail to sit within reach of any STAR deck. The right arm rests at the far end of its
# own travel instead, so the two do not overlap on a machine that has both. Setup reads this once
# to seat each arm on the deck; every read after that answers from the deck.
SIMULATED_LEFT_X_ARM_POSITION = 362.9


class SimulatedXArm(_Simulated, XArm):
  """An X-arm, answering for itself."""

  async def request_firmware_version(self) -> Tuple[str, datetime.date]:
    return self.machine.reported("x_arm")

  async def request_position(self) -> float:
    # Where the arm is is what the model says: a simulated machine has no drive to ask. Until setup
    # has put it on the deck there is nothing to read, and it answers where it powered up.
    if self.resource is not None and self.resource.location is not None:
      anchor = self.resource.get_anchor(x=self.reference_anchor)
      return self.resource.location.x + anchor.x
    if self.side == "left" or self.configuration.x_range is None:
      return SIMULATED_LEFT_X_ARM_POSITION
    return self.configuration.x_range[1]


class _SimulatedHead(_Simulated, Head):
  """What a head answers when there is no head: the same for either of them.

  Each head says which simulated configuration it answers from and where a retract leaves it; what
  its configuration bytes mean is its own, as on a machine.
  """

  _z_safety: float
  _firmware_key: str
  _label: str

  @property
  def _declared(self) -> HeadConfiguration:
    """The head this machine was told it has.

    Distinct from `configuration`, which discovery fills from these answers exactly as it would
    off an instrument.
    """
    raise NotImplementedError("a simulated head says which configuration it answers from")

  async def request_firmware_version(self) -> Tuple[str, datetime.date]:
    return self.machine.reported(self._firmware_key)

  async def request_head_type(self) -> str:
    head_type = self._declared.head_type
    if head_type is None:
      raise RuntimeError(f"the simulated {self._label} has no type; set it on its configuration")
    return head_type

  async def request_x_offset(self) -> float:
    x_offset = self._declared.x_offset
    if x_offset is None:
      raise RuntimeError(
        f"the simulated {self._label} has no X offset; set it on its configuration"
      )
    return x_offset

  async def request_z_position(self) -> float:
    # From the model where there is one, as the real read reports the drive, and in the deck's
    # frame as it answers in. Before setup has put the head on the arm, it rests where a retract
    # would leave it.
    deck = self.machine.deck
    if self.resource is not None and self.resource.location is not None and deck is not None:
      shaft = self.resource.get_item(HEAD_REFERENCE_SHAFT)
      return round(shaft.get_location_wrt(deck).z, 2)
    return self._z_safety

  async def probe_z_max(self, *args: Any, **kwargs: Any) -> float:
    # The firmware retract inside the probe is what puts the head at its safety height. Its own
    # `move_to_safe_z` needs no such override: it is an ordinary move, which this already records.
    self.update_location_by_reference_point(z=self._z_safety)
    return await super().probe_z_max(*args, **kwargs)

  async def move_y(self, y: float, *args: Any, **kwargs: Any):
    # A move is what puts the head somewhere. On the machine the drive holds that and the read
    # reports it; here the model holds it, so the move writes it and the read finds it there.
    # Written after the move, not before: one the real method refuses never happened, and a model
    # updated first would put the head where it was told to go rather than where it is.
    resp = await super().move_y(y, *args, **kwargs)
    self.update_location_by_reference_point(y=y)
    return resp

  async def move_z(self, z: float, *args: Any, **kwargs: Any):
    resp = await super().move_z(z, *args, **kwargs)
    self.update_location_by_reference_point(z=z)
    return resp

  async def request_drive_parameter(self, parameter: str) -> float:
    # Guarded as the real read guards it: a name the head does not store is a caller's mistake, and
    # should say so here as it would there rather than raising a lookup error.
    self.require_drive_parameter(parameter)
    head = self._declared
    if parameter == "yv":
      default = head.y_drive_speed_default
    elif parameter == "yr":
      default = head.y_drive_acceleration_default
    elif parameter == "zv":
      default = head.z_drive_speed_default
    else:
      default = head.z_drive_acceleration_default
    if default is None:
      raise RuntimeError(
        f"the simulated {self._label} has no {parameter} default; set it on its config"
      )
    return default

  async def initialize(self, *args, **kwargs):
    """Whatever was mounted on the head comes off, and it reports itself up."""
    self.machine.initialized[self.configuration.module] = True

  async def request_predefined_y_positions(self) -> List[float]:
    # As a head holds them: the park position first, then nine slots nothing here commands against.
    return [SIMULATED_HEAD_Y_PARK] + [SIMULATED_HEAD_Y_PREDEFINED] * 9

  async def request_y_position(self) -> float:
    # From the model where there is one, as the real read reports the drive. The drive answers in
    # the deck's frame, so the model is read in the deck's too - the resource hangs off the arm,
    # whose own position would otherwise come through. Before setup has put the head on the arm
    # there is nothing to read, and it answers from the middle of its travel.
    deck = self.machine.deck
    if self.resource is not None and self.resource.location is not None and deck is not None:
      shaft = self.resource.get_item(HEAD_REFERENCE_SHAFT)
      return round(shaft.get_location_wrt(deck).y, 2)
    return SIMULATED_HEAD_Y_PARK


class SimulatedHead96(_SimulatedHead, Head96):
  """The 96-head, answering for itself."""

  _firmware_key = "head96"
  _label = "96-head"
  _z_safety = SIMULATED_HEAD96_Z_SAFETY

  @property
  def _declared(self) -> Head96Configuration:
    return self.machine.simulated_head96

  async def request_hardware(self) -> List[str]:
    # Rendered from what this head is, rather than written out separately: discovery parses these
    # three back out of the reply, so a head configured differently answers differently.
    head = self._declared
    return [
      "1" if head.supports_clot_monitoring_clld else "0",
      "0" if head.stop_disc_type == "core_i" else "1",
      "0" if head.instrument_type == "legacy" else "1",
    ] + ["0"] * 7

  async def request_drive_parameter(self, parameter: str) -> float:
    # No register to read, so it answers with what this head's firmware documents.
    head = self._declared
    if parameter == "dv":
      return head.dispensing_drive_speed_default
    if parameter == "dr":
      return head.dispensing_drive_acceleration_default
    if parameter == "sv":
      return head.squeezer_drive_speed_default
    if parameter == "sr":
      return head.squeezer_drive_acceleration_default
    return await super().request_drive_parameter(parameter)


class SimulatedHead384(_SimulatedHead, Head384):
  """The 384-head, answering for itself."""

  _firmware_key = "head384"
  _label = "384-head"
  _z_safety = SIMULATED_HEAD384_Z_SAFETY

  @property
  def _declared(self) -> Head384Configuration:
    return self.machine.simulated_head384

  async def request_hardware(self) -> List[str]:
    # Rendered as the 96-head's is, from the two flags this head reports.
    head = self._declared
    return [
      "1" if head.supports_clot_monitoring_clld else "0",
      "1" if head.supports_lld_absolute_threshold_check else "0",
    ] + ["0"] * 8


class SimulatedISWAP(_Simulated, iSWAP):
  """The iSWAP, answering for itself."""

  async def request_firmware_version(self) -> str:
    return self.machine.simulated_firmware["iswap"]

  async def request_rotation_drive_x_offset(self) -> float:
    return SIMULATED_ISWAP_X_OFFSET

  async def _request_slots(self, table: str) -> List[int]:
    return list(SIMULATED_ISWAP_TABLES[table])

  async def initialize(self):
    self.machine.initialized["R0"] = True


class SimulatedFrontCover(_Simulated, FrontCover):
  """The front cover, answering for itself: it is shut."""

  async def request_position(self) -> CoverPosition:
    return SIMULATED_COVER_POSITION


class SimulatedAutoload(_Simulated, Autoload):
  """The autoload, answering for itself. Its deck and its loading tray are empty."""

  track = 1
  """Where it last moved to."""

  async def request_firmware_version(self) -> Tuple[str, datetime.date]:
    version = self.machine.simulated_autoload.firmware_version
    if version is None:
      raise RuntimeError(
        "the simulated autoload has no firmware version; set it on its configuration"
      )
    return version, parse_firmware_version_date(version)

  async def request_module_configuration(self) -> Tuple[float, bool]:
    # What this machine's own autoload answered: the 0.1 mm scanner, indicators fitted.
    return SIMULATED_AUTOLOAD.x_drive_mm_per_increment, True

  async def request_autoload_type(self) -> str:
    autoload_type = self.machine.simulated_autoload.autoload_type
    if autoload_type is None:
      raise RuntimeError("the simulated autoload has no type; set it on its configuration")
    return autoload_type

  async def request_initialization_status(self) -> bool:
    return self.machine.initialized["I0"]

  async def request_latest_barcode_read(self) -> Optional[str]:
    return SIMULATED_BARCODE

  async def request_adjustment_status(self) -> Tuple[datetime.date, bool]:
    """Answer that this autoload is adjusted, so its stored values are its own."""
    return SIMULATED_AUTOLOAD_ADJUSTMENT_DATE, True

  async def request_init_slot(self) -> int:
    """Answer the track the X drive homes against."""
    return SIMULATED_AUTOLOAD_INIT_TRACK

  async def request_adjustment_values(self) -> str:
    """Answer the adjustment block, which a simulated unit does not hold."""
    return SIMULATED_AUTOLOAD_ADJUSTMENT_VALUES

  async def request_parameter(self, parameter: str) -> str:
    """Answer a stored parameter by name.

    Args:
      parameter: the name to read.

    Returns:
      The reply, as the module would write it.
    """
    return f"I0RAid0000{parameter}{SIMULATED_AUTOLOAD_PARAMETER_VALUE}"

  async def request_track(self) -> int:
    return self.track

  async def move_x(
    self,
    x: float,
    speed: Optional[float] = None,
    acceleration_ramp: Optional[int] = None,
    current_limit: Optional[int] = None,
  ) -> Any:
    # A simulated drive goes exactly where it is told. The real one is read back afterwards, which
    # is what `Autoload` relies on, so the position has to be true here before that read happens or
    # the read returns the position the sled started at and it never moves.
    resp = await super().move_x(
      x, speed=speed, acceleration_ramp=acceleration_ramp, current_limit=current_limit
    )
    self.update_location_by_reference_point(x)
    return resp

  async def request_x_position(self) -> float:
    # Where the sled is is what the model says: a simulated machine has no drive to ask. The model
    # is placed around the carrier-handling wheel, so the wheel stands that far right of its left
    # edge. Before setup has put the sled on the deck there is no model to read, and the track it
    # is on is what it has instead - which is how a park during initialization survives long enough
    # to reach the resource that gets created after it.
    if self.resource is not None and self.resource.location is not None:
      return self.resource.location.x + self.configuration.reference_point_from_sled_left_edge
    return cast(HamiltonDeck, self.machine.deck).rails_to_location(self.track).x

  async def wheel_request_y_position(self) -> float:
    return SIMULATED_AUTOLOAD_Y_POSITION

  async def wheel_request_z_position(self) -> float:
    return SIMULATED_AUTOLOAD_Z_POSITION

  async def sense_carrier_presence_on_deck(self) -> List[int]:
    return []

  async def sense_carrier_presence_on_loading_tray(self) -> List[int]:
    return []

  async def sense_carrier_presence_on_single_loading_tray_track(
    self, track: int, park_after: bool = True
  ) -> bool:
    return False

  async def load_carrier_from_tray_and_scan_carrier_barcode(
    self, track: int, *args, **kwargs
  ) -> Optional[str]:
    return SIMULATED_BARCODE

  async def load_carrier_from_autoload_belt(
    self, barcode_reading: bool = False, *args, **kwargs
  ) -> Dict[int, Optional[str]]:
    """The containers read nothing, and there are as many as were asked for."""
    if not barcode_reading:
      return {}
    containers = kwargs.get("containers_per_carrier", 5)
    return {position: SIMULATED_BARCODE for position in range(containers)}

  async def initialize(self, park_after: bool = True):
    await super().initialize(park_after=park_after)
    self.machine.initialized["I0"] = True

  async def move_to_track(self, track: int, *args, **kwargs):
    # As `move_x` records where a position move put the sled, so this records where a track move
    # did. The deck is what knows where a track is.
    await super().move_to_track(track, *args, **kwargs)
    # A simulated machine is built with a deck or refuses to be built at all, so there is one.
    deck = cast(HamiltonDeck, self.machine.deck)
    self.update_location_by_reference_point(deck.rails_to_location(track).x)
    self.track = track

  async def park(self):
    await super().park()
    self.track = self.track_range[-1]


class STARSimulationDriver(STARDriver):
  """A simulated STAR, driven exactly like the real one."""

  def __init__(
    self,
    configuration: Optional[DeviceConfiguration] = None,
    tips_mounted: Optional[List[bool]] = None,
    firmware: Optional[Dict[str, str]] = None,
    autoload: Optional[AutoloadConfiguration] = None,
    head96: Optional[Head96Configuration] = None,
    head384: Optional[Head384Configuration] = None,
    deck: Optional[HamiltonDeck] = None,
    serial_number: str = SIMULATED_SERIAL_NUMBER,
    initialized: bool = False,
    left_side_panel_installed: bool = False,
  ):
    """
    Args:
      configuration: the instrument to pretend to be. Defaults to `DEFAULT_STAR_CONFIGURATION`.
      tips_mounted: one entry per channel, `True` where a tip sits on the channel. Defaults to no
        tips on any of them.
      firmware: what each capability reports, keyed as `confirmed_firmware_versions` keys it.
        Defaults to `SIMULATED_FIRMWARE`.
      autoload: the autoload this machine has, which it answers about itself. Defaults to
        `SIMULATED_AUTOLOAD`. The capability's own configuration is filled by discovery, as on a
        real machine, so this is what it reads rather than what it becomes.
      head96: the 96-head this machine has, which it answers about itself. Defaults to
        `SIMULATED_HEAD96`. As with `autoload`, this is what discovery reads rather than what the
        capability's own configuration becomes.
      head384: the 384-head this machine has, read as `head96` is. Defaults to
        `SIMULATED_HEAD384`.
      deck: the deck to reflect this machine into. Required: a simulated machine has no firmware
        to ask, so the resource model is the only thing it can answer from.
      serial_number: what this machine calls itself.
      initialized: whether the machine and its modules report themselves already initialized. One
        that has just been switched on does not.
      left_side_panel_installed: whether this machine has its left side panel on. Declared rather
        than discovered, as on a real one: the panel comes off in seconds.

    Raises:
      ValueError: If no deck is given, or `tips_mounted` does not have one entry per channel.
    """
    if deck is None:
      raise ValueError("a simulated STAR answers from its resource model, so it needs a deck")
    super().__init__(
      io=_UnusedTransport(), deck=deck, left_side_panel_installed=left_side_panel_installed
    )

    self.simulated_configuration = configuration or DEFAULT_STAR_CONFIGURATION
    self.simulated_firmware = firmware or dict(SIMULATED_FIRMWARE)
    self.simulated_autoload = autoload or SIMULATED_AUTOLOAD
    self.simulated_head96 = head96 or SIMULATED_HEAD96
    self.simulated_head384 = head384 or SIMULATED_HEAD384
    self.serial_number = serial_number

    channels = self.simulated_configuration.num_pip_channels
    if tips_mounted is None:
      tips_mounted = [False] * channels
    if len(tips_mounted) != channels:
      raise ValueError(f"tips_mounted has {len(tips_mounted)} entries, expected {channels}")
    self.tips_mounted = list(tips_mounted)

    # What each module says when asked whether it is initialized, and where things are.
    self.initialized = {module: initialized for module in ("C0", "I0", "R0", "H0")}

    # The capabilities this machine has, each answering for itself. Discovery builds only the ones
    # that are not already there, so these stand in for the real ones throughout.
    c = self.simulated_configuration
    if c.main_front_cover_monitoring_installed:
      self.front_cover = SimulatedFrontCover(self)
    if c.left_arm is not None:
      self.left_x_arm = SimulatedXArm(self, side="left")
    if c.right_arm is not None:
      self.right_x_arm = SimulatedXArm(self, side="right")
    if c.autoload_installed:
      self.autoload = SimulatedAutoload(self)

    # On the arm whose bits claim them, as discovery would put them. Read off the simulated
    # configuration rather than the arm's own: nothing has been discovered yet at this point.
    for arm, a in ((self.left_x_arm, c.left_arm), (self.right_x_arm, c.right_arm)):
      if arm is None or a is None:
        continue
      if a.pip_installed and c.num_pip_channels > 0:
        arm.pipettes = SimulatedPipettes(self)
      if a.head96_installed:
        arm.head96 = SimulatedHead96(self)
      if a.head384_installed:
        arm.head384 = SimulatedHead384(self)
      if a.iswap_installed:
        arm.iswap = SimulatedISWAP(self)

  def reported(self, capability: str) -> Tuple[str, datetime.date]:
    """What a capability reports for its firmware, and the date in it."""
    version = self.simulated_firmware[capability]
    return version, parse_firmware_version_date(version)

  # -- the machine itself ----------------------------------------------------

  async def _open(self):
    """There is no link to open, and no replies to read."""

  async def _close(self):
    pass

  async def request_device_configuration(self) -> DeviceConfiguration:
    return self.simulated_configuration

  async def request_cover_input_status(self) -> Tuple[bool, bool, bool]:
    return SIMULATED_COVER_INPUTS

  async def request_tip_presence(self) -> List[bool]:
    return list(self.tips_mounted)

  async def request_firmware_version(self) -> Tuple[str, datetime.date]:
    return self.reported("master")

  async def request_initialization_status(self, module: str = "C0") -> bool:
    return self.initialized.get(module, False)

  async def pre_initialize(self):
    """Home every drive. The modules it de-initializes then need their own."""
    self.initialized["C0"] = True

  def _describe_link(self) -> str:
    return "simulation (no link)"

  async def send_command(
    self,
    module: str,
    command: str,
    auto_id=True,
    tip_pattern: Optional[List[bool]] = None,
    write_timeout: Optional[int] = None,
    read_timeout: Optional[int] = None,
    wait=True,
    fmt: Optional[Any] = None,
    **kwargs: Any,
  ) -> None:
    """Say what would have been sent, and answer nothing.

    A command that only moves needs no more than this. One whose answer is read is overridden on
    the capability that reads it, so it never gets here.

    What it logs is what a real link logs: the assembled command as a write, and the answer as a
    read, so a simulated run reads like a recorded one.
    """
    self._log_exchange(
      assemble_channel_command(
        module=module,
        command=command,
        id_=None,
        tip_pattern=tip_pattern,
        num_channels=self.num_channels,
        **kwargs,
      ),
      None,
    )
    return None

  async def send_raw_command(self, command: str, *args: Any, **kwargs: Any) -> None:
    self._log_exchange(command, None)
    return None

  def _log_exchange(self, written: str, read: Optional[str]) -> None:
    """Log a command, and its answer where there is one, as the transport logs a real exchange.

    Nothing answers in simulation unless a capability says so, so most commands log a write alone.
    """
    logger.log(LOG_LEVEL_IO, "%s write: %s", SIMULATED_LINK, written)
    if read is not None:
      logger.log(LOG_LEVEL_IO, "%s read: %s", SIMULATED_LINK, read)
