class DimmerAccessory {
    constructor(platform, knx, device){
        const Service = platform.api.hap.Service;
        const Characteristic = platform.api.hap.Characteristic;
        this.accessory =
            new platform.api.platformAccessory(
                device.name,
                platform.api.hap.uuid.generate(
                    "homebridge-knx-v3:" + device.name
                )
            );
        const service =
            this.accessory.addService(
                Service.Lightbulb,
                device.name
            );
        let currentBrightness = 100;
        let isOn = false;

        service
        .getCharacteristic(Characteristic.Brightness)
        .onSet(v=>{
            currentBrightness = v;
            console.log("[HOMEKIT] Brightness =", v);
            if(isOn){
                knx.write(device.level, currentBrightness);
            }
        });

        service
        .getCharacteristic(Characteristic.On)
        .onSet(async v=>{
            console.log("[HOMEKIT] On =", v);
            isOn = v;
            if(v){
                if(device.power){
                    knx.write(device.power.control,true);
                    await new Promise(r=>setTimeout(r,500));
                }
                knx.write(device.control,true);
                await new Promise(r=>setTimeout(r,500));
                knx.write(device.level,currentBrightness);
            }else{
                knx.write(device.control,false);
                if(device.power){
                    await new Promise(r=>setTimeout(r,500));
                    knx.write(device.power.control,false);
                }
            }
        });

        // ---- Color Temperature (Tunable White, DPT 7.600 = Kelvin) ----
        if(device.cct){
            const MIN_KELVIN = 3000;
            const MAX_KELVIN = 6000;
            const MIN_MIRED = Math.round(1000000 / MAX_KELVIN);
            const MAX_MIRED = Math.round(1000000 / MIN_KELVIN);

            const ctChar = service.getCharacteristic(Characteristic.ColorTemperature);
            ctChar.setProps({
                minValue: MIN_MIRED,
                maxValue: MAX_MIRED
            });

            ctChar.onSet(v=>{
                const mired = Math.max(MIN_MIRED, Math.min(MAX_MIRED, v));
                let kelvin = Math.round(1000000 / mired);
                kelvin = MAX_KELVIN + MIN_KELVIN - kelvin;
                kelvin = Math.max(MIN_KELVIN, Math.min(MAX_KELVIN, kelvin));
                console.log("[HOMEKIT] ColorTemperature =", v, "mired ->", kelvin, "K");
                if(isOn){
                    knx.write(device.cct, kelvin, "7.600");
                }
            });
        }

        platform.registerAccessory(this.accessory);
        platform.log(`[Dimmer] ${device.name}`);
    }
}
module.exports = DimmerAccessory;
