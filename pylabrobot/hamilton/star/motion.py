"""What a STAR's firmware commands ask its drives to do, in the resource tree's frames.

PyLabRobot records where the arm and the channels are once a command has run. How they got there is
the device's own business: its motion controller works it out from the command. The page acts that
out, and needs the command's targets to do it.

`star_motion` reads one command and returns those targets as local positions of the resources that
model the drives, so the page can move them without knowing anything about firmware. A command that
moves nothing, or one this does not read, returns None.

Everything here comes from the driver or from the command itself, except what the driver does not
state and a STAR's own command timings do (Venus HxUsbComm traces; MOTION_PROFILES.md section 8):
the X-arm's speed, acceleration and jerk (it moves on an S-curve); the channels' Y speed and
acceleration, and the ripple in which they start their Y moves one after another; the channels' Z
speed; the slow press of a tip pick-up; and each command's fixed time.

- Channel Z acceleration is the channels' default (`Pipettes.default_z_acceleration`), which the
  traces agree with.
- The stroke of a tip command is the one the simulator records (`_record_tip_command`): across, with
  the arm and the channels moving at once, down onto the spots, and back up. A pick-up presses the
  last stretch, from `tp` to `tz`, slowly.
- An aspiration dwells for as long as its own volume and flow rate say, plus its settling time and
  its mixing, and leaves the liquid at its own swap speed.
- Every command also takes a fixed time, beyond the motion: what the traces measure less what the
  page draws, as a function of the command's own parameters where they matter.
- The 96-head's Y and Z move at the head's own drive defaults (`HeadConfiguration`), or at what a
  move of its own carries; its tip commands make the channels' stroke, channel A1 going to spot A1.
- An iSWAP command moves one drive or two, each at the speed and acceleration the command carries,
  converted by the arm's own configuration. The elbow's Y is stated only as a level, so it too moves
  at constant speed. A joint turns about the pivot the driver turns it about (`proximal_joint`).

Heights: the firmware positions the lowest point of what a channel carries, while a channel's
resource is placed by its stop disc, which sits higher by the length of a mounted tip. Every Z here
is converted to the stop disc, with the overhang the channel has when the move is made.
"""

from typing import Any, Dict, List, Optional, Sequence, cast

from pylabrobot.hamilton.star.driver.features.pipettes import (
  core_tool_face_distance,
  core_tool_grip_line_overhang,
)
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.hamilton.core_grippers import (
  HamiltonCoreGrippers,
  HamiltonCoreGripperTool,
)
from pylabrobot.resources.tip_rack import TipSpot, resting_location

# The driver states no X speed or acceleration: these are fitted to a STAR's own timings, from 2.56
# million command/reply pairs in Venus HxUsbComm traces (`tools/hxusbcomm_timing.py`). The X-arm is
# jerk-limited: an S-curve fits within-group R^2 0.976 against a trapezoid's 0.945 (delta AIC 72.7, 15 groups).
# Jerk is well determined; speed and acceleration less so (anything above ~450 mm/s and ~1100 mm/s^2
# fits nearly as well), since few recorded moves are long enough to cruise.
X_SPEED = 600.0  # mm/s
X_ACCELERATION = 1297.0  # mm/s^2
X_JERK = 3210.0  # mm/s^3
# Channel Y is stated only as an acceleration level, not a rate. Single-channel Y jogs in the same
# traces (`C0 KY`, 1/10/100 mm) fit a trapezoid at 300 mm/s and this acceleration within 5 ms; jerk
# adds nothing. In the full model of `C0 AS/DS` (`tools/hxusbcomm_channels.py`) the Y travel at these
# values enters with slope 1.01. PLR's `default_y_speed` is 250 mm/s.
CHANNEL_Y_SPEED = 300.0  # mm/s
CHANNEL_Y_ACCELERATION = 900.0  # mm/s^2
# The channels start their Y moves one after another (the ripple): the Y cost of a move grows by
# this much per channel moving after the first - 0.30 / 0.69 / 0.76 s for 4 / 7 / 8 channels in
# `C0 DS`, a line with slope 0.118 s and no fixed part (MOTION_PROFILES.md 8.4). The order they go
# in is not measured; the page takes them in channel order.
CHANNEL_Y_STAGGER = 0.118  # s
# Channel Z: dispense-then-aspirate pairs with no X or Y move fit a trapezoid at PLR's acceleration
# and this speed (R^2 within 0.986, delta AIC 186 over a straight line). The speed is loose, 150-200
# mm/s, as the recorded strokes form two clusters. PLR's `default_z_speed` is 125 mm/s.
CHANNEL_Z_SPEED = 150.0  # mm/s
# A tip pick-up goes down to `tp` at the Z drive's speed and presses on to `tz` at this one: the
# fixed time of `C0 TP` grows 0.0871 s per mm of `tp - tz` (R^2 0.83 with TIP_PICKUP_DOWN_FROM; only
# 8 and 10 mm recorded).
TIP_PRESS_SPEED = 11.5  # mm/s
# A tip pick-up lowers its channels before the crossing has finished: from this share of the
# crossing's time (`tools/hxusbcomm_fixed.py`, delta AIC 7,234 over the two in sequence). Moving 18
# rather than 9 mm adds 116 ms of X travel but 7 ms to the command, 27 mm adds 198 ms but 112 ms.
# Nearly every recorded pick-up moves 9 mm, so the share is loosely determined. Not measured for
# drops: hardly any recorded drop travels without a Y move.
TIP_PICKUP_DOWN_FROM = 0.82

