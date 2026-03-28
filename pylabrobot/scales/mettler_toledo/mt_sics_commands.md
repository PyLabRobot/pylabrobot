# MT-SICS Command Reference

MT-SICS = Mettler Toledo Standard Interface Command Set

Commands organized by level and ranked by utility for PyLabRobot integration.
Source: MT-SICS Interface Command Set for Automated Precision Weigh Modules (spec doc).

Status key:
- DONE = implemented in mettler_toledo_backend.py
- HIGH = high priority for implementation
- MED  = medium priority
- LOW  = low priority / niche use case
- N/A  = not applicable to automation use case

## Level 0 - Basic Set (always available)

| Command | Description                              | Spec Page | Status | Notes |
|---------|------------------------------------------|-----------|--------|-------|
| @       | Cancel / reset to determined state       | 16        | DONE   | cancel(). Sent during setup(). Response is I4-style. |
| I0      | List all implemented commands + levels   | 96        | MED    | Useful for runtime capability discovery. Multi-response (B status). |
| I1      | MT-SICS level and level versions         | 97        | DONE   | Queried during setup(). |
| I2      | Device data (type and capacity)          | 98        | DONE   | Split into request_device_type() and request_capacity(). |
| I3      | Software version and type definition     | 99        | MED    | Useful for diagnostics/logging. |
| I4      | Serial number                            | 100       | DONE   | request_serial_number(). |
| I5      | Software material number                 | 101       | LOW    | |
| S       | Stable weight value                      | 223       | DONE   | read_stable_weight(). |
| SI      | Weight value immediately                 | 225       | DONE   | read_weight_value_immediately(). |
| Z       | Zero (wait for stable)                   | 272       | DONE   | zero_stable(). |
| ZI      | Zero immediately                         | 274       | DONE   | zero_immediately(). |
| T       | Tare (wait for stable)                   | 252       | DONE   | tare_stable(). |
| TI      | Tare immediately                         | 257       | DONE   | tare_immediately(). |

## Level 1 - Elementary Commands (always available)

| Command | Description                              | Spec Page | Status | Notes |
|---------|------------------------------------------|-----------|--------|-------|
| C       | Cancel all pending commands              | 23        | DONE   | cancel_all(). Multi-response (B then A). |
| D       | Write text to display                    | 52        | DONE   | set_display_text(). |
| DW      | Show weight on display                   | 61        | DONE   | set_weight_display(). |
| K       | Keys control                             | 153       | LOW    | Lock/unlock terminal keys. |
| SC      | Stable or dynamic value after timeout    | 224       | DONE   | read_dynamic_weight(). |
| SIR     | Weight immediately + repeat              | 232       | MED    | Continuous streaming. Needs multi-response support. |
| SIRU    | Weight immediately + repeat (display unit)| 233      | LOW    | |
| SNR     | Stable weight + repeat on stable change  | 241       | LOW    | |
| SR      | Stable weight + repeat on any change     | 245       | MED    | Continuous streaming. Needs multi-response support. |
| SRU     | Stable weight + repeat (display unit)    | 247       | LOW    | |
| TA      | Tare weight value (query/set)            | 253       | DONE   | request_tare_weight(). |
| TAC     | Clear tare weight value                  | 254       | DONE   | clear_tare(). |
| TC      | Tare with timeout                        | 255       | DONE   | tare_timeout(). |
| ZC      | Zero with timeout                        | 273       | DONE   | zero_timeout(). |

## Level 2 - Extended Commands (model-dependent)

### Device Information (query)

