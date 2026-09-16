import dataclasses
import datetime
import typing
from typing import Any, Union

# -- reading and writing these as JSON ------------------------------------------------------------
# JSON loses three things these configurations rely on: a tuple comes back a list, a dict key comes
# back a string, and a date comes back its own text. What each field is declared to be is enough to
# put all three back, so writing is `dataclasses.fields` and reading is the same walk against the
# declared types.


def to_jsonable(value: Any) -> Any:
  """The value as JSON holds it.

  Args:
    value: what to convert - a configuration, or anything one holds.

  Returns:
    The same value in types `json.dump` accepts.
  """
  if dataclasses.is_dataclass(value) and not isinstance(value, type):
    return {
      field.name: to_jsonable(getattr(value, field.name)) for field in dataclasses.fields(value)
    }
  if isinstance(value, datetime.date):
    return value.isoformat()
  if isinstance(value, (list, tuple)):
    return [to_jsonable(item) for item in value]
  if isinstance(value, dict):
    # Keys are written as text because JSON has no other kind. What they were is on the field.
    return {str(key): to_jsonable(item) for key, item in value.items()}
  return value


def _restore(hint: Any, value: Any) -> Any:
  """One value, back in the type its field is declared to hold.

  Args:
    hint: the declared type.
    value: the value as JSON held it.

  Returns:
    The value in the declared type.
  """
  if value is None:
    return None

  origin = typing.get_origin(hint)
  args = typing.get_args(hint)

  if origin is Union:  # Optional[X] is Union[X, None]; the None case returned above.
    declared = [arg for arg in args if arg is not type(None)]
    return _restore(declared[0], value) if len(declared) == 1 else value
  if origin is tuple:
    # Fixed-length tuples name a type per position; `Tuple[X, ...]` names one for all of them.
    if len(args) == 2 and args[1] is Ellipsis:
      return tuple(_restore(args[0], item) for item in value)
    return tuple(_restore(arg, item) for arg, item in zip(args, value))
  if origin is list:
    return [_restore(args[0], item) for item in value]
  if origin is dict:
    key_hint, value_hint = args
    return {_restore(key_hint, key): _restore(value_hint, item) for key, item in value.items()}
  if hint is int and isinstance(value, str):
    # A dict keyed by int: JSON wrote the key as text, and the field says what it was.
    return int(value)
  if hint is datetime.date:
    return datetime.date.fromisoformat(value)
  if dataclasses.is_dataclass(hint) and isinstance(hint, type):
    # A nested configuration: rebuilt field by field against what its own class declares. Names the
    # class does not have are left out, so a file written by a driver that has since dropped a
    # field still loads.
    field_types = typing.get_type_hints(hint)
    named = {field.name for field in dataclasses.fields(hint)}
    return hint(**{n: _restore(field_types[n], v) for n, v in value.items() if n in named})
  return value
