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
