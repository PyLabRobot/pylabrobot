"""Start a remote deck server."""

import uvicorn

from pylabrobot.resources import Cor_96_wellplate_360ul_Fb, hamilton_96_tiprack_1000uL_filter
from pylabrobot.resources.hamilton import STARDeck
from pylabrobot.resources.remote.server import create_app

deck = STARDeck()
deck.assign_child_resource(hamilton_96_tiprack_1000uL_filter(name="tips"), rails=3)
deck.assign_child_resource(Cor_96_wellplate_360ul_Fb(name="plate"), rails=9)

app = create_app(deck)
uvicorn.run(app, host="0.0.0.0", port=8080)
