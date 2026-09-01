"""Application service for ordinary and workspace-grounded conversations."""

from __future__ import annotations

import base64
import binascii
import re
import secrets
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Generator

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.domains.agent.models import AgentArtifact, AgentRun
from app.domains.artifact.models import Artifact
from app.domains.artifact.service import ArtifactService
from app.domains.chat.consistency import message_citation_check, source_marker_check
from app.domains.chat.models import (
    ChatConversation,
    ChatMessage,
    ChatMessageEvidence,
    ChatMessageImage,
)
from app.domains.discover.models import ResearchOpportunity, ResearchPlan
from app.domains.paper.models import Paper
from app.domains.retrieval.schemas import RetrievalResponse, RetrievalResultItem
from app.domains.retrieval.service import (
    RETRIEVAL_DIAGNOSTIC_MESSAGES,
    find_chunk_record,
    semantic_search,
)
from app.domains.workspace.models import Workspace
from app.domains.workspace.service import WorkspaceService
from app.gateway.llm import LLMGateway, LLMResponse, get_llm_gateway

# A chat stream whose client disconnected mid-flight is marked failed by the
# finally-guard in _stream_complete; rows older than this threshold that are
# still "generating" are treated as dead leftovers (pre-guard rows).
STALE_GENERATING_SECONDS = 15 * 60
PLAN_REFERENCE_PATTERN = re.compile(r"(?:此|这|该)(?:个)?研究计划|当前研究计划|这个计划")
CONFIRMED_OPPORTUNITY_STATUSES = {"confirmed", "edited_confirmed"}
CONFIRMED_PLAN_STATUSES = {"confirmed", "approved"}
CITATION_REPAIR_MAX_TOKENS = 2000
CITATION_REPAIR_FALLBACK = (
    "当前回答未能通过工作区论文引用校验，因此不能把原回答中的结论视为有论文依据。"
    "请基于已列出的证据重新提问，或补充相关论文后重试。"
)
SUPPORTED_IMAGE_MIME_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
}
IMAGE_DATA_URL_PATTERN = re.compile(
    r"^data:(image/(?:jpeg|png|gif|webp));base64,([A-Za-z0-9+/=\r\n]+)$",
    re.IGNORECASE,
)


@dataclass
class WorkspaceContext:
    messages: list[dict[str, Any]]
    evidence: list[ChatMessageEvidence]
    sources: list[dict[str, Any]]
    plan: ResearchPlan | None = None
    retrieval_diagnostic_code: str | None = None
    retrieval_audit: dict[str, Any] = field(default_factory=dict)


@dataclass
class ContextSelection:
    plan: ResearchPlan | None
    artifacts: list[AgentArtifact]


@dataclass
class CitationQualityResult:
    response: LLMResponse
    audit: dict[str, Any]


@dataclass(frozen=True)
class PreparedImage:
    filename: str
    mime_type: str
    content: bytes
    data_url: str


class ChatNotFoundError(LookupError):
    pass


class ChatConflictError(RuntimeError):
    pass


class ChatInputError(ValueError):
    pass


class ChatConfigurationError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        conversation_id: str | None = None,
        assistant_message_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.conversation_id = conversation_id
        self.assistant_message_id = assistant_message_id


class ChatUpstreamError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        conversation_id: str | None = None,
        assistant_message_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.conversation_id = conversation_id
        self.assistant_message_id = assistant_message_id


class ChatRetrievalError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        conversation_id: str | None = None,
        assistant_message_id: str | None = None,
        diagnostic_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.conversation_id = conversation_id
        self.assistant_message_id = assistant_message_id
        self.diagnostic_code = diagnostic_code


def make_conversation_title(content: str) -> str:
    """Create a deterministic title without spending another LLM request."""
    normalized = re.sub(r"\s+", " ", content).strip()
    if not normalized:
        return "新对话"
    return normalized[:38] + ("…" if len(normalized) > 38 else "")


