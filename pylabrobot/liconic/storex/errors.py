"""StoreX controller and plate-handler errors."""

from typing import Dict, Tuple, Type

from .constants import ControllerError, HandlingError


class StoreXControllerRelayError(Exception):
  pass


class StoreXControllerCommandError(Exception):
  pass


class StoreXControllerProgramError(Exception):
  pass


class StoreXControllerHardwareError(Exception):
  pass


class StoreXControllerWriteProtectedError(Exception):
  pass


class StoreXControllerBaseUnitError(Exception):
  pass


controller_error_map: Dict[ControllerError, Tuple[Type[Exception], str]] = {
  ControllerError.RELAY_ERROR: (
    StoreXControllerRelayError,
    "Controller system error. Undefined timer, counter, data memory, check if requested unit is valid",
  ),
  ControllerError.COMMAND_ERROR: (
    StoreXControllerCommandError,
    "Controller system error. Invalid command, check if communication is opened by CR, check command sent to controller, check for interruptions during string transmission",
  ),
  ControllerError.PROGRAM_ERROR: (
    StoreXControllerProgramError,
    "Controller system error. Firmware lost, reprogram controller",
  ),
  ControllerError.HARDWARE_ERROR: (
    StoreXControllerHardwareError,
    "Controller hardware error, turn controller ON/OFF, controller is faulty has to be replaced",
  ),
  ControllerError.WRITE_PROTECTED_ERROR: (
    StoreXControllerWriteProtectedError,
    "Controller system error. Unauthorized Access",
  ),
  ControllerError.BASE_UNIT_ERROR: (
    StoreXControllerBaseUnitError,
    "Controller system error. Unauthorized Access",
  ),
}


class StoreXHandlerPlateRemoveError(Exception):
  pass


class StoreXHandlerBarcodeReadError(Exception):
  pass


class StoreXHandlerPlatePlaceError(Exception):
  pass


class StoreXHandlerPlateSetError(Exception):
  pass


class StoreXHandlerPlateGetError(Exception):
  pass


class StoreXHandlerImportPlateError(Exception):
  pass


class StoreXHandlerExportPlateError(Exception):
  pass


class StoreXHandlerGeneralError(Exception):
  pass


