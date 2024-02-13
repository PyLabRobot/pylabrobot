const SYSTEM_HAMILTON = "Hamilton";
const SYSTEM_OPENTRONS = "Opentrons";

class PipettingChannel {
  constructor(identifier) {
    this.identifier = identifier;
    this.volume = null;
    this.tip = null;
  }

  has_tip() {
    return this.tip !== null;
  }

  pickUpTip(tip) {
    if (this.tip !== null) {
      throw `Tip already on pipetting channel ${this.identifier}`;
    }

    this.tip = tip;
    this.volume = 0;
  }

  dropTip() {
    if (this.tip === null) {
      throw `No tip on pipetting channel ${this.identifier}`;
    }

    if (this.volume !== 0) {
      throw `Cannot drop tip from channel ${this.identifier} with volume ${this.volume}`;
    }

    this.tip = null;
  }

  aspirate(volume) {
    if (this.tip === null) {
      throw `No tip on pipetting channel ${this.identifier}`;
    }

    if (this.volume + volume > this.tip.maximal_volume) {
      throw `Not enough volume in tip on pipetting channel ${this.identifier}`;
    }

    this.volume += volume;
  }

  dispense(volume) {
    if (this.volume - volume < 0) {
      throw `Not enough volume in pipetting channel ${this.identifier}`;
    }

    this.volume -= volume;
  }
}

function checkPipHeadReach(x) {
  // Check if the x coordinate is within the pip head range. Undefined indicates no limit.
  // Returns the error.
  if (config.min_pip_head_location !== -1 && x < config.min_pip_head_location) {
    return `x position ${x} not reachable, because it is lower than the left limit (${config.min_pip_head_location})`;
  }
  if (config.max_pip_head_location !== -1 && x > config.max_pip_head_location) {
    return `x position ${x} not reachable, because it is higher than the right limit (${config.max_pip_head_location})`;
  }
  return undefined;
}

function checkCoreHeadReachable(x) {
  // Check if the x coordinate is within the core head range. Undefined indicates no limit.
  // Returns the error.
  if (
    config.min_core_head_location !== -1 &&
    x < config.min_core_head_location
  ) {
    return `x position ${x} not reachable, because it is lower than the left limit (${config.min_core_head_location})`;
  }
  if (
    config.max_core_head_location !== -1 &&
    x > config.max_core_head_location
  ) {
    return `x position ${x} not reachable, because it is higher than the right limit (${config.max_core_head_location})`;
  }
  return undefined;
}

class LiquidHandler extends Resource {
  constructor(resourceData, parent) {
    super(resourceData, parent);

    // Infer the system from the deck (sloppy).
    let deck = resourceData.children[0];
    if (deck.type === "OTDeck") {
      this.system = SYSTEM_OPENTRONS;
      // Just one channel for Opentrons right now. Should create a UI to select the config.
      this.numChannels = 1;
    } else if (["HamiltonSTARDeck", "HamiltonDeck"].includes(deck.type)) {
      this.system = SYSTEM_HAMILTON;
      this.numChannels = 8;
    } else {
      let errorString = `Unknown deck type: ${deck.type}. Supported deck types: OTDeck, HamiltonSTARDeck, HamiltonDeck.`;
      alert(errorString);
      throw new Error(errorString);
    }

    // Initialize the pipetting channels.
    this.mainHead = [];
    for (let i = 0; i < this.numChannels; i++) {
      this.mainHead.push(new PipettingChannel(i));
    }

    this.CoRe96Head = [];
    for (var i = 0; i < 8; i++) {
      this.CoRe96Head[i] = [];
      for (var j = 0; j < 12; j++) {
        this.CoRe96Head[i].push(new PipettingChannel(`96 head: ${i * 12 + j}`));
      }
    }
  }

  drawMainShape() {
    // Resource method
    // don't draw anything, just draw the children
    return undefined;
  }

  async processEvent(event, data) {
    switch (event) {
      case "setup":
        // Nothing to do (yet?).
        break;

      case "pick_up_tips":
        await sleep(config.pip_tip_pickup_duration);
        this.pickUpTips(data.use_channels, data.channels);
        break;

      case "drop_tips":
        await sleep(config.pip_tip_drop_duration);
        this.dropTips(data.use_channels, data.channels);
        break;

      case "aspirate":
        await sleep(config.pip_aspiration_duration);
        this.aspirate(data.use_channels, data.channels);
        break;

      case "dispense":
        await sleep(config.pip_dispense_duration);
        this.dispense(data.use_channels, data.channels);
        break;

      case "pick_up_tips96":
        await sleep(config.core_tip_pickup_duration);
        this.pickupTips96(data.resource_name);
        break;

      case "drop_tips96":
        await sleep(config.core_tip_drop_duration);
        ret.error = dropTips96(data.resource_name);
        break;

      case "aspirate96":
        await sleep(config.core_aspiration_duration);
        this.aspirate96(data.aspiration);
        break;

      case "dispense96":
        await sleep(config.core_dispense_duration);
        this.dispense96(data.dispense);
        break;

      default:
        throw new Error(`Unknown event: ${event}`);
    }
  }

