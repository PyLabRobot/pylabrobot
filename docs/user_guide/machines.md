# Supported Machines

```{raw} html
<style>
tr > td:first-child { width: 16%; }
tr > td:nth-child(2) { width: 22%; }
tr > td:nth-child(3) { width: 23%; }
tr > td:nth-child(4) { width: 13%; }
tr > td:nth-child(5) { width: 10%; }
tr > td:nth-child(6) { width: 16%; }
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

| Manufacturer | Machine | Features | Status | Coverage | Links |
|--------------|---------|----------|--------|----------|--------|
| Hamilton | STAR(let) | <span class="badge badge-liquid">liquid handling</span><span class="badge badge-transfer">material transfer</span> | Supported | ~90% | [PLR](https://docs.pylabrobot.org/user_guide/00_liquid-handling/hamilton-star/_hamilton-star.html) / [OEM](https://www.hamiltoncompany.com/microlab-star) |
| Hamilton | Vantage | <span class="badge badge-liquid">liquid handling</span><span class="badge badge-transfer">material transfer</span> | Supported | ? | PLR / [OEM](https://www.hamiltoncompany.com/microlab-vantage) |
| Hamilton | Prep | <span class="badge badge-liquid">liquid handling</span><span class="badge badge-transfer">material transfer</span> | WIP [#407](https://github.com/PyLabRobot/pylabrobot/pull/407) | ? | PLR / [OEM](https://www.hamiltoncompany.com/microlab-prep) |
| Tecan | EVO | <span class="badge badge-liquid">liquid handling</span> | Supported | ? | PLR / OEM |
| Opentrons | OT-2 | <span class="badge badge-liquid">liquid handling</span> | Supported | ? | PLR / OEM |

### Pumps

| Manufacturer | Machine | Features | Status | Coverage | Links |
|--------------|---------|----------|--------|----------|--------|
| Cole Parmer | Masterflex | <span class="badge badge-liquid">liquid transfer</span> | Supported | ? | PLR / OEM |
| Agrowtek | Pump Array | <span class="badge badge-liquid">liquid transfer</span> | Supported | ? | PLR / OEM |

---

## Material Handling

### Arms

| Manufacturer | Machine | Features | Status | Coverage | Links |
|--------------|---------|----------|--------|----------|--------|
| Brooks Automation | PreciseFlex 400 | <span class="badge badge-transfer">material transfer</span> | WIP | ? | [PLR](https://docs.pylabrobot.org/user_guide/01_material-handling/arms/c_scara/precise-flex-pf400/_precise-flex-pf400.html) / [OEM](https://www.brooks.com/laboratory-automation/collaborative-robots/preciseflex-400/) |

### Centrifuges

| Manufacturer | Machine | Features | Status | Coverage | Links |
|--------------|---------|----------|--------|----------|--------|
| Agilent | VSpin | <span class="badge badge-centrifuge">plate centrifugation</span> | Supported | ? | PLR / OEM |
| Agilent | VSpin Access2 Loader | <span class="badge badge-centrifuge">plate centrifugation</span><span class="badge badge-transfer">material transfer</span> | Supported | ? | PLR / OEM |

### Fans

| Manufacturer | Machine | Features | Status | Coverage | Links |
|--------------|---------|----------|--------|----------|--------|
| Hamilton | HEPA Fan | <span class="badge badge-air">air filtration</span> | Supported | ? | PLR / OEM |

### Heater shakers

| Manufacturer | Machine | Features | Status | Coverage | Links |
|--------------|---------|----------|--------|----------|--------|
| Inheco | Thermoshake RM | <span class="badge badge-heating">active heating</span><span class="badge badge-shaking">shaking</span> | Supported | ? | PLR / OEM |
| Inheco | Thermoshake | <span class="badge badge-heating">active heating</span><span class="badge badge-shaking">shaking</span> | Probably supported, not tested | ? | PLR / OEM |
| Inheco | Thermoshake AC | <span class="badge badge-heating">active heating</span><span class="badge badge-cooling">active cooling</span><span class="badge badge-shaking">shaking</span> | Probably supported, not tested | ? | PLR / OEM |
| Opentrons | Thermoshake | <span class="badge badge-heating">active heating</span><span class="badge badge-shaking">shaking</span> | Supported | ? | PLR / OEM |
| Hamilton | Heater Shaker | <span class="badge badge-heating">active heating</span><span class="badge badge-shaking">shaking</span> | Supported | ? | PLR / OEM |

### Incubators

| Manufacturer | Machine | Features | Status | Coverage | Links |
|--------------|---------|----------|--------|----------|--------|
| Thermo Fisher Scientific | Cytomat C6000 | <span class="badge badge-heating">active heating</span><span class="badge badge-storage">smart storage</span> | Supported | ? | PLR / OEM |
| Thermo Fisher Scientific | Cytomat C6002 | <span class="badge badge-heating">active heating</span><span class="badge badge-storage">smart storage</span> | Supported | ? | PLR / OEM |
| Thermo Fisher Scientific | Cytomat C2C_50 | <span class="badge badge-heating">active heating</span><span class="badge badge-storage">smart storage</span> | Supported | ? | PLR / OEM |
| Thermo Fisher Scientific | Cytomat C2C_425 | <span class="badge badge-heating">active heating</span><span class="badge badge-storage">smart storage</span> | Supported | ? | PLR / OEM |
| Thermo Fisher Scientific | Cytomat C2C_450_SHAKE | <span class="badge badge-heating">active heating</span><span class="badge badge-shaking">shaking</span><span class="badge badge-storage">smart storage</span> | Supported | ? | PLR / OEM |
| Thermo Fisher Scientific | Cytomat 5C | <span class="badge badge-heating">active heating</span><span class="badge badge-storage">smart storage</span> | Supported | ? | PLR / OEM |
| Thermo/Liconic | Heraeus Cytomat | <span class="badge badge-heating">active heating</span><span class="badge badge-storage">smart storage</span> | WIP [#485](https://github.com/PyLabRobot/pylabrobot/pull/485) | ? | PLR / OEM |
| Inheco | Incubator Shaker MTP & DWP | <span class="badge badge-heating">active heating</span> | WIP | ? | PLR / OEM |

### Peelers

| Manufacturer | Machine | Features | Status | Coverage | Links |
|--------------|---------|----------|--------|----------|--------|
| Azenta Life Sciences | XPeel | <span class="badge badge-sealing">plate sealing removal</span> | WIP | ? | PLR / OEM |

### Sealers

| Manufacturer | Machine | Features | Status | Coverage | Links |
|--------------|---------|----------|--------|----------|--------|
| Azenta Life Sciences | a4S Sealer | <span class="badge badge-sealing">sealing</span> | Supported | 100% | PLR / OEM |

### Thermocyclers

| Manufacturer | Machine | Features | Status | Coverage | Links |
|--------------|---------|----------|--------|----------|--------|
| Opentrons | Thermocycler | <span class="badge badge-thermo">thermocycling</span> | Supported | ? | PLR / OEM |
| Thermo Fisher Scientific | ATC | <span class="badge badge-thermo">thermocycling</span> | WIP | ? | PLR / OEM |
| Thermo Fisher Scientific | Proflex | <span class="badge badge-thermo">thermocycling</span> | WIP [#367](https://github.com/PyLabRobot/pylabrobot/pull/367) | ? | PLR / OEM |
| Inheco | ODTC | <span class="badge badge-thermo">thermocycling</span> | WIP | ? | PLR / OEM |

### Temperature controllers

| Manufacturer | Machine | Features | Status | Coverage | Links |
|--------------|---------|----------|--------|----------|--------|
| Opentrons | Temperature Module | <span class="badge badge-heating">active heating</span><span class="badge badge-cooling">active cooling</span> | Supported | ? | PLR / OEM |
| Inheco | CPAC | <span class="badge badge-heating">active heating</span><span class="badge badge-cooling">active cooling</span> | WIP | ? | PLR / OEM |

### Tilting

| Manufacturer | Machine | Features | Status | Coverage | Links |
|--------------|---------|----------|--------|----------|--------|
| Hamilton | Tilt Module | <span class="badge badge-tilt">plate tilting</span> | Supported | ? | PLR / OEM |

---

## Analytical Machines

### Plate readers

| Manufacturer | Machine | Features | Status | Coverage | Links |
|--------------|---------|----------|--------|----------|--------|
| BMG Labtech | ClarioSTAR | <span class="badge badge-reading">plate reading</span> | Supported | ? | PLR / OEM |
| Agilent (BioTek) | Cytation 1 | <span class="badge badge-reading">plate reading</span><span class="badge badge-microscopy">microscopy</span> | Supported | ? | PLR / OEM |
| Agilent (BioTek) | Cytation 5 | <span class="badge badge-reading">plate reading</span><span class="badge badge-microscopy">microscopy</span> | Supported | ? | PLR / OEM |

### Flow cytometers

| Manufacturer | Machine | Features | Status | Coverage | Links |
|--------------|---------|----------|--------|----------|--------|
| Beckman Coulter | Cytoflex S | <span class="badge badge-cyto">flow cytometry</span> | WIP | ? | PLR / OEM |

### qPCR Machine

| Manufacturer | Machine | Features | Status | Coverage | Links |
|--------------|---------|----------|--------|----------|--------|
| Thermo Fisher Scientific | QuantStudio 5 | <span class="badge badge-thermo">thermocycling</span><span class="badge badge-reading">plate reading</span> | WIP | ? | PLR / OEM |

### Scales

| Manufacturer | Machine | Features | Status | Coverage | Links |
|--------------|---------|----------|--------|----------|--------|
| Mettler Toledo | WXS205SDU | <span class="badge badge-weight">weight measuring</span> | Supported | 100% | PLR / OEM |

---

## Understanding the Table

Classifying lab automation equipment can be challenging, as many machines have overlapping capabilities and different user groups require varying levels of software integration.
PyLabRobot aims to give full access (100%) of the hardware-firmware capabilities that machines possess, even in cases where OEM software might not fully expose these capabilities.
This then allows PLR users to *choose* which functionalities they require.

This table categorizes equipment based on the three machine categories in PyLabRobot:

- **Liquid handling**: Any machine that actively engages or manipulates liquids.
- **Material handling**: All other machines that do not *directly* handle liquids.
- **Analytical**: Any machine primarily responsible for performing measurements.

The table showcases each PyLabRobot-integrated machine:

- **Features**: Core capabilities provided by the machine.
- **Status**: Indicates whether a machine is fully integrated (**Supported**, meaning at least 90% of capabilities available) or still being developed (**WIP**).
- **Coverage**: Shows how much of the machine's functionality is currently supported as a percentage of the known hardware-firmware capabilities of the machine.

---
