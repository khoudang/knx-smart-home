-- Script chèn scene mẫu để test Scene Engine

-- 1. Chèn Scene 'test_light_on' với trạng thái 'approved'
INSERT INTO scenes (id, name, description, trigger_type, status)
VALUES (
    'test_light_on',
    'Bật đèn chính phòng khách',
    'Scene test dùng để verify logic của Scene Engine Phase 3',
    'manual',
    'approved'
)
ON CONFLICT (id) DO UPDATE SET status = 'approved';

-- 2. Chèn Action cho scene trên (Bật lr_light_main)
INSERT INTO scene_actions (scene_id, device_id, action_type, params, action_order)
VALUES (
    'test_light_on',
    'lr_light_main',
    'set',
    '{"state": "ON"}'::jsonb,
    1
);

-- (Tùy chọn) 3. Chèn thêm một action test pulse cho đèn ngủ (nếu muốn thử)
INSERT INTO scenes (id, name, description, trigger_type, status)
VALUES (
    'test_pulse_br',
    'Nháy đèn ngủ 2 giây',
    'Scene test tính năng pulse',
    'manual',
    'approved'
)
ON CONFLICT (id) DO UPDATE SET status = 'approved';

INSERT INTO scene_actions (scene_id, device_id, action_type, params, action_order)
VALUES (
    'test_pulse_br',
    'br_light_pulse',
    'pulse',
    '{"on_duration_ms": 2000}'::jsonb,
    1
);
