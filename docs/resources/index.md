# Resource Management System

<hr>

The PyLabRobot (PLR) Resource Management System (RMS) is a **framework** that **models** both the **physical components** of an automation setup and the **distinct physical behaviors of each component**.
(It does not provide electronic control of machines.
This is the role of PLR's *machine control system*.)
It provides a structured approach for dynamically constructing precise and adaptable automation system layouts.

The PLR Resource Management System consists of two key components, each serving a distinct role:

1. **Resource Ontology System**
    - The ***'blueprint'*** of PLR's physical definition framework, responsible for defining physical resources, modeling their distinct behaviors, and dynamically managing their relationships (i.e. tracking their *state*).
2. **Resource Library**
    - The ***'catalog'*** of premade resource definitions.
    This provides reusable, standardized definitions that enhance consistency and interoperability across automation workflows.
    This ensures smooth integration, scalability, and efficient resource utilization.

```{toctree}
:maxdepth: 1
:hidden:

introduction
custom-resources
```

<hr>

## Resource Ontology System

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
  <!-- Arm subtree -->
  <tr><td>├── <a href="deck/deck.html">Arm</a></td></tr>
  <tr><td>│   ├── ArticulatedArm</td></tr>
  <tr><td>│   ├── CartesianArm</td></tr>
  <tr><td>│   └── SCARA</td></tr>

  <!-- Carrier subtree -->
  <tr><td>├── <a href="carrier/carrier.html">Carrier</a></td></tr>
  <tr><td>│   ├── <a href="carrier/mfx-carrier/mfx_carrier.html">MFXCarrier</a></td></tr>
  <tr><td>│   ├── <a href="carrier/plate-carrier/plate_carrier.html">PlateCarrier</a></td></tr>
  <tr><td>│   ├── <a href="carrier/tip-carrier/tip-carrier.html">TipCarrier</a></td></tr>
  <tr><td>│   ├── <a href="carrier/trough-carrier/trough-carrier.html">TroughCarrier</a></td></tr>
  <tr><td>│   └── <a href="carrier/tube-carrier/tube-carrier.html">TubeCarrier</a></td></tr>

  <!-- <tr><td>├── ChannelHeadTool</td></tr>
  <tr><td>│   ├── <a href="container/trough/trough.html">Gripper</a></td></tr>
  <tr><td>│   └── <a href="resource-holder/plate-holder/plate-holder.html">Tip (to be made a resource)</a></td></tr>
 -->

  <!-- Container subtree -->
  <tr><td>├── <a href="container/container.html">Container</a></td></tr>
  <tr><td>│   ├── <a href="container/petri-dish/petri-dish.html">PetriDish</a></td></tr>
  <tr><td>│   ├── <a href="container/trough/trough.html">Trough</a></td></tr>
  <tr><td>│   ├── <a href="container/tube/tube.html">Tube</a></td></tr>
  <tr><td>│   └── <a href="container/well/well.html">Well</a></td></tr>

  <!-- Deck subtree -->
  <tr><td>├── <a href="deck/deck.html">Deck</a></td></tr>
  <tr><td>│   ├── OTDeck</td></tr>
  <tr><td>│   ├── HamiltonDeck</td></tr>
  <tr><td>│   └── TecanDeck</td></tr>

  <!-- ItemizedResource subtree -->
  <tr><td>├── <a href="itemized-resource/itemized-resource.html">ItemizedResource</a></td></tr>
  <tr><td>│   ├── <a href="itemized-resource/plate/plate.html">Plate</a></td></tr>
  <tr><td>│   ├── TipRack</td></tr>
  <tr><td>│   └── TubeRack</td></tr>

  <!-- ResourceHolder subtree -->
  <tr><td>├── <a href="resource-holder/resource-holder.html">ResourceHolder</a></td></tr>
  <tr><td>│   └── <a href="resource-holder/plate-holder/plate-holder.html">PlateHolder</a></td></tr>

  <!-- Others -->
  <tr><td>├── Lid</td></tr>
  <tr><td>├── <a href="plate-adapter/plate-adapter.html">PlateAdapter</a></td></tr>

  <tr><td>├── ResourceStack</td></tr>
  <tr><td>│   └── <a href="resource-holder/plate-holder/plate-holder.html">NestedTipRackStack (to be made)</a></td></tr>

  <tr><td>└── Workcell (to be made)</td></tr>
</table>

<hr>

<details style="background-color:#f8f9fa; border-left:5px solid #007bff; padding:10px; border-radius:5px;">
    <summary style="font-weight: bold; cursor: pointer;">Note: On the meaning of the terms "Resource" vs "Labware"</summary>
    <hr>
    <p>Most automation software systems (e.g. SDKs, APIs, GUIs) use the term "labware" to describe items on a machine's deck.
    However, in our discussions, it became evident that the term "labware" has different meanings to different stakeholders
    (e.g., "A plate is clearly labware, but is a liquid handler or a plate reader labware?").
    As a result, PLR avoids the ambiguous term "labware".</p>
    <p><u>Every physical item (describable via its <code>item_x</code>, <code>item_y</code>, <code>item_z</code>) is a "resource"</u>.</p>
</details>

<hr>

```{toctree}
:maxdepth: 2
:caption: Resource Ontology
:hidden:

carrier/carrier
container/container
deck/deck
itemized-resource/itemized-resource
resource-holder/resource-holder
resource-holder/plate-holder
plate-adapter/plate-adapter
resource-stack/resource-stack
```

## Resource Library

The PyLabRobot Resource Library (PLR-RL) is PyLabRobot's open-source, crowd-sourced collection of pre-made resource definitions.
Laboratories across the world use an almost infinite number of different resources (e.g. plates, tubes, liquid handlers, microscopes, arms, ...).
We believe the way to most efficiently capture the largest portion of this resource superset is via crowd-sourcing and iteratively peer-reviewing definitions.
If you cannot find something, please contribute what you are looking for!

<hr>

<details style="background-color:#f8f9fa; border-left:5px solid #007bff; padding:10px; border-radius:5px;">
    <summary style="font-weight: bold; cursor: pointer;">Note: On the universality of resource definitions</summary>
    <hr>
    <p>Resource definitions are subject to numerous sources of variability, including (but not limited to) the following:</p>
    <ul>
        <li>Resource batch-to-batch variability (e.g., a plate's wells height might vary ±1.5mm between different purchases of the same well).</li>
        <li>Machine calibration differences (e.g., person A's liquid handler's pipettes are tilted in the x-dimension by 1mm compared to person B's).</li>
    </ul>
    <p>As a result, many automation software systems believe that it is impossible to reuse resource definitions.
    In contrast, PyLabRobot is convinced that carefully created resource definitions combined with smart automation can be reused most of the time.</p>
    <p>PLR is actively addressing these resource resuse constraints in numerous ways:</p>
    <ul>
        <li>Development of self-correcting machine backend methods.</li>
        <li>Using Coordinate Measurement Machine-based generation of resource "ground truths" (e.g., via liquid handler-based resource probing or 3D scanning).</li>
    </ul>
</details>


<hr>


```{toctree}
:caption: Resource Library

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
library/sergi
library/thermo_fisher
library/vwr
```
