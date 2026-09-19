"""Reading a protocol out of the document a protocol file holds, and writing one back.

The document handling needs nothing but text. Encryption is what a real file adds on top, and those
tests skip themselves where the cipher is not installed, since it is an optional dependency.
"""

from __future__ import annotations

import pytest

from pylabrobot.agilent.biotek.lhc.enums.steps.step_action import StepAction
from pylabrobot.agilent.biotek.lhc.enums.steps.step_type import StepType
from pylabrobot.agilent.biotek.lhc.protocols.protocol import Protocol
from pylabrobot.agilent.biotek.lhc.protocols.read_write_utilities import protocol_file
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_interface import Step
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.manifold_prime import ManifoldPrime

PRIME = "DV103|9|A|40|9|True|5|False|00:05"
"""One prime, as a protocol file stores it."""

DOCUMENT = (
  '<?xml version="1.0" encoding="utf-8"?>\r\n'
  "<ProgramProtocol"
  ' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
  ' xmlns:xsd="http://www.w3.org/2001/XMLSchema">\r\n'
  "  <LHCVersion>2.22.6</LHCVersion>\r\n"
  "  <InstrumentName>EL406</InstrumentName>\r\n"
  "  <ComPort>COM1</ComPort>\r\n"
  "  <ProtocolName>RINSE</ProtocolName>\r\n"
  "  <PlateType>96 Well Plate</PlateType>\r\n"
  "  <PlateTypeEnum>4</PlateTypeEnum>\r\n"
  "  <Step>\r\n"
  "    <Action>eStepActionRemark</Action>\r\n"
  "    <Type>-1</Type>\r\n"
  "    <Definition>Rinse the manifold</Definition>\r\n"
  "  </Step>\r\n"
  "  <Step>\r\n"
  "    <Action>eStepActionCustom</Action>\r\n"
  "    <Type>9</Type>\r\n"
  f"    <Definition>{PRIME}</Definition>\r\n"
  "  </Step>\r\n"
  "</ProgramProtocol>"
)
"""A protocol carrying one remark and one step that operates the instrument."""


class TestReadingADocument:
  """What a protocol file's own records become."""

  def test_the_protocol_carries_what_the_file_says_about_itself(self):
    """The protocol carries what the file says about itself."""
    protocol = protocol_file.from_xml(DOCUMENT)
    assert protocol.protocol_name == "RINSE"
    assert protocol.instrument_name == "EL406"
    assert protocol.lhc_version == "2.22.6"
    assert protocol.plate_type == "96 Well Plate"
    assert protocol.plate_type_number == 4

  def test_every_entry_is_kept_including_the_ones_that_do_not_operate_the_instrument(self):
    """Every entry is kept including the ones that do not operate the instrument."""
    protocol = protocol_file.from_xml(DOCUMENT)
    assert len(protocol.entries) == 2
    assert protocol.entries[0].action is StepAction.REMARK
    assert protocol.entries[1].action is StepAction.CUSTOM

  def test_only_the_entries_that_operate_the_instrument_are_steps(self):
    """Only the entries that operate the instrument are steps."""
    protocol = protocol_file.from_xml(DOCUMENT)
    assert len(protocol.device_entries) == 1
    assert protocol.device_entries[0].step_type is StepType.MANIFOLD_PRIME

  def test_reading_a_file_leaves_the_steps_alone_until_they_are_asked_for(self):
    """A file whose steps cannot be read is still readable, writable and inspectable."""
    protocol = protocol_file.from_xml(DOCUMENT)
    assert protocol.steps == []
    built = protocol.build_steps()
    assert len(built) == 1
    assert isinstance(built[0], ManifoldPrime)
    assert protocol.steps == built

  def test_an_entry_that_will_not_read_names_itself(self):
    """Which is what a protocol from another instrument family looks like."""
    protocol = protocol_file.from_xml(DOCUMENT.replace(PRIME, "DV103|9|A|40"))
    with pytest.raises(ValueError, match="MANIFOLD_PRIME"):
      protocol.build_steps()

  def test_an_unknown_action_is_refused(self):
    """An unknown action is refused."""
    document = DOCUMENT.replace("eStepActionRemark", "eStepActionSomethingElse")
    with pytest.raises(ValueError, match="unknown step action"):
      protocol_file.from_xml(document)

  def test_text_that_is_not_a_protocol_is_refused(self):
    """Text that is not a protocol is refused."""
    with pytest.raises(ValueError, match="not a protocol document"):
      protocol_file.from_xml("<nonsense")


class TestWritingADocument:
  """Turning a protocol back into the text a file holds."""

  def test_a_document_round_trips(self):
    """A document round trips."""
    protocol = protocol_file.from_xml(DOCUMENT)
    assert protocol_file.from_xml(protocol_file.to_xml(protocol)).entries == protocol.entries

  def test_the_entries_that_sequence_a_run_survive_being_written_again(self):
    """They are what a file stores rather than something this package models, so writing must not
    drop them."""
    protocol = protocol_file.from_xml(DOCUMENT)
    written = protocol_file.to_xml(protocol)
    assert "eStepActionRemark" in written
    assert "Rinse the manifold" in written

  def test_a_protocol_built_from_steps_gets_entries_for_them(self):
    """A protocol built from steps gets entries for them."""
    steps: list[Step] = [ManifoldPrime(volume=40_000)]
    entries = protocol_file.entries_for(steps)
    assert len(entries) == 1
    assert entries[0].action is StepAction.CUSTOM
    assert entries[0].step_type is StepType.MANIFOLD_PRIME
    assert entries[0].definition.startswith("DV103|9|")

  def test_a_protocol_with_no_entries_is_written_from_its_steps(self):
    """A protocol with no entries is written from its steps."""
    built: list[Step] = [ManifoldPrime(volume=40_000)]
    protocol = Protocol(steps=built, protocol_name="BUILT")
    written = protocol_file.to_xml(protocol)
    assert "BUILT" in written
    assert "eStepActionCustom" in written


class TestTheCipher:
  """What a real file adds on top of the document."""

  def test_a_document_survives_being_encrypted_and_read_back(self):
    """A document survives being encrypted and read back."""
    encryption = pytest.importorskip(
      "pylabrobot.agilent.biotek.lhc.protocols.read_write_utilities.encryption"
    )
    pytest.importorskip("Crypto", reason="the protocol-file cipher is an optional dependency")
    assert encryption.decrypt(encryption.encrypt(DOCUMENT)) == DOCUMENT

  def test_reading_a_file_needs_the_cipher_and_says_so(self):
    """The dependency is optional, so a missing one has to be an explanation rather than an import
    error out of nowhere."""
    from pylabrobot.agilent.biotek.lhc.protocols.read_write_utilities import encryption

    if encryption.HAS_PYCRYPTODOME:
      pytest.skip("the cipher is installed, so there is no missing-dependency message to check")
    with pytest.raises(RuntimeError, match="pycryptodome"):
      encryption.decrypt(b"whatever")


class TestARoundTripThroughEverything:
  """A protocol built here, written, read back, and run against the same instrument."""

  def test_steps_survive_a_write_and_a_read(self):
    """Steps survive a write and a read."""
    original: list[Step] = [ManifoldPrime(volume=40_000, buffer="C", flow_rate=5)]
    protocol = Protocol(steps=original, entries=protocol_file.entries_for(original))
    read_back = protocol_file.from_xml(protocol_file.to_xml(protocol))
    assert read_back.build_steps() == original
