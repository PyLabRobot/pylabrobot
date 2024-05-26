from typing import Type, TypeVar, Optional

T = TypeVar("T")
def find_subclass(class_name: str, cls: Type[T]) -> Optional[Type[T]]:
  """ Recursively find a subclass with the correct name.

  Args:
    class_name: The name of the class to find.
    cls: The class to search in.

  Returns:
    The class with the given name, or `None` if no such class exists.
  """

  if cls.__name__ == class_name:
    return cls
  for subclass in cls.__subclasses__():
    subclass_ = find_subclass(class_name=class_name, cls=subclass)
    if subclass_ is not None:
      return subclass_
  return None
