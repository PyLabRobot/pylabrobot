# -*- coding: utf-8 -*-
"""
Created on {creation_date}

@author: {author}
"""

import os, sys

this_file_dir = os.path.dirname(__file__)

from pyhamilton import (HamiltonInterface, LayoutManager, ResourceType, Plate24, Plate96, Tip96,
    INITIALIZE, PICKUP, EJECT, ASPIRATE, DISPENSE, ISWAP_GET, ISWAP_PLACE, HEPA,
    WASH96_EMPTY, PICKUP96, EJECT96, ASPIRATE96, DISPENSE96,
    oemerr, PositionError)

from pyhamilton.utils import (
    initialize, hepa_on, tip_pick_up, tip_eject, aspirate, dispense, wash_empty_refill,
    tip_pick_up_96, tip_eject_96, aspirate_96, dispense_96,
    resource_list_with_prefix, read_plate, move_plate, add_robot_level_log, add_stderr_logging,
    run_async, yield_in_chunks)

log_dir = os.path.join(this_file_dir, 'log')
if not os.path.exists(log_dir):
    os.mkdir(log_dir)
main_logfile = os.path.join(log_dir, 'main.log')

layfile = os.path.join(this_file_dir, "{layfile}")
lmgr = LayoutManager(layfile)

print("layfile loaded")

# Define resources here...
# aspiration_plate = lmgr.assign_unused_resource(ResourceType(Plate96, "aspiration_plate"))

if __name__ == '__main__':
    with HamiltonInterface(simulate=('--simulate' in sys.argv)) as ham_int:
        initialize(ham_int)
