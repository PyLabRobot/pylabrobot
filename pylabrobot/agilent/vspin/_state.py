from __future__ import annotations

import dataclasses
import enum
from typing import Union


class ConnectionState(enum.Enum):
  """PLR's ownership of a device transport."""

  DISCONNECTED = enum.auto()
  CONNECTING = enum.auto()
  CONNECTED = enum.auto()
  DISCONNECTING = enum.auto()


class VSpinInitializationState(enum.Enum):
  """VSpin NMC initialization known to PLR."""

  UNKNOWN = enum.auto()
  INITIALIZING = enum.auto()
  INITIALIZED = enum.auto()


class VSpinHomingState(enum.Enum):
  """VSpin homing known to PLR."""

  UNKNOWN = enum.auto()
  HOMING = enum.auto()
  HOMED = enum.auto()


class VSpinActivity(enum.Enum):
  """The VSpin workflow currently owning the command lock."""

  IDLE = enum.auto()
  CHANGING_INTERLOCKS = enum.auto()
  POSITIONING = enum.auto()
  TRANSFERRING = enum.auto()
  PREPARING_TO_SPIN = enum.auto()
  ACCELERATING = enum.auto()
  AT_SPEED = enum.auto()
  DECELERATING = enum.auto()


@dataclasses.dataclass(frozen=True)
class VSpinMachineState:
  """VSpin semantic facts not already available from status or resources."""

  connection: ConnectionState = ConnectionState.DISCONNECTED
  initialization: VSpinInitializationState = VSpinInitializationState.UNKNOWN
  homing: VSpinHomingState = VSpinHomingState.UNKNOWN
  recovery_required: bool = False
  activity: VSpinActivity = VSpinActivity.IDLE


class Access2Activity(enum.Enum):
  """A non-transfer Access2 operation."""

  IDLE = enum.auto()
  INITIALIZING = enum.auto()
  HOMING = enum.auto()
  MOVING = enum.auto()


class TransferDirection(enum.Enum):
  """Direction of one plate transfer."""

  INTO_CENTRIFUGE = enum.auto()
  OUT_OF_CENTRIFUGE = enum.auto()


class TransferPhase(enum.Enum):
  """Last requested or confirmed phase of an active transfer."""

  APPROACHING_SOURCE = enum.auto()
  AT_SOURCE = enum.auto()
  GRIPPING = enum.auto()
  HOLDING = enum.auto()
  MOVING_TO_DESTINATION = enum.auto()
  AT_DESTINATION = enum.auto()
  RELEASING = enum.auto()
  RETURNING_TO_PARK = enum.auto()


@dataclasses.dataclass(frozen=True)
class TransferProgress:
  """The single active Access2 transfer operation."""

  direction: TransferDirection
  bucket_teachpoint: int
  phase: TransferPhase = TransferPhase.APPROACHING_SOURCE


Access2Operation = Union[Access2Activity, TransferProgress]


@dataclasses.dataclass(frozen=True)
class Access2MachineState:
  """Access2 semantic facts not already available in the controller status."""

  connection: ConnectionState = ConnectionState.DISCONNECTED
  recovery_required: bool = False
  operation: Access2Operation = Access2Activity.IDLE
  last_teachpoint: int | None = None


@dataclasses.dataclass
class TransitionToken:
  """Record whether a guarded workflow crossed an actuation boundary."""

  actuated: bool = False
  position_uncertain: bool = False

  def mark_actuated(self, *, position_uncertain: bool = False) -> None:
    """Record that hardware may change before the awaited command returns."""
    self.actuated = True
    self.position_uncertain |= position_uncertain

  def confirm_position(self) -> None:
    """Record that the existing motion waiter confirmed the current position."""
    self.position_uncertain = False
