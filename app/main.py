"""FastAPI 应用入口。"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    config.ensure_dirs()
    logger.info("startup: runtime directories ready")
    yield


app = FastAPI(title="视频智能分析平台", version="0.1.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    """健康检查接口。"""
    return {"status": "ok"}
