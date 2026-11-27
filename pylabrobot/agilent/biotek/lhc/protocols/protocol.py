"""A protocol: the steps to run, and everything a protocol file stores alongside them."""

from __future__ import annotations

from dataclasses import dataclass, field

from pylabrobot.agilent.biotek.lhc.enums.steps.step_action import StepAction
from pylabrobot.agilent.biotek.lhc.enums.steps.step_type import StepType
from pylabrobot.agilent.biotek.lhc.protocols.steps import Step, step_from_definition


@dataclass
class ProtocolEntry:
  """One entry of a protocol as a file stores it.

  Only an entry whose action is :attr:`StepAction.CUSTOM` operates the instrument. The rest
  sequence a run -- delays, loops, remarks, plate transfers -- and their definition text is in a
  form of their own rather than a step definition.

  Attributes:
    action: What this entry does.
    step_type: Which operation it performs, for an entry that operates the instrument.
    definition: The entry's parameters as text.
  """

  action: StepAction = StepAction.UNDEFINED
  step_type: StepType = StepType.UNDEFINED
  definition: str = ""

  @property
  def is_device_step(self) -> bool:
    """Whether this entry operates the instrument rather than sequencing the run."""
    return self.action is StepAction.CUSTOM


@dataclass
class Protocol:
  """What a protocol file holds.

  A protocol keeps both what it will run and what it was read from: :attr:`steps` are the steps to
  send, :attr:`entries` the file's own records. Reading a file fills the entries and leaves the
  steps alone, so a file whose steps this package cannot read is still readable, writable and
  inspectable; :meth:`build_steps` is what turns the entries into steps and reports which one
  cannot be read.

  Attributes:
    steps: The steps to run.
    entries: The file's own records, including the ones that sequence the run rather than
      operating the instrument.
    instrument_settings_xml: What the instrument had fitted when the protocol was written, as the
      nested document the file carries. Interpreting it belongs to the device.
    instrument_settings: The settings summary the file carries alongside that document.
    lhc_version: Which version of the vendor's software wrote the file.
    instrument_name: The instrument the protocol was written for.
    com_port: The port that instrument was on, which is informational only.
    protocol_name: The protocol's name.
    protocol_version: The protocol's version.
    plate_type: The plate the protocol runs on, by name.
    plate_type_number: The same plate as a number, or -1 when the file names none.
    comments: Free text stored with the protocol.
    read_only: Whether the vendor's editor treats the file as read-only.
    stacker_enabled: Whether a plate stacker is configured.
    use_stacker: Whether the run takes plates from the stacker.
    entire_stack: Whether the run processes the whole stack.
    stacker_plate_count: How many plates to process when it does not.
  """

  steps: list[Step] = field(default_factory=list)
  entries: list[ProtocolEntry] = field(default_factory=list)
  instrument_settings_xml: str = ""
  instrument_settings: str = ""
  lhc_version: str = ""
  instrument_name: str = ""
  com_port: str = ""
  protocol_name: str = ""
  protocol_version: str = ""
  plate_type: str = ""
  plate_type_number: int = -1
  comments: str = ""
  read_only: bool = False
  stacker_enabled: bool = False
  use_stacker: bool = False
  entire_stack: bool = False
  stacker_plate_count: int = 10

  @property
  def device_entries(self) -> list[ProtocolEntry]:
    """The entries that operate the instrument, in file order."""
    return [entry for entry in self.entries if entry.is_device_step]

  def build_steps(self) -> list[Step]:
    """Read every entry that operates the instrument into a step, and keep the result.

    Entries that sequence the run are dropped rather than modelled, so a protocol using loops or
    delays runs its device steps in file order and nothing else. The entries themselves are still
    there to inspect.

    Returns:
      The steps, which are also stored on the protocol.

    Raises:
      ValueError: If an entry will not read, naming which one. That is what a protocol from
        another instrument family or an older release of the vendor's software looks like.
    """
    built = []
    for index, entry in enumerate(self.entries):
      if not entry.is_device_step:
        continue
      try:
        built.append(step_from_definition(entry.definition))
      except ValueError as error:
        raise ValueError(
          f"step {index} ({entry.step_type.name}) of {self.protocol_name or 'protocol'} "
          f"will not read: {error}"
        ) from error
    self.steps = built
    return built
