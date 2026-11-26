"""Simulated instruments: boards that answer from what a real Biomek i7's boards answered.

`ReplayAceUSB` only replays a recording. `SimulatedAceUSB` also models each axis's state and the
interlock, so configuring, arming and enabling can run against it; nothing it does reaches a USB
device.

A replayed board holds a recording of request -> replies, keyed on everything in a request
but its sequence number, and answers a request it has a recording for with those replies under
the request's sequence number. A request it has no recording for gets no answer, as a board that
ignored it would, and is kept in `unanswered`.
"""

import asyncio
import gzip
import json
import os
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from pylabrobot.beckman_coulter.biomek_i7.driver.board import AceBoard
from pylabrobot.beckman_coulter.biomek_i7.driver.framing import frame, split
from pylabrobot.beckman_coulter.biomek_i7.driver.motors import Axis, MotorTables
from pylabrobot.beckman_coulter.biomek_i7.driver.protocol import AxisState, Device
from pylabrobot.io.usb import USB

# Names the recording the simulator answers from (identity and sensor reads, recorded at an
# instrument's cold start) when no path is given. A generic one ships in `defaults/`
# (`biomek_i7_generic_coldstart.json`), which `BiomekI7(simulation=True)` uses when neither is
# given.
RECORDING_ENV = "BIOMEK_I7_SIMULATOR_RECORDING"


def recording_path(path: Optional[Union[str, Path]] = None) -> Path:
  """`path`, else the file `$BIOMEK_I7_SIMULATOR_RECORDING` names.

  Raises:
    ValueError: with neither.
  """
  path = path or os.environ.get(RECORDING_ENV)
  if not path:
    raise ValueError(
      f"no recording for the simulator to answer from: pass its path or set ${RECORDING_ENV}"
    )
  return Path(path)


def load_recording(path: Optional[Union[str, Path]] = None) -> Dict[str, Any]:
  """A recording (`recording_path`), gzipped (`.gz`) or not."""
  where = recording_path(path)
  if where.suffix == ".gz":
    with gzip.open(where, "rt", encoding="utf-8") as fh:
      return dict(json.load(fh))
  with open(where, encoding="utf-8") as fh:
    return dict(json.load(fh))


def _request_key(message: bytes) -> str:
  return (message[:3] + message[4:]).hex()


class ReplayAceUSB(USB):
  """One board's USB link, answered from a recording."""

  def __init__(self, recording: Dict[str, List[str]], name: str = "replayed ACE board"):
    super().__init__(0x0AB5, 0xACE0, name)
    self.recording = {k: [bytes.fromhex(r) for r in v] for k, v in recording.items()}
    self.requests: List[bytes] = []
    self.unanswered: List[bytes] = []
    self._packets: "asyncio.Queue[bytes]" = asyncio.Queue()

  async def setup(self, empty_buffer=True):
    pass

  async def stop(self):
    pass

  async def write(self, data: bytes, timeout: Optional[float] = None):
    for message in split(data):
      self.requests.append(message)
      replies = self.recording.get(_request_key(message))
      if replies is None:
        self.unanswered.append(message)
        continue
      for reply in replies:
        framed = frame(reply[:3] + message[3:4] + reply[4:])
        for i in range(0, len(framed), 16):
          self._packets.put_nowait(framed[i : i + 16])

  async def read(self, timeout=None, size=None, endpoint=None) -> bytes:
    try:
      return await asyncio.wait_for(self._packets.get(), timeout=0.05)
    except asyncio.TimeoutError:
      raise TimeoutError("no packet") from None


def replay_boards(
  path: Optional[Union[str, Path]] = None, default_timeout: float = 1.0
) -> List[AceBoard]:
  """One replayed board per module in a recording (`recording_path`), as
  `BiomekI7Driver(boards=...)` takes them."""
  data = load_recording(path)
  return [
    AceBoard(
      io=ReplayAceUSB(dict(entries), name=f"replayed {module}"),
      name=f"replayed {module}",
      default_timeout=default_timeout,
    )
    for module, entries in data["boards"].items()
  ]


