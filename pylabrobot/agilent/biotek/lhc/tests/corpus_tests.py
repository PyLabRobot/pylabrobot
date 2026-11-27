"""Reading the protocol files in ``test_data``.

Nothing exercises the readers like protocols a real installation ships: they carry every format
version this package reads, every instrument in the family, and step layouts no hand-written
definition would think of. The tree next to this file is a covering subset of one such installation
-- twenty-six files spanning eight instruments, every step-type layout, both older format markers
and every kind of entry a protocol can hold.

Two instruments in it are deliberately unreadable. Their step layouts are not this package's, and a
protocol written for one of them has to be refused rather than read into the wrong fields.

Set ``LHC_PROTOCOL_CORPUS`` to sweep a larger tree as well as this one.
"""

from __future__ import annotations

import importlib.util
import os
import re
from pathlib import Path

import pytest

from pylabrobot.agilent.biotek.lhc.devices import settings_document
from pylabrobot.agilent.biotek.lhc.devices.instrument_settings import InstrumentSettings
from pylabrobot.agilent.biotek.lhc.protocols.protocol import Protocol
from pylabrobot.agilent.biotek.lhc.protocols.read_write_utilities import encryption, protocol_file

PROTOCOLS = Path(__file__).parent / "test_data" / "protocols"
"""The covering subset that ships with these tests."""

EXTRA = os.environ.get("LHC_PROTOCOL_CORPUS", "")
"""A larger tree to sweep as well, for anyone who has one."""

UNREADABLE_INSTRUMENTS = ("50TS", "ELx405")
"""The instruments this package has no representation for, by the folder they are in."""

HAS_CIPHER = importlib.util.find_spec("Crypto") is not None
"""Whether the cipher a protocol file is stored under is installed."""

pytestmark = pytest.mark.skipif(
  not HAS_CIPHER,
  reason="reading a protocol file needs pycryptodome, which is an optional dependency",
)


def files() -> list[Path]:
  """Every protocol file to sweep.

  Returns:
    The paths, sorted so a failure names the same file every run.
  """
  found = sorted(PROTOCOLS.rglob("*.LHC"))
  if EXTRA and Path(EXTRA).is_dir():
    found += sorted(Path(EXTRA).rglob("*.LHC"))
  return found


def readable() -> list[Path]:
  """The files written for an instrument this package works.

  Returns:
    Those paths.
  """
  return [
    path
    for path in files()
    if not any(instrument in str(path) for instrument in UNREADABLE_INSTRUMENTS)
  ]


def unreadable() -> list[Path]:
  """The files written for an instrument this package has no representation for.

  Returns:
    Those paths.
  """
  return [
    path
    for path in files()
    if any(instrument in str(path) for instrument in UNREADABLE_INSTRUMENTS)
  ]


def test_the_tree_is_there_and_covers_the_family():
  """A file per instrument, so a change that breaks one instrument cannot pass unnoticed."""
  folders = {path.parent.name for path in files()}
  assert len(files()) >= 26
  assert {"EL406", "MultiFlo", "MultiFloFX", "405_TS_and_LS", "50TS", "ELx405"} <= folders


@pytest.mark.parametrize("path", files(), ids=lambda path: path.name)
def test_every_file_decrypts_and_parses(path: Path):
  """Whether its steps can be read or not, a file is readable, writable and inspectable."""
  protocol = protocol_file.read(path)
  assert protocol.entries
  assert protocol.protocol_name or protocol.instrument_name


@pytest.mark.parametrize("path", files(), ids=lambda path: path.name)
def test_the_entries_survive_being_written_again(path: Path):
  """The entries that sequence a run -- delays, loops, remarks -- are what a file stores rather
  than something this package models, so writing must not drop them."""
  protocol = protocol_file.read(path)
  again = protocol_file.from_xml(protocol_file.to_xml(protocol))
  assert again.entries == protocol.entries


@pytest.mark.parametrize("path", readable(), ids=lambda path: path.name)
def test_every_step_of_a_supported_instruments_protocol_reads(path: Path):
  """Every step of a supported instruments protocol reads."""
  protocol = protocol_file.read(path)
  assert protocol.build_steps() or not protocol.device_entries


