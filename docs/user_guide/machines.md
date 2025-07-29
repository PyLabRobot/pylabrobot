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
| Hamilton | STAR(let) | <span class="badge badge-liquid">liquid handling</span><span class="badge badge-transfer">material transfer</span> | Full | [PLR](https://docs.pylabrobot.org/user_guide/00_liquid-handling/hamilton-star/_hamilton-star.html) / [OEM](https://www.hamiltoncompany.com/microlab-star) |
| Hamilton | Vantage | <span class="badge badge-liquid">liquid handling</span><span class="badge badge-transfer">material transfer</span> | Mostly | PLR / [OEM](https://www.hamiltoncompany.com/microlab-vantage) |
| Hamilton | Prep | <span class="badge badge-liquid">liquid handling</span><span class="badge badge-transfer">material transfer</span> | WIP | PLR / [OEM](https://www.hamiltoncompany.com/microlab-prep) |
| Tecan | EVO | <span class="badge badge-liquid">liquid handling</span> | Mostly | PLR / OEM |
| Opentrons | OT-2 | <span class="badge badge-liquid">liquid handling</span> | Mostly | PLR / OEM |

### Pumps

| Manufacturer | Machine | Features | PLR-Support | Links |
|--------------|---------|----------|-------------|--------|
| Cole Parmer | Masterflex | <span class="badge badge-liquid">liquid transfer</span> | Mostly | PLR / OEM |
| Agrowtek | Pump Array | <span class="badge badge-liquid">liquid transfer</span> | Basics | PLR / OEM |

---

## Material Handling

### Arms

| Manufacturer | Machine | Features | PLR-Support | Links |
|--------------|---------|----------|-------------|--------|
| Brooks Automation | PreciseFlex 400 | <span class="badge badge-transfer">material transfer</span> | WIP | [PLR](https://docs.pylabrobot.org/user_guide/01_material-handling/arms/c_scara/precise-flex-pf400/_precise-flex-pf400.html) / [OEM](https://www.brooks.com/laboratory-automation/collaborative-robots/preciseflex-400/) |

### Centrifuges

| Manufacturer | Machine | Features | PLR-Support | Links |
|--------------|---------|----------|-------------|--------|
| Agilent | VSpin | <span class="badge badge-centrifuge">plate centrifugation</span> | Mostly | PLR / OEM |
| Agilent | VSpin Access2 Loader | <span class="badge badge-centrifuge">plate centrifugation</span><span class="badge badge-transfer">material transfer</span> | Mostly | PLR / OEM |

### Fans

| Manufacturer | Machine | Features | PLR-Support | Links |
|--------------|---------|----------|-------------|--------|
| Hamilton | HEPA Fan | <span class="badge badge-air">air filtration</span> | Basics | PLR / OEM |

### Heater Shakers

| Manufacturer | Machine | Features | PLR-Support | Links |
|--------------|---------|----------|-------------|--------|
| Inheco | Thermoshake RM | <span class="badge badge-heating">active heating</span><span class="badge badge-shaking">shaking</span> | Full | PLR / OEM |
| Inheco | Thermoshake | <span class="badge badge-heating">active heating</span><span class="badge badge-shaking">shaking</span> | Mostly | PLR / OEM |
| Inheco | Thermoshake AC | <span class="badge badge-heating">active heating</span><span class="badge badge-cooling">active cooling</span><span class="badge badge-shaking">shaking</span> | Mostly | PLR / OEM |
| Opentrons | Thermoshake | <span class="badge badge-heating">active heating</span><span class="badge badge-shaking">shaking</span> | Mostly | PLR / OEM |
| Hamilton | Heater Shaker | <span class="badge badge-heating">active heating</span><span class="badge badge-shaking">shaking</span> | Mostly | PLR / OEM |

### Incubators

| Manufacturer | Machine | Features | PLR-Support | Links |
|--------------|---------|----------|-------------|--------|
| Thermo Fisher Scientific | Cytomat C6000 | <span class="badge badge-heating">active heating</span><span class="badge badge-storage">smart storage</span> | Mostly | PLR / OEM |
| Thermo Fisher Scientific | Cytomat C6002 | <span class="badge badge-heating">active heating</span><span class="badge badge-storage">smart storage</span> | Mostly | PLR / OEM |
| Thermo Fisher Scientific | Cytomat C2C_50 | <span class="badge badge-heating">active heating</span><span class="badge badge-storage">smart storage</span> | Mostly | PLR / OEM |
| Thermo Fisher Scientific | Cytomat C2C_425 | <span class="badge badge-heating">active heating</span><span class="badge badge-cooling">active cooling</span><span class="badge badge-storage">smart storage</span> | Mostly | PLR / OEM |
| Thermo Fisher Scientific | Cytomat C2C_450_SHAKE | <span class="badge badge-heating">active heating</span><span class="badge badge-shaking">shaking</span><span class="badge badge-storage">smart storage</span> | Mostly | PLR / OEM |
| Thermo Fisher Scientific | Cytomat 5C | <span class="badge badge-heating">active heating</span><span class="badge badge-storage">smart storage</span> | Mostly | PLR / OEM |
| Thermo/Liconic | Heraeus Cytomat | <span class="badge badge-heating">active heating</span><span class="badge badge-storage">smart storage</span> | WIP | PLR / OEM |
| Inheco | Incubator Shaker MTP & DWP | <span class="badge badge-heating">active heating</span> | WIP | PLR / OEM |

### Peelers

| Manufacturer | Machine | Features | PLR-Support | Links |
|--------------|---------|----------|-------------|--------|
| Azenta Life Sciences | XPeel | <span class="badge badge-sealing">plate sealing removal</span> | WIP | PLR / OEM |

### Sealers

| Manufacturer | Machine | Features | PLR-Support | Links |
|--------------|---------|----------|-------------|--------|
| Azenta Life Sciences | a4S Sealer | <span class="badge badge-sealing">sealing</span> | Full | PLR / OEM |

### Thermocyclers

| Manufacturer | Machine | Features | PLR-Support | Links |
|--------------|---------|----------|-------------|--------|
| Opentrons | Thermocycler | <span class="badge badge-thermo">thermocycling</span> | Mostly | PLR / OEM |
| Thermo Fisher Scientific | ATC | <span class="badge badge-thermo">thermocycling</span> | WIP | PLR / OEM |
| Thermo Fisher Scientific | Proflex | <span class="badge badge-thermo">thermocycling</span> | WIP | PLR / OEM |
| Inheco | ODTC | <span class="badge badge-thermo">thermocycling</span> | WIP | PLR / OEM |

### Temperature Controllers

| Manufacturer | Machine | Features | PLR-Support | Links |
|--------------|---------|----------|-------------|--------|
| Opentrons | Temperature Module | <span class="badge badge-heating">active heating</span><span class="badge badge-cooling">active cooling</span> | Mostly | PLR / OEM |
| Inheco | CPAC | <span class="badge badge-heating">active heating</span><span class="badge badge-cooling">active cooling</span> | WIP | PLR / OEM |

### Tilting

| Manufacturer | Machine | Features | PLR-Support | Links |
|--------------|---------|----------|-------------|--------|
| Hamilton | Tilt Module | <span class="badge badge-tilt">plate tilting</span> | Mostly | PLR / OEM |

---

## Analytical Machines

### Plate Readers

| Manufacturer | Machine | Features | PLR-Support | Links |
|--------------|---------|----------|-------------|--------|
| BMG Labtech | CLARIOstar | <span class="badge badge-reading">plate reading</span> | Full | PLR / OEM |
| Agilent (BioTek) | Cytation 1 | <span class="badge badge-reading">plate reading</span><span class="badge badge-microscopy">microscopy</span> | Full | PLR / OEM |
| Agilent (BioTek) | Cytation 5 | <span class="badge badge-reading">plate reading</span><span class="badge badge-microscopy">microscopy</span> | Full | PLR / OEM |

### Flow Cytometers

| Manufacturer | Machine | Features | PLR-Support | Links |
|--------------|---------|----------|-------------|--------|
| Beckman Coulter | CytoFLEX S | <span class="badge badge-cyto">flow cytometry</span> | WIP | PLR / OEM |

### qPCR Machines

| Manufacturer | Machine | Features | PLR-Support | Links |
|--------------|---------|----------|-------------|--------|
| Thermo Fisher Scientific | QuantStudio 5 | <span class="badge badge-thermo">thermocycling</span><span class="badge badge-reading">plate reading</span> | WIP | PLR / OEM |

### Scales

| Manufacturer | Machine | Features | PLR-Support | Links |
|--------------|---------|----------|-------------|--------|
| Mettler Toledo | WXS205SDU | <span class="badge badge-weight">weight measuring</span> | Full | PLR / OEM |

---

## Understanding the Table

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
  Available via `pylabrobot:main` and has documentation pages.
  - **Mostly**: Most capabilities available but incomplete.
  - **Full**: Comprehensive capabilities (≥90%) fully supported.