handler_error_map: Dict[HandlingError, Tuple[Type[Exception], str]] = {
  HandlingError.GENERAL_HANDLING_ERROR: (
    StoreXHandlerGeneralError,
    "Handling action could not be performed in time",
  ),
  HandlingError.GATE_OPEN_ERROR: (
    StoreXHandlerGeneralError,
    "Gate could not reach upper position or Gate did not reach upper position in time",
  ),
  HandlingError.GATE_CLOSE_ERROR: (
    StoreXHandlerGeneralError,
    "Gate could not reach lower position or Gate did not reach lower position in time",
  ),
  HandlingError.GENERAL_LIFT_POSITIONING_ERROR: (
    StoreXHandlerGeneralError,
    "Handler-Lift could not reach desired level position or does not move",
  ),
  HandlingError.USER_ACCESS_ERROR: (
    StoreXHandlerGeneralError,
    "Unauthorized user access in combination with manual rotation of carrousel",
  ),
  HandlingError.STACKER_SLOT_ERROR: (
    StoreXHandlerGeneralError,
    "Stacker slot cannot be reached",
  ),
  HandlingError.REMOTE_ACCESS_LEVEL_ERROR: (
    StoreXHandlerGeneralError,
    "Undefined stacker level has been requested",
  ),
  HandlingError.PLATE_TRANSFER_DETECTION_ERROR: (
    StoreXHandlerGeneralError,
    "Export operation while plate is on transfer station",
  ),
  HandlingError.LIFT_INITIALIZATION_ERROR: (
    StoreXHandlerGeneralError,
    "Lift could not be initialized",
  ),
  HandlingError.PLATE_ON_SHOVEL_DETECTION: (
    StoreXHandlerGeneralError,
    "Trying to load a plate, when a plate is already on the shovel",
  ),
  HandlingError.NO_PLATE_ON_SHOVEL_DETECTION: (
    StoreXHandlerGeneralError,
    "Trying to remove or place plate with no plate on the shovel",
  ),
  HandlingError.NO_RECOVERY: (
    StoreXHandlerGeneralError,
    "Recovery was not possible",
  ),
  HandlingError.IMPORT_PLATE_STACKER_POSITIONING_ERROR: (
    StoreXHandlerImportPlateError,
    "Carrousel could not reach desired radial position during Import Plate procedure or Lift could not reach transfer level during Import Plate procedure.",
  ),
  HandlingError.IMPORT_PLATE_HANDLER_TRANSFER_TURN_OUT_ERROR: (
    StoreXHandlerImportPlateError,
    "Handler could not reach outer turn position at transfer level during Import Plate procedure.",
  ),
  HandlingError.IMPORT_PLATE_SHOVEL_TRANSFER_OUTER_ERROR: (
    StoreXHandlerImportPlateError,
    "Shovel could not reach outer position at transfer level during Import Plate procedure.",
  ),
  HandlingError.IMPORT_PLATE_LIFT_TRANSFER_ERROR: (
    StoreXHandlerImportPlateError,
    "Lift did not reach upper pick position at transfer level during Import Plate procedure.",
  ),
  HandlingError.IMPORT_PLATE_SHOVEL_TRANSFER_INNER_ERROR: (
    StoreXHandlerImportPlateError,
    "Shovel could not reach inner position at transfer level during Import Plate procedure.",
  ),
  HandlingError.IMPORT_PLATE_HANDLER_TRANSFER_TURN_IN_ERROR: (
    StoreXHandlerImportPlateError,
    "Handler could not reach inner turn position at transfer level during Import Plate procedure.",
  ),
  HandlingError.IMPORT_PLATE_LIFT_STACKER_TRAVEL_ERROR: (
    StoreXHandlerImportPlateError,
    "Lift could not reach desired stacker level during Import Plate procedure.",
  ),
  HandlingError.IMPORT_PLATE_SHOVEL_STACKER_FRONT_ERROR: (
    StoreXHandlerImportPlateError,
    "Shovel could not reach front position on stacker access during Plate Import procedure.",
  ),
  HandlingError.IMPORT_PLATE_LIFT_STACKER_PLACE_ERROR: (
    StoreXHandlerImportPlateError,
    "Lift could not reach stacker place level during Import Plate procedure.",
  ),
  HandlingError.IMPORT_PLATE_SHOVEL_STACKER_INNER_ERROR: (
    StoreXHandlerImportPlateError,
    "Shovel could not reach inner position at stacker plate placement during Import Plate procedure.",
  ),
  HandlingError.IMPORT_PLATE_LIFT_TRAVEL_BACK_ERROR: (
    StoreXHandlerImportPlateError,
    "Lift could not reach zero level during Import Plate procedure.",
  ),
  HandlingError.IMPORT_PLATE_LIFT_INIT_ERROR: (
    StoreXHandlerImportPlateError,
    "Lift could not be initialized after Import Plate procedure.",
  ),
  HandlingError.EXPORT_PLATE_LIFT_STACKER_TRAVEL_ERROR: (
    StoreXHandlerExportPlateError,
    "Carrousel could not reach desired radial position during Export Plate procedure or Lift could not reach desired stacker level during Export Plate procedure.",
  ),
  HandlingError.EXPORT_PLATE_SHOVEL_STACKER_FRONT_ERROR: (
    StoreXHandlerExportPlateError,
    "Shovel could not reach front position on stacker access during Plate Export procedure.",
  ),
  HandlingError.EXPORT_PLATE_LIFT_STACKER_IMPORT_ERROR: (
    StoreXHandlerExportPlateError,
    "Lift could not reach stacker pick level during Export Plate procedure.",
  ),
  HandlingError.EXPORT_PLATE_SHOVEL_STACKER_INNER_ERROR: (
    StoreXHandlerExportPlateError,
    "Shovel could not reach inner position at stacker plate pick during Export Plate procedure.",
  ),
  HandlingError.EXPORT_PLATE_LIFT_TRANSFER_POSITIONING_ERROR: (
    StoreXHandlerExportPlateError,
    "Lift could not reach transfer level during Export Plate procedure.",
  ),
  HandlingError.EXPORT_PLATE_HANDLER_TRANSFER_TURN_OUT_ERROR: (
    StoreXHandlerExportPlateError,
    "Handler could not reach outer turn position at transfer level during Export Plate procedure.",
  ),
  HandlingError.EXPORT_PLATE_SHOVEL_TRANSFER_OUTER_ERROR: (
    StoreXHandlerExportPlateError,
    "Shovel could not reach outer position at transfer level during Export Plate procedure.",
  ),
  HandlingError.EXPORT_PLATE_LIFT_TRANSFER_PLACE_ERROR: (
    StoreXHandlerExportPlateError,
    "Lift did not reach lower place position at transfer level during Export Plate procedure.",
  ),
  HandlingError.EXPORT_PLATE_SHOVEL_TRANSFER_INNER_ERROR: (
    StoreXHandlerExportPlateError,
    "Shovel could not reach inner position at transfer level during Export Plate procedure.",
  ),
  HandlingError.EXPORT_PLATE_HANDLER_TRANSFER_TURN_IN_ERROR: (
    StoreXHandlerExportPlateError,
    "Handler could not reach inner turn position at transfer level during Export Plate procedure",
  ),
  HandlingError.EXPORT_PLATE_LIFT_TRAVEL_BACK_ERROR: (
    StoreXHandlerExportPlateError,
    "Lift could not reach Zero position during Export Plate procedure.",
  ),
  HandlingError.EXPORT_PLATE_LIFT_INITIALIZING_ERROR: (
    StoreXHandlerExportPlateError,
    "Lift could not be initialized after Export Plate procedure.",
  ),
  HandlingError.PLATE_REMOVE_GENERAL_HANDLING_ERROR: (
    StoreXHandlerPlateRemoveError,
    "Handling action could not be performed in time.",
  ),
  HandlingError.PLATE_REMOVE_GATE_OPEN_ERROR: (
    StoreXHandlerPlateRemoveError,
    "Gate could not reach upper position or Gate did not reach upper position in time",
  ),
  HandlingError.PLATE_REMOVE_GATE_CLOSE_ERROR: (
    StoreXHandlerPlateRemoveError,
    "Gate could not reach lower position or Gate did not reach lower position in time",
  ),
  HandlingError.PLATE_REMOVE_GENERAL_LIFT_POSITIONING_ERROR: (
    StoreXHandlerPlateRemoveError,
    "Handler-Lift could not reach desired level position or does not move",
  ),
  HandlingError.PLATE_REMOVE_USER_ACCESS_ERROR: (
    StoreXHandlerPlateRemoveError,
    "Unauthorized user access in combination with manual rotation of carrousel",
  ),
  HandlingError.PLATE_REMOVE_STACKER_SLOT_ERROR: (
    StoreXHandlerPlateRemoveError,
    "Stacker slot cannot be reached",
  ),
  HandlingError.PLATE_REMOVE_REMOTE_ACCESS_LEVEL_ERROR: (
    StoreXHandlerPlateRemoveError,
    "Undefined stacker level has been requested",
  ),
  HandlingError.PLATE_REMOVE_PLATE_TRANSFER_DETECTION_ERROR: (
    StoreXHandlerPlateRemoveError,
    "Export operation while plate is on transfer station",
  ),
  HandlingError.PLATE_REMOVE_LIFT_INITIALIZATION_ERROR: (
    StoreXHandlerPlateRemoveError,
    "Lift could not be initialized",
  ),
  HandlingError.PLATE_REMOVE_PLATE_ON_SHOVEL_DETECTION: (
    StoreXHandlerPlateRemoveError,
    "Trying to load a plate, when a plate is already on the shovel",
  ),
  HandlingError.PLATE_REMOVE_NO_PLATE_ON_SHOVEL_DETECTION: (
    StoreXHandlerPlateRemoveError,
    "Trying to remove or place plate with no plate on the shovel",
  ),
  HandlingError.PLATE_REMOVE_NO_RECOVERY: (
    StoreXHandlerPlateRemoveError,
    "Recovery was not possible",
  ),
  HandlingError.BARCODE_READ_GENERAL_HANDLING_ERROR: (
    StoreXHandlerBarcodeReadError,
    "Handling action could not be performed in time.",
  ),
  HandlingError.BARCODE_READ_GATE_OPEN_ERROR: (
    StoreXHandlerBarcodeReadError,
    "Gate could not reach upper position or Gate did not reach upper position in time",
  ),
  HandlingError.BARCODE_READ_GATE_CLOSE_ERROR: (
    StoreXHandlerBarcodeReadError,
    "Gate could not reach lower position or Gate did not reach lower position in time",
  ),
  HandlingError.BARCODE_READ_GENERAL_LIFT_POSITIONING_ERROR: (
    StoreXHandlerBarcodeReadError,
    "Handler-Lift could not reach desired level position or does not move",
  ),
  HandlingError.BARCODE_READ_USER_ACCESS_ERROR: (
    StoreXHandlerBarcodeReadError,
    "Unauthorized user access in combination with manual rotation of carrousel",
  ),
  HandlingError.BARCODE_READ_STACKER_SLOT_ERROR: (
    StoreXHandlerBarcodeReadError,
    "Stacker slot cannot be reached",
  ),
  HandlingError.BARCODE_READ_REMOTE_ACCESS_LEVEL_ERROR: (
    StoreXHandlerBarcodeReadError,
    "Undefined stacker level has been requested",
  ),
  HandlingError.BARCODE_READ_PLATE_TRANSFER_DETECTION_ERROR: (
    StoreXHandlerBarcodeReadError,
    "Export operation while plate is on transfer station",
  ),
  HandlingError.BARCODE_READ_LIFT_INITIALIZATION_ERROR: (
    StoreXHandlerBarcodeReadError,
    "Lift could not be initialized",
  ),
  HandlingError.BARCODE_READ_PLATE_ON_SHOVEL_DETECTION: (
    StoreXHandlerBarcodeReadError,
    "Trying to load a plate, when a plate is already on the shovel",
  ),
  HandlingError.BARCODE_READ_NO_PLATE_ON_SHOVEL_DETECTION: (
    StoreXHandlerBarcodeReadError,
    "Trying to remove or place plate with no plate on the shovel",
  ),
  HandlingError.BARCODE_READ_NO_RECOVERY: (
    StoreXHandlerBarcodeReadError,
    "Recovery was not possible",
  ),
  HandlingError.PLATE_PLACE_GENERAL_HANDLING_ERROR: (
    StoreXHandlerPlatePlaceError,
    "Handling action could not be performed in time.",
  ),
  HandlingError.PLATE_PLACE_GATE_OPEN_ERROR: (
    StoreXHandlerPlatePlaceError,
    "Gate could not reach upper position or Gate did not reach upper position in time",
  ),
  HandlingError.PLATE_PLACE_GATE_CLOSE_ERROR: (
    StoreXHandlerPlatePlaceError,
    "Gate could not reach lower position or Gate did not reach lower position in time",
  ),
  HandlingError.PLATE_PLACE_GENERAL_LIFT_POSITIONING_ERROR: (
    StoreXHandlerPlatePlaceError,
    "Handler-Lift could not reach desired level position or does not move",
  ),
  HandlingError.PLATE_PLACE_USER_ACCESS_ERROR: (
    StoreXHandlerPlatePlaceError,
    "Unauthorized user access in combination with manual rotation of carrousel",
  ),
  HandlingError.PLATE_PLACE_STACKER_SLOT_ERROR: (
    StoreXHandlerPlatePlaceError,
    "Stacker slot cannot be reached",
  ),
  HandlingError.PLATE_PLACE_REMOTE_ACCESS_LEVEL_ERROR: (
    StoreXHandlerPlatePlaceError,
    "Undefined stacker level has been requested",
  ),
  HandlingError.PLATE_PLACE_PLATE_TRANSFER_DETECTION_ERROR: (
    StoreXHandlerPlatePlaceError,
    "Export operation while plate is on transfer station",
  ),
  HandlingError.PLATE_PLACE_LIFT_INITIALIZATION_ERROR: (
    StoreXHandlerPlatePlaceError,
    "Lift could not be initialized",
  ),
  HandlingError.PLATE_PLACE_PLATE_ON_SHOVEL_DETECTION: (
    StoreXHandlerPlatePlaceError,
    "Trying to load a plate, when a plate is already on the shovel",
  ),
  HandlingError.PLATE_PLACE_NO_PLATE_ON_SHOVEL_DETECTION: (
    StoreXHandlerPlatePlaceError,
    "Trying to remove or place plate with no plate on the shovel",
  ),
  HandlingError.PLATE_PLACE_NO_RECOVERY: (
    StoreXHandlerPlatePlaceError,
    "Recovery was not possible",
  ),
  HandlingError.PLATE_SET_GENERAL_HANDLING_ERROR: (
    StoreXHandlerPlateSetError,
    "Handling action could not be performed in time.",
  ),
  HandlingError.PLATE_SET_GATE_OPEN_ERROR: (
    StoreXHandlerPlateSetError,
    "Gate could not reach upper position or Gate did not reach upper position in time",
  ),
  HandlingError.PLATE_SET_GATE_CLOSE_ERROR: (
    StoreXHandlerPlateSetError,
    "Gate could not reach lower position or Gate did not reach lower position in time",
  ),
  HandlingError.PLATE_SET_GENERAL_LIFT_POSITIONING_ERROR: (
    StoreXHandlerPlateSetError,
    "Handler-Lift could not reach desired level position or does not move",
  ),
  HandlingError.PLATE_SET_USER_ACCESS_ERROR: (
    StoreXHandlerPlateSetError,
    "Unauthorized user access in combination with manual rotation of carrousel",
  ),
  HandlingError.PLATE_SET_STACKER_SLOT_ERROR: (
    StoreXHandlerPlateSetError,
    "Stacker slot cannot be reached",
  ),
  HandlingError.PLATE_SET_REMOTE_ACCESS_LEVEL_ERROR: (
    StoreXHandlerPlateSetError,
    "Undefined stacker level has been requested",
  ),
  HandlingError.PLATE_SET_PLATE_TRANSFER_DETECTION_ERROR: (
    StoreXHandlerPlateSetError,
    "Export operation while plate is on transfer station",
  ),
  HandlingError.PLATE_SET_LIFT_INITIALIZATION_ERROR: (
    StoreXHandlerPlateSetError,
    "Lift could not be initialized",
  ),
  HandlingError.PLATE_SET_PLATE_ON_SHOVEL_DETECTION: (
    StoreXHandlerPlateSetError,
    "Trying to load a plate, when a plate is already on the shovel",
  ),
  HandlingError.PLATE_SET_NO_PLATE_ON_SHOVEL_DETECTION: (
    StoreXHandlerPlateSetError,
    "Trying to remove or place plate with no plate on the shovel",
  ),
  HandlingError.PLATE_SET_NO_RECOVERY: (
    StoreXHandlerPlateSetError,
    "Recovery was not possible",
  ),
  HandlingError.PLATE_GET_GENERAL_HANDLING_ERROR: (
    StoreXHandlerPlateGetError,
    "Handling action could not be performed in time.",
  ),
  HandlingError.PLATE_GET_GATE_OPEN_ERROR: (
    StoreXHandlerPlateGetError,
    "Gate could not reach upper position or Gate did not reach upper position in time",
  ),
  HandlingError.PLATE_GET_GATE_CLOSE_ERROR: (
    StoreXHandlerPlateGetError,
    "Gate could not reach lower position or Gate did not reach lower position in time",
  ),
  HandlingError.PLATE_GET_GENERAL_LIFT_POSITIONING_ERROR: (
    StoreXHandlerPlateGetError,
    "Handler-Lift could not reach desired level position or does not move",
  ),
  HandlingError.PLATE_GET_USER_ACCESS_ERROR: (
    StoreXHandlerPlateGetError,
    "Unauthorized user access in combination with manual rotation of carrousel",
  ),
  HandlingError.PLATE_GET_STACKER_SLOT_ERROR: (
    StoreXHandlerPlateGetError,
    "Stacker slot cannot be reached",
  ),
  HandlingError.PLATE_GET_REMOTE_ACCESS_LEVEL_ERROR: (
    StoreXHandlerPlateGetError,
    "Undefined stacker level has been requested",
  ),
  HandlingError.PLATE_GET_PLATE_TRANSFER_DETECTION_ERROR: (
    StoreXHandlerPlateGetError,
    "Export operation while plate is on transfer station",
  ),
  HandlingError.PLATE_GET_LIFT_INITIALIZATION_ERROR: (
    StoreXHandlerPlateGetError,
    "Lift could not be initialized",
  ),
  HandlingError.PLATE_GET_PLATE_ON_SHOVEL_DETECTION: (
    StoreXHandlerPlateGetError,
    "Trying to load a plate, when a plate is already on the shovel",
  ),
  HandlingError.PLATE_GET_NO_PLATE_ON_SHOVEL_DETECTION: (
    StoreXHandlerPlateGetError,
    "Trying to remove or place plate with no plate on the shovel",
  ),
  HandlingError.PLATE_GET_NO_RECOVERY: (
    StoreXHandlerPlateGetError,
    "Recovery was not possible during get plate",
  ),
}
