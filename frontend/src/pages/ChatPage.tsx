import { useCallback, useEffect, useRef, useState } from "react";
import { Button, Drawer, Grid, Modal, Popover, Result, Spin, message } from "antd";
import { DatabaseOutlined, InfoCircleOutlined, LockOutlined } from "@ant-design/icons";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import chatApi, { type ChatContextArtifactOption, type ChatContextPlanOption, type ChatConversation, type ChatImageInput, type ChatMessage, type ChatMessageImage } from "../api/chat";
import workspaceApi from "../api/workspace";
import agentApi, { type AgentRunDetail } from "../api/agent";
import type { Workspace } from "../api/types/workspace";
import { chatConversationPath, chatErrorMessage, retrievalDiagnosticCopy, sortChatMessages, type ChatRetrievalDiagnosticCode } from "../state/chatState";
import { isIndependentWorkspaceName } from "../state/independentMode";
import ChatComposer, { type ChatMode } from "../components/chat/ChatComposer";
import ChatEmptyState from "../components/chat/ChatEmptyState";
import ChatHeader from "../components/chat/ChatHeader";
import ChatHistory from "../components/chat/ChatHistory";
import ChatMessages from "../components/chat/ChatMessages";

const localMessage = (conversationId: string, role: "user" | "assistant", content: string, sequence: number, images: ChatMessageImage[] = []): ChatMessage => ({ id: `local-${role}-${Date.now()}-${sequence}`, conversation_id: conversationId, role, content, status: role === "assistant" ? "generating" : "completed", error_message: null, sequence, model: null, prompt_tokens: null, completion_tokens: null, total_tokens: null, prompt_chars: null, response_chars: null, first_token_latency_ms: null, completion_latency_ms: null, grounding_status: "not_requested", citations: [], images, created_at: new Date().toISOString(), updated_at: new Date().toISOString() });

const localImages = (messageId: string, images: ChatImageInput[]): ChatMessageImage[] => images.map((image, index) => ({ id: `local-image-${Date.now()}-${index}`, message_id: messageId, filename: image.filename, mime_type: image.mime_type, size_bytes: 0, data_url: image.data_url, created_at: new Date().toISOString(), updated_at: new Date().toISOString() }));

const MODE_VALUES: ChatMode[] = ["chat", "research_plan", "code_generation", "analyze", "write", "respond"];

const CHAT_DATA_POLICY = "根据当前部署配置，问题和系统选取的论文片段可能发送至 Embedding、LLM 或其他远程服务。请仅使用已授权、已脱敏的资料；结果会标注证据来源、AI 生成和未确认状态。";

function ChatNoticeBar({ independent, activeWorkspaceId, activeWorkspaceName, hasConversation }: { independent: boolean; activeWorkspaceId?: string; activeWorkspaceName?: string; hasConversation: boolean }) {
  const scopeNotice = independent
    ? { label: "独立模式 · 不检索", message: "当前为独立模式：仅使用本次提供的材料，不会检索课题空间论文或知识库。" }
    : activeWorkspaceId
      ? { label: `${activeWorkspaceName ?? "课题空间"} · 已索引论文`, message: `正在使用“${activeWorkspaceName ?? "课题空间"}”中已索引的论文回答；计划、报告与代码草案会单独标注来源。` }
      : hasConversation
        ? { label: "普通对话 · 不检索", message: "当前是普通 AI 对话，不会自动检索论文或知识库。" }
        : null;

  return <div className="gm-chat-notice-row">
    <Popover title="资料发送提示" content={<div className="gm-chat-notice-popover">{CHAT_DATA_POLICY}</div>} trigger="click" placement="bottomLeft">
      <Button type="text" size="small" className="gm-chat-notice-trigger" icon={<InfoCircleOutlined />}>资料边界</Button>
    </Popover>
    {scopeNotice && <Popover title="当前回答范围" content={<div className="gm-chat-notice-popover">{scopeNotice.message}</div>} trigger="click" placement="bottomLeft">
      <Button type="text" size="small" className="gm-chat-notice-trigger gm-chat-scope-trigger" icon={independent ? <LockOutlined /> : <DatabaseOutlined />}>
        <span>{scopeNotice.label}</span>
      </Button>
    </Popover>}
  </div>;
}

