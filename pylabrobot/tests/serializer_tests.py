import datetime
import json
import math

from pylabrobot.resources.hamilton.tip_creators import TIP_DIAMETER, TipSize
from pylabrobot.serializer import (
  deserialize,
  serialize,
)


def test_serialize_deserialize_closure():
  def outer(x):
    y = 10

    def inner():
      return x + y

    return inner

  closure = outer(5)
  serialized = serialize(closure)
  deserialized = deserialize(serialized, allow_marshal=True)

  assert closure() == deserialized()


def test_serialize_deserialize_cell():
  x = 42

  def func():
    return x

  assert func.__closure__ is not None
  cell = func.__closure__[0]
  serialized = serialize(cell)
  deserialized = deserialize(serialized)

  assert cell.cell_contents == deserialized.cell_contents


def test_serialize_deserialize_function_with_closure():
  x = 10

  def func(y):
    return x + y

  serialized = serialize(func)
  deserialized = deserialize(serialized, allow_marshal=True)

  assert func(5) == deserialized(5)


def test_serialize_deserialize_special_floats():
  assert deserialize(serialize(float("inf"))) == math.inf
  assert deserialize(serialize(float("-inf"))) == -math.inf
  result = deserialize(serialize(float("nan")))
  assert math.isnan(result)


def test_deserialize_calls_custom_deserialize_method():
  """Test that deserialize() calls a class's custom deserialize method when defined."""
  from pylabrobot.resources.tip import Tip
  from pylabrobot.resources.tip_rack import TipSpot

  def make_tip(name):
    return Tip(
      name=name,
      has_filter=False,
      maximal_volume=300,
      fitting_depth=8,
      diameter=TIP_DIAMETER[TipSize.STANDARD_VOLUME],
      size_z=50,
    )

  ts = TipSpot(name="A1", size_x=9, size_y=9, make_tip=make_tip)
  data = ts.serialize()

  # TipSpot.serialize() includes 'prototype_tip' which TipSpot.__init__ doesn't accept.
  # This only works because deserialize() calls TipSpot.deserialize() which handles it.
  assert "prototype_tip" in data
  result = deserialize(data)
  assert isinstance(result, TipSpot)
  assert result.name == "A1"


def test_serialize_dates() -> None:
  """Dates and datetimes serialize as ISO strings inside JSON containers."""
  data = {"dates": [datetime.date(2026, 9, 19), datetime.datetime(2026, 9, 19, 12, 30, 45)]}
  assert json.loads(json.dumps(serialize(data))) == {"dates": ["2026-09-19", "2026-09-19T12:30:45"]}