# What a command takes beyond the motion the page draws: the measured duration less the drawn one,
# as a model of the command's own parameters (MOTION_PROFILES.md 8.6, `tools/hxusbcomm_fixed.py`). Played as a pause before
# the motion, since the traces time commands but not what the firmware does when.
# The channel count is left out: nearly every recorded command uses 7 or 8 channels, so its
# coefficient only tracks protocols (it swings from +0.04 to -0.27 s with 0.1% of the data).
ASPIRATE_FIXED = 1.879  # s
ASPIRATE_FIXED_PER_TRANSPORT_AIR_TIME = 6.761  # s per s of transport air at the flow rate
# Mixing happens at the bottom, so it adds to the dwell: its volume time, and a turnaround per cycle.
MIX_VOLUME_TIME_FACTOR = 0.979
MIX_PER_CYCLE = 0.557  # s
TIP_PICKUP_FIXED = 4.421  # s: the tip clamped and checked, beyond the drawn motion
# Held at the bottom, the tip on the shaft, all but a simple command's handling (as a drop's, HEUR).
TIP_PICKUP_HOLD = TIP_PICKUP_FIXED - 0.065  # s
TIP_DROP_FIXED = (
  5.009  # s, the mean. The channel count adds 0.21 s each (R^2 0.15), but 7 against 8
)
# channels is also one protocol against the others, so it is left out.
# Where in a drop that time goes is not in the traces, which time whole commands. It is played at
# the bottom, as the hold while the tips are pushed off (HEUR, from watching the device), all but a
# simple command's handling before the motion.
TIP_EJECT_HOLD = TIP_DROP_FIXED - 0.065  # s
CHANNELS_UP_FIXED = 0.14  # s: `C0 ZA`, median over 1,783, the channels mostly already up
HEAD96_TIP_PICKUP_FIXED = 4.938  # s: `C0 EP`, median over 155, against PLR's head drive defaults
HEAD96_TIP_DROP_FIXED = 4.490  # s: `C0 ER`, median over 5,867
HEAD96_MOVE_FIXED = 0.11  # s: a head command that travels nothing (`H0 YP`, 10th percentile)
# The 96-head's aspirate and dispense beyond their drawn travel, pumping (volume / flow) and settling
# time, from Venus traces (`tools/hxusbcomm_head96_liquid.py`, MOTION_PROFILES.md 8.6):
# `C0 EA` median of 46 (IQR 5.2-6.9 s, 5.4-6.9 by protocol). `C0 ED` by its mode (`da`): jet (0,
# 1) 1.41 s, median of 12 mode-1 dispenses (1.40-1.44 s); surface and empty (2, 3, 4) 5.85 s, median of 8
# mode-2 dispenses at normal flow. Four traces: modes 0 and 4 are unrecorded, and a few mode-3 or
# 10 uL/s dispenses took 11-13 s the parameters here do not explain.
HEAD96_ASPIRATE_FIXED = 5.80  # s
HEAD96_DISPENSE_FIXED = {0: 1.41, 1: 1.41, 2: 5.85, 3: 5.85, 4: 5.85}  # s, by `da`
# Simple single-drive commands with no recorded counterpart (`C0 JY/JZ/FY`, `X0 XP`, iSWAP `R0`
# primitives): what a single-channel 1 mm jog takes beyond its travel (`C0 KY`, 0.132 s less 0.067 s).
SIMPLE_MOVE_FIXED = 0.065  # s
# CO-RE grip tools have no recorded counterpart either; their strokes are tip strokes (HEUR).
CORE_TOOL_PICKUP_FIXED = TIP_PICKUP_FIXED
CORE_TOOL_RETURN_FIXED = TIP_DROP_FIXED
# Nor do their plate grip and release (`C0 ZP` / `ZR`): taken as the tool strokes' (HEUR, and likely
# long - a grip closes two channels on a plate, with no tip to clamp or check). Named apart so that a
# trace with them in it replaces these alone.
CORE_PLATE_GRIP_FIXED = CORE_TOOL_PICKUP_FIXED
CORE_PLATE_RELEASE_FIXED = CORE_TOOL_RETURN_FIXED

# How close a command's position has to be to a tip spot's centre to be taken as that spot, in mm.
SPOT_TOLERANCE = 1.0

# `TipDropMethod.DROP`, as `C0 TR` sends it in `ti`.
TIP_DROP = 1


def _pipetting_arm(driver: Any) -> Optional[Any]:
  """The arm carrying the channels, or None when there is none or nothing models it yet."""
  arm = next((a for a in getattr(driver, "arms", []) if a.pipettes is not None), None)
  if arm is None or arm.resource is None or not arm.pipettes.resources:
    return None
  return arm


def _xyz(coordinate: Any) -> Dict[str, float]:
  return {"x": float(coordinate.x), "y": float(coordinate.y), "z": float(coordinate.z)}


def _tenths(value: Any) -> float:
  return int(value) / 10


def _as_list(value: Any) -> List[Any]:
  """A per-channel parameter as a list; `JY` sends its values as one space-separated string."""
  if isinstance(value, str):
    return value.split()
  return list(value)


def _involved(pattern: Sequence[Any]) -> List[int]:
  return [i for i, used in enumerate(pattern) if used in (True, 1, "1")]


class _Frames:
  """Converts the positions the drives report into where the resources modelling them sit."""

  def __init__(self, driver: Any, arm: Any):
    self.driver = driver
    self.arm = arm
    self.pipettes = arm.pipettes
    self.channels = arm.pipettes.resources
    self.deck = driver.deck
    self.on_arm = [c.parent.get_location_wrt(self.deck) for c in self.channels]
    self.anchors = [self.pipettes._reference_anchor(c) for c in self.channels]

  def overhang(self, channel: int) -> float:
    """How far what a channel carries hangs below its stop disc, in mm, read off the model.

    The firmware positions the lowest point of what the channel carries: a tip's bottom, a CO-RE
    grip tool's grip line. From the resource tree, so it holds for any driver, not only the
    simulator (which works it out the same way, `SimulatedPipettes._below_stop_disc`).
    """
    shaft = self.shaft(channel)
    if shaft is None:
      return 0.0
    bottom = shaft.tip_bottom()
    if bottom is None:
      return 0.0
    if isinstance(shaft.tip, HamiltonCoreGripperTool):
      return float(-bottom.z - shaft.tip.grip_line_height)
    return float(-bottom.z)

  def arm_x(self, x: float) -> float:
    return round(float(x - self.arm.configuration.reference_point_from_left), 2)

  def channel_y(self, channel: int, y: float) -> float:
    return round(float(y - self.on_arm[channel].y - self.anchors[channel].y), 2)

  def channel_z(self, channel: int, stop_disc_z: float) -> float:
    return round(float(stop_disc_z - self.on_arm[channel].z - self.anchors[channel].z), 2)

  def lowest_point_z(self, channel: int, z: float, overhang: Optional[float] = None) -> float:
    """A firmware height, the lowest point, as the channel's local Z."""
    return self.channel_z(channel, z + (self.overhang(channel) if overhang is None else overhang))

  def current_y(self, channel: int) -> float:
    """Where the channel's reference point is along Y, in mm on the deck."""
    location = self.channels[channel].location
    return float(location.y + self.on_arm[channel].y + self.anchors[channel].y)

  def planned_ys(self, targets: Dict[int, float]) -> Dict[int, float]:
    """Every channel's Y once the named ones are at their targets, on one rail.

    As `Pipettes._plan_y_positions(make_space=True)` plans it - the plan the simulator records a
    tip command with - from where the model has the channels, read as the device reports them, to a
    tenth. Channel 0 is at the back, so Y falls as the channel number rises. Behind the backmost
    named channel and in front of the frontmost, the others are pushed only as far as the spacing
    asks; between two named channels, each unnamed one stands at the spacing in front of the one
    behind it.
    """
    if not targets:
      return {}
    n = len(self.channels)

    def gap(i: int, j: int) -> float:
      return float(self.pipettes._min_spacing_between(i, j))

    positions = [round(self.current_y(c), 1) for c in range(n)]
    positions[-1] = max(positions[-1], self.driver.configuration.left_arm_min_y_position)
    for c in range(n - 2, -1, -1):
      if positions[c] - positions[c + 1] < gap(c, c + 1):
        positions[c] = positions[c + 1] + gap(c, c + 1)
    ys = dict(enumerate(positions))
    ys.update(targets)
    back, front = min(targets), max(targets)
    for c in range(back, 0, -1):
      if ys[c - 1] - ys[c] < gap(c - 1, c):
        ys[c - 1] = ys[c] + gap(c - 1, c)
    for c in range(back + 1, front):
      if c not in targets:
        ys[c] = ys[c - 1] - gap(c - 1, c)
    for c in range(front, n - 1):
      if ys[c] - ys[c + 1] < gap(c, c + 1):
        ys[c + 1] = ys[c] - gap(c, c + 1)
    return {c: round(y, 2) for c, y in ys.items()}

  def shaft(self, channel: int) -> Optional[Any]:
    return next(
      (
        child for child in self.channels[channel].children if child.category == "tip_mounting_shaft"
      ),
      None,
    )

  def spot_at(self, x: float, y: float) -> Optional[TipSpot]:
    """The tip spot centred at (x, y) on the deck, or None."""
    for resource in self.deck.get_all_children():
      if not isinstance(resource, TipSpot):
        continue
      centre = resource.get_location_wrt(self.deck, "c", "c", "b")
      if abs(centre.x - x) <= SPOT_TOLERANCE and abs(centre.y - y) <= SPOT_TOLERANCE:
        return resource
    return None

  def drives(self) -> Dict[str, Dict[str, Optional[float]]]:
    p = self.pipettes
    return {
      "x": {"speed": X_SPEED, "acceleration": X_ACCELERATION, "jerk": X_JERK},
      "y": {
        "speed": CHANNEL_Y_SPEED,
        "acceleration": CHANNEL_Y_ACCELERATION,
        "stagger": CHANNEL_Y_STAGGER,
      },
      "z": {"speed": CHANNEL_Z_SPEED, "acceleration": p.default_z_acceleration},
    }


