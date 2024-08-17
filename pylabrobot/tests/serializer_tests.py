from pylabrobot.serializer import serialize, deserialize


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
