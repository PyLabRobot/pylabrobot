# MT-SICS Command Reference

MT-SICS = Mettler Toledo Standard Interface Command Set

Commands organized by level and ranked by utility for PyLabRobot integration.
Source: MT-SICS Interface Command Set for Automated Precision Weigh Modules (spec doc).

**Important:** I1 reports which standardized level sets are fully implemented, but
individual commands may exist outside those levels. I0 is the definitive source of
command support. During setup(), the backend queries I0 to discover all available
commands and gates methods via `@requires_mt_sics_command`.

**Hardware-validated on WXS205SDU WXA-Bridge (S/N: B207696838):**
I1 reports levels [0, 1] but I0 discovers 62 commands across levels 0-3.
Commands not in I0 (C, D, DW, SC, ZC, TC, I50) return ES (syntax error).

Status key:
- DONE = implemented in backend.py
- HIGH = high priority for implementation
- MED  = medium priority
- LOW  = low priority / niche use case
- N/A  = not applicable to automation use case
- WXS205SDU column: supported/not supported on our test device

## Level 0 - Basic Set (always available)

| Command | Description                              | Spec Page | Status | WXS205SDU | Notes |
|---------|------------------------------------------|-----------|--------|-----------|-------|
| @       | Reset device to determined state         | 16        | DONE   | yes | reset(). Sent during setup(). Response is I4-style. |
| I0      | List all implemented commands + levels   | 96        | DONE   | yes | _request_supported_commands(). Queried during setup(). |
| I1      | MT-SICS level and level versions         | 97        | DONE   | yes | Not used for gating - I0 is authoritative. |
| I2      | Device data (type and capacity)          | 98        | DONE   | yes | request_device_type() and request_capacity(). Response is one quoted string parsed with shlex. |
| I3      | Firmware version and type definition     | 99        | DONE   | yes | request_firmware_version(). Returns "1.10 18.6.4.1361.772" on test device. |
| I4      | Serial number                            | 100       | DONE   | yes | request_serial_number(). |
| I5      | Software material number                 | 101       | DONE   | yes | request_software_material_number(). Returns "11671158C" on test device. |
| S       | Stable weight value                      | 223       | DONE   | yes | read_stable_weight(). |
| SI      | Weight value immediately                 | 225       | DONE   | yes | read_weight_value_immediately(). |
| SIR     | Weight immediately + repeat              | 232       | MED    | yes | Continuous streaming. |
| Z       | Zero (wait for stable)                   | 272       | DONE   | yes | zero_stable(). |
| ZI      | Zero immediately                         | 274       | DONE   | yes | zero_immediately(). |

## Level 1 - Elementary Commands (always available)

| Command | Description                              | Spec Page | Status | WXS205SDU | Notes |
|---------|------------------------------------------|-----------|--------|-----------|-------|
| C       | Cancel all pending commands              | 23        | DONE   | **no** | cancel_all(). Not supported on WXS205SDU bridge. |
| D       | Write text to display                    | 52        | DONE   | **no** | set_display_text(). Not supported in bridge mode (no terminal). |
| DW      | Show weight on display                   | 61        | DONE   | **no** | set_weight_display(). Not supported in bridge mode. |
| K       | Keys control                             | 153       | LOW    | - | Lock/unlock terminal keys. |
| SC      | Stable or dynamic value after timeout    | 224       | DONE   | **no** | read_dynamic_weight(). Not supported on WXS205SDU. |
| SR      | Stable weight + repeat on any change     | 245       | MED    | yes | Continuous streaming. |
| SRU     | Stable weight + repeat (display unit)    | 247       | LOW    | - | |
| T       | Tare (wait for stable)                   | 252       | DONE   | yes | tare_stable(). |
| TA      | Tare weight value (query/set)            | 253       | DONE   | yes | request_tare_weight(). |
| TAC     | Clear tare weight value                  | 254       | DONE   | yes | clear_tare(). |
| TC      | Tare with timeout                        | 255       | DONE   | **no** | tare_timeout(). Not supported on WXS205SDU. |
| TI      | Tare immediately                         | 257       | DONE   | yes | tare_immediately(). |
| ZC      | Zero with timeout                        | 273       | DONE   | **no** | zero_timeout(). Not supported on WXS205SDU. |