def _request(frames: _Frames, kind: str, command: str) -> Dict[str, Any]:
  return {
    "kind": kind,
    "command": command,
    "arm": None,
    "channels": [],
    "traverse": [],
    "attach": [],
    "dwell": 0.0,
    "fixed": 0.0,
    "drives": frames.drives(),
    "moves": [],
    "turns": [],
    "jaws": None,
  }


def _channel(frames: _Frames, channel: int, **targets: Optional[float]) -> Dict[str, Any]:
  return {"name": frames.channels[channel].name, "channel": channel, **targets}


def _stroke(
  frames: _Frames,
  kind: str,
  command: str,
  params: Dict[str, Any],
  traverse: float,
  down: Dict[int, float],
  end: Dict[int, float],
  extra: Optional[Dict[int, Dict[str, float]]] = None,
) -> Dict[str, Any]:
  """A command that travels at a height, goes down onto its targets and comes back up.

  `traverse` is a firmware height; `down` and `end` are already local Z, keyed by channel. `extra`
  adds fields to a channel's entry.
  """
  pattern = _as_list(params["tm"])
  involved = _involved(pattern)
  xs, ys = _as_list(params["xp"]), _as_list(params["yp"])
  request = _request(frames, kind, command)
  if not involved:
    return request
  # The arm ends over the last column the command visited, as the simulator records it.
  request["arm"] = {"name": frames.arm.resource.name, "x": frames.arm_x(_tenths(xs[involved[-1]]))}
  planned = frames.planned_ys({c: _tenths(ys[c]) for c in involved})
  request["traverse"] = [
    _channel(frames, c, z=frames.lowest_point_z(c, traverse)) for c in range(len(frames.channels))
  ]
  request["channels"] = [
    _channel(
      frames,
      c,
      y=frames.channel_y(c, y),
      down=down.get(c),
      end=end.get(c),
      **(extra or {}).get(c, {}),
    )
    for c, y in planned.items()
  ]
  return request


def _mounted_location(shaft: Any, tip: Any) -> Dict[str, float]:
  """Where a tip sits on a shaft once picked up, as `TipMountingShaft` places it: its pick-up
  location `fitting_depth` up the shaft's axis."""
  grip = (tip.pick_up_location or tip.get_anchor("c", "c", "t")).rotated(tip.rotation)
  return _xyz(
    Coordinate(
      shaft.get_size_x() / 2 - grip.x,
      shaft.get_size_y() / 2 - grip.y,
      tip.fitting_depth - grip.z,
    )
  )


def _handover(tip: Any, parent: Any, location: Optional[Dict[str, float]]) -> Dict[str, Any]:
  """A tip changing hands at the bottom of a stroke, placed where the model will place it."""
  return {
    "name": tip.name,
    "parent": None if parent is None else parent.name,
    "location": location,
    "rotation": _xyz(tip.rotation),
  }


def _positions(params: Dict[str, Any], channel: int) -> Any:
  return _tenths(_as_list(params["xp"])[channel]), _tenths(_as_list(params["yp"])[channel])


def _tip_pickup(frames: _Frames, command: str, params: Dict[str, Any]) -> Dict[str, Any]:
  involved = _involved(_as_list(params["tm"]))
  # How far each tip will hang below its stop disc once on: read off the tip in the spot and where
  # the shaft will seat it, as `TipMountingShaft.tip_bottom` then reads it. The simulator's table of
  # defined tip lengths is only the fallback, for a spot the model has no tip in.
  defined = getattr(frames.driver, "defined_tip_lengths", {}).get(int(params["tt"]), 0.0)

  def carried(c: int) -> float:
    spot, shaft = frames.spot_at(*_positions(params, c)), frames.shaft(c)
    if spot is None or spot.tip is None or shaft is None:
      return defined
    return float(-_mounted_location(shaft, spot.tip)["z"])

  request = _stroke(
    frames,
    "tip_pickup",
    command,
    params,
    traverse=_tenths(params["th"]),
    # Down to where the tip begins, then pressed on to the end of the search, slowly.
    down={c: frames.lowest_point_z(c, _tenths(params["tp"])) for c in involved},
    # It comes away carrying the tip, which then hangs below the stop disc.
    end={c: frames.lowest_point_z(c, _tenths(params["th"]), carried(c)) for c in involved},
    extra={
      c: {"press": frames.lowest_point_z(c, _tenths(params["tz"])), "press_speed": TIP_PRESS_SPEED}
      for c in involved
    },
  )
  # The channels hold at the bottom once the tips are on: the pick-up's fixed time, bar the
  # command's handling before it moves.
  request["down_from"] = TIP_PICKUP_DOWN_FROM
  request["dwell"] = TIP_PICKUP_HOLD
  request["fixed"] = round(TIP_PICKUP_FIXED - TIP_PICKUP_HOLD, 3)
  # At the bottom of the stroke each channel takes the tip in the spot under it onto its shaft.
  for c in involved:
    spot, shaft = frames.spot_at(*_positions(params, c)), frames.shaft(c)
    if spot is not None and spot.tip is not None and shaft is not None:
      request["attach"].append(_handover(spot.tip, shaft, _mounted_location(shaft, spot.tip)))
  return request


