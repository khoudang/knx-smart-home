-- Khởi tạo database schema cho Smart Home Platform v2

CREATE TABLE IF NOT EXISTS devices (
    id VARCHAR(50) PRIMARY KEY,
    room VARCHAR(50) NOT NULL,
    type VARCHAR(50) NOT NULL,
    group_address VARCHAR(50),
    capabilities JSONB NOT NULL DEFAULT '[]'::jsonb,
    is_simulated BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_devices_room ON devices(room);

CREATE TABLE IF NOT EXISTS device_states (
    device_id VARCHAR(50) PRIMARY KEY REFERENCES devices(id) ON DELETE CASCADE,
    state JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS device_history (
    id SERIAL PRIMARY KEY,
    device_id VARCHAR(50) NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    actor VARCHAR(50) NOT NULL, -- e.g., 'user', 'ai', 'scene', 'physical'
    action VARCHAR(50) NOT NULL,
    params JSONB,
    result VARCHAR(50),
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Index quan trọng cho device_history để query dạng time-series nhanh
-- và tương thích dễ dàng nếu chuyển sang TimescaleDB (hypertable) sau này
CREATE INDEX idx_device_history_device_id_time ON device_history(device_id, timestamp DESC);
CREATE INDEX idx_device_history_time ON device_history(timestamp DESC);

CREATE TABLE IF NOT EXISTS scenes (
    id VARCHAR(50) PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    description TEXT,
    trigger_type VARCHAR(50) NOT NULL, -- 'manual', 'time', 'condition'
    trigger_config JSONB,
    created_by VARCHAR(50),
    status VARCHAR(20) DEFAULT 'draft', -- 'draft', 'approved'
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_scenes_status ON scenes(status);

CREATE TABLE IF NOT EXISTS scene_actions (
    id SERIAL PRIMARY KEY,
    scene_id VARCHAR(50) NOT NULL REFERENCES scenes(id) ON DELETE CASCADE,
    device_id VARCHAR(50) NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    action_type VARCHAR(50) NOT NULL,
    params JSONB,
    delay_ms INTEGER DEFAULT 0,
    action_order INTEGER DEFAULT 0
);

CREATE INDEX idx_scene_actions_scene_id ON scene_actions(scene_id);

CREATE TABLE IF NOT EXISTS users_permissions (
    user_id VARCHAR(100) PRIMARY KEY,
    platform VARCHAR(50) NOT NULL, -- 'zalo', 'telegram', 'web'
    role VARCHAR(20) DEFAULT 'user', -- 'admin', 'user'
    allowed_rooms JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
