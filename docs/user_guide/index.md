# User guide

<hr>

```{toctree}
:maxdepth: 1
:caption: Getting started
:hidden:

_getting-started/installation
_getting-started/rpi
```


```{toctree}
:maxdepth: 1
:caption: Machines
:hidden:

00_liquid-handling/_liquid-handling
01_material-handling/_material-handling
02_environmental-control/_environmental-control
03_sample-prep-and-processing/_sample-prep-and-processing
04_analytical/_analytical
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
```

```{toctree}
:maxdepth: 1
:caption: Configuration
:hidden:

configuration
```


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
  <tr><td>├── Liquid Handling</td></tr>
  <tr><td>│   ├── Pipetting Robots</td></tr>
  <tr><td>│   ├── Plate Washers</td></tr>
  <tr><td>│   └── Reagent Dispensers</td></tr>

  <!-- Material Handling -->
  <tr><td>├── Material Handling</td></tr>
  <tr><td>│   ├── Transport Systems</td></tr>
  <tr><td>│   │   ├── Conveyors</td></tr>
  <tr><td>│   │   ├── Robotic Arms</td></tr>
  <tr><td>│   │   └── Smart Storage (e.g. carousels)</td></tr>
  <tr><td>│   ├── Consumable Manipulation</td></tr>
  <tr><td>│   │   ├── Cappers & Decappers</td></tr>
  <tr><td>│   │   └── Sealers & Peelers</td></tr>
  <tr><td>│   └── Identification</td></tr>
  <tr><td>│       └── Barcode Labellers And Readers</td></tr>

  <!-- Environmental Control -->
  <tr><td>├── Environmental Control</td></tr>
  <tr><td>│   ├── Temperature And Motion Control</td></tr>
  <tr><td>│   │   ├── Automated Freezers</td></tr>
  <tr><td>│   │   ├── Automated Incubators</td></tr>
  <tr><td>│   │   ├── Heated Cooled Blocks</td></tr>
  <tr><td>│   │   ├── Incubated Shakers</td></tr>
  <tr><td>│   │   ├── Shakers</td></tr>
  <tr><td>│   │   ├── Thermal Shakers</td></tr>
  <tr><td>│   │   └── Thermocyclers</td></tr>
  <tr><td>│   └── Airflow & Contamination Control</td></tr>
  <tr><td>│       ├── Air Circulation Fans</td></tr>
  <tr><td>│       ├── Gas Controlled Chambers</td></tr>
  <tr><td>│       ├── HEPA Filtration Modules</td></tr>
  <tr><td>│       ├── Laminar Flow Hoods</td></tr>
  <tr><td>│       └── UV-C Decontamination Units</td></tr>

  <!-- Sample Preparation And Processing -->
  <tr><td>├── Sample Preparation And Processing</td></tr>
  <tr><td>│   ├── Automated Centrifuges</td></tr>
  <tr><td>│   ├── Chromatography Systems</td></tr>
  <tr><td>│   ├── Colony Pickers</td></tr>
  <tr><td>│   ├── Filtration Units</td></tr>
  <tr><td>│   ├── Liquid Extractors</td></tr>
  <tr><td>│   ├── Lysis Modules</td></tr>
  <tr><td>│   ├── Magnetic Bead Purifiers</td></tr>
  <tr><td>│   ├── Pre-PCR Prep Stations</td></tr>
  <tr><td>│   ├── Sonicators</td></tr>
  <tr><td>│   └── Tissue Homogenizers</td></tr>

  <!-- Analytical And Detection -->
  <tr><td>└── Analytical And Detection</td></tr>
  <tr><td>    ├── Balances / Scales</td></tr>
  <tr><td>    ├── Optical Detection</td></tr>
  <tr><td>        ├── Automated Microscopes</td></tr>
  <!-- <tr><td>        ├── Colony Counters</td></tr> -->
  <tr><td>        ├── Flow Cytometers</td></tr>
  <!-- <tr><td>        ├── Gel Imagers</td></tr> -->
  <!-- <tr><td>        ├── Microarray Scanners</td></tr> -->
  <tr><td>        ├── Plate Readers</td></tr>
  <tr><td>        ├── qPCR Machines</td></tr>
  <tr><td>        ├── Sequencers (DNA / RNA / Protein)</td></tr>
  <tr><td>        └── Spectrophotometers</td></tr>
  <tr><td>    └── pH Meters</td></tr>

</table>


<hr>