def _tip_drop(frames: _Frames, command: str, params: Dict[str, Any]) -> Dict[str, Any]:
  involved = _involved(_as_list(params["tm"]))
  # With `DROP` the heights are the stop disc's, not the lowest point's (`_unchecked_fw_drop_tips`):
  # the tip still on it hangs below them.
  drop = int(params.get("ti", 0)) == TIP_DROP
  request = _stroke(
    frames,
    "tip_drop",
    command,
    params,
    traverse=_tenths(params["th"]),
    down={
      c: frames.lowest_point_z(c, _tenths(params["tz"]), 0.0 if drop else None) for c in involved
    },
    # It comes away empty.
    end={c: frames.lowest_point_z(c, _tenths(params["te"]), 0.0) for c in involved},
  )
  # At the bottom the channels hold while the tips are pushed off: the drop's fixed time, bar the
  # command's handling before it moves.
  request["dwell"] = TIP_EJECT_HOLD
  request["fixed"] = round(TIP_DROP_FIXED - TIP_EJECT_HOLD, 3)
  # At the bottom of the stroke each channel leaves its tip in the spot under it, or, over
  # somewhere that is not a spot - the waste - where it is.
  for c in involved:
    shaft = frames.shaft(c)
    if shaft is None or shaft.tip is None:
      continue
    spot = frames.spot_at(*_positions(params, c))
    if spot is not None and spot.tip is None:
      request["attach"].append(_handover(shaft.tip, spot, _xyz(resting_location(spot, shaft.tip))))
    else:
      request["attach"].append(_handover(shaft.tip, None, None))
  return request


def _as_tip_command(params: Dict[str, Any], back: int, channels: int) -> Dict[str, Any]:
  """A CO-RE tool command's two channels in a tip command's lists: one X, a Y each."""
  xs, ys, pattern = ["0"] * channels, ["0"] * channels, [False] * channels
  for channel, y in ((back, params["ya"]), (back + 1, params["yb"])):
    xs[channel], ys[channel], pattern[channel] = params["xs"], y, True
  return {**params, "xp": xs, "yp": ys, "tm": pattern}


def _below_grip_line(tool: Any) -> float:
  """How far a mounted CO-RE grip tool's grip line hangs below the stop disc, in mm: what the
  master reports a channel carrying one at."""
  pick_up = tool.pick_up_location or tool.get_anchor("c", "c", "t")
  return float(pick_up.z - tool.fitting_depth - tool.grip_line_height)


def _core_grippers(frames: "_Frames") -> Optional[Any]:
  """The driver's CO-RE grippers feature, or None when the driver has none."""
  return getattr(frames.pipettes._driver, "core_grippers", None)


def _core_channels(frames: "_Frames") -> List[int]:
  """The channels carrying CO-RE grip tools, back to front, from the feature that picked them up;
  from the model where the feature does not know."""
  grippers = _core_grippers(frames)
  if grippers is not None and grippers._back_channel is not None:
    return [grippers._back_channel, grippers._front_channel]
  return [
    c
    for c in range(len(frames.channels))
    if isinstance(frames.pipettes.get_mounted_tool(c), HamiltonCoreGripperTool)
  ]


def _core_holder(frames: "_Frames") -> Any:
  """The CO-RE gripper holder the deck carries."""
  deck = frames.pipettes._driver.deck
  for resource in deck.get_all_children():
    if isinstance(resource, HamiltonCoreGrippers):
      return resource
  raise TypeError("the deck carries no CO-RE gripper holder")


def _hold_at_bottom(request: Dict[str, Any], fixed: float) -> Dict[str, Any]:
  """Spend a command's fixed time at the bottom of its stroke - where tips are clamped or pushed
  off, a tool seated, a plate gripped or let go - rather than as a still pause before it moves,
  bar the command's handling (`SIMPLE_MOVE_FIXED`), as the channels' tip commands do. Where in a
  command the firmware spends it is not measured (HEUR); played before the move, the head and the
  grip tools stood for seconds before setting off."""
  request["dwell"] = round(fixed - SIMPLE_MOVE_FIXED, 3)
  request["fixed"] = SIMPLE_MOVE_FIXED
  return request


def _core_tool_pickup(frames: _Frames, command: str, params: Dict[str, Any]) -> Dict[str, Any]:
  """`C0 ZT`: down onto the parked tools, and up carrying them, as a tip pick-up."""
  back = int(params["pa"]) - 1
  holder = _core_holder(frames)
  tools = {back: holder.back_tool, back + 1: holder.front_tool}
  request = _stroke(
    frames,
    "core_tool_pickup",
    command,
    _as_tip_command(params, back, len(frames.channels)),
    traverse=_tenths(params["th"]),
    down={c: frames.lowest_point_z(c, _tenths(params["tz"]), 0.0) for c in tools},
    end={
      c: frames.lowest_point_z(c, _tenths(params["th"]), _below_grip_line(tool))
      for c, tool in tools.items()
    },
  )
  for c, tool in tools.items():
    shaft = frames.shaft(c)
    if shaft is not None:
      request["attach"].append(_handover(tool, shaft, _mounted_location(shaft, tool)))
  return _hold_at_bottom(request, CORE_TOOL_PICKUP_FIXED)


def _core_tool_return(frames: _Frames, command: str, params: Dict[str, Any]) -> Dict[str, Any]:
  """`C0 ZS`: down into the holder with the tools, leaving each where it was parked, and up."""
  channels = _core_channels(frames)
  if len(channels) != 2:
    raise ValueError("a tool return needs two channels carrying tools")
  request = _stroke(
    frames,
    "core_tool_return",
    command,
    _as_tip_command(params, channels[0], len(frames.channels)),
    traverse=_tenths(params["th"]),
    down={c: frames.lowest_point_z(c, _tenths(params["tz"])) for c in channels},
    end={c: frames.lowest_point_z(c, _tenths(params["te"]), 0.0) for c in channels},
  )
  for c in channels:
    shaft = frames.shaft(c)
    if shaft is None or shaft.tip is None:
      continue
    grippers = _core_grippers(frames)
    parked = (
      {
        tool.name: (holder, location, tool.rotation)
        for tool, holder, location in grippers._parked_tools
      }
      if grippers is not None
      else {}
    )
    holder, location, rotation = parked[shaft.tip.name]
    handover = _handover(shaft.tip, holder, _xyz(location))
    handover["rotation"] = _xyz(rotation)
    request["attach"].append(handover)
  return _hold_at_bottom(request, CORE_TOOL_RETURN_FIXED)


