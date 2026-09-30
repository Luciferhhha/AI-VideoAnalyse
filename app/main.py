"""FastAPI 应用入口。"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app import config
from app.api.routes_tasks import router as tasks_router
from app.api.routes_videos import router as videos_router
from app.database.database import init_db
from app.services.video_service import (
    FFmpegNotFoundError,
    InvalidVideoError,
    VideoNotFoundError,
    VideoServiceError,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    config.ensure_dirs()
    init_db()
    logger.info("startup: runtime directories and database ready")
    yield


app = FastAPI(title="视频智能分析平台", version="0.1.0", lifespan=lifespan)

app.include_router(videos_router)
app.include_router(tasks_router)


@app.exception_handler(VideoServiceError)
def handle_video_service_error(_request, exc: VideoServiceError) -> JSONResponse:
    """视频服务错误统一响应（envelope 与 HTTPException 一致：{"detail": ...}）。

    - VideoNotFoundError → 404
    - InvalidVideoError（非视频/解析失败） → 400
    - FFmpegNotFoundError（服务端环境缺失） → 500
    """
    if isinstance(exc, VideoNotFoundError):
        status_code = 404
    elif isinstance(exc, InvalidVideoError):
        status_code = 400
    else:
        status_code = 500
    return JSONResponse(status_code=status_code, content={"detail": str(exc)})


@app.get("/health")
def health() -> dict:
    """健康检查接口。"""
    return {"status": "ok"}
