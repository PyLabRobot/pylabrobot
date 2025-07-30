# Supported Machines

```{raw} html
<style>
tr > td:first-child { width: 15%; }
tr > td:nth-child(2) { width: 15%; }
tr > td:nth-child(3) { width: 20%; }
tr > td:nth-child(4) { width: 15%; }
tr > td:nth-child(5) { width: 15%; }
.badge {
  border-radius: 8px;
  padding: 2px 8px;
  display: inline-block;
  font-size: 90%;
  margin-right: 4px;
  color: black;
}
.badge-liquid { background: #d0eaff; }
.badge-transfer { background: #f0d0ff; }
.badge-centrifuge { background: #e6e6fa; }
.badge-heating { background: #ffd6d6; }
.badge-cooling { background: #d6f1ff; }
.badge-shaking { background: #ddffdd; }
.badge-thermo { background: #fff4cc; }
.badge-sealing { background: #ffe0b3; }
.badge-weight { background: #f0f0f0; }
.badge-reading { background: #d6eaff; }
.badge-cyto { background: #f5ccff; }
.badge-air { background: #e6f7ff; }
.badge-tilt { background: #e0f7da; }
.badge-storage { background: #b2e0e0; }
.badge-microscopy { background: #e0cfff; }
</style>
```

## Liquid Handling

### Liquid Handling Workstations