def _core_plate(frames: _Frames, command: str, params: Dict[str, Any], kind: str) -> Dict[str, Any]:
  """`C0 ZP`, `ZM`, `ZR`: the two tool channels travel, go down to the grip line at the plate's
  centre, one on each side of it, and come up. At the bottom of a grip the plate passes to the
  front tool channel's shaft; at the bottom of a release, to what it is put down on - as
  `COREGripper` leaves it for the command (`Pipettes._core_handover`)."""
  channels = _core_channels(frames)
  if len(channels) != 2:
    raise ValueError("a CO-RE plate command needs two channels carrying tools")
  back, front = channels
  shaft = frames.shaft(front)
  tool = shaft.tip if shaft is not None else None
  if not isinstance(tool, HamiltonCoreGripperTool):
    raise ValueError("a CO-RE plate command needs its CO-RE grip tool on the front channel")
  grippers = _core_grippers(frames)
  handover = grippers._handover if grippers is not None else None
  held = handover[0] if handover else None
  width = held.get_absolute_size_y() if held is not None else _tenths(params.get("yo", 0)) - 3.0
  half = width / 2 + core_tool_face_distance(tool)
  centre_y, grip = _tenths(params["yj"]), _tenths(params["zj"])
  along = {**params, "ya": round((centre_y + half) * 10), "yb": round((centre_y - half) * 10)}
  end = _tenths(params["zj"] if kind == "core_move" else params.get("te", params["th"]))
  below = core_tool_grip_line_overhang(tool)
  request = _stroke(
    frames,
    kind,
    command,
    _as_tip_command(along, back, len(frames.channels)),
    traverse=_tenths(params["th"]),
    down={c: frames.lowest_point_z(c, grip, below) for c in channels},
    end={c: frames.lowest_point_z(c, end, below) for c in channels},
  )
  if handover and kind != "core_move":
    resource, parent, location = handover
    request["attach"].append(
      {
        "name": resource.name,
        "parent": None if parent is None else parent.name,
        "location": None if location is None else _xyz(location),
        "rotation": _xyz(resource.rotation),
      }
    )
  if kind == "core_move":
    return request
  return _hold_at_bottom(
    request, CORE_PLATE_GRIP_FIXED if kind == "core_grip" else CORE_PLATE_RELEASE_FIXED
  )


def _aspirate(frames: _Frames, command: str, params: Dict[str, Any]) -> Dict[str, Any]:
  involved = _involved(_as_list(params["tm"]))

  # Lists other than the positions run over the channels involved, in order.
  def per_channel(field: str) -> Dict[int, float]:
    values = _as_list(params[field])
    return {c: _tenths(values[i]) for i, c in enumerate(involved)}

  def raw(field: str, default: int = 0) -> Dict[int, int]:
    values = _as_list(params.get(field, [default] * len(involved)))
    return {c: int(values[i]) for i, c in enumerate(involved)}

  def optional(field: str) -> Dict[int, float]:
    return per_channel(field) if field in params else {c: 0.0 for c in involved}

  surface = per_channel("zl")
  floor = per_channel("zx")
  swap_speed = per_channel("de")
  # Into the liquid by the immersion depth, or above it when the direction says so (`it` 1); never
  # below the lowest the command allows.
  direction = raw("it")
  immersion = optional("ip")
  down = {
    c: max(surface[c] + (immersion[c] if direction[c] == 1 else -immersion[c]), floor[c])
    for c in involved
  }
  # While it draws, the tip follows the sinking surface down by `fp`, at a steady pace over the
  # time the volume takes. The narrower lower section `zu`/`zr` changes that pace on the device in
  # a way the driver does not document, so it is not drawn.
  following = optional("fp")
  volumes, speeds = per_channel("av"), per_channel("as_")
  draw_time = {c: volumes[c] / speeds[c] if speeds[c] else 0.0 for c in involved}
  followed = {c: max(down[c] - following[c], floor[c]) for c in involved}
  # Out of the liquid at the swap speed, then up by the pull-out distance before the transport air
  # is drawn, at the swap speed too: Venus aspirations otherwise identical take 5.35 s (n 4 against
  # 802) and 4.85 s (n 12 against 3,534) longer with a 10 mm pull-out at 2 mm/s - 10 mm at 2 mm/s.
  # From the surface (HEUR - the driver says only "rise before drawing transport air").
  pull_out = optional("po")

  def extras(c: int) -> Dict[str, float]:
    extra: Dict[str, float] = {}
    if followed[c] < down[c] - 0.05 and draw_time[c] > 0:
      extra["follow"] = frames.lowest_point_z(c, followed[c])
      extra["follow_speed"] = round((down[c] - followed[c]) / draw_time[c], 3)
    if swap_speed[c] > 0:
      extra["leave"] = frames.lowest_point_z(c, surface[c])
      extra["leave_speed"] = swap_speed[c]
      if pull_out[c] > 0:
        extra["pull_out"] = frames.lowest_point_z(c, surface[c] + pull_out[c])
    return extra

  request = _stroke(
    frames,
    "aspirate",
    command,
    params,
    traverse=_tenths(params["th"]),
    down={c: frames.lowest_point_z(c, down[c]) for c in involved},
    end={c: frames.lowest_point_z(c, _tenths(params["te"])) for c in involved},
    extra={c: extras(c) for c in involved},
  )
  # As long as the slowest channel takes to draw its volume, settle and mix.
  settle = per_channel("wt")
  cycles = {c: int(v) for c, v in zip(involved, _as_list(params.get("mc", [0] * len(involved))))}
  mix_volumes = per_channel("mv") if "mv" in params else {c: 0.0 for c in involved}
  mix_speeds = per_channel("ms") if "ms" in params else {c: 0.0 for c in involved}

  def mixing(c: int) -> float:
    if not cycles.get(c) or not mix_speeds[c]:
      return 0.0
    volume_time = 2 * cycles[c] * mix_volumes[c] / mix_speeds[c]
    return MIX_VOLUME_TIME_FACTOR * volume_time + MIX_PER_CYCLE * cycles[c]

  request["dwell"] = round(
    max((volumes[c] / speeds[c] if speeds[c] else 0.0) + settle[c] + mixing(c) for c in involved),
    2,
  )
  transport_air = per_channel("ta") if "ta" in params else {c: 0.0 for c in involved}
  request["fixed"] = round(
    ASPIRATE_FIXED
    + ASPIRATE_FIXED_PER_TRANSPORT_AIR_TIME
    * max(transport_air[c] / speeds[c] if speeds[c] else 0.0 for c in involved),
    3,
  )
  return request


def _move_y(frames: _Frames, command: str, params: Dict[str, Any]) -> Dict[str, Any]:
  request = _request(frames, "move_y", command)
  ys = _as_list(params["yp"])
  request["channels"] = [
    _channel(frames, c, y=frames.channel_y(c, _tenths(y)))
    for c, y in enumerate(ys[: len(frames.channels)])
  ]
  return request


def _free_y_range(frames: _Frames, command: str) -> Dict[str, Any]:
  """`C0 FY`: the master packs the channels as far forward as they fit, each against the ones in
  front of it - where the simulator puts them, and writes them before the command is sent."""
  request = _request(frames, "move_y", command)
  request["recorded_first"] = True
  floor = frames.driver.configuration.left_arm_min_y_position
  widths = [c.width for c in frames.pipettes.configuration.channels]
  request["channels"] = [
    _channel(frames, c, y=frames.channel_y(c, floor + sum(widths[c + 1 :])))
    for c in range(len(frames.channels))
  ]
  return request


def _channels_up(frames: _Frames, command: str) -> Dict[str, Any]:
  """`C0 ZA`: every channel up to the top of its Z travel, where `probe_z_max` leaves them - and
  where the simulator writes them before the command is sent."""
  request = _request(frames, "move_z", command)
  request["recorded_first"] = True
  ceiling = frames.pipettes.configuration.z_range[1]
  request["channels"] = [
    _channel(frames, c, end=frames.channel_z(c, ceiling)) for c in range(len(frames.channels))
  ]
  return request


