"""The MPE2's firmware errors and warnings, by interface, with Hamilton's own help text.

Read from the instrument definitions embedded in Hamilton's module assemblies
(``Hamilton.Module.SPEMainController.dll`` and the ``SPEAccessoryController`` ones, 2021). The
accessories' errors (reagent fill, evaporator) are kept, so an MPE2 fitted with them reports its
errors by name although the driver doesn't control the accessories yet.
"""

from typing import Dict, Tuple

# The bits an interface's own error code is carried in (as the Nimbus error table shows).
INTERFACE_ERROR_BASE = 0x0F00

# (interface name, code) -> (name, Hamilton's help text).
ERRORS: Dict[Tuple[str, int], Tuple[str, str]] = {
  ("ISPEControl", 0x01): (
    "InvalidCommandForState",
    "The command issued may not be executed in the current state of the SPE unit.",
  ),
  ("ISPEControl", 0x02): (
    "InvalidHeight",
    "The height of the plate being added is invalid for the system.",
  ),
  ("ISPEControl", 0x03): (
    "InvalidMotorControlState",
    "The state of the motor controller is not the expected value.",
  ),
  ("ISPEControl", 0x04): (
    "MotorControlUnitNotAvailable",
    "Communication to the the motor control unit failed. The motor control unit may not be connected.",
  ),
  ("ISPEControl", 0x05): ("SPEProcessAborted", "The SPE process was aborted."),
  ("ISPEControl", 0x06): (
    "WasteSystemErrorCausedAbort",
    "An error was reported by the waste system causing an abort.",
  ),
  ("ISPEControl", 0x07): (
    "RequestedFlowOutsideCalibrationRange",
    "The requested flow rate lies outside the calibration range.",
  ),
  ("ISPEControl", 0x08): (
    "InsufficientCalibrationData",
    "Fewer than 2 manifold calibration points are present.",
  ),
  ("ISPEControl", 0x09): (
    "RequestedPressureOutsideCalibrationRange",
    "The requested pressure lies outside the calibration range.",
  ),
  ("ISPEControl", 0x0A): ("InvalidDelay", "The requested delay must be greater than 0."),
  ("ISPEControl", 0x0B): (
    "IntrusionDetectedCausedAbort",
    "An Intrusion Detected event caused an abort.",
  ),
  ("ISPEControl", 0x0C): (
    "PressureDidNotDissipate",
    "Pressure in the system did not dissipate after completing the SPE Operation.",
  ),
  ("ISPEControl", 0x0D): ("InvalidNumberOfPorts", "The configured number of ports is invalid."),
  ("ISPEControl", 0x0E): (
    "MaximumInputPressureExceeded",
    "The input pressure has exceeded 120psi.",
  ),
  ("ISPEControl", 0x0F): (
    "VacuumUnderManualControl",
    "The vacuum is under manual control and unavailable for SPE use.",
  ),
  ("ISPEControl", 0x10): ("MinimumInputPressureNotProvided", "The input pressure is below 80psi."),
  ("ISPEControl", 0x11): (
    "AcmeRequiresWasteContainer",
    "Cannot initialize with no waste container when the Acme Dispenser is present.",
  ),
  ("ISPEControl", 0x12): (
    "AcmeUnitNotAvailable",
    "Communication to the Acme unit failed after initially being detected.",
  ),
  ("ISPEMainControllerService", 0x01): ("VoltageOutOfRange3p3", "3.3 V supply out of range."),
  ("ISPEMainControllerService", 0x02): ("VoltageOutOfRange5", "5 V supply out of range."),
  ("ISPEMainControllerService", 0x03): ("VoltageOutOfRange36", "36.0 V supply out of range."),
  ("ISPEMainControllerService", 0x04): ("VoltageOutOfRange48", "48.0 V supply out of range."),
  ("IValvesAndSensors", 0x01): (
    "DirectionalValveOverCurrent",
    "An over current was detected for the Directional Valve.",
  ),
  ("IValvesAndSensors", 0x02): (
    "ManifoldBleedValveOverCurrent",
    "An over current was detected for the Manifold Bleed Valve.",
  ),
  ("IValvesAndSensors", 0x03): (
    "HighPressureValveOverCurrent",
    "An over current was detected for the High Pressure Valve.",
  ),
  ("IValvesAndSensors", 0x04): (
    "LowPressureValveOverCurrent",
    "An over current was detected for the Low Pressure Valve.",
  ),
  ("IPressureSensorCalibration", 0x01): ("PressureOffsetTooLow", "Pressure offset is too low."),
  ("IPressureSensorCalibration", 0x02): ("PressureOffsetTooHigh", "Pressure offset is too high."),
  ("IFlowSensorCalibration", 0x01): ("FlowOffsetTooLow", "Flow offset is too low."),
  ("IFlowSensorCalibration", 0x02): ("FlowOffsetTooHigh", "Flow offset is too high."),
  ("IWasteSystem", 0x01): ("NoWasteContainerSelected", "No Waste Container is selected."),
  ("IWasteSystem", 0x02): ("WasteContainerBecameFull", "The selected Waste Container became full."),
  ("IWasteSystem", 0x03): ("WasteContainerRemoved", "The selected Waste Container was removed."),
  ("IWasteSystem", 0x04): (
    "WasteContainerNotPresent",
    "The Waste Container is not connected to the unit.",
  ),
  ("IWasteSystem", 0x05): ("WasteContainerIsFull", "The Waste Container is full."),
  ("IWasteSystem", 0x06): (
    "WasteMultiplexOverCurrentDetected",
    "An overcurrent was detected in the waste multiplex.",
  ),
  ("IWasteSystem", 0x07): (
    "VacuumInUseBySPEProcess",
    "The vacuum is in use by an SPE Process, return the system to the Ready state to use.",
  ),
  ("IVacuum", 0x01): (
    "InsufficientVacuum",
    "Sufficient vacuum has not been maintained, check that the container is sealed, lines are intact, and the exhaust is not clogged.",
  ),
  ("IVacuum", 0x02): (
    "ExcessiveVacuum",
    "Excessive vacuum detected, check for clogged, disconnected, or pinched lines.",
  ),
  ("IVacuum", 0x03): ("VacuumAlreadyStarted", "Vacuum Pump has already been started."),
  ("IVacuum", 0x04): (
    "VacuumOverCurrentDetected",
    "An overcurrent was detected during vacuum operation.",
  ),
  ("IVacuum", 0x05): (
    "UnableToBuildVacuum",
    "Was unable to build sufficient vacuum in the allowed time. Check that the container is sealed, lines are intact, and the exhaust is not clogged.",
  ),
  ("IVacuum", 0x06): (
    "NewWasteSystemNotSupported",
    "The Main Controller PCB is Revision 0 which does not support the new waste system configuration.",
  ),
  ("IVacuum", 0x07): (
    "VacuumNotStableBeforeTimeout",
    "The vacuum had not stabilized when the vacuum timeout occurred.",
  ),
  ("IVacuumCalibration", 0x01): ("PressureOffsetTooLow", "Pressure offset is too low."),
  ("IVacuumCalibration", 0x02): ("PressureOffsetTooHigh", "Pressure offset is too high."),
  ("IManifoldCalibration", 0x01): (
    "MotorControllerNotRegistered",
    "The Motor Controller has not registered. The external archive is not accessible.",
  ),
  ("IManifoldCalibration", 0x02): (
    "TooManyCalibrationPoints",
    "The maximum number of calibration points has been exceeded.",
  ),
  ("IManifoldCalibration", 0x03): (
    "TooManyPredictiveGainEntries",
    "The maximum number of predictive gain entries has been exceeded.",
  ),
  ("ILowPressureValvePressureControl", 0x01): (
    "PIDControlNotEnabled",
    "The requested operation could not be performed because PID control is not enabled.",
  ),
  ("IL6470Axis", 0x01): ("MotionWouldExceedTravelLimit", "Motion would exceed travel limit."),
  ("IL6470Stepper", 0x01): (
    "SeekPositionLimitReached",
    "A seek operation attempted to travel beyond the permitted step position.",
  ),
  ("IL6470Stepper", 0x02): (
    "SeekOperationDidNotStart",
    "A seek operation was attempted but did not start.",
  ),
  ("IL6470StepperEx1", 0x01): (
    "OverCurrentDetected",
    "An over current condition has been detected.",
  ),
  ("IL6470StepperEx1", 0x02): (
    "ThermalShutdownDetected",
    "A temperature of over 160 degrees C has been detected.",
  ),
  ("IL6470StepperEx1", 0x03): (
    "UnderVoltageDetected",
    "An under voltage condition has been detected.",
  ),
  ("IL6470StepperEx1", 0x04): ("StallDetectedOnBridgeA", "Stall detected on bridge A."),
  ("IL6470StepperEx1", 0x05): ("StallDetectedOnBridgeB", "Stall detected on bridge B."),
  ("IL6470StepperEx1", 0x06): ("WrongCommand", "Attempted to execute an invalid command."),
  ("IL6470StepperEx1", 0x07): (
    "NonPerformableCommand",
    "Attempted to execute a non-performable command.",
  ),
  ("IL6470StepperEx1", 0x08): (
    "WriteErrorMotorNotStopped",
    "Unable to write to a register while the motor is in motion.",
  ),
  ("IL6470StepperEx1", 0x09): (
    "WriteErrorBridgesNotDisabled",
    "Unable to write to a register while the motor bridges are not in a high impedance state.",
  ),
  ("IL6470StepperEx1", 0x0A): (
    "CommandErrorBusy",
    "Unable to execute a command because the motor driver is busy.",
  ),
  ("IL6470StepperEx1", 0x0B): (
    "CommandErrorNotStopped",
    "Unable to execute a command because the motor driver is state is not stopped.",
  ),
  ("IL6470StepperEx1", 0x0C): (
    "MotionStoppedPrematurely",
    "Motion was stopped prematurely via either by a call to Stop() or HiZ().",
  ),
  ("IPressureControl", 0x01): (
    "PIDControlNotEnabled",
    "The requested operation could not be performed because PID control is not enabled.",
  ),
  ("IPressureControl", 0x02): (
    "PIDControlLost",
    "PID control of the valve was lost due to too large of an error between the desired and actual output pressures.",
  ),
  ("IPressureControl", 0x03): (
    "OverCurrent",
    "The valve was shut down due to an over current condition occurring.",
  ),
  ("IPressureControl", 0x04): ("CalibrationDataNotLoaded", "No calibration data has been loaded."),
  ("IPressureControl", 0x05): (
    "SensorReadFailure",
    "The sensor value was not able to be determined.",
  ),
  ("IPressureControl", 0x06): (
    "MaximumInputPressureExceeded",
    "The input pressure exceeded the maximum, 120PSI.",
  ),
  ("IPressureControl", 0x07): (
    "MinimumInputPressureNotProvided",
    "The input pressure to the system is below the minimum, 80PSI.",
  ),
  ("IAxisWrapper", 0x01): (
    "MotionStoppedPrematurely",
    "Motion was stopped prematurely via a call to IAxisWrapper.Stop(BOOL hard).",
  ),
  ("IAXIS", 0x01): ("HCRESULT_SEQUENCER_EXCEPTION", "Sequencer Exception."),
  ("IAXIS", 0x02): ("HCRESULT_STATIC_POSITION_ERROR_LIMIT", "Static Position Error Limit."),
  ("IAXIS", 0x03): ("HCRESULT_DYNAMIC_POSITION_ERROR_LIMIT", "Dynamic Position Error Limit."),
  ("IAXIS", 0x04): ("HCRESULT_SETTLING_POSITION_ERROR_LIMIT", "Settling Position Error Limit."),
  ("IAXIS", 0x05): ("HCRESULT_POSITIVE_HARD_POSITION_LIMIT", "Positive Hard Position Limit."),
  ("IAXIS", 0x06): ("HCRESULT_NEGATIVE_HARD_POSITION_LIMIT", "Negative Hard Position Limit."),
  ("IAXIS", 0x07): ("HCRESULT_POSITIVE_SOFT_POSITION_LIMIT", "Positive Soft Position Limit."),
  ("IAXIS", 0x08): ("HCRESULT_NEGATIVE_SOFT_POSITION_LIMIT", "Negative Soft Position Limit."),
  ("IAXIS", 0x09): ("HCRESULT_POSITION_FLAG_NOT_FOUND", "Position Flag Not Found."),
  ("IAXIS", 0x0A): (
    "HCRESULT_MOTION_WOULD_EXCEED_TRAVEL_LIMIT",
    "Motion Would Exceed Travel Limit.",
  ),
  ("IAXIS", 0x0B): ("HCRESULT_SERVO_NOT_ENABLED", "Servo Not Enabled."),
  ("IAXIS", 0x0C): ("HCRESULT_COULD_NOT_START_TRAJECTORY", "Could Not Start Trajectory."),
  ("IAXIS", 0x0D): ("HCRESULT_INCOMPLETE_CONFIGURATION", "Incomplete Configuration."),
  ("IAXIS", 0x0E): ("HCRESULT_FLASH_MEMORY_FAILURE", "Flash Memory Failure."),
  ("IAXIS", 0x0F): (
    "HCRESULT_COULD_NOT_START_DUE_TO_SERVO_LOOP_OVERRUN",
    "Could Not Start Due To Servo Loop Overrun.",
  ),
  ("IAXIS", 0x10): (
    "HCRESULT_COULD_NOT_START_DUE_TO_SERVO_NOT_ENABLED",
    "Could Not Start Due To Servo Not Enabled.",
  ),
  ("IAXIS", 0x11): (
    "HCRESULT_COULD_NOT_START_DUE_TO_SEQUENCER_NOT_IDLE",
    "Could Not Start Due To Sequencer Not Idle.",
  ),
  ("IAXIS", 0x12): (
    "HCRESULT_COULD_NOT_START_DUE_TO_SETTLING_WINDOW_TRIP",
    "Could Not Start Due To Settling Window Trip.",
  ),
  ("IAXIS", 0x13): (
    "HCRESULT_COULD_NOT_START_DUE_TO_DYNAMIC_POSITION_ERROR",
    "Could Not Start Due To Dynamic Position Error.",
  ),
  ("IAXIS", 0x14): (
    "HCRESULT_COULD_NOT_START_DUE_TO_STATIC_POSITION_ERROR",
    "Could Not Start Due To Static Position Error.",
  ),
  ("IAXIS", 0x15): (
    "HCRESULT_COULD_NOT_START_DUE_TO_SEQUENCER_EXCEPTION",
    "Could Not Start Due To Sequencer Exception.",
  ),
  ("IAXIS", 0x16): (
    "HCRESULT_COULD_NOT_START_DUE_TO_ZERO_VEL_OR_ACC",
    "Could Not Start Due To Zero Vel Or Acc.",
  ),
  ("IAXIS", 0x17): ("HCRESULT_SERVO_LOOP_OVERRUN", "Servo Loop Overrun."),
  ("ISPEMotorControllerService", 0x01): ("VoltageOutOfRange3p3", "3.3 V supply out of range."),
  ("ISPEMotorControllerService", 0x02): ("VoltageOutOfRange5", "5.0 V supply out of range."),
  ("ISPEMotorControllerService", 0x03): ("VoltageOutOfRange36", "36.0 V supply out of range."),
  ("ISPEMotorControllerService", 0x04): ("VoltageOutOfRange48", "48.0 V supply out of range."),
  ("ISPEMotorControl", 0x01): (
    "InvalidCommand",
    "The command issued may not be executed in the current state of the SPE unit.",
  ),
  ("ISPEMotorControl", 0x02): (
    "FilterPlateDetectedDuringInitialization",
    "A filter plate was detected during initialization. This error also can occur if an evaporator was left on the filter adapter during initialization.",
  ),
  ("ISPEMotorControl", 0x03): (
    "CollectionPlateDetectedDuringInitialization",
    "A collection plate was detected during initialization.",
  ),
  ("ISPEMotorControl", 0x04): (
    "FilterPlateAdapterNotDetectedDuringInitialization",
    "The filter plate adapter was not detected during initialization.",
  ),
  ("ISPEMotorControl", 0x05): (
    "WasteTrayNotDetectedDuringInitialization",
    "The waste tray was not detected during initialization.",
  ),
  ("ISPEMotorControl", 0x06): (
    "FilterPlateTooShort",
    "The specified filter plate is too short to clamp.",
  ),
  ("ISPEMotorControl", 0x07): (
    "FilterPlateNozzlesTooLong",
    "The specified filter plate nozzles are too long to fit into the work station.",
  ),
  ("ISPEMotorControl", 0x08): (
    "FilterPlateNozzlesTooShort",
    "The specified filter plate nozzles are too short.",
  ),
  ("ISPEMotorControl", 0x09): (
    "FilterPlateCollectionPlateComboTooLarge",
    "The filter plate and collection plate are too large to use in the workstation.",
  ),
  ("ISPEMotorControl", 0x0A): (
    "FilterPlateCollectionPlateComboTooLargeDueToEffectiveNozzleHeight",
    "The filter plate and collection plate are too large to use in the workstation due to the effective nozzle height.",
  ),
  ("ISPEMotorControl", 0x0B): (
    "FilterPlateCollectionPlateComboTooLargeDueToEitherEffectiveNozzleHeightOrCollectionPlateOffset",
    "The filter plate and collection plate are too large to use in the workstation due to the effective nozzle height or the collection plate offset.",
  ),
  ("ISPEMotorControl", 0x0C): (
    "NozzlePenetrationTooGreat",
    "The offset specified will cause the nozzles of the filter plate will penetrate too far into the collection plate wells.",
  ),
  ("ISPEMotorControl", 0x0D): (
    "DispensingNotSupportedWithCurrentHardware",
    "Dispensing is not supported with the current hardware.",
  ),
  ("ISPEMotorControl", 0x0E): (
    "DispensingArmNotPresent",
    "Dispensing may not be conducted because the dispensing arm is not present.",
  ),
  ("ISPEMotorControl", 0x0F): (
    "EvaporatorDetectedDuringInitialization",
    "An evaporator was detected during initialization.",
  ),
  ("ISPEMotorControllerDebug", 0x01): (
    "InvalidConformationCode",
    "An invalid confirmation code was used.",
  ),
  ("ISPEMotorControllerDebug", 0x02): (
    "InterfaceIsLocked",
    "The interface is locked and must be unlocked.",
  ),
  ("ISPEMotorControllerDebug", 0x03): (
    "DispenserMotorNotPresent",
    "The dispenser motor is not present and must be in order to override its present state.",
  ),
  ("IPld", 0x01): ("IncompatiblePldVersion", "The PLD version is incompatible."),
  ("IPld", 0x02): ("UnprogrammedPld", "The PLD is not programmed."),
  ("IPlateShuttleAxis", 0x01): (
    "HomeFlagNotActive",
    "The home flag was not active when it was expected to be.",
  ),
  ("IPlateShuttleAxis", 0x02): (
    "FailedToMoveToProcessArea",
    "The shuttle failed to move to the process area.",
  ),
  ("IPlateShuttleAxis", 0x03): (
    "FailedToMoveToIntegrationArea",
    "The shuttle failed to move to the integration area.",
  ),
  ("IPlateShuttleAxis", 0x04): ("StallDetected", "The shuttle stalled during a move."),
  ("ILifterAxis", 0x01): (
    "HomeFlagNotActive",
    "The home flag was not active when it was expected to be.",
  ),
  ("ILifterAxis", 0x02): ("MotorStallDetected", "A motor stall was detected."),
  ("IUpperLifterAxis", 0x01): (
    "UpperLifterNotCalibrated",
    "The upper lifter has not yet been calibrated.",
  ),
  ("IUpperLifterAxis", 0x02): ("AlreadyClamped", "A filter plate is already clamped."),
  ("IUpperLifterAxis", 0x03): (
    "FilterPlateTooTall",
    "The filter plate being clamped is taller that what was specified.",
  ),
  ("IUpperLifterAxis", 0x04): (
    "FilterPlateTooShort",
    "The filter plate being clamped is shorter that what was specified.",
  ),
  ("IUpperLifterAxis", 0x05): (
    "UnableToMoveDueToIntrusion",
    "The upper lifter cannot be used because an obstruction is intruding into its crush zone.",
  ),
  ("IUpperLifterAxis", 0x06): (
    "MotionStoppedDueToIntrusion",
    "The upper lifter has been stopped due to an object intruding into its crush zone.",
  ),
  ("IUpperLifterAxis", 0x07): (
    "IntrusionSensorsNotOperational",
    "The intrusion sensors are not operational.",
  ),
  ("IUpperLifterAxis", 0x08): (
    "ObstructionDetected",
    "An obstruction was detected and motion has been stopped.",
  ),
  ("IUpperLifterAxis", 0x09): (
    "EvaporatorTooTall",
    "The evaporator being clamped is taller than expected.",
  ),
  ("IUpperLifterAxis", 0x0A): (
    "EvaporatorTooShort",
    "The evaporator being clamped is shorter than expected.",
  ),
  ("ILowerLifterAxis", 0x01): (
    "CollectionPlateTooTall",
    "The collection plate being raised is taller that what was specified.",
  ),
  ("ILowerLifterAxis", 0x02): ("LowerLifterStalled", "The lower lifter stalled."),
  ("IDispenseAxis", 0x01): (
    "NotSupportedThisHardwareRevision",
    "The request is not supported with this hardware revision.",
  ),
  ("IDispenseAxis", 0x02): (
    "OptionalMotorNotPresent",
    "The request cannot be executed because the optional motor is not present.",
  ),
  ("IDispenseAxis", 0x03): ("DispenserMotorStalled", "The dispenser motor stalled."),
  ("IPlateShuttleCalibration", 0x01): (
    "MustPrepareShuttleForCalibration",
    "The shuttle has not been prepared for calibration yet.",
  ),
  ("IPlateShuttleCalibration", 0x02): (
    "MustSetShuttleIntegrationArea",
    "The shuttle's integration area must be set first.",
  ),
  ("IPlateShuttleCalibration", 0x03): (
    "MustSetShuttleProcessArea",
    "The shuttle's process area must be set first.",
  ),
  ("IPlateShuttleCalibration", 0x04): (
    "NoCalibrationData",
    "The shuttle has not yet been calibrated.",
  ),
  ("IPlateShuttleCalibration", 0x05): (
    "InvalidLocationForIntegrationPosition",
    "The shuttle is not properly located for integration area calibration.",
  ),
  ("IPlateShuttleCalibration", 0x06): (
    "InvalidLocationForProcessPosition",
    "The shuttle is not properly located for process area calibration.",
  ),
  ("IPlateShuttleCalibration", 0x07): (
    "ProcessAreaSensorNotActivated",
    "The shuttle is not positioned such that the process area sensor is activated.",
  ),
  ("IPlateShuttleCalibrationEx05", 0x01): (
    "MustSetShuttleProcessArea",
    "The shuttle's process area must be set first.",
  ),
  ("IPressureCalibration", 0x01): (
    "UnableToReadSensor",
    "The sensor is unable to be read and the calibration point cannot be added.",
  ),
  ("IPressureCalibration", 0x02): (
    "MaximumNumberOfCalibrationPointsExceeded",
    "The maximum number of calibration points have been added.",
  ),
  ("IPressureCalibration", 0x03): (
    "DuplicateCalibrationPoint",
    "One or more calibration point conflicts with the raw sensor reading or pressure of a different calibration point. No new calibration points have been added.",
  ),
  ("IPressureCalibration", 0x04): (
    "WorkingCalibrationDataNotClear",
    "The operation cannot be completed due to calibration data not being clear. No calibration points are allowed in working memory while this operation is run.",
  ),
  ("IPressureSensor", 0x01): (
    "UnableToReadSensor",
    "The sensor is unable to be read at this time.",
  ),
  ("IExtManagedMemoryBlock", 0x01): ("InvalidIndex", "The index value is invalid."),
  ("IExtManagedMemoryBlock", 0x02): (
    "InvalidLength",
    "The length exceeds the block's capacity relative to the specified data index.",
  ),
  ("IExtManagedMemoryBlock", 0x03): (
    "InvalidConfirmationCode",
    "The confirmation code provided is invalid.",
  ),
  ("IExtManagedMemoryBlock", 0x04): (
    "InvalidArchive",
    "The archive is invalid and the requested operation cannot be performed.",
  ),
  ("IExtManagedMemoryBlock", 0x05): (
    "FailedToCreateArchive",
    "The archive was unable to be prepared for use.",
  ),
  ("IDispenser", 0x01): (
    "ObjectCannotBeUsedDueToMissingHardware",
    "The dispense/priming functionality cannot be used because some hardware is missing or not supported.",
  ),
  ("IDispenser", 0x02): (
    "InvalidPrimeState",
    "The prime command issued may not be executed in the current state.",
  ),
  ("IDispenser", 0x03): (
    "InvalidMotorControllerState",
    "The motor controller is not in the proper state for this command.",
  ),
  ("IDispenser", 0x04): ("InvalidSourceSelected", "A selected source is not valid."),
  ("IDispenser", 0x05): ("SourceSelectionNotAvailable", "A selected source is not available."),
  ("IDispenser", 0x06): (
    "NotEnoughFluid",
    "A selected source will not contain enough fluid for the dispense operation.",
  ),
  ("IDispenser", 0x07): (
    "PrimingOperationStopped",
    "A priming operation has stopped because the vacuum stopped working or the module responsible for it is no longer responding.",
  ),
  ("IDispenser", 0x08): (
    "DeionizedWaterMustBeLastSourceContainer",
    "Deionized water can only be configured as the last source.",
  ),
  ("IDispenser", 0x09): (
    "DeionizedWaterLocationNotSet",
    "The location of deionized water has not been configured.",
  ),
  ("IDispenser", 0x0A): ("VolumeFlowTooLow", "The volume flow is too low."),
  ("IDispenser", 0x0B): ("VolumeFlowTooHigh", "The volume flow is too high."),
  ("IDispenser", 0x0C): ("VolumeTooLow", "The volume/well is too low."),
  ("IDispenser", 0x0D): ("VolumeTooHigh", "The volume/well is too high."),
  ("IDispenser", 0x0E): ("NeedlePenetrationTooDeep", "The needle penetration is too deep."),
  ("IDispenser", 0x0F): ("EdgeToWellOffsetTooLow", "The edge to well offset is too low."),
  ("IDispenser", 0x10): ("EdgeToWellOffsetTooHigh", "The edge to well offset is too high."),
  ("IDispenser", 0x11): ("NoWellToWellOffsetSpecified", "No well to well offsets specified."),
  ("IEvaporator", 0x01): (
    "ObjectCannotBeUsedDueToMissingHardware",
    "The evaporation functionality cannot be used because some hardware is missing or not supported.",
  ),
  ("IEvaporator", 0x02): (
    "InvalidCommand",
    "The command issued may not be executed in the current state of the evaporator.",
  ),
  ("IEvaporator", 0x03): (
    "GasHeaterOverCurrent",
    "An over current condition has been detected while using the gas heater.",
  ),
  ("IEvaporator", 0x04): (
    "EvaporatorHeaterOverCurrent",
    "An over current condition has been detected while using the evaporator heater.",
  ),
  ("IEvaporator", 0x05): (
    "TargetTemperatureTooLow",
    "The target temperature is below the limits of the evaporator.",
  ),
  ("IEvaporator", 0x06): (
    "TargetTemperatureTooHigh",
    "The target temperature is above the limits of the evaporator.",
  ),
  ("IEvaporator", 0x07): (
    "TargetTemperatureCouldNotBeReached",
    "The target temperature could not be reached.",
  ),
  ("IEvaporator", 0x08): (
    "HeatingAbortedEvaporatorSensorFailure",
    "An error occurred while reading an evaporator temperature sensor causing a heating cycle to abort.",
  ),
  ("IEvaporator", 0x09): (
    "HeatingAbortedGasSensorFailure",
    "An error occurred while reading a gas temperature sensor causing a heating cycle to abort.",
  ),
  ("IEvaporator", 0x0A): (
    "TemperatureSensorNotWorking",
    "One or both of the temperature sensors on the evaporator could not be configured.",
  ),
  ("IEvaporator", 0x0B): (
    "OverTemperatureDetected",
    "An over temperature condition was detected causing the evaporation process to end.",
  ),
  ("IEvaporator", 0x0C): (
    "Manifold96Evaporator48",
    "A 96 well manifold has been detected and a 48 well evaporator has been installed.",
  ),
  ("IEvaporator", 0x0D): (
    "Manifold96Evaporator24",
    "A 96 well manifold has been detected and a 24 well evaporator has been installed.",
  ),
  ("IEvaporator", 0x0E): (
    "Manifold48Evaporator96",
    "A 48 well manifold has been detected and a 96 well evaporator has been installed.",
  ),
  ("IEvaporator", 0x0F): (
    "Manifold48Evaporator24",
    "A 48 well manifold has been detected and a 24 well evaporator has been installed.",
  ),
  ("IEvaporator", 0x10): (
    "Manifold24Evaporator96",
    "A 24 well manifold has been detected and a 96 well evaporator has been installed.",
  ),
  ("IEvaporator", 0x11): (
    "Manifold24Evaporator48",
    "A 24 well manifold has been detected and a 48 well evaporator has been installed.",
  ),
  ("IEvaporator", 0x12): (
    "PlateTooTall",
    "The placed plate is too tall for the evaporation process.",
  ),
  ("IEvaporator", 0x13): (
    "PlateTooShort",
    "The placed plate is too short for the evaporation process.",
  ),
  ("IEvaporator", 0x14): ("InvalidFlowRate", "The flow rate is invalid."),
  ("IEvaporator", 0x15): (
    "EvaporatorNotCoolingFastEnough",
    "The evaporator has not cooled in the allotted time.",
  ),
  ("IEvaporator", 0x16): (
    "InvalidFollowRate",
    "The evaporation follow rate and time to evaporate will exceed the depth of the well and initial needle offset.",
  ),
  ("IEvaporator", 0x17): (
    "HeatingAbortedDueToAirFlowFailure",
    "An error occurred during a heating process due to a failure in air flow.",
  ),
  ("IEvaporator", 0x18): (
    "EvaporationParametersWillCauseLifterCollision",
    "The evaporation parameters specified will cause the filter adapter on the lower lifter to collide with the underside of the upper lifter. Check initial needle offset, well depth, follow rate, and evaporation time parameters.",
  ),
  ("IEvaporator", 0x19): (
    "GasTemperatureNotHeatingFastEnough",
    "The evaporator gas is not heating fast enough and could be the result of a damaged thermocouple.",
  ),
  ("ISPEAcmeService", 0x01): (
    "DispenseArmMotorNotPresent",
    "The dispense arm motor is not present on the motor controller.",
  ),
  ("ISPEAcmeService", 0x02): (
    "ReagentDispenserNotPresent",
    "The reagent dispenser module is not present.",
  ),
  ("ISPEAcmeService", 0x03): (
    "ReagentDispenserConfigurationNotValid",
    "The configuration of sources does not match the existing sources.",
  ),
  ("ISPEAcmeService", 0x04): ("VacuumIsNotOperational", "The vacuum is not operational."),
  ("ISPEAcmeService", 0x05): ("ManifoldNotPresent", "The manifold is not present."),
  ("ISPEAcmeService", 0x06): (
    "ManifoldNotConfiguredForUse",
    "The manifold is not configured properly for use.",
  ),
  ("ISPEAcmeService", 0x07): (
    "ManifoldDoesNotContainAValidCalibration",
    "A valid calibration is required to control pressure through the manifold.",
  ),
  ("ISPEAcmeService", 0x08): (
    "SourceBottleConfiguredNotPresent",
    "A source bottle that is configured for use is not present.",
  ),
  ("ISPEAcmeService", 0x09): (
    "DeionizedWaterSpecifiedNotInLastPosition",
    "Deionized water is specified but not as the last source.",
  ),
  ("ISPEAcmeService", 0x0A): (
    "UpperLifterNotPresent",
    "The upper lifter motor is not present on the motor controller.",
  ),
  ("ISPEAcmeService", 0x0B): (
    "PlateShuttleNotPresent",
    "The plate shuttle motor is not present on the motor controller.",
  ),
  ("ISPEAcmeService", 0x0C): (
    "GasHeaterNotPresent",
    "The gas heater is not present on the motor controller.",
  ),
  ("ISPEAcmeService", 0x0D): (
    "RtdSensor1Opened",
    "RTD sensor 1 appears to behave as an opened circuit.",
  ),
  ("ISPEAcmeService", 0x0E): (
    "RtdSensor1Shorted",
    "RTD sensor 1 appears to behave as a shorted circuit.",
  ),
  ("ISPEAcmeService", 0x0F): (
    "RtdSensor2Opened",
    "RTD sensor 2 appears to behave as an opened circuit.",
  ),
  ("ISPEAcmeService", 0x10): (
    "RtdSensor2Shorted",
    "RTD sensor 2 appears to behave as a shorted circuit.",
  ),
  ("ISPEAcmeService", 0x11): (
    "InvalidNumberOfManifoldRowsOrColumns",
    "Invlalid number of manifold rows and/or columns was detected.",
  ),
  ("ISPEAcmeService", 0x12): ("VoltageOutOfRange3p3", "3.3 V supply out of range."),
  ("ISPEAcmeService", 0x13): ("VoltageOutOfRange5", "5.0 V supply out of range."),
  ("ISPEAcmeService", 0x14): ("VoltageOutOfRange48", "48.0 V supply out of range."),
  ("ISPEAcmeDebug", 0x01): ("InvalidConfirmationCode", "An invalid confirmation code was used."),
  ("IHeater", 0x01): (
    "TemperatureSensorError",
    "An error occurred while attempting to measure the temperature.",
  ),
  ("IHeater", 0x02): (
    "OverCurrentDetected",
    "An over current condition was detected. When this occurs the heater is shut down.",
  ),
  ("IHeater", 0x03): (
    "ExcessiveTemperature",
    "Temperature is excessively greater than 65 degrees C was detected causing a heating cycle to abort.",
  ),
  ("IHeater", 0x04): (
    "TemperatureSensorFailure",
    "An error occurred while reading a temperature sensor causing a heating cycle to abort.",
  ),
  ("IHeater", 0x05): (
    "FailedToMaintainTemperature",
    "An heating cycle abort because the desired temperature could not be maintained.",
  ),
  ("IReagentPump", 0x01): (
    "FluidSourceNotSelected",
    "The operation cannot be performed because no fluid source has been selected.",
  ),
  ("IReagentPump", 0x02): (
    "InvalidFlowRate",
    "The supplied Flow Rate is outside of the allowed Flow Rate range.",
  ),
  ("IReagentPump", 0x03): (
    "InvalidVolume",
    "The supplied Volume is outside of the allowed Volume range.",
  ),
  ("IReagentPump", 0x04): ("OperationAborted", "The operation was aborted."),
  ("IReagentPump", 0x05): ("BottleSensorNotAvailable", "Unavailable sensors cannot be enabled."),
  ("IReagentPump", 0x06): ("SourceNotAvailable", "The indicated source is not available."),
  ("IReagentPumpService", 0x01): (
    "Internal3p3VSupplyOutOfRange",
    "The internal 3.3V supply is outside of its allowed limits.",
  ),
  ("IReagentPumpService", 0x02): (
    "Internal5VSupplyOutOfRange",
    "The internal 5V supply is outside of its allowed limits.",
  ),
  ("IReagentPumpService", 0x03): (
    "Internal24VSupplyOutOfRange",
    "The internal 24V supply is outside of its allowed limits.",
  ),
  ("IReagentPumpService", 0x04): (
    "External48VSupplyOutOfRange",
    "The external 48V supply is outside of its allowed limits.",
  ),
  ("IReagentPumpAxis", 0x01): (
    "HomeFlagNotActive",
    "The home flag was not active when it was expected to be.",
  ),
  ("IReagentPumpAxis", 0x02): ("StallDetected", "The pump stalled during a move."),
  ("IReagentPumpCalibration", 0x01): ("NotCalibrated", "The pump has not been calibrated."),
  ("IValveControlService", 0x01): ("VoltageOutOfRange3p3", "3.3 V supply out of range."),
  ("IValveControlService", 0x02): ("VoltageOutOfRange24", "24.0 V supply out of range."),
  ("ISourceContainer", 0x01): (
    "LoadCellFrequencyTooLow",
    "The load cell frequency is below the minimum allowed.",
  ),
  ("ISourceContainer", 0x02): (
    "LoadCellFrequencyTooHigh",
    "The load cell frequency is above the maximum allowed.",
  ),
  ("ISourceContainer", 0x03): (
    "VolumeLimitOutOfRange",
    "The volume limit is bound to 0-100% or 0-MaxVolume mL.",
  ),
  ("ISourceContainerCalibration", 0x01): (
    "CalibrationAlreadyStarted",
    "The calibration process has already been started.",
  ),
  ("ISourceContainerCalibration", 0x02): (
    "CalibrationNotStarted",
    "The calibration process has not been started.",
  ),
  ("ISourceContainerCalibrationEx05", 0x01): (
    "EmptyWeightNotCalibrated",
    "CalibrateEmptyWeight was not called during calibration.",
  ),
  ("ISourceContainerCalibrationEx05", 0x02): (
    "FullWeightNotCalibrated",
    "CalibrateFullWeight was not called during calibration.",
  ),
  ("ISourceContainerCalibrationEx05", 0x03): (
    "InvalidWeight",
    "The measured weight is invalid, i.e. Full weight is less than or equal to Empty.",
  ),
  ("IBottleSensorService", 0x01): ("VoltageOutOfRange3p3", "3.3 V supply out of range."),
  ("IBottleSensorService", 0x02): ("VoltageOutOfRange5", "5 V supply out of range."),
  ("IBottleSensorService", 0x03): ("VoltageOutOfRange48", "48.0 V supply out of range."),
}

