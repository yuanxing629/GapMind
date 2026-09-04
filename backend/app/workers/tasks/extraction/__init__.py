"""抽取流水线子模块。

本模块从 ``workers/tasks/extract_knowledge.py`` 拆出，使每项职责都能单独进行单元测试，
并让 worker 文件收缩为薄的 Celery 入口。参见 ``docs/architecture-refactor-plan-2026-08-04.md``
中的 ``S7``。
"""
