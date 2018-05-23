import os
from pyhamilton import (HamiltonInterface, LayoutManager, ResourceType, Tip96,
    INITIALIZE, PICKUP, NoTipError, HardwareError)

layfile = os.path.abspath(os.path.join('.', 'minimal_error_example.lay'))
lmgr = LayoutManager(layfile)

tip_name_from_line = lambda line: LayoutManager.layline_first_field(line)
tip_name_condition = lambda line: LayoutManager.field_starts_with(tip_name_from_line(line), 'HTF_L_')
tips_type = ResourceType(Tip96, tip_name_condition, tip_name_from_line)
tips = lmgr.assign_unused_resource(tips_type)

if __name__ == '__main__':
    with HamiltonInterface() as hammy:
        print('INITIALIZED!!', hammy.wait_on_response(hammy.send_command(INITIALIZE)))
        try:
            id = hammy.send_command(PICKUP, labwarePositions=str(tips.layout_name()) + ', 1;')
            print(hammy.wait_on_response(id, raise_first_exception=True))
        except NoTipError:
            print('\n'*10 + 'THERE WAS NO TIP THERE' + '\n'*10)
        except HardwareError:
            print('\n'*10 + 'Did I just crash into something?' + '\n'*10)
