# Beckman Coulter

Company page: [Beckman Coulter Life Sciences](https://www.beckman.com)

Transcribed from the tip and labware classes of Biomek 5, Beckman Coulter's software for its Biomek
liquid handlers. The labware is defined as Biomek defines it, under Biomek's class names (in
quotes), whoever makes it. Plates and reservoirs use ordinary PyLabRobot `Plate` and `Well`
resources; Biomek's well sections become each well's `height_volume_data`. Tips are `BiomekTip`s,
which also carry the tip's cone.

## Tips

| Description | Image | PLR definition |
|-|-|-|
| 'T50F'<br>Biomek tip, 50 uL, filter | | `beckman_coulter_biomek_tip_50uL_filter` |
| 'T190F'<br>Biomek tip, 190 uL, filter | | `beckman_coulter_biomek_tip_190uL_filter` |
| 'T230'<br>Biomek tip, 230 uL, no filter | | `beckman_coulter_biomek_tip_230uL` |
| 'T1025F'<br>Biomek tip, 1025 uL, filter | | `beckman_coulter_biomek_tip_1025uL_filter` |
| 'T1070'<br>Biomek tip, 1070 uL, no filter | | `beckman_coulter_biomek_tip_1070uL` |

## Tip racks

| Description | Image | PLR definition |
|-|-|-|
| 'BC50F'<br>Part no.: B85888<br>Biomek tip box: 96 T50F tips | | `beckman_coulter_biomek_96_tiprack_50uL_filter` |
| 'BC190F'<br>Part no.: B85911<br>Biomek tip box: 96 T190F tips | | `beckman_coulter_biomek_96_tiprack_190uL_filter` |
| 'BC230'<br>Part no.: B85906 (sterile), B85903 (non-sterile)<br>Biomek tip box: 96 T230 tips | | `beckman_coulter_biomek_96_tiprack_230uL` |
| 'BC1025F'<br>Part no.: B85955<br>Biomek tip box: 96 T1025F tips | | `beckman_coulter_biomek_96_tiprack_1025uL_filter` |
| 'BC1070'<br>Part no.: B85945 (sterile), B85940 (non-sterile)<br>Biomek tip box: 96 T1070 tips | | `beckman_coulter_biomek_96_tiprack_1070uL` |

## Plates

| Description | Image | PLR definition |
|-|-|-|
| 'Thermo96VB_PP'<br>96 round, V-bottomed wells; with its 'TiterplateLid' | | `beckman_coulter_biomek_96_wellplate_vb` |
| 'BCFlat96'<br>Part no.: 609844<br>96 round, flat-bottomed wells; with its 'TiterplateLid' | | `beckman_coulter_biomek_96_wellplate_flat` |
| 'BCDeep96Square'<br>Part no.: 140504<br>96 deep, square, V-bottomed wells | | `beckman_coulter_biomek_96_deepwell_square` |
| 'Thomson_24w_Square_RB'<br>Part no.: 931568<br>24 square, round-bottomed wells; with its lid | | `beckman_coulter_biomek_24_wellplate_square_rb` |

## Reservoirs

| Description | Image | PLR definition |
|-|-|-|
| 'ThermoNUNC_350CB'<br>one flat-bottomed section; with its 'TiterplateLid' | | `beckman_coulter_biomek_reservoir_350cb` |
| 'Reservoir'<br>Part no.: 148033<br>one flat-bottomed section | | `beckman_coulter_biomek_reservoir` |
| 'ModularReservoir'<br>Part no.: 372795 (frame)<br>four V-bottomed sections | | `beckman_coulter_biomek_modular_reservoir` |
