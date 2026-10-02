"""视频上传与查询 API（阶段四）。

- POST /videos：类型检查 → 大小检查 → 保存 → ffprobe 元数据 → 建 Video 记录。
- GET /videos：列表（关键帧数/章节数/最近任务状态，控制面板用）。
- GET /videos/{video_id}：查询视频信息；不存在返回 404。
"""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app import config
from app.database.database import get_db
from app.database.repository import VideoRepository
from app.schemas.video import VideoCreateResponse, VideoDetailResponse, VideoListResponse
from app.services.panel_service import list_video_summaries
from app.services.video_service import VideoServiceError, get_video_info

router = APIRouter(prefix="/videos", tags=["videos"])

_CHUNK_SIZE = 1024 * 1024  # 1 MB


def _detail_response(video) -> VideoDetailResponse:
    return VideoDetailResponse(
        id=video.id,
        filename=video.filename,
        duration=video.duration,
        width=video.width,
        height=video.height,
        fps=video.fps,
        created_at=video.created_at,
    )


@router.post("", response_model=VideoCreateResponse, status_code=201)
async def upload_video(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> VideoCreateResponse:
    # 1) 文件类型检查（按扩展名）
    original_name = Path(file.filename or "").name  # 防路径穿越
    suffix = Path(original_name).suffix.lower()
    if not original_name or suffix not in config.ALLOWED_VIDEO_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"不支持的文件类型：{original_name or '(空文件名)'}，"
                f"允许：{sorted(config.ALLOWED_VIDEO_EXTENSIONS)}"
            ),
        )

    # 2) 保存 + 大小检查（分块边写边计数，超限/异常即删除残留文件）
    config.UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    dest = config.UPLOADS_DIR / f"{uuid.uuid4().hex[:8]}_{original_name}"
    limit_bytes = config.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    total = 0
    try:
        with dest.open("wb") as out:
            while True:
                chunk = await file.read(_CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                if total > limit_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=f"文件超过大小限制（{config.MAX_UPLOAD_SIZE_MB} MB）",
                    )
                out.write(chunk)
        if total == 0:
            raise HTTPException(status_code=400, detail="上传的文件为空")
    except BaseException:
        dest.unlink(missing_ok=True)
        raise

    # 3) ffprobe 解析元数据（非视频内容 → VideoServiceError → 全局 handler 映射状态码）
    try:
        info = get_video_info(dest)
    except VideoServiceError:
        dest.unlink(missing_ok=True)
        raise

    # 4) 创建 Video 记录（失败则清理已保存文件）
    try:
        video = VideoRepository(db).create(
            filename=original_name,
            filepath=str(dest),
            duration=info["duration"],
            width=info["width"],
            height=info["height"],
            fps=info["fps"],
        )
    except BaseException:
        dest.unlink(missing_ok=True)
        raise

    # 5) 返回 {id, filename}
    return VideoCreateResponse(id=video.id, filename=video.filename)


@router.get("", response_model=list[VideoListResponse])
def list_videos(db: Session = Depends(get_db)) -> list[VideoListResponse]:
    """视频列表（新→旧）：含关键帧数、章节数与最近任务状态，控制面板用。"""
    return [VideoListResponse(**row) for row in list_video_summaries(db)]


@router.get("/{video_id}", response_model=VideoDetailResponse)
def get_video(video_id: int, db: Session = Depends(get_db)) -> VideoDetailResponse:
    video = VideoRepository(db).get(video_id)
    if video is None:
        raise HTTPException(status_code=404, detail=f"视频不存在：id={video_id}")
    return _detail_response(video)
