const fs = require('fs');
const path = require('path');

const SCENES_FILE = path.join(__dirname, '..', 'config', 'scenes.json');

function normalizeName(name) {
    return name.toLowerCase().replace(/ /g, '_').replace(/[^a-z0-9_]/g, '');
}

async function main() {
    if (process.argv.length < 4) {
        console.error("Cách dùng: node create_scene.js <scene_name> <steps_json_array>");
        process.exit(1);
    }

    let sceneName = process.argv[2];
    let stepsJson = process.argv[3];

    sceneName = normalizeName(sceneName);

    let steps;
    try {
        steps = JSON.parse(stepsJson);
        if (!Array.isArray(steps)) {
            throw new Error("steps phải là một mảng JSON");
        }
    } catch (e) {
        console.error("Lỗi parse JSON:", e.message);
        console.log("Cấu trúc yêu cầu: [{\"device_id\":\"den_tron\", \"command\":\"ON\"}, ...]");
        process.exit(1);
    }

    let scenes = {};
    if (fs.existsSync(SCENES_FILE)) {
        try {
            scenes = JSON.parse(fs.readFileSync(SCENES_FILE, 'utf8'));
        } catch (e) {
            console.error("Lỗi đọc scenes.json cũ:", e.message);
        }
    }

    scenes[sceneName] = steps;

    try {
        fs.writeFileSync(SCENES_FILE, JSON.stringify(scenes, null, 2));
        console.log(`✅ Đã tạo/cập nhật kịch bản '${sceneName}' thành công với ${steps.length} hành động.`);
    } catch (e) {
        console.error("Lỗi ghi file:", e.message);
        process.exit(1);
    }
}

main();
