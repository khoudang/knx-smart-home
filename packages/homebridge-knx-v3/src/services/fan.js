class FanAccessory {

    constructor(platform, knx, device){

        this.platform = platform;
        this.knx = knx;
        this.device = device;

        platform.log(
            "[fan] Loaded " + device.name
        );

    }

}


module.exports = FanAccessory;