WARNINGS: Dict[Tuple[str, int], Tuple[str, str]] = {
  ("IL6470StepperEx1", 0x01): (
    "ThermalWarningDetected",
    "A temperature of over 130 degrees C has been detected.",
  ),
  ("IPldI2CInterface", 0x01): ("PowerCycleRequired", "A power cycle is required."),
  ("ISourceContainer", 0x01): ("SourceContainerNotCalibrated", "The container is not calibrated."),
  ("ISourceContainer", 0x02): (
    "SourceContainerNotEnabled",
    "The container's volume monitoring is not enabled.",
  ),
}

# The interfaces of each firmware object, by the last part of its path.
OBJECT_INTERFACES: Dict[str, Tuple[str, ...]] = {
  "SPEControl": ("ISPEControl",),
  "WasteSystem": ("IWasteSystem",),
  "Vacuum": ("IVacuum",),
  "VacuumCalibration": ("IVacuumCalibration",),
  "ValvesAndSensors": ("IValvesAndSensors",),
  "SPEMainControllerService": ("ISPEMainControllerService",),
  "HighPressureValveControl": ("IPressureControl",),
  "LowPressureValveControl": ("IPressureControl", "ILowPressureValvePressureControl"),
  "ManifoldPressureControl": ("IPressureControl",),
  "SPEMotorControl": ("ISPEMotorControl",),
  "UpperLifterAxis": ("IUpperLifterAxis", "ILifterAxis", "IL6470Axis"),
  "LowerLifterAxis": ("ILowerLifterAxis", "ILifterAxis", "IL6470Axis"),
  "PlateShuttleAxis": ("IPlateShuttleAxis", "IL6470Axis"),
  "DispenseAxis": ("IDispenseAxis", "IL6470Axis"),
  "PlateShuttleStepper": ("IL6470Stepper", "IL6470StepperEx1"),
  "UpperLifterStepper": ("IL6470Stepper", "IL6470StepperEx1"),
  "LowerLifterStepper": ("IL6470Stepper", "IL6470StepperEx1"),
  "Dispenser": ("IDispenser",),
  "Evaporator": ("IEvaporator",),
  "SPEAcmeService": ("ISPEAcmeService",),
  "ReagentPump": ("IReagentPump",),
  "EvaporatorHeater": ("IHeater",),
  "GasHeater": ("IHeater",),
  "SourceContainer1": ("ISourceContainer",),
  "SourceContainer2": ("ISourceContainer",),
  "SourceContainer1Calibration": ("ISourceContainerCalibration", "ISourceContainerCalibrationEx05"),
  "SourceContainer2Calibration": ("ISourceContainerCalibration", "ISourceContainerCalibrationEx05"),
}


