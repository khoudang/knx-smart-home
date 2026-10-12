const { getDoc } = require('./skills/sheet_reader');

async function initPermissionsTab() {
    try {
        const doc = await getDoc();
        let sheet = doc.sheetsByTitle['permissions'];

        if (!sheet) {
            console.log("Tab 'permissions' chưa tồn tại. Đang tạo mới...");
            sheet = await doc.addSheet({ title: 'permissions', headerValues: ['User ID', 'Name', 'Role', 'Status'] });
            console.log("Đã tạo tab 'permissions' thành công!");
        } else {
            console.log("Tab 'permissions' đã tồn tại. Đang cập nhật header nếu thiếu...");
            await sheet.loadHeaderRow().catch(async () => {
                await sheet.setHeaderRow(['User ID', 'Name', 'Role', 'Status']);
            });
        }

        const rows = await sheet.getRows();
        const existingIds = rows.map(r => {
            try { return r.get('User ID'); } catch(e) { return null; }
        }).filter(Boolean);

        const adminsToEnsure = (process.env.OPENCLAW_ADMIN_USERS || '').split(',').map(id => id.trim()).filter(Boolean).map(id => ({ id, name: 'Admin' }));

        for (const admin of adminsToEnsure) {
            if (!existingIds.includes(admin.id)) {
                console.log(`Đang thêm quyền Admin cho ${admin.id}...`);
                await sheet.addRow({
                    'User ID': admin.id,
                    'Name': admin.name,
                    'Role': 'admin',
                    'Status': 'active'
                });
            } else {
                console.log(`Quyền Admin cho ${admin.id} đã tồn tại trong Sheet.`);
            }
        }

        console.log("✅ Hoàn tất khởi tạo tab permissions!");
    } catch (err) {
        console.error("❌ Lỗi khi khởi tạo tab permissions:", err.message);
    }
}

initPermissionsTab();
