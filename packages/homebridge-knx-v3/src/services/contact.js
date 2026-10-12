class ContactAccessory {

    constructor(platform, knx, device){

        this.platform = platform;
        this.knx = knx;
        this.device = device;

        platform.log(
            "[contact] Loaded " + device.name
        );

    }

}


module.exports = ContactAccessory;