def _move_z(frames: _Frames, command: str, params: Dict[str, Any]) -> Dict[str, Any]:
  request = _request(frames, "move_z", command)
  zs = _as_list(params["zp"])
  request["channels"] = [
    _channel(frames, c, end=frames.lowest_point_z(c, _tenths(z)))
    for c, z in enumerate(zs[: len(frames.channels)])
  ]
  return request


def _move_x(frames: _Frames, driver: Any, command: str, params: Dict[str, Any]) -> Optional[Dict]:
  side = "left" if command == "XP" else "right"
  arm = next((a for a in driver.arms if a.side == side), None)
  if arm is None or arm.resource is None:
    return None
  increments = params.get(f"{arm.parameter_prefix}a")
  if increments is None:
    return None
  x = arm.configuration.x_increments_to_mm(int(increments))
  request = _request(frames, "move_x", command)
  request["arm"] = {
    "name": arm.resource.name,
    "x": round(x - arm.configuration.reference_point_from_left, 2),
  }
  return request


# -- iSWAP ---------------------------------------------------------------------


def _iswap_of(driver: Any) -> Optional[Any]:
  """The iSWAP, or None when there is none or nothing models it yet."""
  for arm in getattr(driver, "arms", []):
    iswap = arm.iswap
    if iswap is not None and None not in (iswap.resource, iswap.link_1, iswap.gripper):
      return iswap
  return None


def _iswap_request(kind: str, command: str) -> Dict[str, Any]:
  # The iSWAP writes a move's target into the model as it sends it (`elbow_move_to_y_position`,
  # `rotate_to_angles`, `gripper_move_to_jaw_position`): what the model says of these parts before
  # the move is played is already its end.
  return {
    "kind": kind,
    "command": command,
    "recorded_first": True,
    "arm": None,
    "channels": [],
    "traverse": [],
    "attach": [],
    "dwell": 0.0,
    "drives": {},
    "moves": [],
    "turns": [],
    "jaws": None,
  }


def _elbow_move(driver: Any, iswap: Any, command: str, params: Dict[str, Any]) -> Dict[str, Any]:
  """`R0 YA` or `R0 ZA`: the head the arm hangs from, along Y or Z."""
  c = iswap.configuration
  head = iswap.resource
  on_arm, anchor = head.parent.get_location_wrt(driver.deck), head.reference_point
  request = _iswap_request("iswap_move", "R0" + command)
  if command == "YA":
    y = c.y_increments_to_mm(int(params["ya"]))
    request["moves"].append(
      {
        "name": head.name,
        "axis": 1,
        "to": round(float(y - on_arm.y - anchor.y), 2),
        "speed": c.y_increments_to_mm(int(params["yv"])),
        "acceleration": None,
      }
    )
  else:
    # The drive counts the finger plane; the head's bottom stands above it.
    z = c.z_increments_to_mm(int(params["za"])) + c.elbow_z_offset_above_finger
    request["moves"].append(
      {
        "name": head.name,
        "axis": 2,
        "to": round(float(z - on_arm.z - anchor.z), 2),
        "speed": c.z_increments_to_mm(int(params["zv"])),
        "acceleration": round(int(params["zr"]) * 1000 * c.z_mm_per_increment, 2),
      }
    )
  return request


def _joints(iswap: Any, command: str, params: Dict[str, Any]) -> Dict[str, Any]:
  """`R0 PA`: the elbow and the wrist together, each to its own angle at its own speed.

  A joint's angle is stated as its drive reports it; `base` is what the driver subtracts from it to
  get the resource's rotation (`elbow_drive_update_angle`, `wrist_drive_update_angle`).
  """
  c = iswap.configuration
  request = _iswap_request("iswap_turn", "R0" + command)
  link, gripper = iswap.link_1, iswap.gripper
  request["turns"].append(
    {
      "name": link.name,
      "drive": c.elbow_drive_increments_to_angle(int(params["wa"])),
      "base": 90.0,
      "pivot": _xyz(link.proximal_joint),
      "speed": c.elbow_increments_to_deg_per_sec(int(params["wv"])),
      "acceleration": c.elbow_increments_to_deg_per_sec2(int(params["wr"])),
    }
  )
  if c.wrist_drive_predefined_increments is not None:
    request["turns"].append(
      {
        "name": gripper.name,
        "drive": c.wrist_increments_to_deg(int(params["ta"])),
        "base": c.wrist_increments_to_deg(c.wrist_drive_predefined_increments.straight),
        "pivot": _xyz(gripper.proximal_joint),
        "speed": c.wrist_increments_to_deg_per_sec(int(params["tv"])),
        "acceleration": c.wrist_increments_to_deg_per_sec2(int(params["tr"])),
      }
    )
  return request


def _jaws(
  iswap: Any, command: str, width: float, speed: float, acceleration: float
) -> Optional[Dict[str, Any]]:
  """The fingers stood `width` apart, as `MechanicalGripper._place_the_fingers` stands them.

  Each finger travels half of what the width does, in the same time, so at half the drive's speed.
  The page decides from which way they move whether this closes on something or lets it go.
  """
  gripper = iswap.gripper
  low, high = gripper.jaw_range
  if not low <= width <= high:
    return None  # the model leaves the jaws where they are, and so does the page
  centre = gripper.proximal_joint.y + gripper.tool_center_point.y
  fingers = []
  for finger, side in zip(gripper.fingers, (1.0, -1.0)):
    facing = centre + side * width / 2.0
    fingers.append(
      {"name": finger.name, "y": round(facing if side > 0 else facing - finger.get_size_y(), 3)}
    )
  request = _iswap_request("iswap_jaws", command)
  request["jaws"] = {
    "gripper": gripper.name,
    "width": width,
    "fingers": fingers,
    "speed": speed / 2.0,
    "acceleration": acceleration / 2.0,
    # Where the fingers close, in the gripper's own frame: what they take hold of is there.
    "grip_point": _xyz(gripper.proximal_joint + gripper.tool_center_point),
  }
  return request