  pickUpTips(useChannels, operations) {
    for (let i = 0; i < useChannels.length; i++) {
      let channelIndex = useChannels[i];
      let op = operations[i];

      var tipSpot = resources[op.resource_name];
      tipSpot.pickUpTip(resourceLayer);

      if (this.system === SYSTEM_HAMILTON) {
        const pipError = checkPipHeadReach(tipSpot.getAbsoluteLocation().x);
        if (pipError !== undefined) {
          throw new Error(pipError);
        }
      }

      this.mainHead[channelIndex].pickUpTip(tipSpot.tip);
    }
  }

  dropTips(useChannels, operations) {
    for (let i = 0; i < useChannels.length; i++) {
      let channelIndex = useChannels[i];
      let op = operations[i];

      let tipSpot = resources[op.resource_name];
      tipSpot.dropTip(resourceLayer);

      if (this.system === SYSTEM_HAMILTON) {
        const pipError = checkPipHeadReach(tipSpot.getAbsoluteLocation().x);
        if (pipError !== undefined) {
          throw new Error(pipError);
        }
      }

      this.mainHead[channelIndex].dropTip();
    }
  }

  aspirate(useChannels, operations) {
    for (let i = 0; i < useChannels.length; i++) {
      let channelIndex = useChannels[i];
      let { resource_name, volume } = operations[i];

      const well = resources[resource_name];
      well.aspirate(volume);

      if (this.system === SYSTEM_HAMILTON) {
        const pipError = checkPipHeadReach(well.getAbsoluteLocation().x);
        if (pipError !== undefined) {
          throw new Error(pipError);
        }
      }

      this.mainHead[channelIndex].aspirate(volume);
    }
  }

  dispense(useChannels, operations) {
    for (let i = 0; i < useChannels.length; i++) {
      let channelIndex = useChannels[i];
      let { resource_name, volume } = operations[i];

      const well = resources[resource_name];
      well.dispense(volume);

      if (this.system === SYSTEM_HAMILTON) {
        const pipError = checkPipHeadReach(well.getAbsoluteLocation().x);
        if (pipError !== undefined) {
          throw new Error(pipError);
        }
      }

      this.mainHead[channelIndex].dispense(volume);
    }
  }

  // TODO: 96 head check
  pickupTips96(resource_name) {
    if (this.system !== SYSTEM_HAMILTON) {
      throw new Error(
        "The 96 head actions are currently only available on the Hamilton Simulator."
      );
    }

    const tipRack = resources[resource_name];

    // Validate there are enough tips first, and that there are no tips in the head.
    for (let i = 0; i < 8; i++) {
      for (let j = 0; j < 12; j++) {
        const tip_name = tipRack.children[i + tipRack.num_items_y * j].name;
        const tip_spot = resources[tip_name];
        if (!tip_spot.has_tip) {
          throw new Error(
            `There is no tip at (${i},${j}) in ${resource_name}.`
          );
        }
        if (this.CoRe96Head[i][j].has_tip()) {
          throw new Error(
            `There already is a tip in the CoRe 96 head at (${i},${j}) in ${resource_name}.`
          );
        }
      }
    }

    // Check reachable for A1.
    let a1_name = tipRack.children[0].name;
    let a1_resource = resources[a1_name];
    const coreError = checkCoreHeadReachable(a1_resource.x);
    if (coreError !== undefined) {
      throw new Error(coreError);
    }

    // Then pick up the tips.
    for (let i = 0; i < 8; i++) {
      for (let j = 0; j < 12; j++) {
        const tip_name = tipRack.children[i + tipRack.num_items_y * j].name;
        const tip_spot = resources[tip_name];
        tip_spot.pickUpTip(resourceLayer);
        this.CoRe96Head[i][j].pickUpTip(tip_spot.tip);
      }
    }
  }

