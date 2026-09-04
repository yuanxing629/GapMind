"""将当前 FastAPI OpenAPI schema 导出为 JSON 文件。

这是前端 TypeScript 类型的事实来源。从 backend 根目录运行：

    .venv/Scripts/python.exe scripts/export_openapi.py

输出位于 ``backend/openapi.json``，由 ``openapi-typescript`` 消费
（见 ``frontend/package.json`` 中的 ``gen:api-types`` 脚本）。

这里刻意不导入任何会接触数据库的内容：``app.openapi()`` 只遍历已注册路由，
因此不需要测试中的 SQLite engine fixture。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# 脚本通过 ``python scripts/export_openapi.py``（不使用 ``-m``）直接启动时，
# 确保可以导入 ``app``。
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.main import app  # noqa: E402


def main() -> int:
    schema = app.openapi()
    target = Path(__file__).resolve().parent.parent / "openapi.json"
    target.write_text(json.dumps(schema, indent=2, ensure_ascii=False), encoding="utf-8")
    route_count = len(schema.get("paths", {}))
    print(f"Wrote {target} ({route_count} routes, {target.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