## Level 2 - Extended Commands (model-dependent)

### Device Information (query)

| Command | Description                              | Spec Page | Status | WXS205SDU | Notes |
|---------|------------------------------------------|-----------|--------|-----------|-------|
| I10     | Device identification                    | 102       | DONE   | yes | request_device_id() (read). set_device_id() commented out (EEPROM write). |
| I11     | Model designation                        | 103       | DONE   | yes | request_model_designation(). Returns "WXS205SDU" on test device. |
| I14     | Device information (detailed)            | 104       | DONE   | yes | request_device_info(). Multi-response with config, descriptions, SW IDs, serial numbers. |
| I15     | Uptime in minutes since start/restart     | 106       | DONE   | yes | request_uptime_minutes(). Returns minutes, accuracy +/- 5%. |
| I16     | Date of next service                     | 107       | DONE   | yes | request_next_service_date(). |
| I21     | Revision of assortment type tolerances   | 108       | DONE   | yes | request_assortment_type_revision(). |
| I29     | Filter configuration                     | 111       | LOW    | - | |
| I32     | Voltage monitoring                       | 112       | MED    | - | |
| I43     | Selectable units for host unit           | 113       | LOW    | - | |
| I44     | Selectable units for display unit        | 114       | LOW    | - | |
| I45     | Selectable environment filter settings   | 115       | LOW    | - | |
| I46     | Selectable weighing modes                | 117       | LOW    | - | |
| I47     | Switch-on range                          | 118       | LOW    | - | |
| I48     | Initial zero range                       | 119       | LOW    | - | |
| I50     | Remaining weighing ranges                | 120       | DONE   | **no** | request_remaining_weighing_range(). Not on WXS205SDU. |
| I51     | Power-on time                            | 121       | MED    | - | |
| I52     | Auto zero activation settings            | 122       | LOW    | - | |
| I54     | Adjustment loads                         | 125       | LOW    | - | |
| I55     | Menu version                             | 126       | LOW    | - | |
| I59     | Initial zero information                 | 129       | LOW    | - | |
| I62     | Timeout setting                          | 131       | LOW    | - | |
| I65     | Total operating time                     | 132       | MED    | - | |
| I66     | Total load weighed                       | 133       | MED    | - | |
| I67     | Total number of weighings                | 134       | MED    | - | |
| I69     | Service provider address                 | 135       | LOW    | - | |
| I71     | One time adjustment status               | 136       | LOW    | - | |
| I73     | Sign off                                 | 137       | LOW    | - | |
| I74     | GEO code at calibration point (HighRes)  | 138       | LOW    | - | |
| I75     | GEO code at point of use (HighRes)       | 139       | LOW    | - | |
| I76     | Total voltage exceeds                    | 140       | LOW    | - | |
| I77     | Total load cycles                        | 141       | MED    | - | |
| I78     | Zero deviation                           | 143       | LOW    | - | |
| I79     | Total zero deviation exceeds             | 144       | LOW    | - | |
| I80     | Total temperature exceeds                | 145       | LOW    | - | |
| I81     | Temperature gradient                     | 147       | LOW    | - | |
| I82     | Total temperature gradient exceeds       | 148       | LOW    | - | |
| I83     | Software identification                  | 149       | LOW    | - | |
| I100    | Active stability criteria                | 151       | LOW    | - | |
| I101    | Humidity value                           | 152       | LOW    | - | |

### Configuration (read/write)

