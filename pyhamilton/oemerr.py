"""`pyhamilton`-specific exception definitions.
"""
class HamiltonError(Exception):
    """
    Exceptions raised in package pyhamilton
    """
    pass

###########################################
### BEGIN HAMILTON DECK RESOURCE ERRORS ###
###########################################

class HamiltonDeckResourceError(HamiltonError):
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

class HamiltonInterfaceError(HamiltonError):
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

########################################################
### BEGIN HAMILTON CODED STEP ERRORS, CODE MAP BELOW ###
########################################################

class HamiltonStepError(HamiltonError):
    """
    Errors in steps executed by VENUS software coded in the Hamilton error specification.
    """
    pass

class HamiltonSyntaxError(HamiltonStepError):
    """
    There is a wrong set of parameters or parameter ranges.
    """
    pass
 
class HardwareError(HamiltonStepError):
    """
    Steps lost on one or more hardware components, or component not initialized or not functioning.
    """
    pass
 
class NotExecutedError(HamiltonStepError):
    """
    There was an error in previous part command.
    """
    pass

class ClotError(HamiltonStepError):
    """
    Blood clot detected.
    """
    pass

class BarcodeError(HamiltonStepError):
    """
    Barcode could not be read or is missing.
    """
    pass

class InsufficientLiquidError(HamiltonStepError):
    """
    Not enough liquid available.
    """
    pass

class TipPresentError(HamiltonStepError):
    """
    A tip has already been picked up.
    """
    pass

class NoTipError(HamiltonStepError):
    """
    Tip is missing or not picked up.
    """
    pass

class NoCarrierError(HamiltonStepError):
    """
    No carrier present for loading.
    """
    pass

class ExecutionError(HamiltonStepError):
    """
    A step or a part of a step could not be processed.
    """
    pass

class PressureLLDError(HamiltonStepError):
    """
    A dispense with pressure liquid level detection is not allowed.
    """
    pass

class CalibrateError(HamiltonStepError):
    """
    No capacitive signal detected during carrier calibration procedure.
    """
    pass

class UnloadError(HamiltonStepError):
    """
    Not possible to unload the carrier due to occupied loading tray position.
    """
    pass

class PressureLLDError(HamiltonStepError):
    """
    Pressure liquid level detection in a consecutive aspiration is not allowed.
    """
    pass

class ParameterError(HamiltonStepError):
    """
    Dispense in jet mode with pressure liquid level detection is not allowed.
    """
    pass

class CoverOpenError(HamiltonStepError):
    """
    Cover not closed or can not be locked.
    """
    pass

class ImproperAspirationOrDispenseError(HamiltonStepError):
    """
    The pressure-based aspiration / dispensation control reported an error ( not enough liquid ).
    """
    pass

class WashLiquidError(HamiltonStepError):
    """
    Waste full or no more wash liquid available.
    """
    pass

class TemperatureError(HamiltonStepError):
    """
    Incubator temperature out of range.
    """
    pass

class TADMOvershotError(HamiltonStepError):
    """
    Overshot of limits during aspirate or dispense.

    Note:

    On aspirate this error is returned as main error 17.

    On dispense this error is returned as main error 4.
    """
    pass

class LabwareError(HamiltonStepError):
    """
    Labware not available.
    """
    pass

class LabwareGrippedError(HamiltonStepError):
    """
    Labware already gripped.
    """
    pass

class LabwareLostError(HamiltonStepError):
    """
    Labware lost during transport.
    """
    pass

class IllegalTargetPlatePositionError(HamiltonStepError):
    """
    Cannot place plate, plate was gripped in a wrong direction.
    """
    pass

class IllegalInterventionError(HamiltonStepError):
    """
    Cover was opened or a carrier was removed manually.
    """
    pass

class TADMUndershotError(HamiltonStepError):
    """
    Undershot of limits during aspirate or dispense.

    Note:

    On aspirate this error is returned as main error 4.

    On dispense this error is returned as main error 17.
    """
    pass

class PositionError(HamiltonStepError):
    """
    The position is out of range.
    """
    pass

class UnexpectedcLLDError(HamiltonStepError):
    """
    The cLLD detected a liquid level above start height of liquid level search.
    """
    pass

class AreaAlreadyOccupiedError(HamiltonStepError):
    """
    Instrument region already reserved.
    """
    pass

class ImpossibleToOccupyAreaError(HamiltonStepError):
    """
    A region on the instrument cannot be reserved.
    """
    pass

