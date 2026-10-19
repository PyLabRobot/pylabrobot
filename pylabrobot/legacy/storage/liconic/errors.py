from typing import Dict, Tuple, Type

from pylabrobot.legacy.storage.liconic.constants import ControllerError, HandlingError
from pylabrobot.liconic.errors import (
  CONTROLLER_ERRORS as _CONTROLLER_ERRORS,
)
from pylabrobot.liconic.errors import (
  HANDLING_ERRORS as _HANDLING_ERRORS,
)


class LiconicControllerRelayError(Exception):
  pass


class LiconicControllerCommandError(Exception):
  pass


class LiconicControllerProgramError(Exception):
  pass


class LiconicControllerHardwareError(Exception):
  pass


class LiconicControllerWriteProtectedError(Exception):
  pass


class LiconicControllerBaseUnitError(Exception):
  pass


controller_error_map: Dict[ControllerError, Tuple[Type[Exception], str]] = {
  ControllerError.RELAY_ERROR: (
    LiconicControllerRelayError,
    _CONTROLLER_ERRORS[ControllerError.RELAY_ERROR.value],
  ),
  ControllerError.COMMAND_ERROR: (
    LiconicControllerCommandError,
    _CONTROLLER_ERRORS[ControllerError.COMMAND_ERROR.value],
  ),
  ControllerError.PROGRAM_ERROR: (
    LiconicControllerProgramError,
    _CONTROLLER_ERRORS[ControllerError.PROGRAM_ERROR.value],
  ),
  ControllerError.HARDWARE_ERROR: (
    LiconicControllerHardwareError,
    _CONTROLLER_ERRORS[ControllerError.HARDWARE_ERROR.value],
  ),
  ControllerError.WRITE_PROTECTED_ERROR: (
    LiconicControllerWriteProtectedError,
    _CONTROLLER_ERRORS[ControllerError.WRITE_PROTECTED_ERROR.value],
  ),
  ControllerError.BASE_UNIT_ERROR: (
    LiconicControllerBaseUnitError,
    _CONTROLLER_ERRORS[ControllerError.BASE_UNIT_ERROR.value],
  ),
}


class LiconicHandlerPlateRemoveError(Exception):
  pass


class LiconicHandlerBarcodeReadError(Exception):
  pass


class LiconicHandlerPlatePlaceError(Exception):
  pass


class LiconicHandlerPlateSetError(Exception):
  pass


class LiconicHandlerPlateGetError(Exception):
  pass


class LiconicHandlerImportPlateError(Exception):
  pass


class LiconicHandlerExportPlateError(Exception):
  pass


class LiconicHandlerGeneralError(Exception):
  pass


