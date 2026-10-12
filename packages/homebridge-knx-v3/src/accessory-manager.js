class AccessoryManager {

    constructor(platform){

        this.platform = platform;
        this.knx = platform.knx;


        this.types = {

            switch: require("./services/switch"),
            dimmer: require("./services/dimmer"),
            sensor: require("./services/sensor"),
            lock: require("./services/lock"),
            motion: require("./services/motion"),
            contact: require("./services/contact"),
            temperature: require("./services/temperature"),
            humidity: require("./services/humidity"),
            fan: require("./services/fan"),
            cover: require("./services/cover")

        };

    }


    async load(){

        const devices = this.platform.devices || [];


        this.platform.log(
            `[AccessoryManager] Loading ${devices.length} devices`
        );


        for(const device of devices){

            const Accessory = this.types[device.type];


            if(!Accessory){

                this.platform.log(
                    `[AccessoryManager] Unknown type ${device.type}`
                );

                continue;

            }


            const acc = new Accessory(
                this.platform,
                this.knx,
                device
            );

            this.platform.log(
            );

            if(device.status && acc.subscribe){
                acc.subscribe();
            }

        }


    }


}


module.exports = AccessoryManager;