class AntiDropControlError(HamiltonStepError):
    """
    Anti drop controlling out of tolerance.
    """
    pass

class DecapperError(HamiltonStepError):
    """
    Decapper lock error while screw / unscrew a cap by twister channels.
    """
    pass

class DecapperHandlingError(HamiltonStepError):
    """
    Decapper station error while lock / unlock a cap.
    """
    pass

class SlaveError(HamiltonStepError):
    """
    Slave error.
    """
    pass

class WrongCarrierError(HamiltonStepError):
    """
    Wrong carrier barcode detected.
    """
    pass

class NoCarrierBarcodeError(HamiltonStepError):
    """
    Carrier barcode could not be read or is missing.
    """
    pass

class LiquidLevelError(HamiltonStepError):
    """
    Liquid surface not detected.

    This error is created from main / slave error 06/70, 06/73 and 06/87.
    """
    pass

class NotDetectedError(HamiltonStepError):
    """
    Carrier not detected at deck end position.
    """
    pass

class NotAspiratedError(HamiltonStepError):
    """
    Dispense volume exceeds the aspirated volume.

    This error is created from main / slave error 02/54.
    """
    pass

class ImproperDispensationError(HamiltonStepError):
    """
    The dispensed volume is out of tolerance (may only occur for Nano Pipettor Dispense steps). 

    This error is created from main / slave error 02/52 and 02/54.
    """
    pass

class NoLabwareError(HamiltonStepError):
    """
    The labware to be loaded was not detected by autoload module.

    Note:

    May only occur on a Reload Carrier step if the labware property 'MlStarCarPosAreRecognizable' is set to 1.
    """
    pass

class UnexpectedLabwareError(HamiltonStepError):
    """
    The labware contains unexpected barcode ( may only occur on a Reload Carrier step ).
    """
    pass

class WrongLabwareError(HamiltonStepError):
    """
    The labware to be reloaded contains wrong barcode ( may only occur on a Reload Carrier step ).
    """
    pass

class BarcodeMaskError(HamiltonStepError):
    """
    The barcode read doesn't match the barcode mask defined.
    """
    pass

class BarcodeNotUniqueError(HamiltonStepError):
    """
    The barcode read is not unique. Previously loaded labware with same barcode was loaded without unique barcode check.
    """
    pass

class BarcodeAlreadyUsedError(HamiltonStepError):
    """
    The barcode read is already loaded as unique barcode ( it's not possible to load the same barcode twice ).
    """
    pass

class KitLotExpiredError(HamiltonStepError):
    """
    Kit Lot expired.
    """
    pass

class DelimiterError(HamiltonStepError):
    """
    Barcode contains character which is used as delimiter in result string.
    """
    pass


HAMILTON_ERROR_MAP = { 
    1: HamiltonSyntaxError,
    2: HardwareError,
    3: NotExecutedError,
    4: ClotError,
    5: BarcodeError,
    6: InsufficientLiquidError,
    7: TipPresentError,
    8: NoTipError,
    9: NoCarrierError,
    10: ExecutionError,
    11: PressureLLDError,
    12: CalibrateError,
    13: UnloadError,
    14: PressureLLDError,
    15: ParameterError,
    16: CoverOpenError,
    17: ImproperAspirationOrDispenseError,
    18: WashLiquidError,
    19: TemperatureError,
    20: TADMOvershotError,
    21: LabwareError,
    22: LabwareGrippedError,
    23: LabwareLostError,
    24: IllegalTargetPlatePositionError,
    25: IllegalInterventionError,
    26: TADMUndershotError,
    27: PositionError,
    28: UnexpectedcLLDError,
    29: AreaAlreadyOccupiedError,
    30: ImpossibleToOccupyAreaError,
    31: AntiDropControlError,
    32: DecapperError,
    33: DecapperHandlingError,
    99: SlaveError,
    100: WrongCarrierError,
    101: NoCarrierBarcodeError,
    102: LiquidLevelError,
    103: NotDetectedError,
    104: NotAspiratedError,
    105: ImproperDispensationError,
    106: NoLabwareError,
    107: UnexpectedLabwareError,
    108: WrongLabwareError,
    109: BarcodeMaskError,
    110: BarcodeNotUniqueError,
    111: BarcodeAlreadyUsedError,
    112: KitLotExpiredError,
    113: DelimiterError
}
"""
Maps integer error codes from Hamilton step return data to the appropriate `pyhamilton` errors
"""