| Command | Description                              | Spec Page | Status | WXS205SDU | Notes |
|---------|------------------------------------------|-----------|--------|-----------|-------|
| M01     | Weighing mode                            | 157       | DONE   | yes | request_weighing_mode() (read). set commented out (persists to memory). |
| M02     | Environment condition                    | 158       | DONE   | yes | request_environment_condition() (read). set commented out (persists to memory). |
| M03     | Auto zero function                       | 159       | DONE   | yes | request_auto_zero() (read). set commented out (persists to memory). |
| M21     | Unit (host/display)                      | 165       | DONE   | yes | set_host_unit_grams(). |
| M23     | Readability (1d/xd)                      | 169       | LOW    | - | |
| M28     | Temperature value                        | 172       | DONE   | yes | measure_temperature(). Returns 19.8-19.9 C on test device. |
| M35     | Zeroing mode at startup                  | 178       | DONE   | yes | request_zeroing_mode() (read). set commented out (persists to memory). |
| M49     | Permanent tare mode                      | 188       | LOW    | - | |
| M67     | Timeout                                  | 191       | LOW    | - | |
| M68     | Behavior of serial interfaces            | 192       | LOW    | - | |
| COM     | Serial interface parameters              | 46        | DONE   | yes | request_serial_parameters(). set commented out (persists to memory). |
| ECHO    | Echo mode                                | 66        | LOW    | - | |
| LST     | Current user settings                    | 156       | DONE   | yes | request_user_settings(). Level 3 on WXS205SDU. |
| PROT    | Protocol mode                            | 220       | LOW    | - | |

### Adjustment / Calibration

| Command | Description                              | Spec Page | Status | WXS205SDU | Notes |
|---------|------------------------------------------|-----------|--------|-----------|-------|
| C0      | Adjustment setting                       | 24        | DONE   | yes | request_adjustment_setting() (read). set commented out (persists to memory). |
| C1      | Start adjustment (current settings)      | 26        | STUB   | yes | Commented out (moves internal weights). Multi-response. |
| C2      | Start adjustment (external weight)       | 28        | STUB   | yes | Commented out (requires placing external weight). |
| C3      | Start adjustment (built-in weight)       | 30        | STUB   | yes | Commented out (moves internal weights). Multi-response. |
| C4      | Standard / initial adjustment            | 31        | LOW    | - | |
| C5      | Enable/disable step control              | 33        | LOW    | - | |
| C6      | Customer linearization + sensitivity     | 34        | LOW    | - | |
| C7      | Customer standard calibration            | 37        | LOW    | - | |
| C8      | Sensitivity adjustment                   | 40        | LOW    | - | |
| C9      | Scale placement sensitivity adjustment   | 43        | LOW    | - | |
| M19     | Adjustment weight                        | 163       | DONE   | yes | request_adjustment_weight() (read). set commented out (persists to memory). |
| M27     | Adjustment history                       | 171       | DONE   | yes | request_adjustment_history(). Multi-response. |

### Testing

| Command | Description                              | Spec Page | Status | WXS205SDU | Notes |
|---------|------------------------------------------|-----------|--------|-----------|-------|
| TST0    | Query/set test function settings         | 259       | DONE   | yes | request_test_settings() (read). set commented out (persists to memory). |
| TST1    | Test according to current settings       | 260       | STUB   | yes | Commented out (moves internal weights). |
| TST2    | Test with external weight                | 262       | STUB   | yes | Commented out (requires placing test weight). |
| TST3    | Test with built-in weight                | 264       | STUB   | yes | Commented out (moves internal weights). |
| TST5    | Module test with built-in weights        | 265       | LOW    | - | |

### Weight Variants (alternative read commands)

| Command | Description                              | Spec Page | Status | WXS205SDU | Notes |
|---------|------------------------------------------|-----------|--------|-----------|-------|
| SIC1    | Weight with CRC16 immediately            | 226       | LOW    | - | |
| SIC2    | HighRes weight with CRC16 immediately    | 227       | LOW    | - | |
| SIS     | Net weight with unit + weighing status   | 234       | DONE   | yes | request_net_weight_with_status(). |
| SIU     | Weight in display unit immediately       | 237       | LOW    | - | |
| SIUM    | Weight + MinWeigh info immediately       | 238       | LOW    | - | |
| SIX1    | Current gross, net, and tare values      | 239       | HIGH   | - | Not on WXS205SDU. |
| SNR     | Stable weight + repeat on stable change  | 241       | DONE   | yes | read_stable_weight_repeat_on_change(). Use reset() to stop. |
| ST      | Stable weight on Transfer key press      | 249       | N/A    | - | Manual operation. |
| SU      | Stable weight in display unit            | 250       | LOW    | - | |
| SUM     | Stable weight + MinWeigh info            | 251       | LOW    | - | |

