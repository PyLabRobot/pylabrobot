# User guide

<hr>

```{toctree}
:maxdepth: 1
:caption: Getting started
:hidden:

_getting-started/installation
How PLR Works <_getting-started/plr-architecture>
_getting-started/rpi
```


```{toctree}
:maxdepth: 1
:caption: Machines
:hidden:

00_liquid-handling/_liquid-handling
01_material-handling/_material-handling
02_analytical/_analytical
```

```{toctree}
:maxdepth: 1
:caption: Machine-Agnostic Features
:hidden:

machine-agnostic-features/using-the-visualizer
machine-agnostic-features/using-trackers
machine-agnostic-features/writing-robot-agnostic-protocols
machine-agnostic-features/tip-spot-generators
machine-agnostic-features/validation
machine-agnostic-features/error-handling-general
```

```{toctree}
:maxdepth: 1
:caption: Configuration
:hidden:

configuration
```


This guide helps you get started with PyLabRobot (PLR) and shows you how to build and run lab automation workflows. Whether you're new to lab robotics or already have hardware set up, this guide will help you understand how everything fits together.

The guide is divided into three parts:

- **Getting Started** – Learn how how to install PLR, and how its core system is designed.

- **Machines** – Explore how to use the different types of lab hardware supported by PyLabRobot.

- **Machine-Agnostic Features** – Discover powerful tools that work across all devices, like the visualizer, trackers, reusable protocol patterns, validation tools, and error handling.


<hr>


<style>
  .machine_classification {
    border: 3px solid black;
    border-collapse: collapse;
    background-color: #FAF3DD;
    margin-left: 5px;
  }

  .machine_classification td {
    font-family: "Fira Code", monospace;
    font-size: 15px;
    font-weight: bold;
    line-height: 1.2;
    padding: 0 10px;
    border: none;
    white-space: pre;
  }
</style>

<table class="machine_classification">
  <tr><td>Machines</td></tr>

  <!-- Liquid Handling -->
  <tr><td>├── Liquid Handling (liquid contact? - Yes)</td></tr>
  <tr><td>│   ├── Pipetting Robots</td></tr>
  <tr><td>│   ├── Plate Washers</td></tr>
  <tr><td>│   └── Reagent Dispensers</td></tr>

  <!-- Material Handling -->
  <tr><td>├── Material Handling (liquid contact? - No)</td></tr>
  <tr><td>│   ├── Automated Centrifuges</td></tr>
  <tr><td>│   ├── Consumable Manipulation</td></tr>
  <tr><td>│   │   ├── Cappers & Decappers</td></tr>
  <tr><td>│   │   └── Sealers & Peelers</td></tr>
  <tr><td>│   ├── Environmental Control</td></tr>
  <tr><td>│   │   ├── Temperature And Motion Control</td></tr>
  <tr><td>│   │   │   ├── Automated Freezers/Fridges</td></tr>
  <tr><td>│   │   │   ├── Automated Incubators</td></tr>
  <tr><td>│   │   │   ├── Heated Cooled Blocks</td></tr>
  <tr><td>│   │   │   ├── Incubated Shakers</td></tr>
  <tr><td>│   │   │   ├── Shakers</td></tr>
  <tr><td>│   │   │   ├── Thermal Shakers</td></tr>
  <tr><td>│   │   │   └── Thermocyclers</td></tr>
  <tr><td>│   │   └── Airflow & Contamination Control</td></tr>
  <tr><td>│   │       ├── Air Circulation Fans</td></tr>
  <!-- <tr><td>│           ├── Gas Controlled Chambers</td></tr> -->
  <tr><td>│   │       ├── HEPA Filtration Modules</td></tr>
  <!-- <tr><td>│           ├── Laminar Flow Hoods</td></tr> -->
  <tr><td>│   │       └── UV-C Decontamination Units</td></tr>
  <tr><td>│   ├── Identification</td></tr>
  <tr><td>│   │   └── Barcode Labellers And Readers</td></tr>
  <tr><td>│   └── Transport Systems</td></tr>
  <tr><td>│       ├── Conveyors</td></tr>
  <tr><td>│       ├── Robotic Arms</td></tr>
  <tr><td>│       └── Smart Storage (e.g. carousels)</td></tr>

  <!-- Analytical And Detection -->
  <tr><td>├── Analytical And Detection  (measurement? - Yes)</td></tr>
  <tr><td>│   ├── Balances / Scales</td></tr>
  <tr><td>│   ├── Optical Detection</td></tr>
  <tr><td>│   │   ├── Automated Microscopes</td></tr>
  <!-- <tr><td>        ├── Colony Counters</td></tr> -->
  <tr><td>│   │   ├── Flow Cytometers</td></tr>
  <!-- <tr><td>        ├── Gel Imagers</td></tr> -->
  <!-- <tr><td>        ├── Microarray Scanners</td></tr> -->
  <tr><td>│   │   ├── Plate Readers</td></tr>
  <!-- <tr><td>        ├── qPCR Machines</td></tr> -->
  <!-- <tr><td>        ├── Sequencers (DNA / RNA / Protein)</td></tr> -->
  <tr><td>│   │   └── Spectrophotometers</td></tr>
  <tr><td>│   ├── Sensors</td></tr>
  <tr><td>│   │   ├── Temperature</td></tr>
  <tr><td>│   │   └── Humidity</td></tr>
  <tr><td>│   └── pH Meters</td></tr>

  <!-- Sample Preparation And Processing -->
  <tr><td>└── Miscelleanous Processing Machines</td></tr>
  <tr><td>    ├── Chromatography Systems</td></tr>
  <tr><td>    ├── Colony Pickers</td></tr>
  <!-- <tr><td>│   ├── Filtration Units</td></tr> -->
  <!-- <tr><td>│   ├── Liquid Extractors</td></tr> -->
  <!-- <tr><td>│   ├── Lysis Modules</td></tr> -->
  <!-- <tr><td>│   ├── Magnetic Bead Purifiers</td></tr> -->
  <!-- <tr><td>│   ├── Pre-PCR Prep Stations</td></tr> -->
  <!-- <tr><td>│   ├── Sonicators</td></tr> -->
  <tr><td>    └── Tissue Homogenizers</td></tr>

</table>

<hr>
