class ResourceNotFoundError(Exception):
  pass


class TooLittleLiquidError(Exception):
  """ Raised when trying to aspirate more liquid from a container than is still present. """


class TooLittleVolumeError(Exception):
  """ Raised when trying to dispense more liquid into a container than is still available. """


class HasTipError(Exception):
  """ Raised when a tip already exists in a location where a tip is being added. """


class NoTipError(Exception):
  """ Raised when a tip was expected but none was found. """


class CrossContaminationError(Exception):
  """ Raised when attempting to aspirate from a well with a tip that has touched a different liquid.
  """


class ResourceDefinitionIncompleteError(Exception):
  """ Raised when trying to access a resource that has not been defined or is not complete.

  We have some "phantom" resources that have a name and creator function, but are missing some
  information, or they don't have enough metadata to uniquely identify them. This means they are
  effectively useless. These resources often originate from a database import (like venus) that is
  incomplete.

  This error is raised when you try to create a resource like that. Please create a PR to list the
  resource catalog number (or equivalent), or measure and contribute the missing information. Please
  create an issue if you need help with this.

  Tracking the general problem in https://github.com/PyLabRobot/pylabrobot/issues/170.
  """

  def __init__(self, resource_name: str):
    super().__init__(f"Resource '{resource_name}' is incomplete and cannot be used. "
                      "Please create a PR to complete this resource, or create an issue if you "
                      "need help. https://github.com/PyLabRobot/pylabrobot")
