class HumidityAccessory {

    constructor(platform, knx, device){

        this.platform = platform;
        this.knx = knx;
        this.device = device;

        platform.log(
            "[humidity] Loaded " + device.name
        );

    }

}


module.exports = HumidityAccessory;
