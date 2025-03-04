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
    border: 3px solid black;  /* Thick border around the entire table */
    border-collapse: collapse;  /* Ensures borders don’t double */
  }

  .tree td {
    font-family: "Fira Code", monospace;  /* Code-like font */
    font-size: 15px;  /* Matches code cell output */
    padding: 5px 10px;  /* Adjust padding for better spacing */
    line-height: 0.8;  /* Keep compact row spacing */
    font-weight: bold;  /* Make all text bold */
    border: none;  /* Remove all inner borders */
  }

  /* Indentation using text-indent */
  .level-1 { text-indent: 20px; }
  .level-2 { text-indent: 40px; }
  .level-3 { text-indent: 60px; }
</style>




<table class="tree" style="margin-left: 5px; background-color: #FAF3DD; border-collapse: collapse; padding: 50px;">
<tr><td><a href="introduction.html">Resource</a></td></tr>

<tr><td class="level-1">├── <a href="carrier/carrier.html">Carrier</a></td></tr>
<tr><td class="level-2">├── <a href="carrier/plate-carrier/plate_carriers.html">PlateCarrier</a></td></tr>
<tr><td class="level-2">├── TipCarrier</td></tr>
<tr><td class="level-2">├── <a href="carrier/mfx-carrier/mfx_carriers.html">MFXCarrier</a></td></tr>
<tr><td class="level-2">├── TroughCarrier</td></tr>
<tr><td class="level-2">└── TubeCarrier</td></tr>

<tr><td class="level-1">├── Container</td></tr>
<tr><td class="level-2">├── Well</td></tr>
<tr><td class="level-2">├── PetriDish</td></tr>
<tr><td class="level-2">├── Tube</td></tr>
<tr><td class="level-2">└── Trough</td></tr>

<tr><td class="level-1">├── <a href="itemized-resource/itemized-resource.html">ItemizedResource</a></td></tr>
<tr><td class="level-2">├── <a href="itemized-resource/plate/plate.html">Plate</a></td></tr>
<tr><td class="level-2">├── TipRack</td></tr>
<tr><td class="level-2">└── TubeRack</td></tr>

<tr><td class="level-1">├── Lid</td></tr>
<tr><td class="level-1">├── PlateAdapter</td></tr>

<tr><td class="level-1">├── ResourceHolder</td></tr>
<tr><td class="level-2">└── PlateHolder</td></tr>

<tr><td class="level-1">└── ResourceStack</td></tr>
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