class SimulatedAceUSB(ReplayAceUSB):
  """One board's USB link, answered from a model of its axes rather than only a recording.

  Each axis has a state (`AxisState`), is homed or not, and has a position, by the boards' rules:
  an axis starts not configured; Configure leaves it disabled and not homed; Set State is refused
  "not configured" before that. Identity and sensor reads are answered from the recording.

  The boards of one instrument share an `Interlock`. While it is tripped, as at rest, a CiA 402
  axis that was enabled reads FAULT (its drive lost motor power: H1, 2026-10-27; one configured at
  rest reads DISABLED, as on H2 the same day), its enable fails, and the arm's drive supply
  input reads 0.12 V; armed, 35.4 V.
  Enabling a CiA 402 axis in fault resets it, unless it was given a fault that
  does not clear (`_AxisModel.stuck_fault`). The error codes of a failed enable are not known; the
  model answers "interlock tripped" without power and 0x3D for a stuck fault.
  """

  def __init__(
    self,
    recording: Dict[str, List[str]],
    axes: Sequence[Axis] = (),
    homed: Iterable[str] = (),
    name: str = "simulated ACE board",
    configured: bool = False,
    interlock: Optional["Interlock"] = None,
    supply_input: Optional[int] = None,
    door_input: Optional[int] = None,
  ):
    super().__init__(recording, name=name)
    self.door_input = door_input
    homed = set(homed)
    if homed and not configured:
      raise ValueError("an axis cannot be homed on a board that is not configured")
    self.interlock = interlock or Interlock()
    self.supply_input = supply_input
    self.axes: Dict[Tuple[int, int], _AxisModel] = {}
    for a in axes:
      # At rest a configured instrument's SAGIAN axes read ENABLED, as on 2026-10-27; CiA 402
      # axes read FAULT while the interlock is tripped (`_apply_power`).
      state = AxisState.ENABLED if configured else AxisState.NOT_CONFIGURED
      self.axes[(a.device, a.index)] = _AxisModel(a.name, a.name in homed, state, a.drive)

  def _apply_power(self) -> None:
    """An enabled CiA 402 drive faults when its motor power goes. One configured with the power
    already off stays DISABLED (H2, 2026-10-27: every axis read DISABLED after the Master
    Controller's Configure at rest)."""
    if self.interlock.armed:
      return
    for model in self.axes.values():
      if model.drive == "cia402" and model.state == AxisState.ENABLED:
        model.state = AxisState.FAULT

  def set_door(self, open_: bool) -> None:
    """Open or close the door, on the chassis's link: the door sensor reports the change; the
    interlock is not affected (as in Biomek's logs)."""
    self.interlock.door_open = open_
    self._queue(
      bytes([Device.DIGITAL_INPUT, self.door_input or 1, 0, 0, 0x10, 0x03, 0, int(not open_)])
    )

  def set_light_curtain(self, broken: bool) -> None:
    """Break or clear the light curtain: breaking it trips an armed interlock, which reports it
    (Notify On Interlock Trip); while broken, the interlock does not arm."""
    self.interlock.light_curtain_broken = broken
    if broken and self.interlock.armed:
      self.interlock.armed = False
      self._queue(bytes([Device.INTERLOCK, 1, 0, 0, 0x10, 0x03, 0, 0xFF]))

  def axis_model(self, name: str) -> "_AxisModel":
    for model in self.axes.values():
      if model.name == name:
        return model
    raise KeyError(name)

  def answer(self, message: bytes) -> Optional[List[bytes]]:
    device, index, opcode, params = message[0], message[1], message[4], message[5:]
    header = message[:5]
    done = header + b"\x02\x00"
    tripped = header + b"\x00\x0f"
    self._apply_power()
    if device == Device.ANALOG_INPUT and opcode == 0x10 and index == self.supply_input:
      powered = self.interlock.armed and self.interlock.supply_comes_up
      return [done + struct.pack("<f", 35.4 if powered else 0.12)]
    axis = self.axes.get((device, index))
    if axis is not None:
      configured = axis.state != AxisState.NOT_CONFIGURED
      if opcode == 0x11:
        return [done + bytes([axis.state])]
      if opcode == 0x24:
        return [done + struct.pack("<i", axis.position)]
      if opcode == 0x21:
        return [done + (b"\x00" if axis.homed else b"\x01")]
      if opcode == 0x10:
        axis.state, axis.homed = AxisState.DISABLED, False
        return [done]
      if opcode == 0x12:
        if not configured:
          return [header + b"\x00\x30"]
        if params[0] == AxisState.ENABLED and axis.drive == "cia402":
          if not self.interlock.armed:
            return [tripped]
          if axis.stuck_fault:
            axis.state = AxisState.FAULT
            return [header + b"\x00\x3d"]
        axis.state = AxisState(params[0])
        return [done]
    if device == Device.MOTION_SERVICE and opcode == 0x0F:
      return [done]
    if device == Device.MODULE and opcode == 0x0F:
      return [done]
    if device == Device.INTERLOCK and opcode == 0x10:
      return [done + (b"\x00" if self.interlock.armed else b"\xff")]
    if device == Device.DIGITAL_INPUT and opcode == 0x10 and index == self.door_input:
      return [done + (b"\x00" if self.interlock.door_open else b"\x01")]
    if device == Device.INTERLOCK and opcode == 0x11:
      return [done]
    if device == Device.INTERLOCK and opcode == 0x50:
      self.interlock.armed = False
      return [done]
    if device == Device.INTERLOCK and opcode == 0x51:
      if self.interlock.light_curtain_broken:
        return [done + b"\xff"]  # not armed; the reply's byte for this is a guess
      self.interlock.armed = True
      return [done + b"\x00"]  # ARMED
    if device in (Device.STATUS_DISPLAY,):
      return [done]
    if device == Device.IO_BOX:
      return [done + b"\x01"]
    return None

  async def write(self, data: bytes, timeout: Optional[float] = None):
    for message in split(data):
      replies = self.answer(message)
      if replies is None:
        await super().write(frame(message), timeout)
        continue
      self.requests.append(message)
      for reply in replies:
        self._queue(reply)

  def _queue(self, message: bytes) -> None:
    framed = frame(message)
    for i in range(0, len(framed), 16):
      self._packets.put_nowait(framed[i : i + 16])