| Command | Description                              | Spec Page | Status | Notes |
|---------|------------------------------------------|-----------|--------|-------|
| I10     | Device identification                    | 102       | MED    | |
| I11     | Model designation                        | 103       | MED    | |
| I14     | Device information (detailed)            | 104       | MED    | |
| I15     | Uptime                                   | 106       | MED    | |
| I16     | Date of next service                     | 107       | LOW    | |
| I29     | Filter configuration                     | 111       | LOW    | |
| I32     | Voltage monitoring                       | 112       | MED    | Health check. |
| I43     | Selectable units for host unit           | 113       | LOW    | |
| I44     | Selectable units for display unit        | 114       | LOW    | |
| I45     | Selectable environment filter settings   | 115       | LOW    | |
| I46     | Selectable weighing modes                | 117       | LOW    | |
| I47     | Switch-on range                          | 118       | LOW    | |
| I48     | Initial zero range                       | 119       | LOW    | |
| I50     | Remaining weighing ranges                | 120       | DONE   | request_remaining_weighing_range(). Multi-response. |
| I51     | Power-on time                            | 121       | MED    | |
| I52     | Auto zero activation settings            | 122       | LOW    | |
| I54     | Adjustment loads                         | 125       | LOW    | |
| I55     | Menu version                             | 126       | LOW    | |
| I59     | Initial zero information                 | 129       | LOW    | |
| I62     | Timeout setting                          | 131       | LOW    | |
| I65     | Total operating time                     | 132       | MED    | Maintenance tracking. |
| I66     | Total load weighed                       | 133       | MED    | Maintenance tracking. |
| I67     | Total number of weighings                | 134       | MED    | Maintenance tracking. |
| I69     | Service provider address                 | 135       | LOW    | |
| I71     | One time adjustment status               | 136       | LOW    | |
| I73     | Sign off                                 | 137       | LOW    | |
| I74     | GEO code at calibration point (HighRes)  | 138       | LOW    | |
| I75     | GEO code at point of use (HighRes)       | 139       | LOW    | |
| I76     | Total voltage exceeds                    | 140       | LOW    | |
| I77     | Total load cycles                        | 141       | MED    | Maintenance tracking. |
| I78     | Zero deviation                           | 143       | LOW    | |
| I79     | Total zero deviation exceeds             | 144       | LOW    | |
| I80     | Total temperature exceeds                | 145       | LOW    | |
| I81     | Temperature gradient                     | 147       | LOW    | |
| I82     | Total temperature gradient exceeds       | 148       | LOW    | |
| I83     | Software identification                  | 149       | LOW    | |
| I100    | Active stability criteria                | 151       | LOW    | |
| I101    | Humidity value                           | 152       | LOW    | |

### Configuration (read/write)

| Command | Description                              | Spec Page | Status | Notes |
|---------|------------------------------------------|-----------|--------|-------|
| M01     | Weighing mode                            | 157       | MED    | |
| M02     | Environment condition                    | 158       | MED    | Affects filter/stability. |
| M03     | Auto zero function                       | 159       | MED    | |
| M21     | Unit (host/display)                      | 165       | DONE   | set_host_unit_grams(). |
| M23     | Readability (1d/xd)                      | 169       | LOW    | |
| M28     | Temperature value                        | 172       | MED    | |
| M35     | Zeroing mode at startup                  | 178       | LOW    | |
| M49     | Permanent tare mode                      | 188       | LOW    | |
| M67     | Timeout                                  | 191       | LOW    | |
| M68     | Behavior of serial interfaces            | 192       | LOW    | |
| COM     | Serial interface parameters              | 46        | LOW    | Baud rate, parity, etc. |
| ECHO    | Echo mode                                | 66        | LOW    | |
| LST     | Current user settings                    | 156       | LOW    | |
| PROT    | Protocol mode                            | 220       | LOW    | |

### Adjustment / Calibration

| Command | Description                              | Spec Page | Status | Notes |
|---------|------------------------------------------|-----------|--------|-------|
| C0      | Adjustment setting                       | 24        | LOW    | |
| C1      | Start adjustment (current settings)      | 26        | MED    | Multi-response (B status). |
| C2      | Start adjustment (external weight)       | 28        | LOW    | |
| C3      | Start adjustment (built-in weight)       | 30        | MED    | Internal calibration. Multi-response. |
| C4      | Standard / initial adjustment            | 31        | LOW    | |
| C5      | Enable/disable step control              | 33        | LOW    | |
| C6      | Customer linearization + sensitivity     | 34        | LOW    | |
| C7      | Customer standard calibration            | 37        | LOW    | |
| C8      | Sensitivity adjustment                   | 40        | LOW    | |
| C9      | Scale placement sensitivity adjustment   | 43        | LOW    | |
| M19     | Adjustment weight                        | 163       | LOW    | |
| M27     | Adjustment history                       | 171       | MED    | |

