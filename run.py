"""启动入口：python run.py 启动 FastAPI 服务。"""

import uvicorn

from app.main import app

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