| Manufacturer | Machine | Features | PLR-Support | Links |
|--------------|---------|----------|-------------|--------|
| Hamilton | STAR(let) | <span class="badge badge-transfer">arm</span> | Full | [PLR](https://docs.pylabrobot.org/user_guide/00_liquid-handling/hamilton-star/_hamilton-star.html) / [OEM](https://www.hamiltoncompany.com/microlab-star) |
| Hamilton | Vantage | <span class="badge badge-transfer">arm</span> | Mostly | [PLR](https://docs.pylabrobot.org/user_guide/00_liquid-handling/hamilton-vantage/_hamilton-vantage.html) / [OEM](https://www.hamiltoncompany.com/microlab-vantage) |
| Hamilton | Prep | <span class="badge badge-transfer">arm</span> | WIP | PLR / [OEM](https://www.hamiltoncompany.com/microlab-prep) |
| Tecan | EVO |  | Basic | [PLR](https://docs.pylabrobot.org/user_guide/00_liquid-handling/tecan-evo/_tecan-evo.html) / OEM |
| Opentrons | OT-2 |  | Mostly | [PLR](https://docs.pylabrobot.org/user_guide/00_liquid-handling/opentrons-ot2/_opentrons-ot2.html) / OEM |

### Pumps

| Manufacturer | Machine | Features | PLR-Support | Links |
|--------------|---------|----------|-------------|--------|
| Cole Parmer | Masterflex | | Full | [PLR](https://docs.pylabrobot.org/user_guide/00_liquid-handling/pumps/cole-parmer-masterflex.html) / OEM |
| Agrowtek | Pump Array | | Full | PLR / OEM |

---

## Material Handling

### Arms

| Manufacturer | Machine | Features | PLR-Support | Links |
|--------------|---------|----------|-------------|--------|
| Brooks Automation | PreciseFlex 400 | | WIP | [PLR](https://docs.pylabrobot.org/user_guide/01_material-handling/arms/c_scara/precise-flex-pf400/_precise-flex-pf400.html) / [OEM](https://www.brooks.com/laboratory-automation/collaborative-robots/preciseflex-400/) |

### Centrifuges

| Manufacturer | Machine | Features | PLR-Support | Links |
|--------------|---------|----------|-------------|--------|
| Agilent | VSpin | | Mostly | [PLR](https://docs.pylabrobot.org/user_guide/01_material-handling/centrifuge/agilent_vspin.html) / OEM |
| Agilent | VSpin Access2 Loader | | Full | [PLR](https://docs.pylabrobot.org/user_guide/01_material-handling/centrifuge/agilent_vspin.html#loader) / OEM |

### Fans

| Manufacturer | Machine | Features | PLR-Support | Links |
|--------------|---------|----------|-------------|--------|
| Hamilton | HEPA Fan | <span class="badge badge-air">air filtration</span> | Full | [PLR](https://docs.pylabrobot.org/user_guide/01_material-handling/fans/fans.html) / OEM |

### Heater Shakers

| Manufacturer | Machine | Features | PLR-Support | Links |
|--------------|---------|----------|-------------|--------|
| Inheco | Thermoshake RM | <span class="badge badge-heating">heating material</span><span class="badge badge-shaking">shaking</span> | Full | PLR / OEM |
| Inheco | Thermoshake | <span class="badge badge-heating">heating material</span><span class="badge badge-shaking">shaking</span> | Full | PLR / OEM |
| Inheco | Thermoshake AC | <span class="badge badge-heating">heating material</span><span class="badge badge-cooling">cooling</span><span class="badge badge-shaking">shaking</span> | Mostly | PLR / OEM |
| Opentrons | Thermoshake | <span class="badge badge-heating">heating material</span><span class="badge badge-shaking">shaking</span> | Full | PLR / OEM |
| Hamilton | Heater Shaker | <span class="badge badge-heating">heating material</span><span class="badge badge-shaking">shaking</span> | Full | PLR / OEM |

### Incubators

| Manufacturer | Machine | Features | PLR-Support | Links |
|--------------|---------|----------|-------------|--------|
| Thermo Fisher Scientific | Cytomat C6000 | <span class="badge badge-heating">heating space</span> | Full | PLR / OEM |
| Thermo Fisher Scientific | Cytomat C6002 | <span class="badge badge-heating">heating space</span> | Full | PLR / OEM |
| Thermo Fisher Scientific | Cytomat C2C_50 | <span class="badge badge-heating">heating space</span> | Full | PLR / OEM |
| Thermo Fisher Scientific | Cytomat C2C_425 | <span class="badge badge-heating">heating space</span><span class="badge badge-cooling">cooling</span> | Full | PLR / OEM |
| Thermo Fisher Scientific | Cytomat C2C_450_SHAKE | <span class="badge badge-heating">heating space</span><span class="badge badge-shaking">shaking</span> | Full | PLR / OEM |
| Thermo Fisher Scientific | Cytomat 5C | <span class="badge badge-heating">heating spaceting</span> | Full | PLR / OEM |
| Thermo/Liconic | Heraeus Cytomat | <span class="badge badge-heating">heating space</span> | WIP | PLR / OEM |
| Inheco | Incubator Shaker MTP & DWP | <span class="badge badge-heating">heating space</span><span class="badge badge-shaking">shaking</span> | WIP | PLR / OEM |

### Peelers

| Manufacturer | Machine | Features | PLR-Support | Links |
|--------------|---------|----------|-------------|--------|
| Azenta Life Sciences | XPeel | | WIP | PLR / OEM |

### Sealers

| Manufacturer | Machine | Features | PLR-Support | Links |
|--------------|---------|----------|-------------|--------|
| Azenta Life Sciences | a4S Sealer |  | Full | PLR / OEM |

### Thermocyclers

| Manufacturer | Machine | Features | PLR-Support | Links |
|--------------|---------|----------|-------------|--------|
| Opentrons | Thermocycler | | Full | PLR / OEM |
| Thermo Fisher Scientific | ATC | | WIP | PLR / OEM |
| Thermo Fisher Scientific | Proflex | | WIP | PLR / OEM |
| Inheco | ODTC | | WIP | PLR / OEM |

### Temperature Controllers

| Manufacturer | Machine | Features | PLR-Support | Links |
|--------------|---------|----------|-------------|--------|
| Opentrons | Temperature Module | <span class="badge badge-heating">heating material</span><span class="badge badge-cooling">cooling</span> | Mostly | PLR / OEM |
| Inheco | CPAC | <span class="badge badge-heating">heating material</span><span class="badge badge-cooling">cooling</span> | WIP | PLR / OEM |

### Tilting

| Manufacturer | Machine | Features | PLR-Support | Links |
|--------------|---------|----------|-------------|--------|
| Hamilton | Tilt Module | | Full | PLR / OEM |

---

## Analytical Machines

### Plate Readers

| Manufacturer | Machine | Features | PLR-Support | Links |
|--------------|---------|----------|-------------|--------|
| BMG Labtech | CLARIOstar | <span class="badge">Absorbance</span><span class="badge">Fluorescence</span><span class="badge">Luminescence</span> | Full | PLR / OEM |
| Agilent (BioTek) | Cytation 1 | <span class="badge">Absorbance</span><span class="badge">Fluorescence</span><span class="badge">Luminescence</span><span class="badge badge-microscopy">microscopy</span> | Full | PLR / OEM |
| Agilent (BioTek) | Cytation 5 | <span class="badge">Absorbance</span><span class="badge">Fluorescence</span><span class="badge">Luminescence</span><span class="badge badge-microscopy">microscopy</span> | Full | PLR / OEM |
| Byonoy | Absorbance 96 Automate | <span class="badge">Absorbance</span> | WIP | PLR / OEM |
| Byonoy | Luminescence 96 Automate | <span class="badge">Fluorescence</span> | WIP | PLR / OEM |

### Flow Cytometers

| Manufacturer | Machine | Features | PLR-Support | Links |
|--------------|---------|----------|-------------|--------|
| Beckman Coulter | CytoFLEX S | | WIP | PLR / OEM |

### qPCR Machines

| Manufacturer | Machine | Features | PLR-Support | Links |
|--------------|---------|----------|-------------|--------|
| Thermo Fisher Scientific | QuantStudio 5 | | WIP | PLR / OEM |

### Scales

| Manufacturer | Machine | Features | PLR-Support | Links |
|--------------|---------|----------|-------------|--------|
| Mettler Toledo | WXS205SDU | | Full | [PLR](https://docs.pylabrobot.org/user_guide/02_analytical/scales.html#mettler-toledo-wxs205sdu) / OEM |

---

## Understanding the Tables

Classifying lab automation equipment can be challenging, as many machines have overlapping capabilities and different user groups require varying levels of software integration.  
PyLabRobot aims to provide access to all hardware-firmware capabilities available on integrated equipment, even beyond OEM software. This allows users to selectively utilize the functionalities they require.

The machines are organized into three PyLabRobot categories:

- **Liquid handling**: Machines directly manipulating liquids.
- **Material handling**: Machines handling materials other than liquids.
- **Analytical**: Machines primarily responsible for performing measurements.

**Table Columns Explained:**

- **Features**: Core capabilities provided by the machine.
- **PLR-Support**: Indicates PyLabRobot integration status:
  - **WIP**: Work in progress.
  - **Basics**: Core functionalities available.
  Available on `pylabrobot:main` and has documentation pages.
  - **Mostly**: Most capabilities available but incomplete.
  - **Full**: Comprehensive capabilities (≥90%) fully supported.
