"""The 405 TS, a plate washer with no dispensers of its own."""

from __future__ import annotations

import logging
from contextlib import AbstractAsyncContextManager
from typing import ClassVar

from pylabrobot.agilent.biotek.lhc.comm.link import Link
from pylabrobot.agilent.biotek.lhc.comm.transport import DEFAULT_READ_TIMEOUT, Transport
from pylabrobot.agilent.biotek.lhc.devices import batch as batching
from pylabrobot.agilent.biotek.lhc.devices import (
  execution,
  handshake,
  settings_document,
  settings_query,
)
from pylabrobot.agilent.biotek.lhc.devices.build_rules import rules_for
from pylabrobot.agilent.biotek.lhc.devices.components.washer import PlateWasher
from pylabrobot.agilent.biotek.lhc.devices.instrument_settings import InstrumentSettings
from pylabrobot.agilent.biotek.lhc.devices.runtime import Runtime
from pylabrobot.agilent.biotek.lhc.devices.settings_comparison import (
  SettingsComparison,
  compare,
)
from pylabrobot.agilent.biotek.lhc.enums.instrument.instrument_family import InstrumentFamily
from pylabrobot.agilent.biotek.lhc.enums.motion.motor import Motor
from pylabrobot.agilent.biotek.lhc.enums.motion.motor_home_type import MotorHomeType
from pylabrobot.agilent.biotek.lhc.enums.plates.plate_type import PlateType
from pylabrobot.agilent.biotek.lhc.enums.steps.shake_axis import ShakeAxis
from pylabrobot.agilent.biotek.lhc.enums.steps.shake_intensity import ShakeIntensity
from pylabrobot.agilent.biotek.lhc.enums.steps.step_type import StepType
from pylabrobot.agilent.biotek.lhc.plate_geometry.plate_record import PlateRecord
from pylabrobot.agilent.biotek.lhc.plate_geometry.resolution import resolve
from pylabrobot.agilent.biotek.lhc.protocols.protocol import Protocol
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_interface import Step
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_parts.groups import Shake, Soak
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.shake_soak import ShakeSoak
from pylabrobot.agilent.biotek.lhc.protocols.validation.configuration import available_step_types
from pylabrobot.agilent.biotek.lhc.protocols.validation.report import ValidationReport
from pylabrobot.agilent.biotek.lhc.serialization.commands.diagnostics import (
  HomeVerifyMotors,
  ResetInstrument,
  RunSelfCheck,
)
from pylabrobot.agilent.biotek.lhc.serialization.commands.queries import (
  FirmwareVersion,
  GetFirmwareVersion,
  GetSerialNumber,
)
from pylabrobot.agilent.biotek.lhc.serialization.commands.run_control import RunStatus
from pylabrobot.resources import Plate

logger = logging.getLogger(__name__)

PALETTE: tuple[StepType, ...] = (
  StepType.MANIFOLD_WASH,
  StepType.MANIFOLD_ASPIRATE,
  StepType.MANIFOLD_DISPENSE,
  StepType.MANIFOLD_PRIME,
  StepType.MANIFOLD_AUTO_CLEAN,
  StepType.SHAKE_SOAK,
)
"""Every step type this model can be built to run. What it does run depends on what is fitted."""


