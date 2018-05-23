import os
from pyhamilton import (HamiltonInterface, LayoutManager, ResourceType, Tip96, Plate96,
    INITIALIZE, PICKUP, EJECT, ASPIRATE, DISPENSE,
    HamiltonError)

layfile = os.path.abspath(os.path.join('.', 'multi_ch_aspirate_dispense.lay'))
lmgr = LayoutManager(layfile)

tip_name_from_line = lambda line: LayoutManager.layline_first_field(line)
tip_name_condition = lambda line: LayoutManager.field_starts_with(tip_name_from_line(line), 'HTF_L_')
tips_type = ResourceType(Tip96, tip_name_condition, tip_name_from_line)
tips = lmgr.assign_unused_resource(tips_type)

plate_type = ResourceType(Plate96, 'Cos_96_Rd_0001')
plate = lmgr.assign_unused_resource(plate_type)

if __name__ == '__main__':
    tip_labware_pos = ';'.join((tips.layout_name() + ', ' + tips.position_id(tip_no) for tip_no in (23, 26, 29, 14, 17, 20, 8, 11))) # arbitrary tips
    well_labware_pos = ';'.join((plate.layout_name() + ', ' + plate.position_id(well_no) for well_no in range(16, 24))) # column 3
    liq_class = 'HighVolumeFilter_Water_DispenseJet_Empty'
    with HamiltonInterface() as hammy:
        hammy.wait_on_response(hammy.send_command(INITIALIZE))
        ids = [hammy.send_command(PICKUP, labwarePositions=tip_labware_pos),
               hammy.send_command(ASPIRATE, labwarePositions=well_labware_pos, volumes=100.0, liquidClass=liq_class),
               hammy.send_command(DISPENSE, labwarePositions=well_labware_pos, volumes=100.0, liquidClass=liq_class),
               hammy.send_command(EJECT, labwarePositions=tip_labware_pos)]
        for id in ids:
            try:
                print(hammy.wait_on_response(id, raise_first_exception=True))
            except HamiltonError as he:
                print(he)
