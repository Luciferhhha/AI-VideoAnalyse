"""FastAPI 应用入口。"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from app import config
from app.api.routes_panel import router as panel_router
from app.api.routes_tasks import router as tasks_router
from app.api.routes_videos import router as videos_router
from app.database.database import init_db
from app.services import log_service
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

# 控制面板页面（单文件静态页，无构建步骤）
PANEL_HTML: Path = config.BASE_DIR / "app" / "static" / "index.html"


@asynccontextmanager
async def lifespan(_: FastAPI):
    config.ensure_dirs()
    log_service.install()  # 面板「日志流程」的内存环形缓冲（幂等）
    init_db()
    logger.info("startup: runtime directories and database ready")
    yield


app = FastAPI(title=config.APP_TITLE, version=config.APP_VERSION, lifespan=lifespan)

app.include_router(videos_router)
app.include_router(tasks_router)
app.include_router(panel_router)


@app.get("/", include_in_schema=False)
def panel_index() -> FileResponse:
    """控制面板页面（/docs 仍为 FastAPI 自带 Swagger 文档）。"""
    if not PANEL_HTML.is_file():
        raise HTTPException(status_code=404, detail=f"控制面板页面缺失：{PANEL_HTML}")
    return FileResponse(PANEL_HTML, media_type="text/html; charset=utf-8")


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
