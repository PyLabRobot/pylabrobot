import os
from pyhamilton import (HamiltonInterface, LayoutManager, ResourceType, Plate96,
    INITIALIZE, ISWAP_GET, ISWAP_PLACE,
    HamiltonError)

layfile = os.path.abspath(os.path.join('.', 'grip_move_plate.lay'))
lmgr = LayoutManager(layfile)

plate_type = ResourceType(Plate96, 'Cos_96_Rd_0001')
plate = lmgr.assign_unused_resource(plate_type)

target_site_type = ResourceType(Plate96, 'Cos_96_Rd_0002')
target_site = lmgr.assign_unused_resource(target_site_type)

if __name__ == '__main__':
    plate_pos = plate.layout_name() + ', ' + plate.position_id(0)
    target_pos = target_site.layout_name() + ', ' + target_site.position_id(0)
    with HamiltonInterface() as hammy:
        hammy.wait_on_response(hammy.send_command(INITIALIZE))
        for id in (hammy.send_command(ISWAP_GET, plateLabwarePositions=plate_pos),
                   hammy.send_command(ISWAP_PLACE, plateLabwarePositions=target_pos)):
            print(hammy.wait_on_response(id, raise_first_exception=True))
        for id in (hammy.send_command(ISWAP_GET, plateLabwarePositions=target_pos),
                   hammy.send_command(ISWAP_PLACE, plateLabwarePositions=plate_pos)):
            print(hammy.wait_on_response(id, raise_first_exception=True))
