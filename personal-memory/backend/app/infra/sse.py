"""SSE 编码。

协议有两条容易写错的地方：
1. 事件之间必须用**空行**（`\\n\\n`）分隔，前端按这个切。
2. `data:` 里不能有裸换行，所以一律 `json.dumps`，它会把换行转义成 `\\n`。
"""

from __future__ import annotations

import json
from typing import Any


def sse(event: str, data: dict[str, Any] | str) -> str:
    payload = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {payload}\n\n"


SSE_HEADERS = {
    # 关掉 Nginx 的缓冲，否则 token 会攒一批才推给浏览器
    "X-Accel-Buffering": "no",
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
}
