const { KNXPlatform } = require('./src/platform');

const PLUGIN_NAME = 'homebridge-knx-v3';
const PLATFORM_NAME = 'KNXV3';

module.exports = (api) => {
    api.registerPlatform(PLUGIN_NAME, PLATFORM_NAME, KNXPlatform);
};
