class SensorAccessory {

    constructor(platform, knx, device){

        this.platform = platform;
        this.knx = knx;
        this.device = device;

        platform.log(
            "[sensor] Loaded " + device.name
        );

    }

}


module.exports = SensorAccessory;
