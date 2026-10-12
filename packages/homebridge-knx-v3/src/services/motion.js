class MotionAccessory {

    constructor(platform, knx, device){

        this.platform = platform;
        this.knx = knx;
        this.device = device;

        platform.log(
            "[motion] Loaded " + device.name
        );

    }

}


module.exports = MotionAccessory;
