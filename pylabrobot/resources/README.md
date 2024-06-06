# PLR Resource Library

This is the PyLabRobot resource library, your catalog of resources that can be used on any PLR-integrated machine. If you cannot find something, please contribute what you are looking for!

## Plate Naming Standard

PLR is not actively enforcing a specific plate naming standard but recommends the following:

<img src="_ims/PLR_plate_naming_standards.png" alt="PLR_plate_naming_standards" width="500"/>

This standard is similar to the [Opentrons API labware naming standard](https://ecatalog.corning.com/life-sciences/b2b/UK/en/Microplates/Assay-Microplates/96-Well-Microplates/Costar%C2%AE-Multiple-Well-Cell-Culture-Plates/p/3516) but 1) further sub-categorizes "wellplates" to facilitate communication with day-to-day users, and 2) adds information about the well-bottom geometry.

For example:
- `Cos_96_DWP_2mL_Vb`
- `ThermoScientific_96_DWP_1200ul_Rd`
- `Porvair_6_reservoir_47ml_Vb`

| Well types               | Bottom types              |
|--------------------|--------------------|
| - MTP: "micro-titer plate", plates with well volumnes =< 500 ul <br>- DWP: "deep-well plate", plates with well volumnes > 500 ul <br>- reservoir <br>- MWL: "multi-well plate", loose term for plates that don't fall into any of the above categories<br> | - Fb: "flat bottom" <br>- Ub: "U / round bottom" <br>- Vb: "V / conical bottom" <br> |


## Resource Subclasses

In PLR every physical object is a subclass of the `Resource` superclass (except for `Tip`).
Each subclass adds unique methods or attributes to represent its unique physical specifications and behavior.

Standard `Resource` subclasses include:

- `Deck`
- `Carrier`: provide multiple spots subresources in a well-defined layout
  - `TipCarrier`
  - `PlateCarrier`
  - `MFXCarrier`
  - `ShakerCarrier`
  - `TubeCarrier`
- `Container`: contain liquids
  - `Well`
  - `PetriDish`
  - `Tube`
  - `Trough`
- `ItemizedResource`: contains items in a 2D layout
  - `Plate`
  - `TipRack`
  - `TubeRack`
- `Lid`
- `PlateAdapter`
- `MFXModule`
