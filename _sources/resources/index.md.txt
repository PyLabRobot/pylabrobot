# Resource Library

The PyLabRobot Resource Library (PLR-RL) is PyLabRobot's open-source, crowd-sourced collection of labware.
Laboratories across the world use an almost infinite number of different resources/labware.
We believe the way to most efficient capture the largest portion of this resource superset is via crowd-sourcing, and peer-reviewing new entries.
If you cannot find something, please contribute what you are looking for!

----

```{toctree}
:maxdepth: 1

introduction
custom-resources
plr-rl-naming-conventions
```

----

## `Resource` subclasses

In PLR every physical object is a subclass of the `Resource` superclass (except for `Tip`).
Each subclass adds unique methods or attributes to represent its unique physical specifications and behavior.

PLR's `Resource` subclasses in the inheritance tree are:
<style>
  .tree {
    border: 3px solid black;      /* Thick border around the entire table */
    border-collapse: collapse;    /* Ensures borders don’t double */
    background-color: #FAF3DD;    /* Light background */
    margin-left: 5px;             /* A bit of left margin */
  }

  .tree td {
    font-family: "Fira Code", monospace;  /* Code-like font */
    font-size: 15px;                     /* Matches code cell output */
    font-weight: bold;                   /* Make all text bold */
    line-height: 1.2;                    /* Slightly tighten vertical spacing */
    padding: 0 10px;                   /* Padding around each line */
    border: none;                        /* Remove inner borders */
    white-space: pre;                    /* Preserve spacing exactly */
  }
</style>

<table class="tree">
  <tr><td><a href="introduction.html">Resource</a></td></tr>

  <!-- Carrier subtree -->
  <tr><td>├── <a href="carrier/carrier.html">Carrier</a></td></tr>
  <tr><td>│   ├── <a href="carrier/plate-carrier/plate_carriers.html">PlateCarrier</a></td></tr>
  <tr><td>│   ├── TipCarrier</td></tr>
  <tr><td>│   ├── <a href="carrier/mfx-carrier/mfx_carriers.html">MFXCarrier</a></td></tr>
  <tr><td>│   ├── TroughCarrier</td></tr>
  <tr><td>│   └── TubeCarrier</td></tr>

  <!-- Container subtree -->
  <tr><td>├── Container</td></tr>
  <tr><td>│   ├── Well</td></tr>
  <tr><td>│   ├── PetriDish</td></tr>
  <tr><td>│   ├── Tube</td></tr>
  <tr><td>│   └── Trough</td></tr>

  <!-- ItemizedResource subtree -->
  <tr><td>├── <a href="itemized-resource/itemized-resource.html">ItemizedResource</a></td></tr>
  <tr><td>│   ├── <a href="itemized-resource/plate/plate.html">Plate</a></td></tr>
  <tr><td>│   ├── TipRack</td></tr>
  <tr><td>│   └── TubeRack</td></tr>

  <!-- ResourceHolder subtree -->
  <tr><td>├── ResourceHolder</td></tr>
  <tr><td>│   └── PlateHolder</td></tr>

  <!-- Others -->
  <tr><td>├── Lid</td></tr>
  <tr><td>├── PlateAdapter</td></tr>
  <tr><td>└── ResourceStack</td></tr>
</table>

----

```{toctree}
:maxdepth: 3
:caption: Resource subclass explanations
:hidden:

carrier/carrier
containers
itemized-resource/itemized-resource
```

## Library

```{toctree}
:caption: Library

library/agenbio
library/alpaqua
library/azenta
library/biorad
library/boekel
library/celltreat
library/cellvis
library/corning_axygen
library/corning_costar
library/eppendorf
library/falcon
library/hamilton
library/nest
library/opentrons
library/porvair
library/revvity
library/thermo_fisher
library/vwr
```
