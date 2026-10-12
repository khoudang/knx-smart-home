class CoverAccessory {

    constructor(platform, knx, device){

        this.platform = platform;
        this.knx = knx;
        this.device = device;

        platform.log(
            "[cover] Loaded " + device.name
        );

    }

}


module.exports = CoverAccessory;
