
const knx = require("knx");
class KNXClient {
    constructor(config){
        this.host = config.host;
        this.port = config.port || 3671;
        this.connection = null;
        this.connected = false;
        this.connecting = false;
    }
    async connect(){
        if(this.connected){
            return;
        }
        if(this.connecting){
            return;
        }
        this.connecting = true;
        console.log(
            `[KNX] Connecting ${this.host}:${this.port}`
        );
        this.connection = knx.Connection({
            suppress_ack_ldatareq: true,
            ipAddr: this.host,
            ipPort: this.port,
            handlers: {
                connected: ()=>{
                    this.connected = true;
                    this.connecting = false;
                    console.log(
                        "[KNX] Connected"
                    );
                },
                disconnected: ()=>{
                    this.connected = false;
                    this.connecting = false;
                    console.log(
                        "[KNX] Disconnected"
                    );
                },
                error:(err)=>{
                    console.log(
                        "[KNX] Error",
                        err
                    );
                }
            }
        });
        return new Promise((resolve)=>{
            const timer = setInterval(()=>{
                if(this.connected){
                    clearInterval(timer);
                    resolve();
                }
            },100);
        });
    }
    write(address, value, dpt){
        if(!this.connection){
            console.log("[KNX] Not connected");
            return;
        }
        console.log("[KNX WRITE]", address, value, typeof value, dpt || "(auto)");
        if(dpt){
            this.connection.write(
                address,
                value,
                "DPT" + dpt
            );
        } else if (typeof value === "boolean") {
            this.connection.write(
                address,
                value,
                "DPT1.001"
            );
        } else {
            this.connection.write(
                address,
                value,
                "DPT5.001"
            );
        }
    }
    subscribe(address, callback){
        if(!this.connection){
            return;
        }
        this.connection.on(
            "event",
            evt=>{
                if(evt.destination === address){
                    callback(
                        evt.data
                    );
                }
            }
        );
    }
}
module.exports = KNXClient;
