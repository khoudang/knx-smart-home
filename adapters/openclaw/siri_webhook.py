from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel
import subprocess
import os

app = FastAPI(title="Siri KNX Webhook")

# Secret key để bảo mật, Siri gửi mã này lên để xác thực
SECRET_TOKEN = os.getenv("SIRI_SECRET_TOKEN", "")

class KNXCommand(BaseModel):
    device_id: str
    ga: str
    command: str
    value: str = ""

@app.post("/siri/control")
async def siri_control(cmd: KNXCommand, authorization: str = Header(None)):
    if not SECRET_TOKEN or authorization != f"Bearer {SECRET_TOKEN}":
        raise HTTPException(status_code=403, detail="Sai mã bảo mật Siri!")

    middleware_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "middleware.py")
    if not os.path.exists(middleware_path):
        raise HTTPException(status_code=500, detail="Không tìm thấy middleware.py")

    try:
        import sys
        # Gọi thẳng vào middleware.py của hệ thống
        args = [sys.executable, middleware_path, cmd.device_id, cmd.ga, cmd.command]
        if cmd.value:
            args.append(cmd.value)

        result = subprocess.run(args, capture_output=True, text=True, timeout=10)

        if result.returncode != 0:
            error_msg = result.stderr.strip() or result.stdout.strip()
            raise HTTPException(status_code=500, detail=f"Lỗi chạy KNX: {error_msg}")

        return {"status": "success", "message": f"Siri đã gửi lệnh {cmd.command} thành công!"}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
