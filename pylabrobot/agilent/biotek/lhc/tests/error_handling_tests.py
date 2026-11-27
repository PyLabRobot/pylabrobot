"""What an error code means, and which exception it becomes.

A code is a bit field rather than an index: parts of it name which motor or which link failed, and
what a code means depends on the instrument family. So the table is checked structurally -- every
code classifies, the ranges classify as the kind of thing they are, and the same code can read
differently on two models.
"""

from __future__ import annotations

import pytest

from pylabrobot.agilent.biotek.lhc.enums.instrument.instrument_family import InstrumentFamily
from pylabrobot.agilent.biotek.lhc.error_handling import (
  NO_ERROR,
  NOT_ACKNOWLEDGED,
  PORT_WOULD_NOT_OPEN,
  REPLY_TIMED_OUT,
  WRITE_FAILED,
  BiotekError,
  ErrorKind,
  LinkError,
  RejectedError,
  classify,
  describe,
  error_message,
  fail,
  info_for,
  normalize,
  raise_for_status,
)

TRANSPORT_CODES = (WRITE_FAILED, NOT_ACKNOWLEDGED, REPLY_TIMED_OUT, PORT_WOULD_NOT_OPEN)
"""The failures this package finds itself, which share the table with the instrument's own."""


class TestEveryCode:
  """Properties that have to hold across the whole space, since a code is a bit field."""

  def test_every_code_classifies(self):
    """A code with no row still has to say what kind of thing it is, so a caller can decide what to
    do without a table lookup of its own."""
    for code in range(0, 0x10000, 7):
      assert isinstance(classify(code), ErrorKind)

  def test_every_code_has_a_message(self):
    """Every code has a message."""
    for code in range(0, 0x10000, 101):
      assert isinstance(error_message(code), str)

  def test_a_code_with_no_row_has_no_message_rather_than_a_made_up_one(self):
    """A code with no row has no message rather than a made up one."""
    assert error_message(0x0001) == ""

  def test_success_is_not_an_error(self):
    """Success is not an error."""
    assert classify(NO_ERROR) is ErrorKind.NONE
    raise_for_status(NO_ERROR)

  @pytest.mark.parametrize("code, unsigned", [(-1, 0xFFFF), (-2, 0xFFFE)])
  def test_a_negative_code_is_read_unsigned(self, code: int, unsigned: int):
    """Some commands report the status as a signed word, and the table is indexed unsigned."""
    assert normalize(code) == unsigned

  @pytest.mark.parametrize(
    "code, kind",
    [
      (0x0201, ErrorKind.MOTOR),
      (0x8110, ErrorKind.LINK),
      (0x6029, ErrorKind.REJECTED),
      (0x4001, ErrorKind.STORAGE),
    ],
  )
  def test_a_range_classifies_as_the_kind_of_thing_it_is(self, code: int, kind: ErrorKind):
    """Which range a code falls in is what says whether a retry, a person or a different request is
    what it needs."""
    assert classify(code) is kind


class TestWhichExceptionACodeBecomes:
  """The class is the caller's decision, so the mapping is what matters."""

  def test_success_raises_nothing(self):
    """Success raises nothing."""
    raise_for_status(0)

  def test_a_refused_request_is_its_own_kind(self):
    """A refused request is its own kind."""
    with pytest.raises(RejectedError):
      raise_for_status(0x6029)

  @pytest.mark.parametrize("code", TRANSPORT_CODES)
  def test_a_transport_failure_is_a_link_failure(self, code: int):
    """These share the numbering space with the instrument's own faults, and they are the one kind
    where sending the same thing again is reasonable."""
    with pytest.raises(LinkError):
      raise_for_status(code)

  def test_every_exception_is_one_of_ours(self):
    """Every exception is one of ours."""
    for code in range(0x6000, 0x6100, 3):
      try:
        raise_for_status(code)
      except BiotekError as error:
        assert isinstance(error, BiotekError)
        assert error.code == normalize(code)

  def test_a_failure_this_package_found_itself_carries_no_code(self):
    """A failure this package found itself carries no code."""
    error = fail(ErrorKind.LINK, "the port went away", operation="write")
    assert error.code == NO_ERROR
    assert "the port went away" in str(error)
    assert "write" in str(error)


class TestWhatAFailureSays:
  """The text a caller sees, which has to name what was being attempted."""

  def test_a_failure_names_the_operation_and_the_code(self):
    """A failure names the operation and the code."""
    info = info_for(0x6029, InstrumentFamily.EL406, operation="opening the batch")
    assert "opening the batch" in str(info)
    assert "0x6029" in str(info)

  def test_a_description_carries_the_code_and_its_message(self):
    """A description carries the code and its message."""
    assert "6029" in describe(0x6029) or "24617" in describe(0x6029)

  def test_the_family_changes_what_a_motor_code_means(self):
    """Part of a motor code is which motor, and the models do not number their motors the same way,
    so the same code names different hardware on different instruments."""
    original = error_message(0x0202, InstrumentFamily.EL406)
    wash_only = error_message(0x0202, InstrumentFamily.MODEL_405_TS)
    assert "Dispense Head" in original
    assert "Washer Head" in wash_only
    assert original != wash_only

  def test_most_motor_codes_read_the_same_on_every_model(self):
    """Only the motors a model does not have are renamed, so a shared one reads alike."""
    families = list(InstrumentFamily)
    assert len({error_message(0x0201, family) for family in families}) == 1
