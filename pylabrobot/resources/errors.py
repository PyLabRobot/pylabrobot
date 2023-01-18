class ResourceNotFoundError(Exception):
  pass


class ContainerTooLittleLiquidError(Exception):
  """ Raised when trying to aspirate more liquid from a well than is still present. """


class ContainerTooLittleVolumeError(Exception):
  """ Raised when trying to dispense more liquid into a well than is still available. """


class TipTooLittleLiquidError(Exception):
  """ Raised when trying to dispense more liquid from a tip than is still present. """


class TipTooLittleVolumeError(Exception):
  """ Raised when trying to aspirate more liquid into a tip than is still available. """


class TipSpotHasTipError(Exception):
  """ Raised when a tip spot has a tip, e.g. when trying to drop a tip with a tip spot that has a
  tip. """


class TipSpotHasNoTipError(Exception):
  """ Raised when a tip spot has no tip, e.g. when trying to pick up a tip with a tip spot that does
  not have a tip. """
