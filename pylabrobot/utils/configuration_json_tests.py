import datetime
import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from pylabrobot.utils.configuration_json import restore, to_jsonable


@dataclass
class _Inner:
  when: datetime.date
  span: Tuple[float, float]


@dataclass
class _Outer:
  name: str
  inner: _Inner
  maybe: Optional[_Inner] = None
  values: Tuple[int, ...] = ()
  listed: List[_Inner] = field(default_factory=list)
  by_channel: Dict[int, float] = field(default_factory=dict)


def _through_json(value):
  return json.loads(json.dumps(to_jsonable(value)))


def test_a_nested_dataclass_comes_back_as_it_was_written():
  """Tuples, int dict keys and dates survive JSON because the declared field types put them back."""
  written = _Outer(
    name="prep",
    inner=_Inner(when=datetime.date(2026, 9, 16), span=(0.2, 299.2)),
    maybe=_Inner(when=datetime.date(2026, 1, 1), span=(1.0, 2.0)),
    values=(1, 2, 3),
    listed=[_Inner(when=datetime.date(2026, 2, 2), span=(3.0, 4.0))],
    by_channel={0: 171.784, 1: 171.091},
  )
  read = restore(_Outer, _through_json(written))
  assert read == written
  assert isinstance(read.values, tuple) and isinstance(read.inner.span, tuple)
  assert set(read.by_channel) == {0, 1}


def test_none_and_unknown_names_are_handled():
  """None stays None, and names the class no longer has are left out rather than failing."""
  saved = _through_json(
    _Outer(name="star", inner=_Inner(when=datetime.date(2026, 3, 3), span=(0.0, 1.0)))
  )
  saved["a_field_that_was_dropped"] = 42
  read = restore(_Outer, saved)
  assert read.maybe is None
  assert read.name == "star"