handler_error_map: Dict[HandlingError, Tuple[Type[Exception], str]] = {
  HandlingError.GENERAL_HANDLING_ERROR: (
    LiconicHandlerGeneralError,
    _HANDLING_ERRORS[HandlingError.GENERAL_HANDLING_ERROR.value],
  ),
  HandlingError.GATE_OPEN_ERROR: (
    LiconicHandlerGeneralError,
    _HANDLING_ERRORS[HandlingError.GATE_OPEN_ERROR.value],
  ),
  HandlingError.GATE_CLOSE_ERROR: (
    LiconicHandlerGeneralError,
    _HANDLING_ERRORS[HandlingError.GATE_CLOSE_ERROR.value],
  ),
  HandlingError.GENERAL_LIFT_POSITIONING_ERROR: (
    LiconicHandlerGeneralError,
    _HANDLING_ERRORS[HandlingError.GENERAL_LIFT_POSITIONING_ERROR.value],
  ),
  HandlingError.USER_ACCESS_ERROR: (
    LiconicHandlerGeneralError,
    _HANDLING_ERRORS[HandlingError.USER_ACCESS_ERROR.value],
  ),
  HandlingError.STACKER_SLOT_ERROR: (
    LiconicHandlerGeneralError,
    _HANDLING_ERRORS[HandlingError.STACKER_SLOT_ERROR.value],
  ),
  HandlingError.REMOTE_ACCESS_LEVEL_ERROR: (
    LiconicHandlerGeneralError,
    _HANDLING_ERRORS[HandlingError.REMOTE_ACCESS_LEVEL_ERROR.value],
  ),
  HandlingError.PLATE_TRANSFER_DETECTION_ERROR: (
    LiconicHandlerGeneralError,
    _HANDLING_ERRORS[HandlingError.PLATE_TRANSFER_DETECTION_ERROR.value],
  ),
  HandlingError.LIFT_INITIALIZATION_ERROR: (
    LiconicHandlerGeneralError,
    _HANDLING_ERRORS[HandlingError.LIFT_INITIALIZATION_ERROR.value],
  ),
  HandlingError.PLATE_ON_SHOVEL_DETECTION: (
    LiconicHandlerGeneralError,
    _HANDLING_ERRORS[HandlingError.PLATE_ON_SHOVEL_DETECTION.value],
  ),
  HandlingError.NO_PLATE_ON_SHOVEL_DETECTION: (
    LiconicHandlerGeneralError,
    _HANDLING_ERRORS[HandlingError.NO_PLATE_ON_SHOVEL_DETECTION.value],
  ),
  HandlingError.NO_RECOVERY: (
    LiconicHandlerGeneralError,
    _HANDLING_ERRORS[HandlingError.NO_RECOVERY.value],
  ),
  HandlingError.IMPORT_PLATE_STACKER_POSITIONING_ERROR: (
    LiconicHandlerImportPlateError,
    _HANDLING_ERRORS[HandlingError.IMPORT_PLATE_STACKER_POSITIONING_ERROR.value],
  ),
  HandlingError.IMPORT_PLATE_HANDLER_TRANSFER_TURN_OUT_ERROR: (
    LiconicHandlerImportPlateError,
    _HANDLING_ERRORS[HandlingError.IMPORT_PLATE_HANDLER_TRANSFER_TURN_OUT_ERROR.value],
  ),
  HandlingError.IMPORT_PLATE_SHOVEL_TRANSFER_OUTER_ERROR: (
    LiconicHandlerImportPlateError,
    _HANDLING_ERRORS[HandlingError.IMPORT_PLATE_SHOVEL_TRANSFER_OUTER_ERROR.value],
  ),
  HandlingError.IMPORT_PLATE_LIFT_TRANSFER_ERROR: (
    LiconicHandlerImportPlateError,
    _HANDLING_ERRORS[HandlingError.IMPORT_PLATE_LIFT_TRANSFER_ERROR.value],
  ),
  HandlingError.IMPORT_PLATE_SHOVEL_TRANSFER_INNER_ERROR: (
    LiconicHandlerImportPlateError,
    _HANDLING_ERRORS[HandlingError.IMPORT_PLATE_SHOVEL_TRANSFER_INNER_ERROR.value],
  ),
  HandlingError.IMPORT_PLATE_HANDLER_TRANSFER_TURN_IN_ERROR: (
    LiconicHandlerImportPlateError,
    _HANDLING_ERRORS[HandlingError.IMPORT_PLATE_HANDLER_TRANSFER_TURN_IN_ERROR.value],
  ),
  HandlingError.IMPORT_PLATE_LIFT_STACKER_TRAVEL_ERROR: (
    LiconicHandlerImportPlateError,
    _HANDLING_ERRORS[HandlingError.IMPORT_PLATE_LIFT_STACKER_TRAVEL_ERROR.value],
  ),
  HandlingError.IMPORT_PLATE_SHOVEL_STACKER_FRONT_ERROR: (
    LiconicHandlerImportPlateError,
    _HANDLING_ERRORS[HandlingError.IMPORT_PLATE_SHOVEL_STACKER_FRONT_ERROR.value],
  ),
  HandlingError.IMPORT_PLATE_LIFT_STACKER_PLACE_ERROR: (
    LiconicHandlerImportPlateError,
    _HANDLING_ERRORS[HandlingError.IMPORT_PLATE_LIFT_STACKER_PLACE_ERROR.value],
  ),
  HandlingError.IMPORT_PLATE_SHOVEL_STACKER_INNER_ERROR: (
    LiconicHandlerImportPlateError,
    _HANDLING_ERRORS[HandlingError.IMPORT_PLATE_SHOVEL_STACKER_INNER_ERROR.value],
  ),
  HandlingError.IMPORT_PLATE_LIFT_TRAVEL_BACK_ERROR: (
    LiconicHandlerImportPlateError,
    _HANDLING_ERRORS[HandlingError.IMPORT_PLATE_LIFT_TRAVEL_BACK_ERROR.value],
  ),
  HandlingError.IMPORT_PLATE_LIFT_INIT_ERROR: (
    LiconicHandlerImportPlateError,
    _HANDLING_ERRORS[HandlingError.IMPORT_PLATE_LIFT_INIT_ERROR.value],
  ),
  HandlingError.EXPORT_PLATE_LIFT_STACKER_TRAVEL_ERROR: (
    LiconicHandlerExportPlateError,
    _HANDLING_ERRORS[HandlingError.EXPORT_PLATE_LIFT_STACKER_TRAVEL_ERROR.value],
  ),
  HandlingError.EXPORT_PLATE_SHOVEL_STACKER_FRONT_ERROR: (
    LiconicHandlerExportPlateError,
    _HANDLING_ERRORS[HandlingError.EXPORT_PLATE_SHOVEL_STACKER_FRONT_ERROR.value],
  ),
  HandlingError.EXPORT_PLATE_LIFT_STACKER_IMPORT_ERROR: (
    LiconicHandlerExportPlateError,
    _HANDLING_ERRORS[HandlingError.EXPORT_PLATE_LIFT_STACKER_IMPORT_ERROR.value],
  ),
  HandlingError.EXPORT_PLATE_SHOVEL_STACKER_INNER_ERROR: (
    LiconicHandlerExportPlateError,
    _HANDLING_ERRORS[HandlingError.EXPORT_PLATE_SHOVEL_STACKER_INNER_ERROR.value],
  ),
  HandlingError.EXPORT_PLATE_LIFT_TRANSFER_POSITIONING_ERROR: (
    LiconicHandlerExportPlateError,
    _HANDLING_ERRORS[HandlingError.EXPORT_PLATE_LIFT_TRANSFER_POSITIONING_ERROR.value],
  ),
  HandlingError.EXPORT_PLATE_HANDLER_TRANSFER_TURN_OUT_ERROR: (
    LiconicHandlerExportPlateError,
    _HANDLING_ERRORS[HandlingError.EXPORT_PLATE_HANDLER_TRANSFER_TURN_OUT_ERROR.value],
  ),
  HandlingError.EXPORT_PLATE_SHOVEL_TRANSFER_OUTER_ERROR: (
    LiconicHandlerExportPlateError,
    _HANDLING_ERRORS[HandlingError.EXPORT_PLATE_SHOVEL_TRANSFER_OUTER_ERROR.value],
  ),
  HandlingError.EXPORT_PLATE_LIFT_TRANSFER_PLACE_ERROR: (
    LiconicHandlerExportPlateError,
    _HANDLING_ERRORS[HandlingError.EXPORT_PLATE_LIFT_TRANSFER_PLACE_ERROR.value],
  ),
  HandlingError.EXPORT_PLATE_SHOVEL_TRANSFER_INNER_ERROR: (
    LiconicHandlerExportPlateError,
    _HANDLING_ERRORS[HandlingError.EXPORT_PLATE_SHOVEL_TRANSFER_INNER_ERROR.value],
  ),
  HandlingError.EXPORT_PLATE_HANDLER_TRANSFER_TURN_IN_ERROR: (
    LiconicHandlerExportPlateError,
    _HANDLING_ERRORS[HandlingError.EXPORT_PLATE_HANDLER_TRANSFER_TURN_IN_ERROR.value],
  ),
  HandlingError.EXPORT_PLATE_LIFT_TRAVEL_BACK_ERROR: (
    LiconicHandlerExportPlateError,
    _HANDLING_ERRORS[HandlingError.EXPORT_PLATE_LIFT_TRAVEL_BACK_ERROR.value],
  ),
  HandlingError.EXPORT_PLATE_LIFT_INITIALIZING_ERROR: (
    LiconicHandlerExportPlateError,
    _HANDLING_ERRORS[HandlingError.EXPORT_PLATE_LIFT_INITIALIZING_ERROR.value],
  ),
  HandlingError.PLATE_REMOVE_GENERAL_HANDLING_ERROR: (
    LiconicHandlerPlateRemoveError,
    _HANDLING_ERRORS[HandlingError.PLATE_REMOVE_GENERAL_HANDLING_ERROR.value],
  ),
  HandlingError.PLATE_REMOVE_GATE_OPEN_ERROR: (
    LiconicHandlerPlateRemoveError,
    _HANDLING_ERRORS[HandlingError.PLATE_REMOVE_GATE_OPEN_ERROR.value],
  ),
  HandlingError.PLATE_REMOVE_GATE_CLOSE_ERROR: (
    LiconicHandlerPlateRemoveError,
    _HANDLING_ERRORS[HandlingError.PLATE_REMOVE_GATE_CLOSE_ERROR.value],
  ),
  HandlingError.PLATE_REMOVE_GENERAL_LIFT_POSITIONING_ERROR: (
    LiconicHandlerPlateRemoveError,
    _HANDLING_ERRORS[HandlingError.PLATE_REMOVE_GENERAL_LIFT_POSITIONING_ERROR.value],
  ),
  HandlingError.PLATE_REMOVE_USER_ACCESS_ERROR: (
    LiconicHandlerPlateRemoveError,
    _HANDLING_ERRORS[HandlingError.PLATE_REMOVE_USER_ACCESS_ERROR.value],
  ),
  HandlingError.PLATE_REMOVE_STACKER_SLOT_ERROR: (
    LiconicHandlerPlateRemoveError,
    _HANDLING_ERRORS[HandlingError.PLATE_REMOVE_STACKER_SLOT_ERROR.value],
  ),
  HandlingError.PLATE_REMOVE_REMOTE_ACCESS_LEVEL_ERROR: (
    LiconicHandlerPlateRemoveError,
    _HANDLING_ERRORS[HandlingError.PLATE_REMOVE_REMOTE_ACCESS_LEVEL_ERROR.value],
  ),
  HandlingError.PLATE_REMOVE_PLATE_TRANSFER_DETECTION_ERROR: (
    LiconicHandlerPlateRemoveError,
    _HANDLING_ERRORS[HandlingError.PLATE_REMOVE_PLATE_TRANSFER_DETECTION_ERROR.value],
  ),
  HandlingError.PLATE_REMOVE_LIFT_INITIALIZATION_ERROR: (
    LiconicHandlerPlateRemoveError,
    _HANDLING_ERRORS[HandlingError.PLATE_REMOVE_LIFT_INITIALIZATION_ERROR.value],
  ),
  HandlingError.PLATE_REMOVE_PLATE_ON_SHOVEL_DETECTION: (
    LiconicHandlerPlateRemoveError,
    _HANDLING_ERRORS[HandlingError.PLATE_REMOVE_PLATE_ON_SHOVEL_DETECTION.value],
  ),
  HandlingError.PLATE_REMOVE_NO_PLATE_ON_SHOVEL_DETECTION: (
    LiconicHandlerPlateRemoveError,
    _HANDLING_ERRORS[HandlingError.PLATE_REMOVE_NO_PLATE_ON_SHOVEL_DETECTION.value],
  ),
  HandlingError.PLATE_REMOVE_NO_RECOVERY: (
    LiconicHandlerPlateRemoveError,
    _HANDLING_ERRORS[HandlingError.PLATE_REMOVE_NO_RECOVERY.value],
  ),
  HandlingError.BARCODE_READ_GENERAL_HANDLING_ERROR: (
    LiconicHandlerBarcodeReadError,
    _HANDLING_ERRORS[HandlingError.BARCODE_READ_GENERAL_HANDLING_ERROR.value],
  ),
  HandlingError.BARCODE_READ_GATE_OPEN_ERROR: (
    LiconicHandlerBarcodeReadError,
    _HANDLING_ERRORS[HandlingError.BARCODE_READ_GATE_OPEN_ERROR.value],
  ),
  HandlingError.BARCODE_READ_GATE_CLOSE_ERROR: (
    LiconicHandlerBarcodeReadError,
    _HANDLING_ERRORS[HandlingError.BARCODE_READ_GATE_CLOSE_ERROR.value],
  ),
  HandlingError.BARCODE_READ_GENERAL_LIFT_POSITIONING_ERROR: (
    LiconicHandlerBarcodeReadError,
    _HANDLING_ERRORS[HandlingError.BARCODE_READ_GENERAL_LIFT_POSITIONING_ERROR.value],
  ),
  HandlingError.BARCODE_READ_USER_ACCESS_ERROR: (
    LiconicHandlerBarcodeReadError,
    _HANDLING_ERRORS[HandlingError.BARCODE_READ_USER_ACCESS_ERROR.value],
  ),
  HandlingError.BARCODE_READ_STACKER_SLOT_ERROR: (
    LiconicHandlerBarcodeReadError,
    _HANDLING_ERRORS[HandlingError.BARCODE_READ_STACKER_SLOT_ERROR.value],
  ),
  HandlingError.BARCODE_READ_REMOTE_ACCESS_LEVEL_ERROR: (
    LiconicHandlerBarcodeReadError,
    _HANDLING_ERRORS[HandlingError.BARCODE_READ_REMOTE_ACCESS_LEVEL_ERROR.value],
  ),
  HandlingError.BARCODE_READ_PLATE_TRANSFER_DETECTION_ERROR: (
    LiconicHandlerBarcodeReadError,
    _HANDLING_ERRORS[HandlingError.BARCODE_READ_PLATE_TRANSFER_DETECTION_ERROR.value],
  ),
  HandlingError.BARCODE_READ_LIFT_INITIALIZATION_ERROR: (
    LiconicHandlerBarcodeReadError,
    _HANDLING_ERRORS[HandlingError.BARCODE_READ_LIFT_INITIALIZATION_ERROR.value],
  ),
  HandlingError.BARCODE_READ_PLATE_ON_SHOVEL_DETECTION: (
    LiconicHandlerBarcodeReadError,
    _HANDLING_ERRORS[HandlingError.BARCODE_READ_PLATE_ON_SHOVEL_DETECTION.value],
  ),
  HandlingError.BARCODE_READ_NO_PLATE_ON_SHOVEL_DETECTION: (
    LiconicHandlerBarcodeReadError,
    _HANDLING_ERRORS[HandlingError.BARCODE_READ_NO_PLATE_ON_SHOVEL_DETECTION.value],
  ),
  HandlingError.BARCODE_READ_NO_RECOVERY: (
    LiconicHandlerBarcodeReadError,
    _HANDLING_ERRORS[HandlingError.BARCODE_READ_NO_RECOVERY.value],
  ),
  HandlingError.PLATE_PLACE_GENERAL_HANDLING_ERROR: (
    LiconicHandlerPlatePlaceError,
    _HANDLING_ERRORS[HandlingError.PLATE_PLACE_GENERAL_HANDLING_ERROR.value],
  ),
  HandlingError.PLATE_PLACE_GATE_OPEN_ERROR: (
    LiconicHandlerPlatePlaceError,
    _HANDLING_ERRORS[HandlingError.PLATE_PLACE_GATE_OPEN_ERROR.value],
  ),
  HandlingError.PLATE_PLACE_GATE_CLOSE_ERROR: (
    LiconicHandlerPlatePlaceError,
    _HANDLING_ERRORS[HandlingError.PLATE_PLACE_GATE_CLOSE_ERROR.value],
  ),
  HandlingError.PLATE_PLACE_GENERAL_LIFT_POSITIONING_ERROR: (
    LiconicHandlerPlatePlaceError,
    _HANDLING_ERRORS[HandlingError.PLATE_PLACE_GENERAL_LIFT_POSITIONING_ERROR.value],
  ),
  HandlingError.PLATE_PLACE_USER_ACCESS_ERROR: (
    LiconicHandlerPlatePlaceError,
    _HANDLING_ERRORS[HandlingError.PLATE_PLACE_USER_ACCESS_ERROR.value],
  ),
  HandlingError.PLATE_PLACE_STACKER_SLOT_ERROR: (
    LiconicHandlerPlatePlaceError,
    _HANDLING_ERRORS[HandlingError.PLATE_PLACE_STACKER_SLOT_ERROR.value],
  ),
  HandlingError.PLATE_PLACE_REMOTE_ACCESS_LEVEL_ERROR: (
    LiconicHandlerPlatePlaceError,
    _HANDLING_ERRORS[HandlingError.PLATE_PLACE_REMOTE_ACCESS_LEVEL_ERROR.value],
  ),
  HandlingError.PLATE_PLACE_PLATE_TRANSFER_DETECTION_ERROR: (
    LiconicHandlerPlatePlaceError,
    _HANDLING_ERRORS[HandlingError.PLATE_PLACE_PLATE_TRANSFER_DETECTION_ERROR.value],
  ),
  HandlingError.PLATE_PLACE_LIFT_INITIALIZATION_ERROR: (
    LiconicHandlerPlatePlaceError,
    _HANDLING_ERRORS[HandlingError.PLATE_PLACE_LIFT_INITIALIZATION_ERROR.value],
  ),
  HandlingError.PLATE_PLACE_PLATE_ON_SHOVEL_DETECTION: (
    LiconicHandlerPlatePlaceError,
    _HANDLING_ERRORS[HandlingError.PLATE_PLACE_PLATE_ON_SHOVEL_DETECTION.value],
  ),
  HandlingError.PLATE_PLACE_NO_PLATE_ON_SHOVEL_DETECTION: (
    LiconicHandlerPlatePlaceError,
    _HANDLING_ERRORS[HandlingError.PLATE_PLACE_NO_PLATE_ON_SHOVEL_DETECTION.value],
  ),
  HandlingError.PLATE_PLACE_NO_RECOVERY: (
    LiconicHandlerPlatePlaceError,
    _HANDLING_ERRORS[HandlingError.PLATE_PLACE_NO_RECOVERY.value],
  ),
  HandlingError.PLATE_SET_GENERAL_HANDLING_ERROR: (
    LiconicHandlerPlateSetError,
    _HANDLING_ERRORS[HandlingError.PLATE_SET_GENERAL_HANDLING_ERROR.value],
  ),
  HandlingError.PLATE_SET_GATE_OPEN_ERROR: (
    LiconicHandlerPlateSetError,
    _HANDLING_ERRORS[HandlingError.PLATE_SET_GATE_OPEN_ERROR.value],
  ),
  HandlingError.PLATE_SET_GATE_CLOSE_ERROR: (
    LiconicHandlerPlateSetError,
    _HANDLING_ERRORS[HandlingError.PLATE_SET_GATE_CLOSE_ERROR.value],
  ),
  HandlingError.PLATE_SET_GENERAL_LIFT_POSITIONING_ERROR: (
    LiconicHandlerPlateSetError,
    _HANDLING_ERRORS[HandlingError.PLATE_SET_GENERAL_LIFT_POSITIONING_ERROR.value],
  ),
  HandlingError.PLATE_SET_USER_ACCESS_ERROR: (
    LiconicHandlerPlateSetError,
    _HANDLING_ERRORS[HandlingError.PLATE_SET_USER_ACCESS_ERROR.value],
  ),
  HandlingError.PLATE_SET_STACKER_SLOT_ERROR: (
    LiconicHandlerPlateSetError,
    _HANDLING_ERRORS[HandlingError.PLATE_SET_STACKER_SLOT_ERROR.value],
  ),
  HandlingError.PLATE_SET_REMOTE_ACCESS_LEVEL_ERROR: (
    LiconicHandlerPlateSetError,
    _HANDLING_ERRORS[HandlingError.PLATE_SET_REMOTE_ACCESS_LEVEL_ERROR.value],
  ),
  HandlingError.PLATE_SET_PLATE_TRANSFER_DETECTION_ERROR: (
    LiconicHandlerPlateSetError,
    _HANDLING_ERRORS[HandlingError.PLATE_SET_PLATE_TRANSFER_DETECTION_ERROR.value],
  ),
  HandlingError.PLATE_SET_LIFT_INITIALIZATION_ERROR: (
    LiconicHandlerPlateSetError,
    _HANDLING_ERRORS[HandlingError.PLATE_SET_LIFT_INITIALIZATION_ERROR.value],
  ),
  HandlingError.PLATE_SET_PLATE_ON_SHOVEL_DETECTION: (
    LiconicHandlerPlateSetError,
    _HANDLING_ERRORS[HandlingError.PLATE_SET_PLATE_ON_SHOVEL_DETECTION.value],
  ),
  HandlingError.PLATE_SET_NO_PLATE_ON_SHOVEL_DETECTION: (
    LiconicHandlerPlateSetError,
    _HANDLING_ERRORS[HandlingError.PLATE_SET_NO_PLATE_ON_SHOVEL_DETECTION.value],
  ),
  HandlingError.PLATE_SET_NO_RECOVERY: (
    LiconicHandlerPlateSetError,
    _HANDLING_ERRORS[HandlingError.PLATE_SET_NO_RECOVERY.value],
  ),
  HandlingError.PLATE_GET_GENERAL_HANDLING_ERROR: (
    LiconicHandlerPlateGetError,
    _HANDLING_ERRORS[HandlingError.PLATE_GET_GENERAL_HANDLING_ERROR.value],
  ),
  HandlingError.PLATE_GET_GATE_OPEN_ERROR: (
    LiconicHandlerPlateGetError,
    _HANDLING_ERRORS[HandlingError.PLATE_GET_GATE_OPEN_ERROR.value],
  ),
  HandlingError.PLATE_GET_GATE_CLOSE_ERROR: (
    LiconicHandlerPlateGetError,
    _HANDLING_ERRORS[HandlingError.PLATE_GET_GATE_CLOSE_ERROR.value],
  ),
  HandlingError.PLATE_GET_GENERAL_LIFT_POSITIONING_ERROR: (
    LiconicHandlerPlateGetError,
    _HANDLING_ERRORS[HandlingError.PLATE_GET_GENERAL_LIFT_POSITIONING_ERROR.value],
  ),
  HandlingError.PLATE_GET_USER_ACCESS_ERROR: (
    LiconicHandlerPlateGetError,
    _HANDLING_ERRORS[HandlingError.PLATE_GET_USER_ACCESS_ERROR.value],
  ),
  HandlingError.PLATE_GET_STACKER_SLOT_ERROR: (
    LiconicHandlerPlateGetError,
    _HANDLING_ERRORS[HandlingError.PLATE_GET_STACKER_SLOT_ERROR.value],
  ),
  HandlingError.PLATE_GET_REMOTE_ACCESS_LEVEL_ERROR: (
    LiconicHandlerPlateGetError,
    _HANDLING_ERRORS[HandlingError.PLATE_GET_REMOTE_ACCESS_LEVEL_ERROR.value],
  ),
  HandlingError.PLATE_GET_PLATE_TRANSFER_DETECTION_ERROR: (
    LiconicHandlerPlateGetError,
    _HANDLING_ERRORS[HandlingError.PLATE_GET_PLATE_TRANSFER_DETECTION_ERROR.value],
  ),
  HandlingError.PLATE_GET_LIFT_INITIALIZATION_ERROR: (
    LiconicHandlerPlateGetError,
    _HANDLING_ERRORS[HandlingError.PLATE_GET_LIFT_INITIALIZATION_ERROR.value],
  ),
  HandlingError.PLATE_GET_PLATE_ON_SHOVEL_DETECTION: (
    LiconicHandlerPlateGetError,
    _HANDLING_ERRORS[HandlingError.PLATE_GET_PLATE_ON_SHOVEL_DETECTION.value],
  ),
  HandlingError.PLATE_GET_NO_PLATE_ON_SHOVEL_DETECTION: (
    LiconicHandlerPlateGetError,
    _HANDLING_ERRORS[HandlingError.PLATE_GET_NO_PLATE_ON_SHOVEL_DETECTION.value],
  ),
  HandlingError.PLATE_GET_NO_RECOVERY: (
    LiconicHandlerPlateGetError,
    _HANDLING_ERRORS[HandlingError.PLATE_GET_NO_RECOVERY.value],
  ),
}
