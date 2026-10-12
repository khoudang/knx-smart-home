class SwitchAccessory {

    constructor(platform, knx, device){

        const Service = platform.api.hap.Service;
        const Characteristic = platform.api.hap.Characteristic;

        const accessory =
            new platform.api.platformAccessory(
                device.name,
                platform.api.hap.uuid.generate(
                    "homebridge-knx-v3:" + device.name
                )
            );

        const service =
            accessory.addService(
                Service.Switch,
                device.name
            );

        service
        .getCharacteristic(Characteristic.On)
        .onSet(async value => {

            if (device.name.startsWith("Scene-")) {

                if (!value) {
                    return;
                }

                knx.write(device.control, true);

                await new Promise(r => setTimeout(r, 500));

                knx.write(device.control, false);

                service
                    .getCharacteristic(Characteristic.On)
                    .updateValue(false);

                return;
            }

            knx.write(device.control, value);

        });

        platform.registerAccessory(accessory);

        platform.log(
            `[Switch] Loaded ${device.name}`
        );

    }

}

module.exports = SwitchAccessory;