### Stored Weight

| Command | Description                              | Spec Page | Status | WXS205SDU | Notes |
|---------|------------------------------------------|-----------|--------|-----------|-------|
| SIMC    | Clear stored weight value                | 228       | LOW    | - | |
| SIMR    | Recall stored weight value               | 229       | LOW    | - | |
| SIMRC   | Recall and clear stored weight value     | 230       | LOW    | - | |
| SIMS    | Store weight immediately                 | 231       | LOW    | - | |

### Date/Time

| Command | Description                              | Spec Page | Status | WXS205SDU | Notes |
|---------|------------------------------------------|-----------|--------|-----------|-------|
| DAT     | Date                                     | 53        | DONE   | yes | request_date(). |
| DATI    | Date and time                            | 54        | MED    | - | |
| TIM     | Time                                     | 258       | DONE   | yes | request_time(). |

### Digital I/O

| Command | Description                              | Spec Page | Status | WXS205SDU | Notes |
|---------|------------------------------------------|-----------|--------|-----------|-------|
| DIN     | Configuration for digital inputs         | 55        | LOW    | - | |
| DIS     | Digital input status                     | 56        | LOW    | - | |
| DOS     | Digital output status                    | 57        | LOW    | - | |
| DOT     | Configuration for digital outputs        | 58        | LOW    | - | |
| DOTC    | Configurable digital outputs (weight)    | 59        | LOW    | - | |

### System / Lifecycle

| Command | Description                              | Spec Page | Status | WXS205SDU | Notes |
|---------|------------------------------------------|-----------|--------|-----------|-------|
| E01     | Current system error state               | 62        | HIGH   | - | Not on WXS205SDU. |
| E02     | Weighing device errors and warnings      | 63        | HIGH   | - | Not on WXS205SDU. |
| E03     | Current system errors and warnings       | 65        | HIGH   | - | Not on WXS205SDU. |
| FSET    | Reset all settings to factory defaults   | 95        | LOW    | yes | Level 3 on WXS205SDU. Destructive. |
| RO1     | Restart device                           | 221       | MED    | - | |
| RDB     | Readability                              | 222       | DONE   | yes | request_readability(). Level 3 on WXS205SDU. |
| UPD     | Update rate for SIR/SIRU                 | 267       | DONE   | yes | request_update_rate() (read). set commented out (persists to memory). |
| USTB    | User defined stability criteria          | 268       | DONE   | yes | request_stability_criteria() (read). set commented out. Level 3 on WXS205SDU. |

### Network (not relevant for serial)

| Command | Description                              | Spec Page | Status | WXS205SDU | Notes |
|---------|------------------------------------------|-----------|--------|-----------|-------|
| I53     | IPv4 runtime network config              | 123       | N/A    | - | Ethernet only. |
| M69     | IPv4 network configuration mode          | 193       | N/A    | - | |
| M70     | IPv4 host address + netmask              | 195       | N/A    | - | |
| M71     | IPv4 default gateway                     | 197       | N/A    | - | |
| M72     | IPv4 DNS server                          | 199       | N/A    | - | |
| M109    | IPv4 managed network config              | 204       | N/A    | - | |
| M117    | TCP port number                          | 209       | N/A    | - | |
| M118    | Fieldbus network stack type              | 211       | N/A    | - | |
| NID     | Node identification                      | 218       | N/A    | - | |
| NID2    | Device node ID                           | 219       | N/A    | - | |

### Application-Specific (Level 3, filling/dosing)

