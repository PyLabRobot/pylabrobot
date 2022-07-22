"""`pyhamilton`-specific exception definitions.  """

from pyhamilton.liquid_handling.backends.hamilton.errors import VENUSError
# for legacy, import all errors here
from pyhamilton.liquid_handling.backends.hamilton.errors import *

###########################################
### BEGIN HAMILTON DECK RESOURCE ERRORS ###
###########################################

class HamiltonDeckResourceError(VENUSError):
    """
    Error with any deck object in interface with robot.
    """
    pass

class ResourceUnavailableError(HamiltonDeckResourceError):
    """
    Layout manager found deck resource type not present or all of this type assigned
    """
    pass

#######################################
### BEGIN HAMILTON INTERFACE ERRORS ###
#######################################

class HamiltonInterfaceError(VENUSError):
    """
    Error in any phase of communication with robot.
    """
    pass

class HamiltonTimeoutError(HamiltonInterfaceError):
    """
    An asynchronous request to the Hamilton robot timed out.
    """
    pass

class InvalidErrCodeError(HamiltonInterfaceError):
    """
    Error code returned from instrument not known.
    """
    pass

class HamiltonReturnParseError(HamiltonInterfaceError):
    """
    Return string from instrument was malformed.
    """
    pass