  dropTips96(resource_name) {
    if (this.system !== SYSTEM_HAMILTON) {
      throw new Error(
        "The 96 head actions are currently only available on the Hamilton Simulator."
      );
    }

    const tipRack = resources[resource_name];

    // Validate there are enough tips first, and that there are no tips in the head.
    for (let i = 0; i < 8; i++) {
      for (let j = 0; j < 12; j++) {
        const tip_name = tipRack.children[i * tipRack.num_items_x + j].name;
        const tip_spot = resources[tip_name];
        if (tip_spot.has_tip) {
          throw new Error(
            `There already is a tip at (${i},${j}) in ${resource_name}.`
          );
        }
        if (!this.CoRe96Head[i][j].has_tip()) {
          throw new Error(
            `There is no tip in the CoRe 96 head at (${i},${j}) in ${resource_name}.`
          );
        }
      }
    }

    // Check reachable for A1.
    let a1_name = tipRack.children[0].name;
    let a1_resource = resources[a1_name];
    const coreError = checkCoreHeadReachable(a1_resource.x);
    if (coreError !== undefined) {
      throw new Error(coreError);
    }

    // Then pick up the tips.
    for (let i = 0; i < 8; i++) {
      for (let j = 0; j < 12; j++) {
        const tip_spot = tipRack.children[i * tipRack.num_items_x + j];
        tip_spot.dropTip(resourceLayer);
        this.CoRe96Head[i][j].dropTip();
      }
    }
  }

  aspirate96(aspiration) {
    if (this.system !== SYSTEM_HAMILTON) {
      throw new Error(
        "The 96 head actions are currently only available on the Hamilton Simulator."
      );
    }

    const resource_name = aspiration.resource_name;
    const plate = resources[resource_name];

    // Check reachable for A1.
    let a1_name = plate.children[0].name;
    let a1_resource = resources[a1_name];
    const coreError = checkCoreHeadReachable(a1_resource.x);
    if (coreError !== undefined) {
      throw new Error(coreError);
    }

    // Validate there is enough liquid available, that it fits in the tips, and that each channel
    // has a tip before aspiration.
    for (let i = 0; i < plate.num_items_y; i++) {
      for (let j = 0; j < plate.num_items_x; j++) {
        const well = plate.children[i * plate.num_items_x + j];
        if (well.volume < aspiration.volume) {
          throw new Error(
            `Not enough volume in well ${well.name}: ${well.volume}uL.`
          );
        }
        if (
          this.CoRe96Head[i][j].volume + aspiration.volume >
          this.CoRe96Head[i][j].tip.maximal_volume
        ) {
          throw new Error(
            `Aspirated volume (${aspiration.volume}uL) + volume of tip (${this.CoRe96Head[i][j].volume}uL) > maximal volume of tip (${this.CoRe96Head[i][j].tip.maximal_volume}uL).`
          );
        }
        if (!this.CoRe96Head[i][j].has_tip()) {
          throw new Error(
            `CoRe 96 head channel (${i},${j}) does not have a tip.`
          );
        }
      }
    }

    for (let i = 0; i < plate.num_items_y; i++) {
      for (let j = 0; j < plate.num_items_x; j++) {
        const well = plate.children[i * plate.num_items_x + j];
        this.CoRe96Head[i][j].aspirate(aspiration.volume);
        well.aspirate(aspiration.volume);
      }
    }
  }

  dispense96(dispense) {
    if (this.system !== SYSTEM_HAMILTON) {
      throw new Error(
        "The 96 head actions are currently only available on the Hamilton Simulator."
      );
    }

    const resource_name = dispense.resource_name;
    const plate = resources[resource_name];

    // Check reachable for A1.
    let a1_name = plate.children[0].name;
    let a1_resource = resources[a1_name];
    const coreError = checkCoreHeadReachable(a1_resource.x);
    if (coreError !== undefined) {
      throw new Error(coreError);
    }

    // Validate there is enough liquid available, that it fits in the well, and that each channel
    // has a tip before dispense.
    for (let i = 0; i < plate.num_items_y; i++) {
      for (let j = 0; j < plate.num_items_x; j++) {
        const well = plate.children[i * plate.num_items_x + j];
        if (this.CoRe96Head[i][j].volume < dispense.volume) {
          throw new Error(
            `Not enough volume in head: ${this.CoRe96Head[i][j].volume}uL.`
          );
        }
        if (well.volume + dispense.volume > well.maxVolume) {
          throw new Error(
            `Dispensed volume (${dispense.volume}uL) + volume of well (${well.volume}uL) > maximal volume of well (${well.maxVolume}uL).`
          );
        }
        if (!this.CoRe96Head[i][j].has_tip()) {
          throw new Error(
            `CoRe 96 head channel (${i},${j}) does not have a tip.`
          );
        }
      }
    }

    for (let i = 0; i < plate.num_items_y; i++) {
      for (let j = 0; j < plate.num_items_x; j++) {
        const well = plate.children[i * plate.num_items_x + j];
        this.CoRe96Head[i][j].dispense(dispense.volume);
        well.dispense(dispense.volume);
      }
    }
  }
}
