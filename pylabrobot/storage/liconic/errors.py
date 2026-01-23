from typing import Dict

from pylabrobot.storage.liconic.constants import ControllerError, HandlingError

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

controller_error_map: Dict[ControllerError, Exception] = {
  ControllerError.RELAY_ERROR: LiconicControllerRelayError(
    "Controller system error. Undefined timer, counter, data memory, check if requested unit is valid"
  ),
  ControllerError.COMMAND_ERROR: LiconicControllerCommandError(
    "Controller system error. Invalid command, check if communication is opened by CR, check command sent to controller, check for interruptions during string transmission"
  ),
  ControllerError.PROGRAM_ERROR: LiconicControllerProgramError(
    "Controller system error. Firmware lost, reprogram controller"
  ),
  ControllerError.HARDWARE_ERROR: LiconicControllerHardwareError(
    "Controller hardware error, turn controller ON/OFF, controller is faulty has to be replaced"
  ),
  ControllerError.WRITE_PROTECTED_ERROR: LiconicControllerWriteProtectedError(
    "Controller system error. Unauthorized Access"
  ),
  ControllerError.BASE_UNIT_ERROR: LiconicControllerBaseUnitError(
    "Controller system error. Unauthorized Access"
  )
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

handler_error_map: Dict[HandlingError, Exception] = {
  HandlingError.GENERAL_HANDLING_ERROR: LiconicHandlerGeneralError("Handling action could not be performed in time"),
  HandlingError.GATE_OPEN_ERROR: LiconicHandlerGeneralError("Gate could not reach upper position or Gate did not reach upper position in time"),
  HandlingError.GATE_CLOSE_ERROR: LiconicHandlerGeneralError("Gate could not reach lower position or Gate did not reach lower position in time"),
  HandlingError.GENERAL_LIFT_POSITIONING_ERROR: LiconicHandlerGeneralError("Handler-Lift could not reach desired level position or does not move"),
  HandlingError.USER_ACCESS_ERROR: LiconicHandlerGeneralError("Unauthorized user access in combination with manual rotation of carrousel"),
  HandlingError.STACKER_SLOT_ERROR: LiconicHandlerGeneralError("Stacker slot cannot be reached "),
  HandlingError.REMOTE_ACCESS_LEVEL_ERROR: LiconicHandlerGeneralError("Undefined stacker level has been requested"),
  HandlingError.PLATE_TRANSFER_DETECTION_ERROR: LiconicHandlerGeneralError("Export operation while plate is on transfer station"),
  HandlingError.LIFT_INITIALIZATION_ERROR: LiconicHandlerGeneralError("Lift could not be initialized "),
  HandlingError.PLATE_ON_SHOVEL_DETECTION: LiconicHandlerGeneralError("Trying to load a plate, when a plate is already on the shovel"),
  HandlingError.NO_PLATE_ON_SHOVEL_DETECTION: LiconicHandlerGeneralError("Trying to remove or place plate with no plate on the shovel"),
  HandlingError.NO_RECOVERY: LiconicHandlerGeneralError("Recovery was not possible "),

  HandlingError.IMPORT_PLATE_STACKER_POSITIONING_ERROR: LiconicHandlerImportPlateError("Carrousel could not reach desired radial position during Import Plate procedure or Lift could not reach transfer level during Import Plate procedure."),
  HandlingError.IMPORT_PLATE_HANDLER_TRANSFER_TURN_OUT_ERROR: LiconicHandlerImportPlateError("Handler could not reach outer turn position at transfer level during Import Plate procedure."),
  HandlingError.IMPORT_PLATE_SHOVEL_TRANSFER_OUTER_ERROR: LiconicHandlerImportPlateError("Shovel could not reach outer position at transfer level during Import Plate procedure."),
  HandlingError.IMPORT_PLATE_LIFT_TRANSFER_ERROR: LiconicHandlerImportPlateError("Lift did not reach upper pick position at transfer level during Import Plate procedure."),
  HandlingError.IMPORT_PLATE_SHOVEL_TRANSFER_INNER_ERROR: LiconicHandlerImportPlateError("Shovel could not reach inner position at transfer level during Import Plate procedure."),
  HandlingError.IMPORT_PLATE_HANDLER_TRANSFER_TURN_IN_ERROR: LiconicHandlerImportPlateError("Handler could not reach inner turn position at transfer level during Import Plate procedure."),
  HandlingError.IMPORT_PLATE_LIFT_STACKER_TRAVEL_ERROR: LiconicHandlerImportPlateError("Lift could not reach desired stacker level during Import Plate procedure."),
  HandlingError.IMPORT_PLATE_SHOVEL_STACKER_FRONT_ERROR: LiconicHandlerImportPlateError("Shovel could not reach front position on stacker access during Plate Import procedure."),
  HandlingError.IMPORT_PLATE_LIFT_STACKER_PLACE_ERROR: LiconicHandlerImportPlateError("Lift could not reach stacker place level during Import Plate procedure."),
  HandlingError.IMPORT_PLATE_SHOVEL_STACKER_INNER_ERROR: LiconicHandlerImportPlateError("Shovel could not reach inner position at stacker plate placement during Import Plate procedure."),
  HandlingError.IMPORT_PLATE_LIFT_TRAVEL_BACK_ERROR: LiconicHandlerImportPlateError("Lift could not reach zero level during Import Plate procedure."),
  HandlingError.IMPORT_PLATE_LIFT_INIT_ERROR: LiconicHandlerImportPlateError("Lift could not be initialized after Import Plate procedure."),

  HandlingError.EXPORT_PLATE_LIFT_STACKER_TRAVEL_ERROR: LiconicHandlerExportPlateError("Carrousel could not reach desired radial position during Export Plate procedure or Lift could not reach desired stacker level during Export Plate procedure."),
  HandlingError.EXPORT_PLATE_SHOVEL_STACKER_FRONT_ERROR: LiconicHandlerExportPlateError("Shovel could not reach front position on stacker access during Plate Export procedure."),
  HandlingError.EXPORT_PLATE_LIFT_STACKER_IMPORT_ERROR: LiconicHandlerExportPlateError("Lift could not reach stacker pick level during Export Plate procedure."),
  HandlingError.EXPORT_PLATE_SHOVEL_STACKER_INNER_ERROR: LiconicHandlerExportPlateError("Shovel could not reach inner position at stacker plate pick during Export Plate procedure."),
  HandlingError.EXPORT_PLATE_LIFT_TRANSFER_POSITIONING_ERROR: LiconicHandlerExportPlateError("Lift could not reach transfer level during Export Plate procedure."),
  HandlingError.EXPORT_PLATE_HANDLER_TRANSFER_TURN_OUT_ERROR: LiconicHandlerExportPlateError("Handler could not reach outer turn position at transfer level during Export Plate procedure."),
  HandlingError.EXPORT_PLATE_SHOVEL_TRANSFER_OUTER_ERROR: LiconicHandlerExportPlateError("Shovel could not reach outer position at transfer level during Export Plate procedure."),
  HandlingError.EXPORT_PLATE_LIFT_TRANSFER_PLACE_ERROR: LiconicHandlerExportPlateError("Lift did not reach lower place position at transfer level during Export Plate procedure."),
  HandlingError.EXPORT_PLATE_SHOVEL_TRANSFER_INNER_ERROR: LiconicHandlerExportPlateError("Shovel could not reach inner position at transfer level during Export Plate procedure."),
  HandlingError.EXPORT_PLATE_HANDLER_TRANSFER_TURN_IN_ERROR: LiconicHandlerExportPlateError("Handler could not reach inner turn position at transfer level during Export Plate procedure"),
  HandlingError.EXPORT_PLATE_LIFT_TRAVEL_BACK_ERROR: LiconicHandlerExportPlateError("Lift could not reach Zero position during Export Plate procedure."),
  HandlingError.EXPORT_PLATE_LIFT_INITIALIZING_ERROR: LiconicHandlerExportPlateError("Lift could not be initialized after Export Plate procedure."),

  HandlingError.PLATE_REMOVE_GENERAL_HANDLING_ERROR: LiconicHandlerPlateRemoveError("Handling action could not be performed in time."),
  HandlingError.PLATE_REMOVE_GATE_OPEN_ERROR: LiconicHandlerPlateRemoveError("Gate could not reach upper position or Gate did not reach upper position in time"),
  HandlingError.PLATE_REMOVE_GATE_CLOSE_ERROR: LiconicHandlerPlateRemoveError("Gate could not reach lower position or Gate did not reach lower position in time"),
  HandlingError.PLATE_REMOVE_GENERAL_LIFT_POSITIONING_ERROR: LiconicHandlerPlateRemoveError("Handler-Lift could not reach desired level position or does not move"),
  HandlingError.PLATE_REMOVE_USER_ACCESS_ERROR: LiconicHandlerPlateRemoveError("Unauthorized user access in combination with manual rotation of carrousel"),
  HandlingError.PLATE_REMOVE_STACKER_SLOT_ERROR: LiconicHandlerPlateRemoveError("Stacker slot cannot be reached"),
  HandlingError.PLATE_REMOVE_REMOTE_ACCESS_LEVEL_ERROR: LiconicHandlerPlateRemoveError("Undefined stacker level has been requested"),
  HandlingError.PLATE_REMOVE_PLATE_TRANSFER_DETECTION_ERROR: LiconicHandlerPlateRemoveError("Export operation while plate is on transfer station"),
  HandlingError.PLATE_REMOVE_LIFT_INITIALIZATION_ERROR: LiconicHandlerPlateRemoveError("Lift could not be initialized"),
  HandlingError.PLATE_REMOVE_PLATE_ON_SHOVEL_DETECTION: LiconicHandlerPlateRemoveError("Trying to load a plate, when a plate is already on the shovel"),
  HandlingError.PLATE_REMOVE_NO_PLATE_ON_SHOVEL_DETECTION: LiconicHandlerPlateRemoveError("Trying to remove or place plate with no plate on the shovel"),
  HandlingError.PLATE_REMOVE_NO_RECOVERY: LiconicHandlerPlateRemoveError("Recovery was not possible"),

  HandlingError.BARCODE_READ_GENERAL_HANDLING_ERROR: LiconicHandlerBarcodeReadError("Handling action could not be performed in time."),
  HandlingError.BARCODE_READ_GATE_OPEN_ERROR: LiconicHandlerBarcodeReadError("Gate could not reach upper position or Gate did not reach upper position in time"),
  HandlingError.BARCODE_READ_GATE_CLOSE_ERROR: LiconicHandlerBarcodeReadError("Gate could not reach lower position or Gate did not reach lower position in time"),
  HandlingError.BARCODE_READ_GENERAL_LIFT_POSITIONING_ERROR: LiconicHandlerBarcodeReadError("Handler-Lift could not reach desired level position or does not move"),
  HandlingError.BARCODE_READ_USER_ACCESS_ERROR: LiconicHandlerBarcodeReadError("Unauthorized user access in combination with manual rotation of carrousel"),
  HandlingError.BARCODE_READ_STACKER_SLOT_ERROR: LiconicHandlerBarcodeReadError("Stacker slot cannot be reached"),
  HandlingError.BARCODE_READ_REMOTE_ACCESS_LEVEL_ERROR: LiconicHandlerBarcodeReadError("Undefined stacker level has been requested"),
  HandlingError.BARCODE_READ_PLATE_TRANSFER_DETECTION_ERROR: LiconicHandlerBarcodeReadError("Export operation while plate is on transfer station"),
  HandlingError.BARCODE_READ_LIFT_INITIALIZATION_ERROR: LiconicHandlerBarcodeReadError("Lift could not be initialized"),
  HandlingError.BARCODE_READ_PLATE_ON_SHOVEL_DETECTION: LiconicHandlerBarcodeReadError("Trying to load a plate, when a plate is already on the shovel"),
  HandlingError.BARCODE_READ_NO_PLATE_ON_SHOVEL_DETECTION: LiconicHandlerBarcodeReadError("Trying to remove or place plate with no plate on the shovel"),
  HandlingError.BARCODE_READ_NO_RECOVERY: LiconicHandlerBarcodeReadError("Recovery was not possible"),

  HandlingError.PLATE_PLACE_GENERAL_HANDLING_ERROR: LiconicHandlerPlatePlaceError("Handling action could not be performed in time."),
  HandlingError.PLATE_PLACE_GATE_OPEN_ERROR: LiconicHandlerPlatePlaceError("Gate could not reach upper position or Gate did not reach upper position in time"),
  HandlingError.PLATE_PLACE_GATE_CLOSE_ERROR: LiconicHandlerPlatePlaceError("Gate could not reach lower position or Gate did not reach lower position in time"),
  HandlingError.PLATE_PLACE_GENERAL_LIFT_POSITIONING_ERROR: LiconicHandlerPlatePlaceError("Handler-Lift could not reach desired level position or does not move"),
  HandlingError.PLATE_PLACE_USER_ACCESS_ERROR: LiconicHandlerPlatePlaceError("Unauthorized user access in combination with manual rotation of carrousel"),
  HandlingError.PLATE_PLACE_STACKER_SLOT_ERROR: LiconicHandlerPlatePlaceError("Stacker slot cannot be reached"),
  HandlingError.PLATE_PLACE_REMOTE_ACCESS_LEVEL_ERROR: LiconicHandlerPlatePlaceError("Undefined stacker level has been requested"),
  HandlingError.PLATE_PLACE_PLATE_TRANSFER_DETECTION_ERROR: LiconicHandlerPlatePlaceError("Export operation while plate is on transfer station"),
  HandlingError.PLATE_PLACE_LIFT_INITIALIZATION_ERROR: LiconicHandlerPlatePlaceError("Lift could not be initialized"),
  HandlingError.PLATE_PLACE_PLATE_ON_SHOVEL_DETECTION: LiconicHandlerPlatePlaceError("Trying to load a plate, when a plate is already on the shovel"),
  HandlingError.PLATE_PLACE_NO_PLATE_ON_SHOVEL_DETECTION: LiconicHandlerPlatePlaceError("Trying to remove or place plate with no plate on the shovel"),
  HandlingError.PLATE_PLACE_NO_RECOVERY: LiconicHandlerPlatePlaceError("Recovery was not possible"),

  HandlingError.PLATE_SET_GENERAL_HANDLING_ERROR: LiconicHandlerPlateSetError("Handling action could not be performed in time."),
  HandlingError.PLATE_SET_GATE_OPEN_ERROR: LiconicHandlerPlateSetError("Gate could not reach upper position or Gate did not reach upper position in time"),
  HandlingError.PLATE_SET_GATE_CLOSE_ERROR: LiconicHandlerPlateSetError("Gate could not reach lower position or Gate did not reach lower position in time"),
  HandlingError.PLATE_SET_GENERAL_LIFT_POSITIONING_ERROR: LiconicHandlerPlateSetError("Handler-Lift could not reach desired level position or does not move"),
  HandlingError.PLATE_SET_USER_ACCESS_ERROR: LiconicHandlerPlateSetError("Unauthorized user access in combination with manual rotation of carrousel"),
  HandlingError.PLATE_SET_STACKER_SLOT_ERROR: LiconicHandlerPlateSetError("Stacker slot cannot be reached"),
  HandlingError.PLATE_SET_REMOTE_ACCESS_LEVEL_ERROR: LiconicHandlerPlateSetError("Undefined stacker level has been requested"),
  HandlingError.PLATE_SET_PLATE_TRANSFER_DETECTION_ERROR: LiconicHandlerPlateSetError("Export operation while plate is on transfer station"),
  HandlingError.PLATE_SET_LIFT_INITIALIZATION_ERROR: LiconicHandlerPlateSetError("Lift could not be initialized"),
  HandlingError.PLATE_SET_PLATE_ON_SHOVEL_DETECTION: LiconicHandlerPlateSetError("Trying to load a plate, when a plate is already on the shovel"),
  HandlingError.PLATE_SET_NO_PLATE_ON_SHOVEL_DETECTION: LiconicHandlerPlateSetError("Trying to remove or place plate with no plate on the shovel"),
  HandlingError.PLATE_SET_NO_RECOVERY: LiconicHandlerPlateSetError("Recovery was not possible"),

  HandlingError.PLATE_GET_GENERAL_HANDLING_ERROR: LiconicHandlerPlateGetError("Handling action could not be performed in time."),
  HandlingError.PLATE_GET_GATE_OPEN_ERROR: LiconicHandlerPlateGetError("Gate could not reach upper position or Gate did not reach upper position in time"),
  HandlingError.PLATE_GET_GATE_CLOSE_ERROR: LiconicHandlerPlateGetError("Gate could not reach lower position or Gate did not reach lower position in time"),
  HandlingError.PLATE_GET_GENERAL_LIFT_POSITIONING_ERROR: LiconicHandlerPlateGetError("Handler-Lift could not reach desired level position or does not move"),
  HandlingError.PLATE_GET_USER_ACCESS_ERROR: LiconicHandlerPlateGetError("Unauthorized user access in combination with manual rotation of carrousel"),
  HandlingError.PLATE_GET_STACKER_SLOT_ERROR: LiconicHandlerPlateGetError("Stacker slot cannot be reached"),
  HandlingError.PLATE_GET_REMOTE_ACCESS_LEVEL_ERROR: LiconicHandlerPlateGetError("Undefined stacker level has been requested"),
  HandlingError.PLATE_GET_PLATE_TRANSFER_DETECTION_ERROR: LiconicHandlerPlateGetError("Export operation while plate is on transfer station"),
  HandlingError.PLATE_GET_LIFT_INITIALIZATION_ERROR: LiconicHandlerPlateGetError("Lift could not be initialized"),
  HandlingError.PLATE_GET_PLATE_ON_SHOVEL_DETECTION: LiconicHandlerPlateGetError("Trying to load a plate, when a plate is already on the shovel"),
  HandlingError.PLATE_GET_NO_PLATE_ON_SHOVEL_DETECTION: LiconicHandlerPlateGetError("Trying to remove or place plate with no plate on the shovel"),
  HandlingError.PLATE_GET_NO_RECOVERY: LiconicHandlerPlateGetError("Recovery was not possible during get plate")
}