### Testing

| Command | Description                              | Spec Page | Status | Notes |
|---------|------------------------------------------|-----------|--------|-------|
| TST0    | Query/set test function settings         | 259       | LOW    | |
| TST1    | Test according to current settings       | 260       | LOW    | |
| TST2    | Test with external weight                | 262       | LOW    | |
| TST3    | Test with built-in weight                | 264       | LOW    | |
| TST5    | Module test with built-in weights        | 265       | LOW    | |

### Weight Variants (alternative read commands)

| Command | Description                              | Spec Page | Status | Notes |
|---------|------------------------------------------|-----------|--------|-------|
| SIC1    | Weight with CRC16 immediately            | 226       | LOW    | Data integrity. |
| SIC2    | HighRes weight with CRC16 immediately    | 227       | LOW    | |
| SIS     | Net weight with unit + weighing status   | 234       | MED    | More info than S/SI. |
| SIU     | Weight in display unit immediately       | 237       | LOW    | |
| SIUM    | Weight + MinWeigh info immediately       | 238       | LOW    | |
| SIX1    | Current gross, net, and tare values      | 239       | HIGH   | All three values in one call. |
| ST      | Stable weight on Transfer key press      | 249       | N/A   | Manual operation. |
| SU      | Stable weight in display unit            | 250       | LOW    | |
| SUM     | Stable weight + MinWeigh info            | 251       | LOW    | |

### Stored Weight

| Command | Description                              | Spec Page | Status | Notes |
|---------|------------------------------------------|-----------|--------|-------|
| SIMC    | Clear stored weight value                | 228       | LOW    | |
| SIMR    | Recall stored weight value               | 229       | LOW    | |
| SIMRC   | Recall and clear stored weight value     | 230       | LOW    | |
| SIMS    | Store weight immediately                 | 231       | LOW    | |

### Date/Time

| Command | Description                              | Spec Page | Status | Notes |
|---------|------------------------------------------|-----------|--------|-------|
| DAT     | Date                                     | 53        | LOW    | |
| DATI    | Date and time                            | 54        | MED    | |
| TIM     | Time                                     | 258       | LOW    | |

### Digital I/O

| Command | Description                              | Spec Page | Status | Notes |
|---------|------------------------------------------|-----------|--------|-------|
| DIN     | Configuration for digital inputs         | 55        | LOW    | |
| DIS     | Digital input status                     | 56        | LOW    | |
| DOS     | Digital output status                    | 57        | LOW    | |
| DOT     | Configuration for digital outputs        | 58        | LOW    | |
| DOTC    | Configurable digital outputs (weight)    | 59        | LOW    | |

### System / Lifecycle

| Command | Description                              | Spec Page | Status | Notes |
|---------|------------------------------------------|-----------|--------|-------|
| E01     | Current system error state               | 62        | HIGH   | Error monitoring. |
| E02     | Weighing device errors and warnings      | 63        | HIGH   | Error monitoring. |
| E03     | Current system errors and warnings       | 65        | HIGH   | Error monitoring. |
| FSET    | Reset all settings to factory defaults   | 95        | LOW    | Destructive. |
| RO1     | Restart device                           | 221       | MED    | |
| RDB     | Readability                              | 222       | LOW    | |
| UPD     | Update rate for SIR/SIRU                 | 267       | LOW    | |
| USTB    | User defined stability criteria          | 268       | LOW    | |

### Network (not relevant for serial)