export default function ChatPage() {
  const { conversationId, id: routeWorkspaceId } = useParams<{ conversationId: string; id: string }>();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const promptFromReader = searchParams.get("prompt");
  const screens = Grid.useBreakpoint();
  const isMobile = !screens.md;
  const [historyOpen, setHistoryOpen] = useState(false);
  const [history, setHistory] = useState<ChatConversation[]>([]);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState<string | undefined>(routeWorkspaceId);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [historyQuery, setHistoryQuery] = useState("");
  const [conversation, setConversation] = useState<ChatConversation | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loadingConversation, setLoadingConversation] = useState(false);
  const [conversationError, setConversationError] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [imageInputs, setImageInputs] = useState<ChatImageInput[]>([]);
  const [sending, setSending] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [retryingId, setRetryingId] = useState<string>();
  const [mode, setMode] = useState<ChatMode>(() => {
    // stage direct-entry (LifecycleModules): /chat/new?mode=respond etc.
    const requested = searchParams.get("mode");
    if (!requested || !MODE_VALUES.includes(requested as ChatMode)) return "chat";
    // plan/code modes are corpus-bound; ignore them on standalone /chat routes
    if (!routeWorkspaceId && (requested === "research_plan" || requested === "code_generation")) return "chat";
    return requested as ChatMode;
  });
  const [contextPlans, setContextPlans] = useState<ChatContextPlanOption[]>([]);
  const [contextArtifacts, setContextArtifacts] = useState<ChatContextArtifactOption[]>([]);
  const [researchPlanId, setResearchPlanId] = useState<string | undefined>(() => searchParams.get("research_plan_id") || undefined);
  const [sourceArtifactIds, setSourceArtifactIds] = useState<string[]>([]);
  const [agentRuns, setAgentRuns] = useState<AgentRunDetail[]>([]);
  const [agentActionId, setAgentActionId] = useState<string>();
  const messagesRef = useRef<HTMLDivElement>(null);
  const workspaceNames = Object.fromEntries(workspaces.map((workspace) => [workspace.id, workspace.name]));
  const activeWorkspaceId = conversation?.workspace_id ?? selectedWorkspaceId;
  const activeWorkspaceName = activeWorkspaceId ? workspaceNames[activeWorkspaceId] : undefined;
  const independentMode = isIndependentWorkspaceName(activeWorkspaceName);
  const workspaceEnabled = Boolean(activeWorkspaceId) && !independentMode;

  useEffect(() => {
    if (!conversationId && promptFromReader) setInput(promptFromReader);
  }, [conversationId, promptFromReader]);

  const loadAgentRuns = useCallback(async (workspaceId: string, targetConversationId: string) => {
    try {
      const listed = await agentApi.list(workspaceId, { conversation_id: targetConversationId, limit: 50 });
      const details = await Promise.all(listed.items.map((run) => agentApi.get(workspaceId, run.id)));
      setAgentRuns(details);
    } catch (error) {
      message.error(chatErrorMessage(error));
    }
  }, []);

  const loadHistory = useCallback(async () => {
    setHistoryLoading(true);
    try { setHistory((await chatApi.listConversations({ query: historyQuery || undefined, limit: 100 })).items); }
    catch (error) { message.error(chatErrorMessage(error)); }
    finally { setHistoryLoading(false); }
  }, [historyQuery]);

  const loadConversation = useCallback(async (id: string) => {
    setLoadingConversation(true); setConversationError(null);
    try { const detail = await chatApi.getConversation(id); setConversation(detail.conversation); setMessages(sortChatMessages(detail.messages)); }
    catch (error) { setConversation(null); setMessages([]); setConversationError(chatErrorMessage(error)); }
    finally { setLoadingConversation(false); }
  }, []);

  useEffect(() => { const timer = window.setTimeout(() => void loadHistory(), 180); return () => window.clearTimeout(timer); }, [loadHistory]);
  useEffect(() => { workspaceApi.list({ limit: 200 }).then((result) => setWorkspaces(result.items)).catch(() => setWorkspaces([])); }, []);
  useEffect(() => { if (!conversationId) setSelectedWorkspaceId(routeWorkspaceId); }, [conversationId, routeWorkspaceId]);
  // P0.5-1: while an SSE stream is in flight we must NOT reload the conversation
  // here — the backend only has the empty "generating" assistant at that point,
  // and replacing the optimistic (local-stream-*) message with the DB row (real
  // id) makes appendDelta unable to find it, so the UI appears one-shot. The
  // send() flow flips streaming off when the stream ends; this effect then
  // reloads the persisted full message.
  useEffect(() => { if (streaming) return; if (conversationId) void loadConversation(conversationId); else { setConversation(null); setMessages([]); setConversationError(null); } }, [conversationId, loadConversation, streaming]);
  useEffect(() => { if (conversation) setSelectedWorkspaceId(conversation.workspace_id ?? undefined); }, [conversation]);
  useEffect(() => { const node = messagesRef.current; if (node) node.scrollTop = node.scrollHeight; }, [messages, sending]);
  useEffect(() => {
    if (!workspaceEnabled || !activeWorkspaceId) {
      setContextPlans([]);
      setContextArtifacts([]);
      setResearchPlanId(undefined);
      setSourceArtifactIds([]);
      return;
    }
    chatApi.listContextOptions(activeWorkspaceId).then((response) => {
      setContextPlans(response.plans);
      setContextArtifacts(response.artifacts);
      setResearchPlanId((current) => current && response.plans.some((plan) => plan.id === current) ? current : undefined);
    }).catch(() => {
      setContextPlans([]);
      setContextArtifacts([]);
      setResearchPlanId(undefined);
      setSourceArtifactIds([]);
    });
  }, [activeWorkspaceId, workspaceEnabled]);
  useEffect(() => {
    if (!researchPlanId) {
      setSourceArtifactIds([]);
      return;
    }
    const allowed = new Set(contextArtifacts.filter((item) => item.plan_id === researchPlanId).map((item) => item.id));
    setSourceArtifactIds((current) => current.filter((id) => allowed.has(id)));
  }, [contextArtifacts, researchPlanId]);
  useEffect(() => {
    if (independentMode && (mode === "research_plan" || mode === "code_generation")) setMode("chat");
  }, [independentMode, mode]);
  useEffect(() => {
    if (conversationId && activeWorkspaceId) void loadAgentRuns(activeWorkspaceId, conversationId);
    else setAgentRuns([]);
  }, [activeWorkspaceId, conversationId, loadAgentRuns]);
  useEffect(() => {
    if (!conversationId || !activeWorkspaceId || !agentRuns.some((run) => ["queued", "running"].includes(run.status))) return;
    const timer = window.setInterval(() => { void loadConversation(conversationId); void loadAgentRuns(activeWorkspaceId, conversationId); }, 1800);
    return () => window.clearInterval(timer);
  }, [activeWorkspaceId, agentRuns, conversationId, loadAgentRuns, loadConversation]);

  const selectConversation = (item: ChatConversation) => { navigate(chatConversationPath(item)); setImageInputs([]); setHistoryOpen(false); };
  const newConversation = () => { navigate(routeWorkspaceId ? `/workspaces/${routeWorkspaceId}/assistant` : "/chat/new"); setInput(""); setImageInputs([]); setHistoryOpen(false); };
  const changeWorkspace = (workspaceId?: string) => {
    setSelectedWorkspaceId(workspaceId);
    if (workspaceId) navigate(`/workspaces/${workspaceId}/assistant`);
    else if (routeWorkspaceId) navigate("/chat/new");
  };
  const startAgent = async (content: string) => {
    if (mode === "chat") return;
    let wsId = activeWorkspaceId;
    if (!wsId) {
      // P1.5: standalone W7 agents run in the system independent workspace.
      try {
        const independent = await workspaceApi.independent();
        wsId = independent.id;
        setWorkspaces((current) => current.some((workspace) => workspace.id === independent.id)
          ? current
          : [...current, independent]);
      }
      catch (error) { message.error(chatErrorMessage(error)); return; }
    }
    setSending(true);
    setInput("");
    try {
      let targetConversationId = conversationId;
      if (!targetConversationId) {
        const created = await chatApi.createConversation(content.slice(0, 38), wsId);
        targetConversationId = created.id;
        setConversation(created);
      }
      const planOrNone = workspaceEnabled ? researchPlanId || undefined : undefined;
      const agentInput = mode === "research_plan"
        ? {}
        : mode === "code_generation"
          ? { research_plan_id: researchPlanId, framework: "PyTorch" }
          : mode === "respond"
            ? { research_plan_id: planOrNone, reviewer_comments: content }
            : { research_plan_id: planOrNone };
      const run = await agentApi.start(wsId, {
        agent_type: mode,
        prompt: content,
        conversation_id: targetConversationId,
        input: agentInput,
      });
      if (!conversationId) navigate(`/workspaces/${wsId}/assistant/${targetConversationId}`, { replace: true });
      await Promise.all([loadConversation(targetConversationId), loadAgentRuns(wsId, targetConversationId)]);
      const agentLabel = mode === "research_plan" ? "研究计划" : mode === "code_generation" ? "代码生成" : mode === "analyze" ? "结果分析" : mode === "write" ? "论文写作" : mode === "respond" ? "审稿回复" : "Agent";
      message.success(`${agentLabel} Agent 已启动`);
      setAgentActionId(run.id);
      window.setTimeout(() => setAgentActionId(undefined), 500);
      void loadHistory();
    } catch (error) {
      setInput(content);
      message.error(chatErrorMessage(error));
    } finally { setSending(false); }
  };

  const requestAgentStart = async (content: string) => {
    const labels: Record<Exclude<ChatMode, "chat">, string> = {
      research_plan: "生成研究计划",
      code_generation: "代码生成",
      analyze: "结果分析",
      write: "论文写作",
      respond: "审稿回复",
    };
    await new Promise<void>((resolve) => {
      Modal.confirm({
        title: `建议启动“${labels[mode as Exclude<ChatMode, "chat">]}”`,
        content: "确认后才会创建 AgentRun、Task 或长期产物。取消则继续留在普通提问入口。",
        okText: "确认启动",
        cancelText: "继续提问",
        onOk: async () => {
          await startAgent(content);
          resolve();
        },
        onCancel: () => resolve(),
      });
    });
  };

  const send = async (content: string, images: ChatImageInput[] = []) => {
    if (mode !== "chat") { await requestAgentStart(content); return; }
    let targetId = conversationId;
    setInput("");
    setImageInputs([]);
    setSending(true);
    // P0.5-1: mark streaming BEFORE navigating so the [conversationId, streaming]
    // effect sees streaming=true on the new route and won't clobber the optimistic
    // message with the backend's empty "generating" row.
    setStreaming(true);
    if (!targetId) {
      try {
        const created = await chatApi.createConversation(content.slice(0, 38), selectedWorkspaceId);
        targetId = created.id;
        setConversation(created);
        navigate(chatConversationPath(created), { replace: true });
      } catch (error) {
        setStreaming(false);
        setSending(false);
        setImageInputs(images);
        message.error(chatErrorMessage(error));
        return;
      }
    }
    const assistantKey = `local-stream-${Date.now()}`;
    const optimisticUserId = `local-user-${Date.now()}`;
    const optimisticUser = { ...localMessage(targetId, "user", content, messages.length + 1, localImages(optimisticUserId, images)), id: optimisticUserId };
    const optimisticAssistant = { ...localMessage(targetId, "assistant", "", messages.length + 2), id: assistantKey };
    setMessages((current) => [...current, optimisticUser, optimisticAssistant]);
    // Browser paint is frame-driven: even per-token DOM updates collapse to a
    // single paint if the tokens arrive within one frame. Throttle rendering to
    // a fixed cadence (~20 chars / 60ms) so the UI visibly streams regardless
    // of how the browser coalesces SSE chunks.
    let pendingDelta = "";
    let streamTimer: number | null = null;
    const appendDelta = (delta: string) => {
      pendingDelta += delta;
      if (streamTimer == null) {
        streamTimer = window.setInterval(() => {
          if (pendingDelta) {
            const slice = pendingDelta.slice(0, 20);
            pendingDelta = pendingDelta.slice(20);
            setMessages((current) => current.map((m) => m.id === assistantKey ? { ...m, content: m.content + slice } : m));
          }
          if (!pendingDelta && streamTimer != null) {
            window.clearInterval(streamTimer);
            streamTimer = null;
          }
        }, 60);
      }
    };
    try {
      await streamAssistant(targetId, content, images, appendDelta);
      // Let the throttled renderer flush any remaining buffered tokens before
      // the effect reload replaces the optimistic message with the full one.
      await new Promise<void>((resolve) => {
        const wait = () => {
          if (streamTimer != null || pendingDelta) window.setTimeout(wait, 50);
          else resolve();
        };
        wait();
      });
      // P0.5-1: streaming is over — flip the flag so the [conversationId,
      // streaming] effect reloads the persisted full message (the DB row now has
      // the complete content), then refresh the sidebar history.
      setStreaming(false);
      void loadHistory();
    } catch (error) {
      setStreaming(false);
      setImageInputs(images);
      const rawDiagnosticCode = error && typeof error === "object" && "diagnostic_code" in error
        ? String((error as { diagnostic_code?: unknown }).diagnostic_code || "") || null
        : null;
      const diagnostic = retrievalDiagnosticCopy(rawDiagnosticCode);
      const diagnosticCode: ChatRetrievalDiagnosticCode | null = diagnostic && rawDiagnosticCode
        ? rawDiagnosticCode as ChatRetrievalDiagnosticCode
        : null;
      const displayError = diagnostic ? `${diagnostic.title} ${diagnostic.recovery}` : chatErrorMessage(error);
      setMessages((current) => current.map((m) => m.id === assistantKey ? {
        ...m,
        status: "failed" as const,
        error_message: displayError,
        retrieval_diagnostic_code: diagnosticCode,
      } : m));
      message.error(displayError);
      void loadHistory();
    } finally { setSending(false); }
  };

  const streamAssistant = async (conversationId: string, content: string, images: ChatImageInput[], appendDelta: (d: string) => void) => {
    console.debug("[chat-stream] enter", { conversationId, at: new Date().toISOString() });
    const resp = await chatApi.streamSend(conversationId, content, {
      researchPlanId: workspaceEnabled ? researchPlanId : undefined,
      sourceArtifactIds: workspaceEnabled ? sourceArtifactIds : [],
      images,
    });
    if (!resp.ok || !resp.body) throw new Error("流式响应不可用");
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let chunks = 0;
    let tokens = 0;
    let firstTokenAt: string | null = null;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      chunks += 1;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop() ?? "";
      for (const part of parts) {
        const line = part.trim();
        if (!line.startsWith("data: ")) continue;
        let event: { type?: string; content?: string; message?: string; diagnostic_code?: ChatRetrievalDiagnosticCode | null } | undefined;
        try {
          event = JSON.parse(line.slice(6)) as { type?: string; content?: string; message?: string };
        } catch { /* ignore malformed SSE line */ }
        if (event?.type === "token" && typeof event.content === "string") {
          if (firstTokenAt === null) firstTokenAt = new Date().toISOString();
          tokens += 1;
          appendDelta(event.content);
        }
        if (event?.type === "error") {
          const streamError = new Error(event.message || "回答失败，请重试。") as Error & { diagnostic_code?: string | null };
          streamError.diagnostic_code = event.diagnostic_code;
          throw streamError;
        }
      }
    }
    console.debug("[chat-stream] done", { chunks, tokens, firstTokenAt, at: new Date().toISOString() });
  };

  const refreshAgent = async (run: AgentRunDetail) => {
    if (!activeWorkspaceId) return;
    setAgentActionId(run.id);
    try { await Promise.all([loadAgentRuns(activeWorkspaceId, run.conversation_id ?? conversationId ?? ""), conversationId ? loadConversation(conversationId) : Promise.resolve()]); }
    finally { setAgentActionId(undefined); }
  };
  const confirmAgent = async (run: AgentRunDetail) => {
    if (!activeWorkspaceId) return;
    setAgentActionId(run.id);
    try {
      await agentApi.confirm(activeWorkspaceId, run.id);
      message.success("研究计划已保存到研究中心");
      await refreshAgent(run);
      const response = await chatApi.listContextOptions(activeWorkspaceId);
      setContextPlans(response.plans);
      setContextArtifacts(response.artifacts);
    } catch (error) { message.error(chatErrorMessage(error)); }
    finally { setAgentActionId(undefined); }
  };
  const cancelAgent = async (run: AgentRunDetail) => {
    if (!activeWorkspaceId) return;
    setAgentActionId(run.id);
    try { await agentApi.cancel(activeWorkspaceId, run.id); message.success("Agent 已停止"); await refreshAgent(run); }
    catch (error) { message.error(chatErrorMessage(error)); }
    finally { setAgentActionId(undefined); }
  };
  const requestCodeRepair = (run: AgentRunDetail) => {
    if (!activeWorkspaceId || !run.conversation_id || run.parent_run_id) return;
    const repairConversationId = run.conversation_id;
    const repairPlanId = String(
      (run.result ?? {}).research_plan_id
      ?? run.input_payload.research_plan_id
      ?? "",
    );
    Modal.confirm({
      title: "建议生成一次候选修复",
      content: "确认后只会创建一次代码修复 AgentRun 和候选产物。系统不会覆盖原代码、不会运行代码或测试；请在生成后人工审查变更。",
      okText: "确认生成候选",
      cancelText: "暂不生成",
      onOk: async () => {
        setAgentActionId(run.id);
        try {
          const child = await agentApi.start(activeWorkspaceId, {
            agent_type: "code_generation",
            prompt: "针对上一轮交付完整性检查缺口生成一次最小候选修复。",
            conversation_id: repairConversationId,
            input: { research_plan_id: repairPlanId, framework: "PyTorch", repair_parent_run_id: run.id },
          });
          message.success("候选修复已启动；原代码不会被覆盖");
          await Promise.all([loadConversation(repairConversationId), loadAgentRuns(activeWorkspaceId, repairConversationId)]);
          setAgentActionId(child.id);
          window.setTimeout(() => setAgentActionId(undefined), 500);
        } catch (error) {
          setAgentActionId(undefined);
          message.error(chatErrorMessage(error));
        }
      },
    });
  };
  const downloadArtifact = async (run: AgentRunDetail, artifactId: string) => {
    if (!activeWorkspaceId) return;
    try { await agentApi.downloadArtifact(activeWorkspaceId, run.id, artifactId); }
    catch (error) { message.error(chatErrorMessage(error)); }
  };
  const retry = async (failed: ChatMessage) => {
    if (!conversationId) return;
    setRetryingId(failed.id);
    try { const result = await chatApi.retryMessage(conversationId, failed.id); setConversation(result.conversation); setMessages((current) => current.map((item) => item.id === failed.id ? result.assistant_message : item)); void loadHistory(); }
    catch (error) { message.error(chatErrorMessage(error)); void loadConversation(conversationId); }
    finally { setRetryingId(undefined); }
  };

  const rename = (item: ChatConversation) => {
    let nextTitle = item.title;
    Modal.confirm({ title: "重命名对话", content: <input autoFocus defaultValue={item.title} maxLength={255} onChange={(event) => { nextTitle = event.target.value; }} style={{ width: "100%", padding: 8 }} />, okText: "保存", cancelText: "取消", onOk: async () => { if (!nextTitle.trim()) { message.error("标题不能为空"); return Promise.reject(); } const updated = await chatApi.renameConversation(item.id, nextTitle.trim()); if (conversationId === item.id) setConversation(updated); void loadHistory(); } });
  };
  const remove = (item: ChatConversation) => {
    Modal.confirm({ title: "删除这段对话？", content: "删除后将从历史列表中移除，消息无法在界面中恢复。", okText: "删除", okButtonProps: { danger: true }, cancelText: "取消", onOk: async () => { try { await chatApi.deleteConversation(item.id); if (conversationId === item.id) newConversation(); void loadHistory(); message.success("已删除对话"); } catch (error) { message.error(chatErrorMessage(error)); } } });
  };

  const historyPanel = <ChatHistory items={history} selectedId={conversationId} loading={historyLoading} query={historyQuery} workspaceNames={workspaceNames} onQueryChange={setHistoryQuery} onNew={newConversation} onSelect={selectConversation} onRename={rename} onDelete={remove} />;
  const planOptions = contextPlans.map((plan) => ({ value: plan.id, label: plan.title, title: plan.research_question }));
  const sourceOptions = contextArtifacts
    .filter((artifact) => artifact.plan_id === researchPlanId)
    .map((artifact) => ({ value: artifact.id, label: `${artifact.label}：${artifact.title}`, title: artifact.status }));
  return <div className="gm-chat-page"><div className="gm-chat-layout">{!isMobile && <aside className="gm-chat-sidebar">{historyPanel}</aside>}<main className="gm-chat-main"><ChatHeader title={conversation?.title ?? "新对话"} workspaces={workspaces} workspaceId={activeWorkspaceId} independent={independentMode} scopeLocked={Boolean(conversation)} onWorkspaceChange={changeWorkspace} onOpenHistory={() => setHistoryOpen(true)} /><ChatNoticeBar independent={independentMode} activeWorkspaceId={activeWorkspaceId} activeWorkspaceName={activeWorkspaceName} hasConversation={Boolean(conversation)} /><div className="gm-chat-scroll" ref={messagesRef}>{conversationError ? <Result status="404" title="找不到这段对话" subTitle={conversationError} extra={<Button type="primary" onClick={newConversation}>开始新对话</Button>} /> : loadingConversation ? <div className="gm-chat-loading"><Spin /></div> : messages.length === 0 ? <ChatEmptyState onExample={setInput} workspaceName={workspaceEnabled ? activeWorkspaceName : undefined} independent={independentMode} /> : <ChatMessages conversationId={conversationId} messages={messages} agentRuns={agentRuns} onRetry={retry} retryingId={retryingId} agentActionId={agentActionId} onRefreshAgent={(run) => void refreshAgent(run)} onConfirmAgent={(run) => void confirmAgent(run)} onCancelAgent={(run) => void cancelAgent(run)} onRepairCode={(run) => requestCodeRepair(run)} onDownloadAgent={(run) => activeWorkspaceId ? void agentApi.downloadBundle(activeWorkspaceId, run.id) : undefined} onDownloadArtifact={(run, artifactId) => void downloadArtifact(run, artifactId)} />}</div>{sending && <div className="gm-chat-sending-note">{mode === "chat" ? "正在检索并组织回答，请稍候…" : "正在执行已确认的 Agent 操作，请稍候…"}</div>}<ChatComposer value={input} onChange={setInput} onSend={(value, images) => void send(value, images)} loading={sending || Boolean(retryingId)} workspaceEnabled={workspaceEnabled} mode={mode} onModeChange={setMode} planOptions={planOptions} researchPlanId={researchPlanId} onResearchPlanChange={setResearchPlanId} sourceOptions={sourceOptions} sourceArtifactIds={sourceArtifactIds} onSourceArtifactChange={setSourceArtifactIds} imageInputs={imageInputs} onImageInputsChange={setImageInputs} /></main></div><Drawer title="历史对话" placement="left" open={isMobile && historyOpen} onClose={() => setHistoryOpen(false)} width={300}>{historyPanel}</Drawer></div>;
}
