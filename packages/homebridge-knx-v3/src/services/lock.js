class LockAccessory {

    constructor(platform, knx, device){

        this.platform = platform;
        this.knx = knx;
        this.device = device;

        platform.log(
            "[lock] Loaded " + device.name
        );

    }

}


module.exports = LockAccessory;
