# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Unreleased

### Added

- LI-COR Odyssey Classic (model 9120) infrared imaging system at `pylabrobot.li_cor.odyssey`
- `Scanning`, `ImageRetrieval`, and `InstrumentStatus` capabilities at `pylabrobot.capabilities.scanning.*`
- `DeviceCard` class and `HasDeviceCard` mixin at `pylabrobot.device_card` for instrument identity / provenance metadata (model-base + per-instance two-tier cards)

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