def _iswap_motion(
  driver: Any, iswap: Any, module: str, command: str, params: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
  c = iswap.configuration
  if module == "R0" and command in ("YA", "ZA"):
    return _elbow_move(driver, iswap, command, params)
  if module == "R0" and command == "PA":
    return _joints(iswap, command, params)
  if module == "R0" and command == "GA":
    return _jaws(
      iswap,
      "R0GA",
      c.gripper_increments_to_mm(int(params["ga"])),
      c.gripper_increments_to_mm_per_sec(int(params["gv"])),
      c.gripper_increments_to_mm_per_sec2(int(params["gr"])),
    )
  if module == "C0" and command == "GC":
    # Closes onto what is there, at the drive's closing speed; the width is in tenths of a mm.
    return _jaws(
      iswap,
      "C0GC",
      _tenths(params["gb"]),
      c.gripper_increments_to_mm_per_sec(c.gripper_close_speed_default_increments),
      c.gripper_increments_to_mm_per_sec2(c.gripper_acceleration_default_increments),
    )
  return None


# -- 96-head -------------------------------------------------------------------------------------


def _head96_of(driver: Any) -> Optional[Any]:
  """The 96-head, or None when there is none or nothing models it yet."""
  for arm in getattr(driver, "arms", []):
    head = arm.head96
    if head is not None and head.resource is not None and head.resource.location is not None:
      return head
  return None


class _HeadFrames:
  """Converts what the head's drives report - channel A1, on the deck - into where its resource
  sits, as `Head.update_location_by_reference_point` records it."""

  def __init__(self, driver: Any, head: Any):
    self.driver = driver
    self.head = head
    self.resource = head.resource
    self.a1 = head.resource.get_item("A1")
    self.on_arm = head.resource.parent.get_location_wrt(driver.deck)
    # The drives report channel A1's axis; a shaft, like any resource, is placed by its corner, set
    # back from the axis by its radius. Read as the corner, the head was drawn half a shaft (3.5 mm)
    # off in X and Y - the tips against the walls of a MIDI plate's 6.8 mm wells.
    self.axis_in_head = self.a1.location + Coordinate(
      self.a1.get_size_x() / 2, self.a1.get_size_y() / 2, 0
    )

  def axis_on_deck(self) -> Coordinate:
    """Where channel A1's axis is now, in deck mm (Z at the shaft's end)."""
    return cast(Coordinate, self.a1.get_location_wrt(self.driver.deck, x="c", y="c", z="b"))

  def local_y(self, y: float) -> float:
    return round(float(y - self.on_arm.y - self.axis_in_head.y), 2)

  def local_z(self, stop_disc_z: float) -> float:
    return round(float(stop_disc_z - self.on_arm.z - self.a1.location.z), 2)

  def overhang(self) -> float:
    """How far what channel A1 carries hangs below it, in mm."""
    bottom = self.a1.tip_bottom()
    return -float(bottom.z) if bottom is not None else 0.0

  def drives(self) -> Dict[str, Dict[str, Optional[float]]]:
    c = self.head.configuration
    return {
      "x": {"speed": X_SPEED, "acceleration": X_ACCELERATION, "jerk": X_JERK},
      "y": {"speed": c.y_drive_speed_default, "acceleration": c.y_drive_acceleration_default},
      "z": {"speed": c.z_drive_speed_default, "acceleration": c.z_drive_acceleration_default},
    }

  def request(self, kind: str, command: str) -> Dict[str, Any]:
    return {
      "kind": kind,
      "command": command,
      "arm": None,
      "channels": [],
      "traverse": [],
      "attach": [],
      "dwell": 0.0,
      "drives": self.drives(),
      "moves": [],
      "turns": [],
      "jaws": None,
    }


def _spots_under_head(deck: Any, head: Any, x: float, y: float) -> List[Optional[Any]]:
  """Per shaft, the tip spot it stands over with head channel A1 at (x, y): the rack of whichever
  spot is there, paired with the shafts as the head pairs them for an offset command
  (`Head96._spots_under_shafts`). All None over no rack."""
  for resource in deck.get_all_children():
    if isinstance(resource, TipSpot) and resource.parent is not None:
      centre = resource.get_location_wrt(deck, "c", "c", "b")
      if abs(centre.x - x) <= SPOT_TOLERANCE and abs(centre.y - y) <= SPOT_TOLERANCE:
        rack = resource.parent
        if rack.num_items != 96:
          break
        a1 = rack.get_item("A1").get_location_wrt(deck, "c", "c", "b")
        under = head._spots_under_shafts(Coordinate(x - a1.x, y - a1.y, 0))
        return [None if j is None else rack.get_item(j) for j in under]
  return [None] * 96


def _head96_tips(driver: Any, head: Any, command: str, params: Dict[str, Any]) -> Dict[str, Any]:
  """`C0 EP` or `C0 ER`: the head's stroke, channel A1 to spot A1.

  Up to traverse height, across - the arm in X, the head in Y - down with the stop discs to the
  spot's height, the 96 tips changing hands, and up to where the command ends: the bottom of what
  the head then carries, as the simulator records it (`SimulatedHead96._place_after_tip_command`).
  """
  frames = _HeadFrames(driver, head)
  pick_up = command == "EP"
  request = frames.request("head96_tip_pickup" if pick_up else "head96_tip_drop", "C0" + command)
  x = _tenths(params["xs"]) * (-1 if int(params["xd"]) else 1)
  y = _tenths(params["yh"])
  arm = head.arm
  if arm is not None and arm.resource is not None:
    a1 = frames.axis_on_deck()
    request["arm"] = {
      "name": arm.resource.name,
      "x": round(float(arm.resource.location.x + x - a1.x), 2),
    }

  # Each shaft works the spot under it, whichever spot of the rack channel A1 stands over: a head
  # sent over a rack shifted by whole columns (a partial pick-up off a tip support) pairs them
  # shifted.
  under = _spots_under_head(driver.deck, head, x, y)
  shafts = frames.resource.get_all_items()
  after = 0.0
  if pick_up:
    for shaft, spot in zip(shafts, under):
      if spot is not None and spot.tip is not None:
        mounted = _mounted_location(shaft, spot.tip)
        request["attach"].append(_handover(spot.tip, shaft, mounted))
        if after == 0.0:
          after = -mounted["z"]
  else:
    for i, shaft in enumerate(shafts):
      if shaft.tip is None:
        continue
      spot = under[i]
      if spot is not None and spot.tracks_tips and spot.tip is None:
        request["attach"].append(
          _handover(shaft.tip, spot, _xyz(resting_location(spot, shaft.tip)))
        )
      else:
        request["attach"].append(_handover(shaft.tip, None, None))

  name = frames.resource.name
  request["traverse"] = [
    {"name": name, "z": frames.local_z(_tenths(params["zh"]) + frames.overhang())}
  ]
  request["channels"] = [
    {
      "name": name,
      "channel": 0,
      "y": frames.local_y(y),
      # The stop discs go to the spot's height: channel A1 to the centre of spot A1, at its Z.
      "down": frames.local_z(_tenths(params["za"])),
      "end": frames.local_z(_tenths(params["ze"]) + after),
    }
  ]
  # The 96 tips are clamped on, or pushed off, at the bottom: the measured fixed time is spent there.
  return _hold_at_bottom(request, HEAD96_TIP_PICKUP_FIXED if pick_up else HEAD96_TIP_DROP_FIXED)


def _head96_liquid(driver: Any, head: Any, command: str, params: Dict[str, Any]) -> Dict[str, Any]:
  """`C0 EA` or `C0 ED`: the head's stroke, channel A1 over well A1 (or centred over a container):
  up to traverse height, across, down with the tips to the liquid surface the command gives, and
  up to where it ends. The liquid itself is the model's, booked by the command."""
  frames = _HeadFrames(driver, head)
  aspirate = command == "EA"
  request = frames.request("head96_aspirate" if aspirate else "head96_dispense", "C0" + command)
  x = _tenths(params["xs"]) * (-1 if int(params["xd"]) else 1)
  y = _tenths(params["yh"])
  arm = head.arm
  if arm is not None and arm.resource is not None:
    a1 = frames.axis_on_deck()
    request["arm"] = {
      "name": arm.resource.name,
      "x": round(float(arm.resource.location.x + x - a1.x), 2),
    }
  overhang = frames.overhang()
  name = frames.resource.name
  request["traverse"] = [{"name": name, "z": frames.local_z(_tenths(params["zh"]) + overhang)}]
  request["channels"] = [
    {
      "name": name,
      "channel": 0,
      "y": frames.local_y(y),
      "down": frames.local_z(_tenths(params["zt"]) + overhang),
      "end": frames.local_z(_tenths(params["ze"]) + overhang),
    }
  ]
  # At the bottom: the volume at the command's flow rate, the settling time, and the command's
  # measured fixed time (by dispense mode for a dispense).
  volume, flow = (params["af"], params["ag"]) if aspirate else (params["df"], params["dg"])
  pumping = _tenths(volume) / max(_tenths(flow), 1e-6)
  if aspirate:
    fixed = HEAD96_ASPIRATE_FIXED
  else:
    fixed = HEAD96_DISPENSE_FIXED.get(int(params.get("da", 2)), HEAD96_DISPENSE_FIXED[2])
  _hold_at_bottom(request, fixed)
  request["dwell"] = round(request["dwell"] + pumping + _tenths(params["wh"]), 3)
  return request


def _head96_move(driver: Any, head: Any, command: str, params: Dict[str, Any]) -> Dict[str, Any]:
  """`H0 YA` or `H0 ZA`: the head alone, along Y or Z, at the speed the command carries."""
  frames = _HeadFrames(driver, head)
  c = head.configuration
  request = frames.request("head96_move", head.configuration.module + command)
  if command == "YA":
    request["moves"].append(
      {
        "name": frames.resource.name,
        "axis": 1,
        "to": frames.local_y(c.y_drive_increments_to_mm(int(params["ya"]))),
        "speed": c.y_drive_increments_to_mm(int(params["yv"])),
        "acceleration": c.y_drive_acceleration_increments_to_mm(int(params["yr"])),
      }
    )
  else:
    request["moves"].append(
      {
        "name": frames.resource.name,
        "axis": 2,
        "to": frames.local_z(c.z_drive_increments_to_mm(int(params["za"]))),
        "speed": c.z_drive_increments_to_mm(int(params["zv"])),
        "acceleration": c.z_drive_acceleration_increments_to_mm(int(params["zr"])),
      }
    )
  return request


def star_motion(
  driver: Any, module: str, command: str, params: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
  """What one STAR firmware command asks the drives to do, as local targets, or None.

  Args:
    driver: the STAR driver the command is going to, which knows the arm and the channels.
    module: the module the command is addressed to.
    command: the two-letter command code.
    params: the command's parameters, as the driver passes them to be assembled.

  Returns:
    A motion request for the page: the command's `kind`; the arm's target X; each channel's
    targets - `y`, the heights `down` and `end`, and for an aspiration where it `leave`s the liquid
    and at what `leave_speed`; the height every channel rises to first (`traverse`); the tips that
    change hands at the bottom of the stroke (`attach`: a tip's name and the resource that takes
    it, or None to leave it where it is); how long the stroke dwells at the bottom; the command's
    `fixed` time beyond its motion; and the drives' speeds (the channels' Y with the `stagger` one
    channel starts after another). A tip pick-up's channels also `press` to a height at a
    `press_speed`. None for a command that moves nothing, or one this does not read.
  """
  if command[0] in ("R", "Q"):
    return None
  request = _decode(driver, module, command, params)
  if request is not None and not request.get("fixed"):
    request["fixed"] = _FIXED.get(module + command, SIMPLE_MOVE_FIXED)
  return request


# The fixed time of the commands whose time does not depend on their parameters; any other command
# the decoder reads is a simple single-drive move (`SIMPLE_MOVE_FIXED`).
_FIXED = {
  "C0ZT": CORE_TOOL_PICKUP_FIXED,
  "C0ZS": CORE_TOOL_RETURN_FIXED,
  "C0ZP": CORE_TOOL_PICKUP_FIXED,
  "C0ZR": CORE_TOOL_RETURN_FIXED,
  "C0ZA": CHANNELS_UP_FIXED,
  "C0EP": HEAD96_TIP_PICKUP_FIXED,
  "C0ER": HEAD96_TIP_DROP_FIXED,
  "C0EA": HEAD96_ASPIRATE_FIXED,
  "C0ED": HEAD96_DISPENSE_FIXED[2],
  "H0YA": HEAD96_MOVE_FIXED,
  "H0ZA": HEAD96_MOVE_FIXED,
}


def _decode(
  driver: Any, module: str, command: str, params: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
  key = module + command
  try:
    iswap = _iswap_of(driver)
    if iswap is not None and (module == "R0" or key == "C0GC"):
      return _iswap_motion(driver, iswap, module, command, params)
    head = _head96_of(driver)
    if head is not None and key in ("C0EP", "C0ER"):
      return _head96_tips(driver, head, command, params)
    if head is not None and key in ("C0EA", "C0ED"):
      return _head96_liquid(driver, head, command, params)
    if head is not None and module == head.configuration.module and command in ("YA", "ZA"):
      return _head96_move(driver, head, command, params)
    arm = _pipetting_arm(driver)
    if arm is None:
      return None
    frames = _Frames(driver, arm)
    if key == "C0TP":
      return _tip_pickup(frames, key, params)
    if key == "C0TR":
      return _tip_drop(frames, key, params)
    if key == "C0ZT":
      return _core_tool_pickup(frames, key, params)
    if key == "C0ZS":
      return _core_tool_return(frames, key, params)
    if key == "C0ZP":
      return _core_plate(frames, key, params, "core_grip")
    if key == "C0ZM":
      return _core_plate(frames, key, params, "core_move")
    if key == "C0ZR":
      return _core_plate(frames, key, params, "core_release")
    if key == "C0AS":
      return _aspirate(frames, key, params)
    if key == "C0JY":
      return _move_y(frames, key, params)
    if key == "C0JZ":
      return _move_z(frames, key, params)
    if key == "C0FY":
      return _free_y_range(frames, key)
    if key == "C0ZA":
      return _channels_up(frames, key)
    if module == "X0" and command in ("XP", "SP"):
      return _move_x(frames, driver, command, params)
  except (KeyError, IndexError, ValueError, TypeError, RuntimeError):
    # A command shaped otherwise than this reads it is not acted out; the model still records
    # where it ends.
    return None
  return None


def attach_viewer_motion(driver: Any, viewer: Any) -> None:
  """Let a viewer act out what the driver's commands move.

  The driver hands each command to its `motion_listener` before the device answers it (a
  `STARSimulationDriver` does); this reads the command for what it moves (`star_motion`) and hands
  it to the viewer's `act_out`, which holds the command until the pages have played it. A read
  moves nothing and is not worth a message.

  Args:
    driver: a simulated STAR driver with a `motion_listener`.
    viewer: a `Viewer3D` with an `act_out`.
  """

  async def listener(module: str, command: str, params: Dict[str, Any]) -> None:
    motion = star_motion(driver, module, command, params)
    if motion is None and command[0] in ("R", "Q"):
      return
    await viewer.act_out(module + command, motion)

  driver.motion_listener = listener
