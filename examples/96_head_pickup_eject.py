import os
from pyhamilton import (HamiltonInterface, LayoutManager, ResourceType, Tip96,
    INITIALIZE, PICKUP96, EJECT96)

layfile = os.path.abspath(os.path.join('.', '96_head_pickup_eject.lay'))
lmgr = LayoutManager(layfile)

tip_name_from_line = lambda line: LayoutManager.layline_first_field(line)
tip_name_condition = lambda line: LayoutManager.field_starts_with(tip_name_from_line(line), 'STF_L_')
tips_type = ResourceType(Tip96, tip_name_condition, tip_name_from_line)
tips = lmgr.assign_unused_resource(tips_type)

if __name__ == '__main__':
    with HamiltonInterface() as hammy:
        print('INITIALIZED!!', hammy.wait_on_response(hammy.send_command(INITIALIZE)))
        labware = str(tips.layout_name())
        labware_poss = '; '.join([labware + ',' + str(i+1) for i in range(96)]) + ';'
         # A dictionary can be unpacked into the command...
        cmd_dict = {'labwarePositions':labware_poss}
        id = hammy.send_command(PICKUP96, **cmd_dict)
        print(hammy.wait_on_response(id, raise_first_exception=True))
         # Or the command fields can be specified with keyword arguments
        id = hammy.send_command(EJECT96, labwarePositions=labware_poss)
        print(hammy.wait_on_response(id, raise_first_exception=True))