@pytest.mark.parametrize("path", unreadable(), ids=lambda path: path.name)
def test_an_instrument_without_a_representation_is_refused(path: Path):
  """Reading one of these would put values in the wrong fields, so it has to fail."""
  protocol = protocol_file.read(path)
  with pytest.raises(ValueError):
    protocol.build_steps()


@pytest.mark.parametrize("path", readable(), ids=lambda path: path.name)
def test_every_step_that_reads_encodes_and_writes_itself_back(path: Path):
  """The two conversions a run needs: to the wire, and back to the text a file stores."""
  protocol = protocol_file.read(path)
  settings = _declared_settings(protocol)
  for step in protocol.build_steps():
    assert isinstance(step.to_bytes(settings), bytes)
    assert step.to_definition()


def _canonical(field: str) -> str:
  """One field of a definition, as it is written rather than as some release wrote it.

  Two fields are deliberately not carried through unchanged. A shake intensity is stored as free
  text and several releases abbreviate it, so it is written back in full. And a sub-step's own type
  field is not read at all -- a wash reads its sub-steps by position -- so a file that names the
  wrong type there is loaded anyway and written back with the right one.

  Args:
    field: The field as the file carried it.

  Returns:
    The field with those two normalisations applied, so what is left to compare is the values.
  """
  return field.split(" (")[0]


@pytest.mark.parametrize("path", readable(), ids=lambda path: path.name)
def test_a_step_read_and_written_keeps_every_field_the_file_carried(path: Path):
  """Filling in what an older release did not write must not move a field that is there."""
  protocol = protocol_file.read(path)
  for entry, step in zip(protocol.device_entries, protocol.build_steps()):
    parts = zip(entry.definition.split("#"), step.to_definition().split("#"))
    for index, (original, written) in enumerate(parts):
      carried = [field for field in original.split("|") if field]
      wrote = [field for field in written.split("|") if field]
      start = 1 if carried and carried[0].startswith("DV") else 0
      # A sub-step's own type field is ignored on the way in, so it is not compared on the way out.
      first = 1 if index else 0
      assert [_canonical(field) for field in wrote[1 + first : len(carried) - start + 1]] == [
        _canonical(field) for field in carried[start + first :]
      ]


def _declared_settings(protocol: Protocol) -> InstrumentSettings:
  """What the protocol says the instrument was, or a default when it says nothing readable.

  Args:
    protocol: The protocol to read.

  Returns:
    The settings to encode against.
  """
  document = protocol.instrument_settings_xml.strip()
  if not document:
    return InstrumentSettings()
  try:
    return settings_document.from_xml(document)
  except ValueError:
    return InstrumentSettings()


SAVED_BY = re.compile(r"[ ]*<(AuditTrail|ArchiveRevision)\b.*?(?:</\1>|/>)\r?\n?", re.S)
"""The records of who saved a protocol, from where and when.

They are read but never written: inventing one would fabricate the record of who touched a protocol,
so a file this package writes carries none.
"""

NOT_MODELLED = re.compile(
  r"[ ]*<(BioStackUseLids|BioStackLidDefinition|BioStackLidName|PlateHeightOverride)\b"
  r".*?(?:</\1>|/>)\r?\n?",
  re.S,
)
"""Elements a later release added that a protocol here has no field for, so they are not written."""


@pytest.mark.parametrize("path", files(), ids=lambda path: path.name)
def test_a_file_is_written_back_exactly_as_it_came(path: Path):
  """The document has to match what wrote it, down to the element order, the empty-element form and
  the carriage returns, because the instrument's own software has to be able to read it again.

  Two things do not survive, both on purpose and both named above: the records of who saved the
  protocol, and the elements a later release added that nothing here models.
  """
  document = encryption.decrypt(path.read_bytes()).strip().lstrip("\ufeff")
  again = protocol_file.to_xml(protocol_file.from_xml(document))
  assert NOT_MODELLED.sub("", SAVED_BY.sub("", document)) == again


@pytest.mark.parametrize("path", files(), ids=lambda path: path.name)
def test_the_multi_line_elements_keep_their_carriage_returns(path: Path):
  """Reading an element's text normalises line endings away, and a file stores them, so what is
  read has to put them back -- the fitted-options document, the comments and the older prose form of
  the options all run to several lines."""
  protocol = protocol_file.read(path)
  for text in (protocol.instrument_settings_xml, protocol.comments, protocol.instrument_settings):
    assert "\n" not in text.replace("\r\n", ""), path
