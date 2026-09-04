# Chunk JSONL 不再导出到此目录。
#
# 规范副本是不可变的 `chunk_index` Artifact，由 `papers.chunk_index_artifact_id` 引用，
# 并存储在 `APP_STORAGE_DIR` 下。
# Retrieval 和 indexing 通过数据库解析该 Artifact，使 workspace 所有权和存储路径保持一致。
#
#
# 保留此 README 仅用于说明已废弃的路径。新部署不需要 `backend/data/chunks/`。
#
