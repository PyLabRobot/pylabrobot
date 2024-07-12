# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Unreleased

### Changed

- The `offset` attribute of `Pickup`, `Drop`, `Aspirate`, and `Dispense` dataclasses are now always wrt the center of its `resource`.
- The `offset` parameter is no longer optional in `Pickup`, `Drop`, `Aspirate`, and `Dispense` dataclasses. With LiquidHandler it defaults to `Coordinate(0, 0, 0)`.
- When providing offsets to LH, individual items in the `offsets` list are no longer optional. They must be provided as `Coordinate` objects. The `offsets` list itself is still optional and defaults to `[Coordinate(0, 0, 0)]*len(use_channels)`.
- To aspirate from a single resource with multiple channels, you must now provide that single resource in a list when calling `LiquidHandler.aspirate` and `LiquidHandler.dispense`.
- The non-firmware level commands of `STAR` now take parameters in PLR-native units (mm, uL, mg, etc.) instead of the mixture of PLR-native and firmware-native units (0.1mm, 0.1uL, etc.) that were previously used. The affected commands are `pick_up_tips`, `drop_tips`, `aspirate`, `dispense`, `pick_up_tips96`, `drop_tips96`, `aspirate96`, `dispense96`, `iswap_pick_up_resource`, `iswap_move_picked_up_resource`, `iswap_release_picked_up_resource`, `core_pick_up_resource`, `core_move_picked_up_resource`, `core_release_picked_up_resource`, `move_resource`, and `core_check_resource_exists_at_location_center` (https://github.com/PyLabRobot/pylabrobot/pull/191).

### Added

- Cor_96_wellplate_360ul_Fb plate (catalog number [3603](https://ecatalog.corning.com/life-sciences/b2b/NL/en/Microplates/Assay-Microplates/96-Well-Microplates/Corning®-96-well-Black-Clear-and-White-Clear-Bottom-Polystyrene-Microplates/p/3603))

### Deprecated

- All VENUS-imported Corning-Costar plates, because they don't have unique and usable identifiers, and are probably wrong.
- HamiltonDeck.load_from_lay_file
- Passing single values to LiquidHandler `pick_up_tips`, `drop_tips`, `aspirate`, and `dispense` methods. These methods now require a list of values.
- `hamilton_parse` module and the VENUS labware database parser.
- `PLT_CAR_L4_SHAKER` was deprecated in favor of `MFX_CAR_L5_base` (https://github.com/PyLabRobot/pylabrobot/pull/188/).

### Fixed

- Don't apply an offset to rectangle drawing in the Visualizer.
- Fix Opentrons resource loading (well locations are now lfb instead of ccc)
- Fix Opentrons backend resource definitions: Opentrons takes well locations as ccc instead of lfb