@dataclass
class Interlock:
  """The interlock one instrument's simulated boards share. `supply_comes_up=False` models an
  interlock that arms while the drive supply stays off."""

  armed: bool = False
  supply_comes_up: bool = True
  door_open: bool = False
  light_curtain_broken: bool = False


@dataclass
class _AxisModel:
  name: str
  homed: bool
  state: AxisState = AxisState.NOT_CONFIGURED
  drive: str = "cia402"
  stuck_fault: bool = False
  position: int = 0


# The analog input carrying the interlocked drive supply, per module (the Master Controller's cold
# start: "+36V Interlocked" is the chassis's input 5, "+36V Interlock" each arm's input 4).
SUPPLY_INPUTS = {"chassis": 5, "multichannel": 4, "span8": 4}
# The chassis's digital input 1 is "Door Sensor".
DOOR_INPUTS = {"chassis": 1}


def simulated_boards(
  motor_tables: MotorTables,
  homed: Union[bool, Dict[str, Iterable[str]]] = False,
  path: Optional[Union[str, Path]] = None,
  default_timeout: float = 1.0,
  configured: Optional[Union[bool, Dict[str, bool]]] = None,
) -> List[AceBoard]:
  """A simulated instrument as `BiomekI7Driver(boards=...)` takes it: the recorded chassis, a left
  Multichannel and a right Span-8 pod.

  Args:
    motor_tables: the axes to model: the declared configuration's
      (`DeviceConfiguration.motor_tables`), the one the driver is given.
    homed: True for every axis homed, False for none, or the homed axes' names per arm type.
    path: the recording that answers identity and sensor reads (`recording_path`).
    configured: whether each arm's board has its motor tables, as one value or per arm type.
      Defaults to configured wherever an axis is homed, and not configured (just powered up)
      otherwise.
  """
  tables = motor_tables
  data = load_recording(path)
  interlock = Interlock()
  boards = []
  for module, entries in data["boards"].items():
    axes = tables.axes(module) if module in tables.modules else []
    if isinstance(homed, bool):
      homed_axes: Iterable[str] = [a.name for a in axes] if homed else []
    else:
      homed_axes = homed.get(module, [])
    homed_axes = list(homed_axes)
    if configured is None:
      is_configured = bool(homed_axes)
    elif isinstance(configured, bool):
      is_configured = configured
    else:
      is_configured = configured.get(module, False)
    usb = SimulatedAceUSB(
      dict(entries),
      axes,
      homed_axes,
      name=f"simulated {module}",
      configured=is_configured,
      interlock=interlock,
      supply_input=SUPPLY_INPUTS.get(module),
      door_input=DOOR_INPUTS.get(module),
    )
    boards.append(AceBoard(io=usb, name=f"simulated {module}", default_timeout=default_timeout))
  return boards