# Objects whose interfaces share error codes: the entry's HOI interface id says which one it is.
INTERFACE_IDS: Dict[str, Dict[int, str]] = {
  # The motor objects' interface ids, from the tested MPE2's tree (25-11-2026).
  "PlateShuttleAxis": {1: "IL6470Axis", 2: "IPlateShuttleAxis"},
  "UpperLifterAxis": {1: "IL6470Axis", 2: "ILifterAxis", 3: "IUpperLifterAxis"},
  "LowerLifterAxis": {1: "IL6470Axis", 2: "ILifterAxis", 3: "ILowerLifterAxis"},
  "DispenseAxis": {1: "IL6470Axis", 2: "IDispenseAxis"},
  "PlateShuttleStepper": {1: "IL6470Stepper", 2: "IL6470StepperEx1"},
  "UpperLifterStepper": {1: "IL6470Stepper", 2: "IL6470StepperEx1"},
  "LowerLifterStepper": {1: "IL6470Stepper", 2: "IL6470StepperEx1"},
  "SourceContainer1Calibration": {
    1: "ISourceContainerCalibration",
    2: "ISourceContainerCalibrationEx05",
  },
  "SourceContainer2Calibration": {
    1: "ISourceContainerCalibration",
    2: "ISourceContainerCalibrationEx05",
  },
}