class Washer405TS:
  """A 405 TS plate washer.

  Everything it does goes through its one wash manifold, reached as :attr:`washer`. It carries no
  syringes and no peristaltic pumps, so it dispenses nothing of its own; a protocol written for a
  washer-dispenser has its dispensing steps refused by :meth:`can_run`.

  .. code-block:: python

    device = Washer405TS(port="/dev/ttyUSB0")
    await device.setup()
    device.set_plate(plate)
    await device.washer.wash(cycles=3)
    await device.stop()

  Args:
    port: The port the instrument is on. A string carrying a device serial number names a USB
      bridge; anything else is taken to be a serial port.
    name: What to call this instrument in logs and error messages.
    timeout: How long to wait for a reply, in seconds. Commands that answer only once the
      instrument has stopped moving carry longer timeouts of their own.
    io: An already open transport to use instead of opening ``port``, for a test that replays a
      recorded exchange.

  Attributes:
    washer: The wash manifold.
  """

  family: ClassVar[InstrumentFamily] = InstrumentFamily.MODEL_405_TS

  def __init__(
    self,
    port: str = "",
    name: str = "405 TS",
    timeout: float = DEFAULT_READ_TIMEOUT,
    io: Transport | None = None,
  ) -> None:
    self._runtime = Runtime(
      link=Link(port=port, family=self.family, name=name, timeout=timeout, io=io),
      rules=rules_for(self.family),
    )
    self.washer = PlateWasher(self._runtime)

  @property
  def name(self) -> str:
    """What this instrument is called in logs and error messages."""
    return self._runtime.link.name

  @property
  def settings(self) -> InstrumentSettings:
    """What the instrument reported as fitted when :meth:`setup` last ran.

    Raises:
      RejectedError: If :meth:`setup` has not run. There is no default to fall back on: a record
        nobody read would describe some other machine, and answering from one is how a check comes
        to allow hardware this instrument does not have.
    """
    return self._runtime.settings

  @property
  def settle(self) -> float:
    """How long to wait after the instrument accepts a step before polling it, in seconds.

    The first poll of a step that has only just started can still report the instrument idle, so
    this is what stops a step being called finished before it began. It is paid once per step, so a
    long protocol on a quick instrument is where lowering it is worth something.
    """
    return self._runtime.settle

  @settle.setter
  def settle(self, seconds: float) -> None:
    self._runtime.settle = seconds

  @property
  def plate(self) -> PlateRecord | None:
    """The plate the instrument is set to work, or None while none has been set."""
    return self._runtime.plate

  async def setup(self) -> None:
    """Open the link and read what the instrument has fitted.

    Reading the fitted options is not optional: every step is encoded against them, and checking a
    protocol measures it against them.

    Raises:
      BiotekError: If the port will not open, nothing answers on it, or the fitted options cannot
        be read.
    """
    link = self._runtime.link
    await link.setup()
    await handshake.check_communications(link)
    self._runtime.reported_settings = await settings_query.read_settings(link, self.family)
    self._runtime.forget_instrument_facts()
    logger.info("%s is ready: %s", self.name, self._runtime.settings)

  async def stop(self) -> None:
    """Close the link. Calling this on a closed instrument does nothing."""
    await self._runtime.link.stop()

  def set_plate(self, plate: Plate, plate_type: PlateType | None = None) -> None:
    """Tell the instrument which plate is on its carrier.

    Args:
      plate: The labware on the carrier. Its columns, rows and well depth decide which of the
        formats the instrument works it as.
      plate_type: The format to use, for labware that cannot be resolved on its own or that is to
        be worked as something else.

    Raises:
      ValueError: If the plate matches no format this model works, or more than one. The message
        names the candidates, which are what ``plate_type`` may be set to.
    """
    self._runtime.plate = resolve(plate, self.family, plate_type)
    self._runtime.forget_instrument_facts()
    logger.info("%s is set to a %s", self.name, self._runtime.plate.label)

  def clear_plate(self) -> None:
    """Forget which plate is on the carrier. Nothing can run until another is set."""
    self._runtime.plate = None
    self._runtime.forget_instrument_facts()

  def get_available_steps(self) -> list[StepType]:
    """Which operations this instrument can carry out as it is fitted.

    Returns:
      The step types, in the order this model offers them, which is the order its palette above
      lists rather than the order they are numbered. A type this model is never built to run is
      absent whatever is fitted, and so is one whose hardware is missing.
    """
    fitted = set(available_step_types(self._runtime.settings))
    return [step_type for step_type in PALETTE if step_type in fitted]

  def compare_settings(self, protocol: Protocol | InstrumentSettings) -> SettingsComparison:
    """Compare the options a protocol was written for with the ones this instrument reports.

    Nothing calls this on its own: a protocol runs against the instrument as it actually is, and
    whether it can run is what :meth:`can_run` answers. This is for the rarer question of whether a
    protocol was written for a differently equipped machine, which is worth asking before running
    one that came from elsewhere.

    Args:
      protocol: The protocol, whose fitted-options document is read, or a settings record to
        compare directly.

    Returns:
      The comparison, truthy when the protocol was written for an instrument equipped like this
      one, and printing as the list of options that differ.

    Raises:
      ValueError: If the protocol carries no fitted-options document, which is how the oldest
        releases wrote a file. There is nothing to compare against in that case.
    """
    if isinstance(protocol, InstrumentSettings):
      return compare(protocol, self._runtime.settings)
    if not protocol.instrument_settings_xml.strip():
      raise ValueError(
        f"{protocol.protocol_name or 'the protocol'} carries no fitted-options document, so there "
        "is nothing to compare against what the instrument reports"
      )
    declared = settings_document.from_xml(protocol.instrument_settings_xml)
    return compare(declared, self._runtime.settings)

  async def can_run(self, protocol: Protocol | list[Step]) -> ValidationReport:
    """Check whether a protocol can run on the instrument as it is.

    The report is truthy when every step can run, and prints as the list of those that cannot, so
    it reads as the answer to the question. Running a protocol does this first; call it beforehand
    to see what is wrong without touching the plate.

    Args:
      protocol: The protocol, or the steps on their own.

    Returns:
      The report.

    Raises:
      RejectedError: If no plate has been set.
      ValueError: If the protocol carries a step this package cannot read.
    """
    return await execution.can_run(self._runtime, execution.steps_of(protocol))

  async def run_protocol(
    self,
    protocol: Protocol | list[Step],
    check: bool = True,
    home_on_close: bool = False,
  ) -> None:
    """Run every step of a protocol, in one batch.

    Args:
      protocol: The protocol, or the steps on their own.
      check: Whether to check the protocol first. Turning this off also gives up what the check
        works out about the pumps, so the batch opens against whatever cassette is fitted rather
        than the one the protocol asks for.
      home_on_close: Whether to home the transport before closing the batch.

    Raises:
      BiotekError: If the protocol cannot run, the hardware cannot be made to match it, or a step
        fails.
      RejectedError: If no plate has been set.
      ValueError: If the protocol carries a step this package cannot read.
    """
    await execution.run_steps(
      self._runtime, execution.steps_of(protocol), check=check, home_on_close=home_on_close
    )

  async def run_step(self, step: Step) -> None:
    """Run one step built outright, for an operation the capability objects do not spell out.

    Args:
      step: The step to run.

    Raises:
      BiotekError: If the step cannot run, or fails while running.
      RejectedError: If no plate has been set.
    """
    await execution.run_steps(self._runtime, [step])

  def batch(self, home_on_close: bool = False) -> AbstractAsyncContextManager[None]:
    """Hold one batch open across several operations.

    Opening a batch homes the motors and takes the instrument, which is worth doing once rather
    than per operation. Nesting is allowed and does nothing: an operation inside an open batch
    joins it.

    Args:
      home_on_close: Whether to home the transport before closing the batch. The instrument does
        not do this of its own accord; ask for it when the next thing to touch the plate is a
        person.

    Returns:
      A context manager holding the batch open for the body of the block.
    """
    return batching.batch(self._runtime, home_on_close=home_on_close)

  async def shake(
    self,
    duration: int = 5,
    intensity: ShakeIntensity = "Medium",
    axis: ShakeAxis = "X",
    soak_duration: int = 0,
    move_carrier_home: bool = True,
  ) -> None:
    """Shake the plate, soak it, or both.

    Args:
      duration: How long to shake, in seconds. Zero shakes not at all.
      intensity: How hard to shake.
      axis: Which axis to shake along.
      soak_duration: How long to leave the plate still afterwards, in seconds. Zero soaks not at
        all.
      move_carrier_home: Whether the carrier returns home afterwards.

    Raises:
      BiotekError: If the step cannot run, or fails while running.
    """
    shake = Shake(enabled=duration > 0, intensity=intensity, axis=axis)
    if duration > 0:
      shake.duration = duration
    soak = Soak(enabled=soak_duration > 0)
    if soak_duration > 0:
      soak.duration = soak_duration
    await execution.run_steps(
      self._runtime,
      [ShakeSoak(shake=shake, soak=soak, move_carrier_home=move_carrier_home)],
    )

  async def get_status(self) -> RunStatus:
    """Ask what the instrument is doing.

    Returns:
      The run state, and the phase and countdown of a running step.

    Raises:
      BiotekError: If the instrument reports a fault.
    """
    return await execution.status(self._runtime)

  async def get_serial_number(self) -> str:
    """Read the instrument's serial number.

    Returns:
      The serial number.

    Raises:
      BiotekError: If it cannot be read.
    """
    command = GetSerialNumber()
    return command.parse(await self._runtime.link.request(command, operation="serial number"))

  async def get_firmware_version(self) -> FirmwareVersion:
    """Read the instrument's firmware version.

    Returns:
      The version record, whose halves are the instrument's two processors.

    Raises:
      BiotekError: If it cannot be read.
    """
    command = GetFirmwareVersion()
    return command.parse(await self._runtime.link.request(command, operation="firmware version"))

  async def self_check(self) -> None:
    """Run the instrument's own self-check and wait for it.

    Raises:
      BiotekError: If the check does not pass, reporting what failed.
    """
    await self._runtime.link.request(RunSelfCheck(), operation="self check")

  async def reset(self) -> None:
    """Reset the instrument, and wait for it to come back.

    Returns it to the state it is in after power-on: motion stopped, motors dereferenced. What is
    fitted does not change, so the record :meth:`setup` read still stands.

    Raises:
      BiotekError: If the instrument does not come back.
    """
    await self._runtime.link.request(ResetInstrument(), operation="reset")

  async def home(self, motor: Motor | None = None) -> None:
    """Drive the transport to its home position and confirm it arrived.

    Args:
      motor: One motor to home, or None to home the carrier and the heads together. A motor this
        instrument does not have is refused by the instrument rather than here.

    Raises:
      BiotekError: If a motor does not reach its home position.
    """
    home_type = MotorHomeType.HOME_MOTOR if motor is not None else MotorHomeType.HOME_XYZ_MOTORS
    await self._runtime.link.request(
      HomeVerifyMotors(int(home_type), int(motor) if motor is not None else 0),
      operation="home",
    )

  async def abort(self) -> None:
    """Stop the running step and wait for the instrument to come back.

    The instrument stops where it is and homes itself, and is off the wire for as long as that
    motion lasts: it acknowledges the request, answers nothing while it homes, and reports itself
    ready once it is home. The step does not finish -- whatever was waiting for it is told it was
    stopped.

    Raises:
      BiotekError: If the instrument does not come back, or reports a fault when it does.
    """
    await execution.abort(self._runtime)

  async def pause(self) -> None:
    """Pause the running step.

    Raises:
      BiotekError: If the instrument will not pause.
    """
    await execution.pause(self._runtime)

  async def resume(self) -> None:
    """Resume a paused step.

    Raises:
      BiotekError: If the instrument will not resume.
    """
    await execution.resume(self._runtime)
