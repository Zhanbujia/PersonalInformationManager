"""本地磁盘文件存储。

三个刻意的决定：

1. **落盘文件名用 uuid，不用原始文件名**。原始名可能带 `../`、重名、非法字符；
   只把原始名存数据库，下载时再回填到 `Content-Disposition`，两个问题一起解决。
2. **相对路径按 personal-memory-backend/ 解析**，不按进程 cwd 解析。worker 和 API 的 cwd 可能不同，
   按 cwd 解析会让同一个存储目录在两处指向不同位置——这种 bug 很难查。
3. **边写边算 sha256**，既做去重键，又不用为了算哈希把文件再整读一遍。
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from uuid import UUID

from app.config import settings

_BACKEND_ROOT = Path(__file__).resolve().parents[2]  # personal-memory-backend/


class FileStore:
    def __init__(self, root: str | Path | None = None) -> None:
        base = Path(root) if root is not None else Path(settings.file_storage_dir)
        if not base.is_absolute():
            base = _BACKEND_ROOT / base
        self.root = base.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def new_key(self, user_id: UUID | str, ext: str) -> str:
        ext = ext if ext.startswith(".") else f".{ext}"
        return f"{user_id}/{uuid.uuid4().hex}{ext.lower()}"

    def abs_path(self, key: str) -> Path:
        """把存储键解析成绝对路径，并挡住 `../` 穿越。"""
        p = (self.root / key).resolve()
        if p != self.root and self.root not in p.parents:
            raise ValueError(f"非法的存储路径：{key!r}")
        return p

    def prepare(self, key: str) -> Path:
        p = self.abs_path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def remove(self, key: str) -> None:
        try:
            self.abs_path(key).unlink(missing_ok=True)
        except (OSError, ValueError):
            pass

    async def save_upload(self, key: str, upload, *, max_bytes: int) -> tuple[int, str]:
        """流式写入上传文件（duck-typed 的 `await upload.read(n)`）。

        返回 (字节数, sha256 十六进制)。超过 `max_bytes` 会抛 ValueError 并清理半成品。
        """
        path = self.prepare(key)
        h = hashlib.sha256()
        size = 0
        try:
            with path.open("wb") as f:
                while True:
                    chunk = await upload.read(1 << 20)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValueError("文件超过大小上限")
                    h.update(chunk)
                    f.write(chunk)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return size, h.hexdigest()
