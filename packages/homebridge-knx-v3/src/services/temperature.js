class TemperatureAccessory {

    constructor(platform, knx, device){

        const Service = platform.api.hap.Service;
        const Characteristic = platform.api.hap.Characteristic;

        this.service =
            new Service.TemperatureSensor(
                device.name
            );


        this.service
            .getCharacteristic(
                Characteristic.CurrentTemperature
            )
            .updateValue(0);


        platform.log(
            `[temperature] Loaded ${device.name}`
        );

    }


    getServices(){

        return [
            this.service
        ];

    }

}


module.exports = TemperatureAccessory;
