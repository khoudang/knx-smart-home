"""Verify API initialization with temporary state, no credentials, and no hardware."""
import os
from pathlib import Path
import runpy
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    previous = os.environ.copy()
    try:
        with tempfile.TemporaryDirectory(prefix='knx-api-smoke-') as directory:
            os.environ.update({
                'DATABASE_PATH': str(Path(directory) / 'registry.db'),
                'KNX_DRY_RUN': 'true',
                'MQTT_DRY_RUN': 'true',
                'DEEPSEEK_API_KEY': '',
                'OPENAI_API_KEY': '',
                'KNX_BRIDGE_URL': '',
                'KNX_BRIDGE_TOKEN': '',
                'API_HOST': '127.0.0.1',
            })
            namespace = runpy.run_path(str(ROOT / 'ai-agent/api.py'))
            orchestrator = namespace['orchestrator']
            try:
                client = namespace['app'].test_client()
                health = client.get('/health')
                if health.status_code != 200 or health.get_json().get('status') != 'ok':
                    raise RuntimeError('Health endpoint did not initialize correctly')
                missing = client.post('/chat', json={})
                if missing.status_code != 400:
                    raise RuntimeError('Chat input validation did not reject missing message')
                disabled = client.post('/api/set_device_state', json={})
                if disabled.status_code != 410:
                    raise RuntimeError('Legacy direct-control endpoint was not disabled')
            finally:
                orchestrator.conn.close()
        print('API smoke check passed: health, validation, and disabled direct-control endpoint.')
    finally:
        os.environ.clear()
        os.environ.update(previous)


if __name__ == '__main__':
    main()