| Command | Description                              | Spec Page | Status | WXS205SDU | Notes |
|---------|------------------------------------------|-----------|--------|-----------|-------|
| A01     | Percent weighing reference               | 17        | N/A    | - | Application mode. |
| A02     | Sample identification                    | 18        | N/A    | - | |
| A03     | Sample name                              | 19        | N/A    | - | |
| A06     | Dynamic weighing behavior                | 20        | N/A    | - | |
| A10     | Nominal, +Tolerance, -Tolerance          | 21        | N/A    | - | |
| A30     | Internal loads                           | 22        | N/A    | - | |
| CW02    | Time for weighing                        | 48        | N/A    | - | |
| CW03    | Triggered weight value                   | 50        | N/A    | - | |
| CW11    | Check weighing: weight calculation mode  | 51        | N/A    | - | |
| F01-F16 | Filling functions (16 commands)          | 69-91     | N/A    | - | Filling/dosing application. |
| FCUT    | Filter cut-off frequency                 | 92        | DONE   | yes | request_filter_cutoff() (read). set commented out. Level 3 on WXS205SDU. |
| FCUT2   | Alt weight path cut-off frequency        | 93        | N/A    | - | |
| WMCF    | Weight monitoring functions              | 270       | N/A    | - | |
| M17     | ProFACT: Single time criteria            | 160       | DONE   | yes | request_profact_time_criteria() (read). set commented out. Level 2 on WXS205SDU. |
| M18     | ProFACT/FACT: Temperature criterion      | 162       | DONE   | yes | request_profact_temperature_criterion() (read). set commented out. Level 2 on WXS205SDU. |
| M22     | Custom unit definitions                  | 168       | N/A    | - | |
| M31     | Operating mode after restart             | 174       | DONE   | yes | request_operating_mode() (read). set commented out. Level 2 on WXS205SDU. |
| M32     | ProFACT: Time criteria                   | 175       | DONE   | yes | request_profact_time() (read). set commented out. Level 2 on WXS205SDU. |
| M33     | ProFACT: Day of the week                 | 176       | DONE   | yes | request_profact_day() (read). set commented out. Level 2 on WXS205SDU. |
| M34     | MinWeigh: Method                         | 177       | N/A    | - | |
| M38     | Selective parameter reset                | 179       | N/A    | - | |
| M39     | SmartTrac: Graphic                       | 180       | N/A    | - | |
| M43     | Custom unit                              | 181       | N/A    | - | |
| M44     | Command after startup response           | 182       | N/A    | - | |
| M45     | RS422/485 line termination               | 183       | N/A    | - | |
| M47     | Frequently changed test weight settings  | 184       | N/A    | - | |
| M48     | Infrequently changed test weight settings| 186       | N/A    | - | |
| M66     | GWP: Certified test weight settings      | 189       | N/A    | - | |
| M89     | Interface command set                    | 201       | N/A    | - | |
| M103    | RS422/485 driver mode                    | 202       | N/A    | - | |
| M110    | Change display resolution                | 205       | N/A    | - | |
| M111    | SAI Cyclic data format                   | 207       | N/A    | - | |
| M116    | Ignore Ethernet initial parametrization  | 208       | N/A    | - | |
| M119    | Byte order mode for automation           | 212       | N/A    | - | |
| M124    | Power supply for daisy chain             | 214       | N/A    | - | |
| MOD     | Various user modes                       | 215       | N/A    | - | |
| MONH    | Monitor on interface                     | 217       | N/A    | - | |
| SNRU    | Stable weight (display unit) + repeat    | 243       | N/A    | - | |

## Priority Summary

### HIGH (not available on WXS205SDU)
- E01/E02/E03 (error monitoring)
- SIX1 (gross, net, tare in one call)

### MED (useful but not urgent)
- SIR/SR (continuous streaming) - needs async iterator architecture
- DATI (date + time combined) - not on WXS205SDU

### STUB (commented out, require physical interaction)
- C1/C3 (internal weight adjustment)
- C2 (external weight adjustment)
- TST1-TST3 (test procedures)
