const KNXClient = require("./core/knx-client");
const AccessoryManager = require("./accessory-manager");


class KNXPlatform {

    constructor(log, config, api) {

        this.log = log;
        this.config = config;
        this.api = api;

        this.knx = new KNXClient(config.knx);

        this.devices = config.devices || [];

        this.loadedAccessories = [];

        this.accessoryManager = new AccessoryManager(this);


        api.on(
            "didFinishLaunching",
            async ()=>{

                this.log("[KNX] Starting client...");

                await this.knx.connect();

                this.log("[KNX] Ready");

            }
        );


    }


    accessories(callback){

        this.accessoryManager.load();

        callback(
            this.loadedAccessories
        );

    }


    registerAccessory(accessory){

        this.loadedAccessories.push({

            name: accessory.displayName,

            getServices(){

                return accessory.services;

            }

        });

    }


}


module.exports = { KNXPlatform };
