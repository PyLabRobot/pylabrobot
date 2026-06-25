# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Unreleased

### Added

- HighRes Biosolutions MicroSpin centrifuge backend (`pylabrobot.centrifuge.highres.MicroSpinBackend`) speaking the device's ASCII command/response protocol over TCP/1000, plus a `MicroSpin(...)` factory.
- In-process `MicroSpinMockServer` (`pylabrobot.centrifuge.highres.mock_server`) that faithfully emulates the MicroSpin's wire protocol -- including the firmware's "`status` blocks until the spindle has stopped" semantics and the low-G spin-down-detection hang -- usable as a Python async context manager or runnable as a script (`python -m pylabrobot.centrifuge.highres.mock_server`) for `nc`/`telnet` debugging.
- `MicroSpinBackend.reset()` recovery helper that issues `abort` -> `clearbuttonabort` -> `status`, using the last as the gate that genuinely confirms the rotor has stopped.
- User guide notebook for the MicroSpin (`docs/user_guide/01_material-handling/centrifuge/highres_microspin.ipynb`).
- `Plate`: optional `stacking_z_height` parameter -- the per-plate vertical pitch when plates are stacked directly on top of each other (`size_z` minus the nesting overlap), mirroring `NestedTipRack.stacking_z_height`. Because it is a physical dimension, plates that differ in it no longer compare equal; `Plate` also now serializes `stacking_z_height` and the pre-existing `plate_type` so both round-trip through `deserialize`/`copy`. (#1110)
- `ResourceStack`: bare plates stacked in the z direction now nest into one another by their `stacking_z_height` (a stack of `N` identical plates is `size_z + (N - 1) * stacking_z_height` tall, for both `get_size_z()` and child placement). Plates without a `stacking_z_height`, and plates wearing a lid, do not nest, so existing behaviour is unchanged. (#1112)

### Fixed

- Imported `unittest.mock` in `pylabrobot/centrifuge/centrifuge_tests.py` (pre-existing bug that prevented the test class from running).

## 0.2.1

### Added

- Tecan Infinite 200 PRO plate reader backend (Infinite M Plex) (#797)
- Tecan Spark plate reader backend (#798)
- `height_volume_data` attribute on `Container` with piecewise-linear interpolation (#938)
- `eppendorf_96_wellplate_500ul_Vb` (#945)
- `thermo_TS_nalgene_1_troughplate_300mL_Fb` (#939)

### Fixed

- Visualizer: PlateAdapter with hole grid and magnetic rack styling (#946)
- Single persistent reader thread in Hamilton backend (#952)

### Changed

- Updated Corning 3603 with empirical cLLD `height_volume_data` (#948)
- Optional deps are now truly optional (#941)
- Unpinned dependency versions (#942)
- Renamed Hamilton trough 60mL/200mL to correct SI casing (#947)
- ARP fallback and streaming for SiLA discovery (#940)

### Removed

- GUI leftovers