| Command | Description                              | Spec Page | Status | Notes |
|---------|------------------------------------------|-----------|--------|-------|
| I53     | IPv4 runtime network config              | 123       | N/A   | Ethernet only. |
| M69     | IPv4 network configuration mode          | 193       | N/A   | |
| M70     | IPv4 host address + netmask              | 195       | N/A   | |
| M71     | IPv4 default gateway                     | 197       | N/A   | |
| M72     | IPv4 DNS server                          | 199       | N/A   | |
| M109    | IPv4 managed network config              | 204       | N/A   | |
| M117    | TCP port number                          | 209       | N/A   | |
| M118    | Fieldbus network stack type              | 211       | N/A   | |
| NID     | Node identification                      | 218       | N/A   | |
| NID2    | Device node ID                           | 219       | N/A   | |

### Application-Specific (Level 3, filling/dosing)

| Command | Description                              | Spec Page | Status | Notes |
|---------|------------------------------------------|-----------|--------|-------|
| A01     | Percent weighing reference               | 17        | N/A   | Application mode. |
| A02     | Sample identification                    | 18        | N/A   | |
| A03     | Sample name                              | 19        | N/A   | |
| A06     | Dynamic weighing behavior                | 20        | N/A   | |
| A10     | Nominal, +Tolerance, -Tolerance          | 21        | N/A   | |
| A30     | Internal loads                           | 22        | N/A   | |
| CW02    | Time for weighing                        | 48        | N/A   | |
| CW03    | Triggered weight value                   | 50        | N/A   | |
| CW11    | Check weighing: weight calculation mode  | 51        | N/A   | |
| F01-F16 | Filling functions (16 commands)          | 69-91     | N/A   | Filling/dosing application. |
| FCUT    | Filter cut-off frequency                 | 92        | N/A   | |
| FCUT2   | Alt weight path cut-off frequency        | 93        | N/A   | |
| WMCF    | Weight monitoring functions              | 270       | N/A   | |
| M17     | ProFACT: Single time criteria            | 160       | N/A   | |
| M18     | ProFACT/FACT: Temperature criterion      | 162       | N/A   | |
| M22     | Custom unit definitions                  | 168       | N/A   | |
| M31     | Operating mode after restart             | 174       | N/A   | |
| M32     | ProFACT: Time criteria                   | 175       | N/A   | |
| M33     | ProFACT: Day of the week                 | 176       | N/A   | |
| M34     | MinWeigh: Method                         | 177       | N/A   | |
| M38     | Selective parameter reset                | 179       | N/A   | |
| M39     | SmartTrac: Graphic                       | 180       | N/A   | |
| M43     | Custom unit                              | 181       | N/A   | |
| M44     | Command after startup response           | 182       | N/A   | |
| M45     | RS422/485 line termination               | 183       | N/A   | |
| M47     | Frequently changed test weight settings  | 184       | N/A   | |
| M48     | Infrequently changed test weight settings| 186       | N/A   | |
| M66     | GWP: Certified test weight settings      | 189       | N/A   | |
| M89     | Interface command set                    | 201       | N/A   | |
| M103    | RS422/485 driver mode                    | 202       | N/A   | |
| M110    | Change display resolution                | 205       | N/A   | |
| M111    | SAI Cyclic data format                   | 207       | N/A   | |
| M116    | Ignore Ethernet initial parametrization  | 208       | N/A   | |
| M119    | Byte order mode for automation           | 212       | N/A   | |
| M124    | Power supply for daisy chain             | 214       | N/A   | |
| MOD     | Various user modes                       | 215       | N/A   | |
| MONH    | Monitor on interface                     | 217       | N/A   | |
| SNRU    | Stable weight (display unit) + repeat    | 243       | N/A   | |

## Priority Summary

### HIGH (should implement next)
- E01/E02/E03 (error monitoring) - Level 2
- SIX1 (gross, net, tare in one call) - Level 2

### MED (useful but not urgent)
- I0 (command discovery)
- I3 (software version)
- I11 (model designation)
- I14/I15/I51 (device info, uptime, power-on time)
- I65/I66/I67/I77 (maintenance tracking)
- SIR/SR (continuous streaming - blocked by multi-response TODO)
- SIS (net weight + status)
- C1/C3 (adjustment - blocked by multi-response TODO)
- M01/M02/M03 (weighing mode, environment, auto-zero)
- M27 (adjustment history)
- M28 (temperature)
- DATI (date/time)
- RO1 (restart)
