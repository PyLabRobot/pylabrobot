# BackendParams Classes Needing Documentation

These classes have no existing documentation source and need manual documentation
(likely requires hardware manual or domain expertise):

(none -- all BackendParams classes have been documented)

## Summary of documentation added

All BackendParams dataclasses in the codebase have been documented. The following
classes received new or expanded docstrings in this pass:

### Hamilton STAR PIP backend (`pylabrobot.hamilton.liquid_handlers.star.pip_backend`)
- `STARPIPBackend.PickUpTipsParams` -- expanded from one-liner
- `STARPIPBackend.DropTipsParams` -- expanded from one-liner
- `STARPIPBackend.AspirateParams` -- expanded from one-liner (legacy source: `aspirate_pip`)
- `STARPIPBackend.DispenseParams` -- expanded from one-liner (legacy source: `dispense_pip`)

### Hamilton STAR 96-head backend (`pylabrobot.hamilton.liquid_handlers.star.head96_backend`)
- `STARHead96Backend.PickUpTips96Params` -- expanded from one-liner
- `STARHead96Backend.DropTips96Params` -- expanded from one-liner
- `STARHead96Backend.Aspirate96Params` -- expanded from one-liner
- `STARHead96Backend.Dispense96Params` -- expanded from one-liner

### Hamilton iSWAP backend (`pylabrobot.hamilton.liquid_handlers.star.iswap`)
- `iSWAPBackend.ParkParams` -- new docstring
- `iSWAPBackend.CloseGripperParams` -- new docstring
- `iSWAPBackend.PickUpParams` -- new docstring (legacy source: `iswap_get_plate`)
- `iSWAPBackend.DropParams` -- new docstring (legacy source: `iswap_put_plate`)
- `iSWAPBackend.MoveToLocationParams` -- new docstring (legacy source: `move_plate_to_position`)

### Hamilton CoRe gripper backend (`pylabrobot.hamilton.liquid_handlers.star.core`)
- `CoreGripper.PickUpParams` -- new docstring
- `CoreGripper.DropParams` -- new docstring
- `CoreGripper.MoveToLocationParams` -- new docstring

### Azenta XPeel (`pylabrobot.azenta.xpeel`)
- `XPeelPeelerBackend.PeelParams` -- new docstring

### BMG Labtech CLARIOstar (`pylabrobot.bmg_labtech.clariostar.absorbance_backend`)
- `CLARIOstarAbsorbanceParams` -- new docstring

### Byonoy Luminescence 96 (`pylabrobot.byonoy.luminescence_96`)
- `Luminescence96.LuminescenceParams` -- new docstring

### Agilent VSpin (`pylabrobot.agilent.vspin.vspin`)
- `VSpinCentrifugeBackend.SpinParams` -- new docstring

### Agilent BioTek Cytation (`pylabrobot.agilent.biotek.cytation`)
- `CytationImagingBackend.CaptureParams` -- new docstring

### Agilent BioTek (`pylabrobot.agilent.biotek.biotek`)
- `BioTekBackend.LuminescenceParams` -- new docstring

### Molecular Devices SpectraMax M5 (`pylabrobot.molecular_devices.spectramax`)
- `SpectraMaxM5FluorescenceBackend.FluorescenceParams` -- new docstring
- `SpectraMaxM5LuminescenceBackend.LuminescenceParams` -- new docstring
- `MolecularDevicesAbsorbanceBackend.AbsorbanceParams` -- new docstring

### Brooks PreciseFlex (`pylabrobot.brooks.precise_flex`)
- `PreciseFlexArmBackend.PickUpParams` -- new docstring
- `PreciseFlexArmBackend.DropParams` -- new docstring
- `PreciseFlexArmBackend.MoveToJointPositionParams` -- new docstring
- `PreciseFlexArmBackend.MoveToLocationParams` -- new docstring

### Already documented (no changes needed)
- `MultidropCombiPeristalticDispensingBackend.DispenseParams`
- `MultidropCombiPeristalticDispensingBackend.PrimeParams`
- `MultidropCombiPeristalticDispensingBackend.PurgeParams`
- `EL406SyringeDispensingBackend.DispenseParams`
- `EL406SyringeDispensingBackend.PrimeParams`
- `EL406PlateWashingBackend.WashParams`
- `EL406PlateWashingBackend.PrimeParams`
- `EL406PeristalticDispensingBackend.DispenseParams`
- `EL406PeristalticDispensingBackend.PrimeParams`
- `_DictBackendParams` (legacy wrapper)
- `BackendParams` (base class)