class ChatService:
    def __init__(self, db: Session, gateway: LLMGateway | None = None) -> None:
        self.db = db
        self.gateway = gateway

    def list_conversations(
        self,
        query: str | None,
        limit: int,
        offset: int,
        workspace_id: str | None = None,
        *,
        actor_id: str | None = None,
    ) -> tuple[list[ChatConversation], int]:
        stmt = select(ChatConversation).where(ChatConversation.is_deleted.is_(False))
        if actor_id is not None:
            stmt = stmt.where(ChatConversation.owner_id == actor_id)
        if workspace_id:
            WorkspaceService(self.db).get(workspace_id, actor_id=actor_id)
            stmt = stmt.where(ChatConversation.workspace_id == workspace_id)
        if query and query.strip():
            stmt = stmt.where(ChatConversation.title.ilike(f"%{query.strip()}%"))
        total = int(self.db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
        items = list(
            self.db.scalars(
                stmt.order_by(
                    ChatConversation.last_message_at.desc().nullslast(),
                    ChatConversation.updated_at.desc(),
                )
                .offset(offset)
                .limit(limit)
            )
        )
        return items, total

    def create_conversation(
        self,
        title: str | None = None,
        workspace_id: str | None = None,
        *,
        actor_id: str | None = None,
    ) -> ChatConversation:
        if workspace_id:
            WorkspaceService(self.db).get(workspace_id, actor_id=actor_id)
        conversation = ChatConversation(
            title=(title or "新对话").strip() or "新对话",
            workspace_id=workspace_id,
            owner_id=actor_id or "user",
        )
        self.db.add(conversation)
        self.db.commit()
        self.db.refresh(conversation)
        return conversation

    def get_conversation(
        self, conversation_id: str, *, actor_id: str | None = None
    ) -> ChatConversation:
        conditions = [
            ChatConversation.id == conversation_id,
            ChatConversation.is_deleted.is_(False),
        ]
        if actor_id is not None:
            conditions.append(ChatConversation.owner_id == actor_id)
        conversation = self.db.scalar(select(ChatConversation).where(*conditions))
        if conversation is None:
            raise ChatNotFoundError("conversation not found")
        return conversation

    def detail(
        self, conversation_id: str, *, actor_id: str | None = None
    ) -> tuple[ChatConversation, list[ChatMessage]]:
        conversation = self.get_conversation(conversation_id, actor_id=actor_id)
        messages = list(
            self.db.scalars(
                select(ChatMessage)
                .where(ChatMessage.conversation_id == conversation.id)
                .order_by(ChatMessage.sequence.asc())
            )
        )
        return conversation, messages

    def rename(
        self, conversation_id: str, title: str, *, actor_id: str | None = None
    ) -> ChatConversation:
        conversation = self.get_conversation(conversation_id, actor_id=actor_id)
        conversation.title = title.strip()
        self.db.commit()
        self.db.refresh(conversation)
        return conversation

    def soft_delete(self, conversation_id: str, *, actor_id: str | None = None) -> None:
        conversation = self.get_conversation(conversation_id, actor_id=actor_id)
        conversation.is_deleted = True
        self.db.commit()

    def send_new(
        self,
        content: str,
        workspace_id: str | None = None,
        research_plan_id: str | None = None,
        source_artifact_ids: list[str] | None = None,
        images: list[dict[str, str]] | None = None,
        *,
        actor_id: str | None = None,
    ) -> tuple[ChatConversation, ChatMessage, ChatMessage]:
        content = self._validate_content(content)
        prepared_images = self._prepare_images(images)
        if workspace_id:
            WorkspaceService(self.db).get(workspace_id, actor_id=actor_id)
        conversation = ChatConversation(
            title=make_conversation_title(content),
            workspace_id=workspace_id,
            owner_id=actor_id or "user",
        )
        self.db.add(conversation)
        self.db.flush()
        user_message, assistant_message = self._create_pending_messages(conversation, content)
        image_data_urls = self._persist_images(user_message, prepared_images)
        self.db.commit()
        return self._complete(
            conversation.id,
            user_message.id,
            assistant_message.id,
            [{"role": "user", "content": content}],
            research_plan_id=research_plan_id,
            source_artifact_ids=source_artifact_ids,
            image_data_urls=image_data_urls,
        )

    def send(
        self,
        conversation_id: str,
        content: str,
        workspace_id: str | None = None,
        research_plan_id: str | None = None,
        source_artifact_ids: list[str] | None = None,
        images: list[dict[str, str]] | None = None,
        *,
        actor_id: str | None = None,
    ) -> tuple[ChatConversation, ChatMessage, ChatMessage]:
        content = self._validate_content(content)
        prepared_images = self._prepare_images(images)
        conversation = self.get_conversation(conversation_id, actor_id=actor_id)
        if workspace_id is not None and workspace_id != conversation.workspace_id:
            raise ChatConflictError("conversation workspace cannot be changed")
        self._ensure_not_generating(conversation.id)
        existing = self._completed_messages(conversation.id)
        user_message, assistant_message = self._create_pending_messages(conversation, content)
        image_data_urls = self._persist_images(user_message, prepared_images)
        self.db.commit()
        context = self._build_context(existing, content)
        return self._complete(
            conversation.id,
            user_message.id,
            assistant_message.id,
            context,
            research_plan_id=research_plan_id,
            source_artifact_ids=source_artifact_ids,
            image_data_urls=image_data_urls,
        )

    def retry(
        self, conversation_id: str, assistant_message_id: str, *, actor_id: str | None = None
    ) -> tuple[ChatConversation, ChatMessage, ChatMessage]:
        conversation = self.get_conversation(conversation_id, actor_id=actor_id)
        assistant = self.db.scalar(
            select(ChatMessage).where(
                ChatMessage.id == assistant_message_id,
                ChatMessage.conversation_id == conversation.id,
            )
        )
        if assistant is None or assistant.role != "assistant":
            raise ChatNotFoundError("assistant message not found")
        if assistant.status != "failed":
            raise ChatConflictError("only failed assistant messages can be retried")
        self._ensure_not_generating(conversation.id)
        prior = list(
            self.db.scalars(
                select(ChatMessage)
                .where(
                    ChatMessage.conversation_id == conversation.id,
                    ChatMessage.sequence < assistant.sequence,
                    ChatMessage.status == "completed",
                )
                .order_by(ChatMessage.sequence.asc())
            )
        )
        user_message = next((item for item in reversed(prior) if item.role == "user"), None)
        if user_message is None:
            raise ChatConflictError("no user message is available for retry")
        assistant.status = "generating"
        assistant.error_message = None
        assistant.content = ""
        assistant.retrieval_diagnostic_code = None
        self.db.commit()
        previous_sources = list(assistant.source_manifest or [])
        plan_id = next(
            (
                str(source.get("source_id"))
                for source in previous_sources
                if source.get("source_type") == "plan" and source.get("source_id")
            ),
            None,
        )
        artifact_ids = [
            str(source.get("source_id"))
            for source in previous_sources
            if source.get("source_type") in {"report", "code_draft"}
            and source.get("source_id")
        ]
        image_data_urls = self._image_data_urls(user_message)
        return self._complete(
            conversation.id,
            user_message.id,
            assistant.id,
            self._build_context(
                [item for item in prior if item.id != user_message.id], user_message.content
            ),
            research_plan_id=plan_id,
            source_artifact_ids=artifact_ids,
            image_data_urls=image_data_urls,
        )

    def _create_pending_messages(
        self, conversation: ChatConversation, content: str
    ) -> tuple[ChatMessage, ChatMessage]:
        max_sequence = self.db.scalar(
            select(func.max(ChatMessage.sequence)).where(
                ChatMessage.conversation_id == conversation.id
            )
        )
        sequence = int(max_sequence or 0) + 1
        user_message = ChatMessage(
            conversation_id=conversation.id,
            role="user",
            content=content,
            status="completed",
            sequence=sequence,
        )
        assistant_message = ChatMessage(
            conversation_id=conversation.id,
            role="assistant",
            content="",
            status="generating",
            sequence=sequence + 1,
        )
        self.db.add_all([user_message, assistant_message])
        self.db.flush()
        return user_message, assistant_message

    @staticmethod
    def _validate_content(content: str) -> str:
        content = content.strip()
        if not content:
            raise ChatInputError("消息不能为空")
        if len(content) > settings.chat_max_input_chars:
            raise ChatInputError(f"消息长度不能超过 {settings.chat_max_input_chars} 个字符")
        return content

    def _ensure_not_generating(self, conversation_id: str) -> None:
        active = self.db.scalar(
            select(ChatMessage)
            .where(
                ChatMessage.conversation_id == conversation_id,
                ChatMessage.role == "assistant",
                ChatMessage.status == "generating",
            )
            .limit(1)
        )
        if active is None:
            return
        # P0.5-1 hardening: a stream whose client vanished mid-flight can leave
        # a row stuck in "generating" (rows created before the finally-guard
        # existed stay stuck forever). Treat rows untouched for longer than
        # STALE_GENERATING_SECONDS as dead so the conversation is not bricked.
        stale_for = None
        if active.updated_at is not None:
            updated_at = active.updated_at
            if updated_at.tzinfo is None:  # SQLite tests return naive datetimes
                updated_at = updated_at.replace(tzinfo=timezone.utc)
            stale_for = datetime.now(timezone.utc) - updated_at
        if stale_for is not None and stale_for.total_seconds() > STALE_GENERATING_SECONDS:
            self._mark_failed(active, "流式响应中断（超时自动恢复）")
            return
        raise ChatConflictError("a response is already being generated")

    def _completed_messages(self, conversation_id: str) -> list[ChatMessage]:
        return list(
            self.db.scalars(
                select(ChatMessage)
                .where(
                    ChatMessage.conversation_id == conversation_id,
                    ChatMessage.status == "completed",
                )
                .order_by(ChatMessage.sequence.desc())
                .limit(settings.chat_history_message_limit)
            )
        )[::-1]

    def _build_context(self, messages: Iterable[ChatMessage], content: str) -> list[dict[str, Any]]:
        context_reversed: list[dict[str, Any]] = []
        total_chars = 0
        # ``_completed_messages`` already returns the latest N rows in
        # chronological order. Fill the history budget from the newest turn
        # backwards, then restore chronology for the LLM. The old forward pass
        # could spend the whole budget on stale turns and omit the user's most
        # recent intent.
        for message in reversed(list(messages)):
            if message.role not in {"user", "assistant"} or message.status != "completed":
                continue
            if total_chars + len(message.content) > settings.chat_history_char_limit:
                continue
            context_reversed.append({"role": message.role, "content": message.content})
            total_chars += len(message.content)
        context = list(reversed(context_reversed))
        context.append({"role": "user", "content": content})
        return context

    def _prepare_images(
        self, images: list[dict[str, str]] | None
    ) -> list[PreparedImage]:
        """Decode and validate browser data URLs before creating a message."""

        if not images:
            return []
        if len(images) > settings.chat_max_image_count:
            raise ChatInputError(
                f"每次最多上传 {settings.chat_max_image_count} 张图片"
            )

        prepared: list[PreparedImage] = []
        for image in images:
            data_url = str(image.get("data_url") or "")
            match = IMAGE_DATA_URL_PATTERN.fullmatch(data_url)
            if match is None:
                raise ChatInputError("图片格式无效，仅支持 JPEG、PNG、GIF 或 WebP")

            mime_type = match.group(1).lower()
            declared_mime = str(image.get("mime_type") or "").lower()
            if declared_mime != mime_type:
                raise ChatInputError("图片类型与内容不一致")
            encoded = match.group(2).replace("\r", "").replace("\n", "")
            estimated_bytes = max(0, (len(encoded) * 3) // 4 - encoded.count("="))
            if estimated_bytes > settings.chat_max_image_bytes:
                raise ChatInputError(
                    f"单张图片不能超过 {settings.chat_max_image_bytes // (1024 * 1024)} MB"
                )
            try:
                content = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ChatInputError("图片内容无法读取") from exc
            if not content or len(content) > settings.chat_max_image_bytes:
                raise ChatInputError("图片内容为空或超过大小限制")
            if self._detect_image_mime(content) != mime_type:
                raise ChatInputError("图片内容与声明格式不一致")

            raw_filename = str(image.get("filename") or "image")
            filename = re.split(r"[/\\]", raw_filename)[-1].strip()[:512] or "image"
            normalized_data_url = (
                f"data:{mime_type};base64,{base64.b64encode(content).decode('ascii')}"
            )
            prepared.append(
                PreparedImage(
                    filename=filename,
                    mime_type=mime_type,
                    content=content,
                    data_url=normalized_data_url,
                )
            )
        return prepared

    def _persist_images(
        self, message: ChatMessage, images: list[PreparedImage]
    ) -> list[str]:
        """Persist chat images and return normalized data URLs for this request."""

        if not images:
            return []
        storage_root = Path(settings.app_storage_dir).resolve()
        message_dir = storage_root / "chat" / message.conversation_id / message.id
        try:
            message_dir.mkdir(parents=True, exist_ok=True)
            data_urls: list[str] = []
            for image in images:
                stored_path = message_dir / (
                    f"{secrets.token_hex(16)}{SUPPORTED_IMAGE_MIME_TYPES[image.mime_type]}"
                )
                stored_path.write_bytes(image.content)
                relative_path = str(stored_path.relative_to(storage_root)).replace("\\", "/")
                self.db.add(
                    ChatMessageImage(
                        message_id=message.id,
                        filename=image.filename,
                        mime_type=image.mime_type,
                        file_path=relative_path,
                        size_bytes=len(image.content),
                    )
                )
                data_urls.append(image.data_url)
            return data_urls
        except OSError as exc:
            raise ChatInputError("图片保存失败，请稍后重试") from exc

    def _image_data_urls(self, message: ChatMessage) -> list[str]:
        """Read persisted images for retrying the message that originally used them."""

        return [self._image_data_url(image) for image in message.images]

    def _image_data_url(self, image: ChatMessageImage) -> str:
        path = self._image_path(image)
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise ChatInputError("历史图片材料不可用，请重新上传") from exc
        if len(content) > settings.chat_max_image_bytes:
            raise ChatInputError("历史图片超过当前大小限制，请重新上传")
        if self._detect_image_mime(content) != image.mime_type:
            raise ChatInputError("历史图片格式无法验证，请重新上传")
        return f"data:{image.mime_type};base64,{base64.b64encode(content).decode('ascii')}"

    def image_file(
        self,
        conversation_id: str,
        message_id: str,
        image_id: str,
        *,
        actor_id: str | None = None,
    ) -> tuple[Path, ChatMessageImage]:
        """Resolve one image only after checking conversation ownership."""

        conversation = self.get_conversation(conversation_id, actor_id=actor_id)
        image = self.db.scalar(
            select(ChatMessageImage)
            .join(ChatMessage, ChatMessage.id == ChatMessageImage.message_id)
            .where(
                ChatMessageImage.id == image_id,
                ChatMessageImage.message_id == message_id,
                ChatMessage.conversation_id == conversation.id,
            )
        )
        if image is None:
            raise ChatNotFoundError("chat image not found")
        path = self._image_path(image)
        if not path.is_file():
            raise ChatNotFoundError("chat image file not found")
        return path, image

    def _image_path(self, image: ChatMessageImage) -> Path:
        storage_root = Path(settings.app_storage_dir).resolve()
        path = (storage_root / image.file_path).resolve()
        try:
            path.relative_to(storage_root)
        except ValueError as exc:
            raise ChatInputError("图片存储路径无效") from exc
        return path

    @staticmethod
    def _detect_image_mime(content: bytes) -> str | None:
        if content.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if content.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if content.startswith((b"GIF87a", b"GIF89a")):
            return "image/gif"
        if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
            return "image/webp"
        return None

    @staticmethod
    def _vision_model(gateway: Any, image_data_urls: list[str]) -> str | None:
        if not image_data_urls:
            return None
        model = str(getattr(gateway, "vision_model", "") or "").strip()
        if not model:
            raise ChatConfigurationError("DeepSeek 视觉模型未配置")
        return model

    @staticmethod
    def _attach_images(
        context: list[dict[str, Any]], image_data_urls: list[str]
    ) -> list[dict[str, Any]]:
        if not image_data_urls:
            return context
        if not context or context[-1].get("role") != "user":
            raise ChatInputError("图片必须附加在当前用户消息上")
        text = context[-1].get("content")
        if not isinstance(text, str):
            raise ChatInputError("当前消息内容格式无效")
        content: list[dict[str, Any]] = [{"type": "text", "text": text}]
        content.extend(
            {
                "type": "image_url",
                "image_url": {"url": data_url, "detail": "auto"},
            }
            for data_url in image_data_urls
        )
        return [*context[:-1], {"role": "user", "content": content}]

    def _complete(
        self,
        conversation_id: str,
        user_id: str,
        assistant_id: str,
        context: list[dict[str, Any]],
        *,
        research_plan_id: str | None = None,
        source_artifact_ids: list[str] | None = None,
        image_data_urls: list[str] | None = None,
    ) -> tuple[ChatConversation, ChatMessage, ChatMessage]:
        assistant = self.db.get(ChatMessage, assistant_id)
        conversation = self.db.get(ChatConversation, conversation_id)
        user_message = self.db.get(ChatMessage, user_id)
        image_data_urls = image_data_urls or []
        try:
            if (research_plan_id or source_artifact_ids) and not conversation.workspace_id:
                raise ChatInputError("研究计划和补充来源必须绑定当前工作区")
            evidence: list[ChatMessageEvidence] = []
            sources: list[dict[str, Any]] = []
            if conversation.workspace_id:
                workspace_context = self._workspace_context(
                    conversation,
                    user_message.content,
                    context,
                    assistant.id,
                    research_plan_id=research_plan_id,
                    source_artifact_ids=source_artifact_ids,
                )
                context, evidence, sources = (
                    workspace_context.messages,
                    workspace_context.evidence,
                    workspace_context.sources,
                )
                assistant.source_manifest = sources
                assistant.retrieval_diagnostic_code = workspace_context.retrieval_diagnostic_code
                assistant.retrieval_audit = workspace_context.retrieval_audit
                if not evidence and not sources and not image_data_urls:
                    return self._complete_without_evidence(
                        conversation,
                        user_message,
                        assistant,
                    )
            gateway = self.gateway or get_llm_gateway()
            if not getattr(gateway, "api_key", None):
                raise ChatConfigurationError("DeepSeek API key is not configured")
            vision_model = self._vision_model(gateway, image_data_urls)
            if image_data_urls:
                context = self._attach_images(context, image_data_urls)
            generation_started = time.perf_counter()
            prompt_chars = self._prompt_char_count(context)
            response = gateway.chat_completion(
                context,
                temperature=0.2,
                disable_thinking=True,
                **({"model_override": vision_model} if vision_model else {}),
            )
            quality = self._apply_citation_quality_gate(
                gateway,
                context,
                response,
                evidence,
                sources,
                model_override=vision_model,
            )
            response = quality.response
            assistant.citation_quality = quality.audit
            self._set_generation_observability(
                assistant,
                prompt_chars=prompt_chars,
                response_chars=len(response.content),
                first_token_latency_ms=None,
                completion_latency_ms=(time.perf_counter() - generation_started) * 1000,
            )
        except ChatConfigurationError as exc:
            self._mark_failed(assistant, str(exc))
            raise ChatConfigurationError(
                str(exc), conversation_id=conversation_id, assistant_message_id=assistant_id
            ) from exc
        except ChatInputError:
            self._mark_failed(assistant, "上下文选择无效")
            raise
        except ChatRetrievalError:
            raise
        except Exception as exc:
            safe_error = _safe_error_message(exc)
            self._mark_failed(assistant, safe_error)
            raise ChatUpstreamError(
                "DeepSeek request failed",
                conversation_id=conversation_id,
                assistant_message_id=assistant_id,
            ) from exc

        assistant.status = "completed"
        assistant.content = response.content
        assistant.error_message = None
        assistant.model = response.model
        assistant.prompt_tokens = response.prompt_tokens
        assistant.completion_tokens = response.completion_tokens
        assistant.total_tokens = response.total_tokens
        assistant.grounding_status = (
            "grounded"
            if evidence
            else "plan_context"
            if sources
            else "not_requested"
        ) if conversation.workspace_id else "not_requested"
        if conversation.workspace_id:
            assistant.citations = evidence
        conversation.model = response.model
        conversation.last_message_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(conversation)
        self.db.refresh(assistant)
        return conversation, user_message, assistant


    # ------------------------------------------------------------ streaming (P0.5-1)
    def stream_send_new(
        self,
        content: str,
        workspace_id: str | None = None,
        research_plan_id: str | None = None,
        source_artifact_ids: list[str] | None = None,
        images: list[dict[str, str]] | None = None,
        *,
        actor_id: str | None = None,
    ) -> Generator[dict[str, Any], None, None]:
        """Stream a new-conversation message. Yields event dicts (see _stream_complete)."""
        content = self._validate_content(content)
        prepared_images = self._prepare_images(images)
        if workspace_id:
            WorkspaceService(self.db).get(workspace_id, actor_id=actor_id)
        conversation = ChatConversation(
            title=make_conversation_title(content),
            workspace_id=workspace_id,
            owner_id=actor_id or "user",
        )
        self.db.add(conversation)
        self.db.flush()
        user_message, assistant_message = self._create_pending_messages(conversation, content)
        image_data_urls = self._persist_images(user_message, prepared_images)
        self.db.commit()
        yield from self._stream_complete(
            conversation.id, user_message.id, assistant_message.id,
            [{"role": "user", "content": content}],
            research_plan_id=research_plan_id,
            source_artifact_ids=source_artifact_ids,
            image_data_urls=image_data_urls,
        )

    def stream_send(
        self,
        conversation_id: str,
        content: str,
        workspace_id: str | None = None,
        research_plan_id: str | None = None,
        source_artifact_ids: list[str] | None = None,
        images: list[dict[str, str]] | None = None,
        *,
        actor_id: str | None = None,
    ) -> Generator[dict[str, Any], None, None]:
        """Stream a message into an existing conversation. Yields event dicts."""
        content = self._validate_content(content)
        prepared_images = self._prepare_images(images)
        conversation = self.get_conversation(conversation_id, actor_id=actor_id)
        if workspace_id is not None and workspace_id != conversation.workspace_id:
            raise ChatConflictError("conversation workspace cannot be changed")
        self._ensure_not_generating(conversation.id)
        existing = self._completed_messages(conversation.id)
        user_message, assistant_message = self._create_pending_messages(conversation, content)
        image_data_urls = self._persist_images(user_message, prepared_images)
        self.db.commit()
        context = self._build_context(existing, content)
        yield from self._stream_complete(
            conversation.id,
            user_message.id,
            assistant_message.id,
            context,
            research_plan_id=research_plan_id,
            source_artifact_ids=source_artifact_ids,
            image_data_urls=image_data_urls,
        )

    def _stream_complete(
        self,
        conversation_id: str,
        user_id: str,
        assistant_id: str,
        context: list[dict[str, Any]],
        *,
        research_plan_id: str | None = None,
        source_artifact_ids: list[str] | None = None,
        image_data_urls: list[str] | None = None,
    ) -> Generator[dict[str, Any], None, None]:
        """Stream LLM tokens for a message, persisting on completion.

        Yields ``{"type": ...}`` events: ``start`` (ids), ``evidence`` (retrieval
        citations), ``token`` (one delta per event), ``done`` (final content), or
        ``error``. Structured-format callers keep using ``_complete``.
        """
        assistant = self.db.get(ChatMessage, assistant_id)
        conversation = self.db.get(ChatConversation, conversation_id)
        user_message = self.db.get(ChatMessage, user_id)
        image_data_urls = image_data_urls or []
        evidence: list[ChatMessageEvidence] = []
        sources: list[dict[str, Any]] = []
        try:
            if (research_plan_id or source_artifact_ids) and not conversation.workspace_id:
                raise ChatInputError("研究计划和补充来源必须绑定当前工作区")
            if conversation.workspace_id:
                workspace_context = self._workspace_context(
                    conversation,
                    user_message.content,
                    context,
                    assistant.id,
                    research_plan_id=research_plan_id,
                    source_artifact_ids=source_artifact_ids,
                )
                context, evidence, sources = (
                    workspace_context.messages,
                    workspace_context.evidence,
                    workspace_context.sources,
                )
                assistant.source_manifest = sources
                assistant.retrieval_diagnostic_code = workspace_context.retrieval_diagnostic_code
                assistant.retrieval_audit = workspace_context.retrieval_audit
            gateway = self.gateway or get_llm_gateway()
            if not getattr(gateway, "api_key", None):
                raise ChatConfigurationError("DeepSeek API key is not configured")
        except ChatConfigurationError as exc:
            self._mark_failed(assistant, str(exc))
            yield {"type": "error", "message": str(exc)}
            return
        except ChatRetrievalError as exc:
            # Streaming responses have already started by the time the
            # generator body runs, so the central HTTP exception handler
            # cannot turn this into a structured error response. Emit the
            # documented SSE error event instead of closing the connection
            # while the browser still shows the optimistic message as
            # "generating".
            yield {
                "type": "error",
                "message": str(exc),
                "diagnostic_code": exc.diagnostic_code,
            }
            return
        except ChatInputError as exc:
            self._mark_failed(assistant, "上下文选择无效")
            yield {"type": "error", "message": str(exc)}
            return

        if conversation.workspace_id and not evidence and not sources and not image_data_urls:
            self._complete_without_evidence(conversation, user_message, assistant)
            yield {"type": "done", "content": assistant.content}
            return

        yield {"type": "start", "conversation_id": conversation_id, "assistant_message_id": assistant_id}
        interrupted = True
        try:
            if conversation.workspace_id and evidence:
                yield {
                    "type": "evidence",
                    "citations": [
                        {
                            "id": ev.id,
                            "paper_title": ev.paper_title,
                            "section": ev.section,
                            "excerpt": ev.excerpt,
                            "rank": ev.rank,
                        }
                        for ev in evidence
                    ],
                }
            chunks: list[str] = []
            generation_started = time.perf_counter()
            first_token_latency_ms: float | None = None
            vision_model = self._vision_model(gateway, image_data_urls)
            if image_data_urls:
                context = self._attach_images(context, image_data_urls)
            prompt_chars = self._prompt_char_count(context)
            try:
                for delta in gateway.stream_chat_completion(
                    context,
                    temperature=0.2,
                    disable_thinking=True,
                    **({"model_override": vision_model} if vision_model else {}),
                ):
                    if delta and first_token_latency_ms is None:
                        first_token_latency_ms = (time.perf_counter() - generation_started) * 1000
                    chunks.append(delta)
                    yield {"type": "token", "content": delta}
            except Exception as exc:
                safe_error = _safe_error_message(exc)
                self._mark_failed(assistant, safe_error)
                yield {"type": "error", "message": safe_error}
                return

            content = "".join(chunks)
            quality = self._apply_citation_quality_gate(
                gateway,
                context,
                LLMResponse(
                    content=content,
                    model=getattr(gateway, "model", "stream"),
                    prompt_tokens=0,
                    completion_tokens=0,
                    total_tokens=0,
                ),
                evidence,
                sources,
                model_override=vision_model,
            )
            content = quality.response.content
            assistant.citation_quality = quality.audit
            self._set_generation_observability(
                assistant,
                prompt_chars=prompt_chars,
                response_chars=len(content),
                first_token_latency_ms=first_token_latency_ms,
                completion_latency_ms=(time.perf_counter() - generation_started) * 1000,
            )
            assistant.status = "completed"
            assistant.content = content
            assistant.error_message = None
            assistant.grounding_status = (
                "grounded" if evidence else "plan_context" if sources else "not_requested"
            ) if conversation.workspace_id else "not_requested"
            if conversation.workspace_id:
                assistant.citations = evidence
            conversation.last_message_at = datetime.now(timezone.utc)
            self.db.commit()
            self.db.refresh(assistant)
            interrupted = False
            yield {"type": "done", "content": content}
        finally:
            # P0.5-1 hardening: a client disconnect mid-stream raises
            # GeneratorExit at a yield point; without this guard the row stays
            # "generating" forever and blocks the whole conversation.
            if interrupted and assistant.status == "generating":
                try:
                    self._mark_failed(assistant, "流式响应中断：客户端提前断开")
                except Exception:
                    self.db.rollback()

    def _apply_citation_quality_gate(
        self,
        gateway: Any,
        context: list[dict[str, Any]],
        response: LLMResponse,
        evidence: list[ChatMessageEvidence],
        sources: list[dict[str, Any]],
        *,
        model_override: str | None = None,
    ) -> CitationQualityResult:
        """Validate one answer and allow at most one bounded marker repair.

        This gate only repairs citation/source boundaries. It never retrieves
        new material and never invents a citation. If the repaired answer is
        still mechanically invalid, return a deterministic evidence-insufficiency
        message instead of persisting an answer with a broken provenance chain.
        """

        grounded = bool(evidence)
        citation_check, source_check = self._quality_checks(response.content, evidence, sources, grounded)
        audit: dict[str, Any] = {
            "status": "not_needed" if not evidence and not sources else "passed",
            "attempts": 0,
            "initial_broken_citations": citation_check.broken,
            "initial_grounded_without_citations": citation_check.grounded_without_citations,
            "initial_broken_sources": source_check.broken,
            "final_broken_citations": citation_check.broken,
            "final_grounded_without_citations": citation_check.grounded_without_citations,
            "final_broken_sources": source_check.broken,
            "fallback": False,
        }
        if citation_check.ok and not citation_check.grounded_without_citations and source_check.ok:
            return CitationQualityResult(response=response, audit=audit)

        audit["attempts"] = 1
        repair_response: LLMResponse | None = None
        try:
            repair_response = gateway.chat_completion(
                self._citation_repair_context(
                    context,
                    response.content,
                    evidence,
                    sources,
                    citation_check,
                    source_check,
                ),
                temperature=0.0,
                max_tokens=CITATION_REPAIR_MAX_TOKENS,
                disable_thinking=True,
                **({"model_override": model_override} if model_override else {}),
            )
        except Exception:
            repair_response = None

        final_content = repair_response.content if repair_response is not None else ""
        final_citation_check, final_source_check = self._quality_checks(
            final_content, evidence, sources, grounded
        )
        audit["final_broken_citations"] = final_citation_check.broken
        audit["final_grounded_without_citations"] = (
            final_citation_check.grounded_without_citations
        )
        audit["final_broken_sources"] = final_source_check.broken
        if (
            repair_response is not None
            and final_citation_check.ok
            and not final_citation_check.grounded_without_citations
            and final_source_check.ok
        ):
            audit["status"] = "repaired"
            return CitationQualityResult(
                response=replace(
                    response,
                    content=final_content,
                    prompt_tokens=response.prompt_tokens + repair_response.prompt_tokens,
                    completion_tokens=response.completion_tokens + repair_response.completion_tokens,
                    total_tokens=response.total_tokens + repair_response.total_tokens,
                ),
                audit=audit,
            )

        audit["status"] = "rejected"
        audit["fallback"] = True
        return CitationQualityResult(
            response=replace(response, content=CITATION_REPAIR_FALLBACK),
            audit=audit,
        )

    @staticmethod
    def _quality_checks(
        content: str,
        evidence: list[ChatMessageEvidence],
        sources: list[dict[str, Any]],
        grounded: bool,
    ) -> tuple[Any, Any]:
        citation_check = message_citation_check(
            content,
            [item.rank for item in evidence],
            grounded=grounded,
        )
        source_check = source_marker_check(
            content,
            {
                f"[{source.get('marker')}]"
                for source in sources
                if source.get("marker")
            },
        )
        return citation_check, source_check

    @staticmethod
    def _citation_repair_context(
        context: list[dict[str, Any]],
        content: str,
        evidence: list[ChatMessageEvidence],
        sources: list[dict[str, Any]],
        citation_check: Any,
        source_check: Any,
    ) -> list[dict[str, Any]]:
        allowed_papers = ", ".join(f"[E{item.rank}]" for item in evidence) or "无"
        allowed_sources = ", ".join(
            f"[{source['marker']}]"
            for source in sources
            if source.get("marker") and source.get("source_type") != "paper"
        ) or "无"
        instruction = (
            "你是工作区问答的引用质量修复器。只修复回答中的引用边界，不增加事实、"
            "不改写成新的研究结论。论文引用只能使用已存在的标记："
            f"{allowed_papers}。计划/报告/代码来源只能使用：{allowed_sources}。"
            "不要伪造标记；如果现有证据不能支持某个结论，明确写出证据不足。"
            "请输出完整的修复后回答，不要解释修复过程。"
        )
        diagnostics = (
            f"原回答的失效论文引用：{citation_check.broken or '无'}；"
            f"有工作区论文但未标注引用：{citation_check.grounded_without_citations}；"
            f"原回答的失效来源标记：{source_check.broken or '无'}。"
        )
        return [
            {"role": "system", "content": instruction},
            *context,
            {"role": "assistant", "content": content},
            {"role": "user", "content": f"{diagnostics}\n请输出修复后的完整回答。"},
        ]

    def _workspace_context(
        self,
        conversation: ChatConversation,
        question: str,
        context: list[dict[str, Any]],
        assistant_id: str,
        *,
        research_plan_id: str | None = None,
        source_artifact_ids: list[str] | None = None,
    ) -> WorkspaceContext:
        workspace = WorkspaceService(self.db).get(conversation.workspace_id)
        selection = self._resolve_context_selection(
            workspace.id,
            question,
            research_plan_id=research_plan_id,
            source_artifact_ids=source_artifact_ids or [],
        )
        result = semantic_search(
            workspace_id=workspace.id,
            query=question,
            top_k=settings.chat_rag_top_k,
            use_reranker=True,
            diversify_by_paper=True,
        )
        if result.status == "failed":
            diagnostic_code = result.diagnostic_code or "unknown"
            diagnostic_message = RETRIEVAL_DIAGNOSTIC_MESSAGES.get(
                diagnostic_code,
                RETRIEVAL_DIAGNOSTIC_MESSAGES["unknown"],
            )
            assistant = self.db.get(ChatMessage, assistant_id)
            assistant.retrieval_audit = self._retrieval_audit(result, [])
            self._mark_failed(
                assistant,
                diagnostic_message,
                grounding_status="retrieval_failed",
                retrieval_diagnostic_code=diagnostic_code,
            )
            raise ChatRetrievalError(
                diagnostic_message,
                conversation_id=conversation.id,
                assistant_message_id=assistant_id,
                diagnostic_code=diagnostic_code,
            )

        evidence = self._materialize_evidence(
            workspace,
            assistant_id,
            result.items,
        )
        sources = self._source_manifest(selection.plan, evidence, selection.artifacts)
        profile = self._clip_context_text(
            self._workspace_profile(workspace),
            settings.chat_workspace_profile_max_context_chars,
        )
        plan_text = self._clip_context_text(
            self._plan_prompt(selection.plan) if selection.plan else "未选择研究计划。",
            settings.chat_plan_max_context_chars,
        )
        artifact_text = self._artifact_prompt(selection.artifacts)
        evidence_text = self._evidence_prompt(evidence) if evidence else "本次没有检索到可用的工作区论文证据。"
        system_message = {
            "role": "system",
            "content": (
                "你是 GapMind 的课题空间研究助手。请清楚区分来源，不要把研究计划、报告或代码草案伪装成论文证据。"
                "只有工作区论文可以使用 [E1]、[E2] 形式引用；计划使用 [P1]，已确认报告使用 [D1]，代码草案使用 [C1]。"
                "不要复制报告或代码草案内部的 [E] 标记。可以用对话历史理解代词，但历史中的助手回答不能替代来源。"
                "如果论文证据不足，请明确说明不足；如果研究计划没有定义损失函数，也必须明确说计划中未提供，"
                "不得根据常见做法或论文内容猜一个损失函数。代码草案始终标为未运行验证。\n\n"
                f"工作区资料（非论文来源）：\n{profile}\n\n"
                f"已确认研究计划 [P1]：\n{plan_text}\n\n"
                f"可选报告/代码草案：\n{artifact_text}\n\n"
                f"工作区论文检索证据（仅此部分可作为论文事实依据）：\n{evidence_text}"
            ),
        }
        return WorkspaceContext(
            self._budget_prompt_messages(system_message, context),
            evidence,
            sources,
            selection.plan,
            result.diagnostic_code,
            self._retrieval_audit(result, evidence),
        )

    @staticmethod
    def _retrieval_audit(
        result: RetrievalResponse,
        evidence: list[ChatMessageEvidence],
    ) -> dict[str, Any]:
        filters = result.filters_applied or {}
        if result.diagnostic_code == "reranker_degraded":
            reranker_status = "degraded"
        elif filters.get("reranker_applied"):
            reranker_status = "applied"
        elif filters.get("reranker_enabled"):
            reranker_status = "enabled_no_rerank"
        else:
            reranker_status = "unknown"
        return {
            "request_id": result.request_id,
            "status": result.status,
            "diagnostic_code": result.diagnostic_code,
            "recall_count": filters.get("recall_count"),
            "returned_chunk_count": result.total,
            "final_paper_count": len({item.paper_id for item in evidence if item.paper_id}),
            "latency_ms": result.latency_ms,
            "reranker_status": reranker_status,
        }

    def context_options(
        self, workspace_id: str, *, actor_id: str | None = None
    ) -> dict[str, list[dict[str, str]]]:
        """List only current-workspace plan/report/code context candidates."""

        workspace = WorkspaceService(self.db).get(workspace_id, actor_id=actor_id)
        plans = self._eligible_plans(workspace.id)
        plan_ids = {plan.id for plan in plans}
        plan_options = [
            {
                "id": plan.id,
                "title": self._postgres_safe_text(plan.title),
                "research_question": self._postgres_safe_text(plan.research_question),
                "status": "confirmed",
            }
            for plan in plans
        ]
        artifacts: list[dict[str, str]] = []
        rows = self.db.execute(
            select(AgentArtifact, AgentRun)
            .join(AgentRun, AgentRun.id == AgentArtifact.run_id)
            .where(
                AgentRun.workspace_id == workspace.id,
                AgentRun.status == "succeeded",
                AgentArtifact.is_deleted.is_(False),
            )
            .order_by(AgentArtifact.updated_at.desc())
        ).all()
        for artifact, run in rows:
            plan_id = str(
                (run.result or {}).get("research_plan_id")
                or (run.input_payload or {}).get("research_plan_id")
                or (artifact.metadata_payload or {}).get("research_plan_id")
                or ""
            )
            if plan_id not in plan_ids:
                continue
            if artifact.artifact_type == "deep_research_report":
                if artifact.validation_status != "confirmed":
                    continue
                source_type = "report"
                label = "已确认报告"
            elif artifact.artifact_type == "code":
                source_type = "code_draft"
                label = "代码草案，未运行验证"
            else:
                continue
            artifacts.append(
                {
                    "id": artifact.id,
                    "plan_id": plan_id,
                    "source_type": source_type,
                    "label": label,
                    "title": self._postgres_safe_text(artifact.filename),
                    "status": artifact.validation_status,
                }
            )
        return {"plans": plan_options, "artifacts": artifacts}

    def _resolve_context_selection(
        self,
        workspace_id: str,
        question: str,
        *,
        research_plan_id: str | None,
        source_artifact_ids: list[str],
    ) -> ContextSelection:
        plans = self._eligible_plans(workspace_id)
        plan_by_id = {plan.id: plan for plan in plans}
        plan: ResearchPlan | None = None
        if research_plan_id:
            requested = self.db.get(ResearchPlan, research_plan_id)
            if requested is None or requested.workspace_id != workspace_id:
                raise ChatInputError("研究计划必须属于当前工作区")
            plan = plan_by_id.get(requested.id)
            if plan is None:
                raise ChatInputError("只能绑定当前工作区内已确认的研究计划")
        elif PLAN_REFERENCE_PATTERN.search(question or ""):
            if len(plans) > 1:
                titles = "；".join(self._postgres_safe_text(item.title) for item in plans[:3])
                raise ChatInputError(f"当前工作区有多个已确认研究计划，请先选择：{titles}")
            if len(plans) == 1:
                plan = plans[0]

        artifacts: list[AgentArtifact] = []
        if source_artifact_ids:
            if plan is None:
                raise ChatInputError("选择报告或代码草案前必须先选择研究计划")
            rows = self.db.execute(
                select(AgentArtifact, AgentRun)
                .join(AgentRun, AgentRun.id == AgentArtifact.run_id)
                .where(
                    AgentArtifact.id.in_(source_artifact_ids),
                    AgentArtifact.is_deleted.is_(False),
                )
            ).all()
            row_by_id = {artifact.id: (artifact, run) for artifact, run in rows}
            if len(row_by_id) != len(set(source_artifact_ids)):
                raise ChatInputError("补充来源不存在或不可用")
            for artifact_id in source_artifact_ids:
                artifact, run = row_by_id[artifact_id]
                linked_plan_id = str(
                    (run.result or {}).get("research_plan_id")
                    or (run.input_payload or {}).get("research_plan_id")
                    or (artifact.metadata_payload or {}).get("research_plan_id")
                    or ""
                )
                if run.workspace_id != workspace_id or run.status != "succeeded" or linked_plan_id != plan.id:
                    raise ChatInputError("补充来源必须属于当前工作区和所选研究计划")
                if artifact.artifact_type == "deep_research_report":
                    if artifact.validation_status != "confirmed":
                        raise ChatInputError("只能引用已确认的深度研究报告")
                elif artifact.artifact_type != "code":
                    raise ChatInputError("该产物不能作为助手上下文来源")
                artifacts.append(artifact)
        return ContextSelection(plan, artifacts)

    def _eligible_plans(self, workspace_id: str) -> list[ResearchPlan]:
        plans = list(
            self.db.scalars(
                select(ResearchPlan)
                .where(ResearchPlan.workspace_id == workspace_id)
                .order_by(ResearchPlan.updated_at.desc())
            )
        )
        return [plan for plan in plans if self._is_confirmed_plan(plan, workspace_id)]

    def _is_confirmed_plan(self, plan: ResearchPlan, workspace_id: str) -> bool:
        if plan.workspace_id != workspace_id:
            return False
        if plan.status in CONFIRMED_PLAN_STATUSES:
            return True
        if plan.opportunity_id:
            opportunity = self.db.get(ResearchOpportunity, plan.opportunity_id)
            if (
                opportunity is not None
                and not opportunity.is_deleted
                and opportunity.workspace_id == workspace_id
                and opportunity.status in CONFIRMED_OPPORTUNITY_STATUSES
            ):
                return True
        if plan.agent_run_id:
            run = self.db.get(AgentRun, plan.agent_run_id)
            if (
                run is not None
                and run.workspace_id == workspace_id
                and run.status == "succeeded"
                and run.agent_type == "research_plan"
            ):
                return True
        return False

    def _source_manifest(
        self,
        plan: ResearchPlan | None,
        evidence: list[ChatMessageEvidence],
        artifacts: list[AgentArtifact],
    ) -> list[dict[str, Any]]:
        sources: list[dict[str, Any]] = []
        if plan is not None:
            sources.append(
                {
                    "marker": "P1",
                    "source_type": "plan",
                    "source_id": plan.id,
                    "label": "已确认研究计划",
                    "title": self._postgres_safe_text(plan.title),
                    "status": "confirmed",
                    "detail": "计划来源，不是论文证据",
                }
            )
        for item in evidence:
            sources.append(
                {
                    "marker": f"E{item.rank}",
                    "source_type": "paper",
                    "source_id": item.paper_id or item.id,
                    "label": "工作区论文",
                    "title": self._postgres_safe_text(item.paper_title) or "未命名论文",
                    "status": "indexed",
                    "detail": self._postgres_safe_text(item.section) or None,
                }
            )
        report_index = 1
        code_index = 1
        for artifact in artifacts:
            if artifact.artifact_type == "deep_research_report":
                marker = f"D{report_index}"
                report_index += 1
                label = "已确认报告"
                status = "confirmed"
                source_type = "report"
                detail = "人工确认的 AI 报告，不是论文原文"
            else:
                marker = f"C{code_index}"
                code_index += 1
                label = "代码草案，未运行验证"
                status = "not_run"
                source_type = "code_draft"
                detail = "AI 候选产物，不代表已执行或复现"
            sources.append(
                {
                    "marker": marker,
                    "source_type": source_type,
                    "source_id": artifact.id,
                    "label": label,
                    "title": self._postgres_safe_text(artifact.filename),
                    "status": status,
                    "detail": detail,
                }
            )
        return sources

    def _plan_prompt(self, plan: ResearchPlan) -> str:
        fields = [
            f"标题：{self._postgres_safe_text(plan.title)}",
            f"研究问题：{self._postgres_safe_text(plan.research_question)}",
            f"假设：{self._postgres_safe_text(plan.hypothesis)}",
            f"范围与前提：{self._postgres_safe_text(plan.scope_and_assumptions)}",
            f"数据集：{self._postgres_safe_text('；'.join(plan.datasets or []))}",
            f"基线：{self._postgres_safe_text('；'.join(plan.baselines or []))}",
            f"指标：{self._postgres_safe_text('；'.join(plan.metrics or []))}",
            f"验证步骤：{self._postgres_safe_text('；'.join(plan.validation_steps or []))}",
            f"预期支持结果：{self._postgres_safe_text(plan.expected_supporting_result)}",
            f"证伪标准：{self._postgres_safe_text(plan.falsification_criteria)}",
            f"风险：{self._postgres_safe_text('；'.join(plan.risks or []))}",
            f"资源约束：{self._postgres_safe_text(plan.resource_constraints)}",
            "独立损失函数：研究计划未提供此字段；除非补充来源明确写出，否则必须回答‘未指定’。",
        ]
        return "\n".join(fields)

    def _artifact_prompt(self, artifacts: list[AgentArtifact]) -> str:
        if not artifacts:
            return "未选择补充报告或代码草案。"
        blocks: list[str] = []
        report_index = 1
        code_index = 1
        total_chars = 0
        budget = settings.chat_artifact_max_context_chars
        for artifact in artifacts:
            if artifact.artifact_type == "deep_research_report":
                marker = f"[D{report_index}] 已确认报告"
                report_index += 1
            else:
                marker = f"[C{code_index}] 代码草案，未运行验证"
                code_index += 1
            content = self._postgres_safe_text(artifact.content)
            # Do not let an embedded report citation accidentally become a
            # citation to this chat's paper evidence ranks.
            content = re.sub(r"\[E\d+\]", "[来源内部标记]", content)
            remaining = budget - total_chars
            if remaining <= 0:
                break
            block = f"{marker} 文件：{self._postgres_safe_text(artifact.filename)}\n{content}"
            blocks.append(block[:remaining])
            total_chars += min(len(block), remaining)
        return "\n\n".join(blocks)

    def _materialize_evidence(
        self,
        workspace: Workspace,
        assistant_id: str,
        items: list[RetrievalResultItem],
    ) -> list[ChatMessageEvidence]:
        evidence: list[ChatMessageEvidence] = []
        for rank, item in enumerate(items, 1):
            if not item.paper_id:
                continue
            paper = self.db.get(Paper, item.paper_id)
            if paper is None or paper.is_deleted or paper.workspace_id != workspace.id:
                continue
            chunk = (
                find_chunk_record(workspace.id, paper.id, item.chunk_id) if item.chunk_id else None
            )
            artifact_id = chunk.source_artifact_id if chunk else item.artifact_id
            artifact = self.db.get(Artifact, artifact_id) if artifact_id else None
            if artifact is not None and (
                artifact.is_deleted or artifact.workspace_id != workspace.id
            ):
                artifact_id = None
            excerpt = self._postgres_safe_text(item.text).strip()[:4000]
            if not excerpt:
                continue
            evidence.append(
                ChatMessageEvidence(
                    message_id=assistant_id,
                    workspace_id=workspace.id,
                    paper_id=paper.id,
                    artifact_id=artifact_id,
                    chunk_id=self._postgres_safe_text(item.chunk_id) or None,
                    paper_title=self._postgres_safe_text(paper.title) or None,
                    section=self._postgres_safe_text(item.section) or None,
                    excerpt=excerpt,
                    start_char=chunk.start_char if chunk else None,
                    end_char=chunk.end_char if chunk else None,
                    score=float(item.score),
                    rank=rank,
                )
            )
        return evidence

    @staticmethod
    def _prompt_char_count(context: list[dict[str, Any]]) -> int:
        """Count prompt characters without persisting the prompt contents."""

        total = 0
        for message in context:
            content = message.get("content")
            if isinstance(content, str):
                total += len(content)
            elif isinstance(content, list):
                total += sum(
                    len(str(part.get("text") or ""))
                    for part in content
                    if isinstance(part, dict)
                )
        return total

    @staticmethod
    def _set_generation_observability(
        assistant: ChatMessage,
        *,
        prompt_chars: int,
        response_chars: int,
        first_token_latency_ms: float | None,
        completion_latency_ms: float,
    ) -> None:
        assistant.prompt_chars = max(0, prompt_chars)
        assistant.response_chars = max(0, response_chars)
        assistant.first_token_latency_ms = (
            round(max(0.0, first_token_latency_ms), 2)
            if first_token_latency_ms is not None
            else None
        )
        assistant.completion_latency_ms = round(max(0.0, completion_latency_ms), 2)

    @staticmethod
    def _postgres_safe_text(value: object | None) -> str:
        """Remove NUL bytes that PostgreSQL rejects in text and JSON values.

        PDF extraction can preserve embedded ``0x00`` characters inside an
        otherwise valid chunk.  They are not meaningful prose, so removing
        them at the persistence boundary keeps evidence offsets and source
        artifacts auditable while preventing an entire Agent run from failing.
        """

        return str(value or "").replace("\x00", "")

    @staticmethod
    def _workspace_profile(workspace: Workspace) -> str:
        fields = [f"名称：{workspace.name}"]
        if workspace.topic:
            fields.append(f"主题：{workspace.topic}")
        if workspace.keywords:
            fields.append(f"关键词：{', '.join(workspace.keywords)}")
        if workspace.goals:
            fields.append(f"目标：{workspace.goals}")
        if workspace.constraints:
            fields.append(f"约束：{workspace.constraints}")
        return "\n".join(fields)

    @staticmethod
    def _evidence_prompt(evidence: list[ChatMessageEvidence]) -> str:
        blocks: list[str] = []
        total_chars = 0
        for item in evidence:
            block = (
                f"[E{item.rank}] 论文：{item.paper_title or '未命名论文'}；"
                f"章节：{item.section or '未知'}；相关度：{item.score:.3f}\n"
                f"{item.excerpt}"
            )
            remaining = settings.chat_rag_max_context_chars - total_chars
            if remaining <= 0:
                break
            blocks.append(block[:remaining])
            total_chars += min(len(block), remaining)
        return "\n\n".join(blocks)

    @staticmethod
    def _clip_context_text(text: str, max_chars: int) -> str:
        """Bound a named context source without dropping its provenance header."""

        if max_chars <= 0:
            return ""
        if len(text) <= max_chars:
            return text
        suffix = "\n[该来源已按上下文预算截断]"
        return text[: max(0, max_chars - len(suffix))] + suffix

    @staticmethod
    def _budget_prompt_messages(
        system_message: dict[str, Any],
        context: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Apply one total prompt budget while preserving the current question.

        Source blocks are independently capped before this function. The
        remaining budget belongs to dialogue history and is filled from newest
        to oldest, preserving chronological order in the final prompt.
        """

        if not context:
            return [system_message]
        current_message = context[-1]
        history = context[:-1]
        remaining = max(
            0,
            settings.chat_prompt_max_context_chars
            - len(system_message["content"])
            - len(current_message["content"]),
        )
        selected_reversed: list[dict[str, Any]] = []
        for message in reversed(history):
            if len(message["content"]) > remaining:
                continue
            selected_reversed.append(message)
            remaining -= len(message["content"])
        return [system_message, *reversed(selected_reversed), current_message]

    def _complete_without_evidence(
        self,
        conversation: ChatConversation,
        user_message: ChatMessage,
        assistant: ChatMessage,
    ) -> tuple[ChatConversation, ChatMessage, ChatMessage]:
        assistant.status = "completed"
        assistant.content = (
            "当前工作区没有检索到可用于回答这个问题的已索引论文内容。"
            "请先确认论文 PDF 已完成解析和向量化，或者换一个更具体的问题后重试。"
        )
        assistant.error_message = None
        assistant.grounding_status = "no_evidence"
        conversation.last_message_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(conversation)
        self.db.refresh(assistant)
        return conversation, user_message, assistant

    def evidence_context(
        self,
        conversation_id: str,
        message_id: str,
        evidence_id: str,
        *,
        actor_id: str | None = None,
    ) -> tuple[ChatMessageEvidence, Artifact | None, str | None, str | None]:
        conversation = self.get_conversation(conversation_id, actor_id=actor_id)
        evidence = self.db.scalar(
            select(ChatMessageEvidence)
            .join(ChatMessage, ChatMessage.id == ChatMessageEvidence.message_id)
            .where(
                ChatMessageEvidence.id == evidence_id,
                ChatMessageEvidence.message_id == message_id,
                ChatMessageEvidence.workspace_id == conversation.workspace_id,
                ChatMessage.conversation_id == conversation.id,
            )
        )
        if evidence is None:
            raise ChatNotFoundError("chat evidence not found")
        if not evidence.artifact_id:
            return evidence, None, None, "证据没有可定位的原文文件"
        artifact = self.db.get(Artifact, evidence.artifact_id)
        if (
            artifact is None
            or artifact.is_deleted
            or artifact.workspace_id != evidence.workspace_id
        ):
            return evidence, None, None, "证据原文文件已不可用"
        path = ArtifactService(self.db).resolve_abs_path(artifact)
        if not path.exists():
            return evidence, artifact, None, "证据原文文件不存在"
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return evidence, artifact, None, "证据原文读取失败"
        return evidence, artifact, content, None

    def _mark_failed(
        self,
        assistant: ChatMessage,
        error_message: str,
        *,
        grounding_status: str | None = None,
        retrieval_diagnostic_code: str | None = None,
    ) -> None:
        assistant.status = "failed"
        assistant.error_message = error_message[:1000]
        assistant.retrieval_diagnostic_code = retrieval_diagnostic_code
        if grounding_status:
            assistant.grounding_status = grounding_status
        conversation = self.db.get(ChatConversation, assistant.conversation_id)
        conversation.last_message_at = datetime.now(timezone.utc)
        self.db.commit()


def _safe_error_message(exc: Exception) -> str:
    raw = f"{type(exc).__name__}: {exc}"
    raw = re.sub(r"(?i)(api[-_ ]?key|authorization|bearer)\s*[:=]?\s*\S+", r"\1: [redacted]", raw)
    raw = re.sub(r"(?i)sk-[a-z0-9_-]+", "[redacted]", raw)
    return raw[:1000]
