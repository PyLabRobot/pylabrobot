# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Unreleased

### Added

- Cor_96_wellplate_360ul_Fb plate (catalog number [3603](https://ecatalog.corning.com/life-sciences/b2b/NL/en/Microplates/Assay-Microplates/96-Well-Microplates/Corning®-96-well-Black-Clear-and-White-Clear-Bottom-Polystyrene-Microplates/p/3603))

### Deprecated

- All VENUS-imported Corning-Costar plates, because they don't have unique and usable identifiers, and are probably wrong.
- HamiltonDeck.load_from_lay_file

### Fixed

- Don't apply an offset to rectangle drawing in the Visualizer.
- Fix Opentrons resource loading (well locations are now lfb instead of ccc)
- Fix Opentrons backend resource definitions: Opentrons takes well locations as ccc instead of lfb
