#!python3

from pyhamilton import HamiltonInterface, LayoutManager, INITIALIZE, WASH96_EMPTY

def wash_empty_refill(ham, **more_options):
    print('wash_empty_refill: empty the washer' +
            ('' if not more_options else ' with extra options ' + str(more_options)))
    ham.wait_on_response(ham.send_command(WASH96_EMPTY, **more_options))

if __name__ == '__main__':
    LayoutManager('with_washer.lay', install=True)
    with HamiltonInterface() as ham_int:
        print('Please wait, system starting--preparing to wash washer.\n')
        ham_int.wait_on_response(ham_int.send_command(INITIALIZE), raise_first_exception=True)
        wash_empty_refill(ham_int, refillAfterEmpty=1, chamber1WashLiquid=1, chamber2WashLiquid=1) # 1=both chambers; 1=Liquid 2 (water)
        wash_empty_refill(ham_int) # empty