import { useState } from "react";
import { Alert, Button, Empty, Space, Spin, Tooltip, Typography } from "antd";
import { CheckOutlined, CopyOutlined, ReloadOutlined } from "@ant-design/icons";
import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import type { ChatMessage } from "../../api/chat";
import { apiBaseURL } from "../../api/client";
import { chatFailureMessage, retrievalDiagnosticCopy } from "../../state/chatState";
import ChatCitations from "./ChatCitations";
import ChatGraphAudit from "./ChatGraphAudit";
import ChatAgentRunCard from "./ChatAgentRunCard";
import type { AgentRunDetail } from "../../api/agent";

/**
* P0.5-3：assistant 经常用普通括号包裹行内数学公式 `[ LaTeX ]` 或 `( LaTeX )`，
* remark-math 无法识别（它要求 `$...$` / `\(...\)` / `\[...\]`）。渲染前将
* 括号包裹的公式规范化为 `$...$`，并将裸的 `X_{sub}` 下标规范化为 `$X_{sub}$`。
* 已有的 `$...$` 块和合法的 `\left[...\right]` 对会被保护，避免重复包裹。
 */
export function normalizeConversationMath(content: string): string {
  const protectedBlocks: Array<[string, string]> = [];
  const protect = (text: string, re: RegExp) => text.replace(re, (m) => {
    const key = `__M${protectedBlocks.length}__`;
    protectedBlocks.push([key, m]);
    return key;
  });
  const isMathLike = (formula: string, allowSingleIdentifier = true) => {
    const trimmed = formula.trim();
    return /(?:\\[A-Za-z]+|[=^_{}]|\b(?:in|notin|times|mid|sum|prod|mathbb|mathcal)\b)/.test(trimmed)
      || (allowSingleIdentifier && /^[A-Za-z](?:['′]?\d*)?$/.test(trimmed));
  };

  const normalizeDelimitedMath = (value: string, open: string, close: string): string => {
    let normalized = "";
    let index = 0;
    while (index < value.length) {
      if (value[index] !== open) {
        normalized += value[index];
        index += 1;
        continue;
      }

      let depth = 0;
      let closingIndex = -1;
      for (let cursor = index; cursor < value.length; cursor += 1) {
        if (value[cursor] === open) depth += 1;
        if (value[cursor] === close) {
          depth -= 1;
          if (depth === 0) {
            closingIndex = cursor;
            break;
          }
        }
      }

      if (closingIndex > index) {
        const rawFormula = value.slice(index + 1, closingIndex);
        const formula = rawFormula.trim();
        const isMarkdownLink = close === "]" && value[closingIndex + 1] === "(";
        if (!isMarkdownLink && isMathLike(formula, close === ")")) {
          normalized += rawFormula.includes("\n") ? `\n$$\n${formula}\n$$\n` : `$${formula}$`;
          index = closingIndex + 1;
          continue;
        }
      }

      normalized += value[index];
      index += 1;
    }
    return normalized;
  };

// 1. 保护代码和已有的美元符号数学块。转换 LaTeX 分隔符 \\[...\\] 和 \\(...\\)，
//    因为 remark-math 在此渲染流水线中无法稳定解析这些分隔符。
  let text = protect(content, /```[\s\S]*?```|\$\$[\s\S]*?\$\$|\$[^$\n]*\$/g);
  text = text.replace(/\\\[([\s\S]*?)\\\]/g, (_match, formula: string) => `\n$$\n${formula.trim()}\n$$\n`);
  text = text.replace(/\\\(([\s\S]*?)\\\)/g, (_match, formula: string) => `$${formula.trim()}$`);
  text = protect(text, /\\left\[[^\[\]]*\\right\]/g);
  text = protect(text, /\$\$[\s\S]*?\$\$|\$[^$\n]*\$/g);

// 2. `[ X \\in ... ]` 等方括号公式不是标准 Markdown 数学格式。扫描器支持跨多行公式，
//    不会处理普通 citation 或 Markdown 链接。
  text = normalizeDelimitedMath(text, "[", "]");
  text = protect(text, /\$\$[\s\S]*?\$\$|\$[^$\n]*\$/g);

// 3. 处理 `(G=(V,E))` 这类嵌套圆括号公式。简单正则会在 `(V,E)` 处停止，产生无效 LaTeX。
  text = normalizeDelimitedMath(text, "(", ")");

// 4. 保护已生成的数学块，然后只在剩余普通文本中转换裸下标。
  text = protect(text, /```[\s\S]*?```|\$\$[\s\S]*?\$\$|\$[^$\n]*\$/g);
  text = text.replace(/(^|[^$`\\])\b([A-Za-z])_(\{[^{}]*\}|[A-Za-z0-9]+)(?=\s|[^A-Za-z0-9_]|$)/g, (_match, prefix: string, base: string, sub: string) => {
    const clean = sub.startsWith("{") ? sub.slice(1, -1) : sub;
    return `${prefix}$${base}_{${clean.length === 1 ? clean : `\\mathrm{${clean}}`}}$`;
  });

// 5. 恢复所有受保护的范围。
  for (const [key, val] of [...protectedBlocks].reverse()) text = text.split(key).join(val);
  return text;
}

interface Props { conversationId?: string; messages: ChatMessage[]; agentRuns?: AgentRunDetail[]; onRetry: (message: ChatMessage) => void; retryingId?: string; agentActionId?: string; onRefreshAgent: (run: AgentRunDetail) => void; onConfirmAgent: (run: AgentRunDetail) => void; onCancelAgent: (run: AgentRunDetail) => void; onDownloadAgent: (run: AgentRunDetail) => void; onDownloadArtifact?: (run: AgentRunDetail, artifactId: string) => void; onRepairCode?: (run: AgentRunDetail) => void; }

export default function ChatMessages({ conversationId, messages, agentRuns = [], onRetry, retryingId, agentActionId, onRefreshAgent, onConfirmAgent, onCancelAgent, onDownloadAgent, onDownloadArtifact, onRepairCode }: Props) {
  if (messages.length === 0) return <Empty className="gm-chat-empty-messages" image={Empty.PRESENTED_IMAGE_SIMPLE} description="开始一段新的研究对话" />;
  const byAssistant = new Map(agentRuns.map((run) => [run.assistant_message_id, run]));
  return <div className="gm-chat-messages">{messages.map((message) => {
    const run = byAssistant.get(message.id);
    return <div key={message.id}>{run ? <><ChatAgentRunCard run={run} loading={agentActionId === run.id} onRefresh={() => onRefreshAgent(run)} onConfirm={() => onConfirmAgent(run)} onCancel={() => onCancelAgent(run)} onDownload={() => onDownloadAgent(run)} onDownloadArtifact={onDownloadArtifact} onRepairCode={onRepairCode} />{conversationId && (message.citations?.length ?? 0) > 0 && <div className="gm-agent-citations"><ChatCitations conversationId={conversationId} messageId={message.id} citations={message.citations ?? []} /></div>}</> : <ChatMessageItem conversationId={conversationId} message={message} onRetry={onRetry} retrying={retryingId === message.id} />}</div>;
  })}</div>;
}

function ChatMessageItem({ conversationId, message, onRetry, retrying }: { conversationId?: string; message: ChatMessage; onRetry: (message: ChatMessage) => void; retrying: boolean }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    await navigator.clipboard?.writeText(message.content);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1200);
  };
  const isUser = message.role === "user";
  const normalizedContent = isUser ? message.content : normalizeConversationMath(message.content);
  const retrievalDiagnostic = !isUser ? retrievalDiagnosticCopy(message.retrieval_diagnostic_code) : null;
  const imageUrl = (image: NonNullable<ChatMessage["images"]>[number]) => image.data_url
    ?? `${apiBaseURL.replace(/\/$/, "")}/chat/conversations/${message.conversation_id}/messages/${message.id}/images/${image.id}`;
  return <article className={`gm-chat-message ${isUser ? "is-user" : "is-assistant"}`}>
    <div className="gm-chat-message-body">
      {(message.images?.length ?? 0) > 0 && <div className="gm-chat-message-images">
        {message.images?.map((image) => <img key={image.id} src={imageUrl(image)} alt={image.filename} loading="lazy" />)}
      </div>}
      {message.status === "generating" ? (message.content ? <div className="gm-chat-markdown gm-chat-streaming"><ReactMarkdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex]}>{normalizedContent}</ReactMarkdown><div className="gm-chat-streaming-hint"><Spin size="small" /><Typography.Text type="secondary">正在生成…</Typography.Text></div></div> : <Space><Spin size="small" /><Typography.Text type="secondary">正在思考…</Typography.Text></Space>) : message.status === "failed" ? <div><Typography.Text type="danger">{chatFailureMessage(message)}</Typography.Text><div><Button type="link" size="small" icon={<ReloadOutlined />} loading={retrying} onClick={() => onRetry(message)}>重新尝试</Button></div></div> : isUser ? <Typography.Paragraph className="gm-chat-plain-text">{message.content}</Typography.Paragraph> : <div className="gm-chat-markdown"><ReactMarkdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex]}>{normalizedContent}</ReactMarkdown></div>}
      {!isUser && message.status === "completed" && message.grounding_status === "no_evidence" && <Typography.Text type="warning">本次没有使用工作区证据。</Typography.Text>}
      {!isUser && message.status === "completed" && retrievalDiagnostic && <Alert type="warning" showIcon message={retrievalDiagnostic.title} description={retrievalDiagnostic.recovery} />}
      {!isUser && message.status === "completed" && message.citation_check && !message.citation_check.ok && <Typography.Text type="danger">检测到失效引用：[E{message.citation_check.broken.join("]、[E")}] 未找到对应证据，请核对来源。</Typography.Text>}
      {!isUser && message.status === "completed" && message.citation_check?.grounded_without_citations && <Typography.Text type="warning">已使用工作区证据，但回答未标注 [E] 引用，关键结论可能缺少直接支撑。</Typography.Text>}
      {!isUser && message.status === "completed" && message.citation_quality?.status === "rejected" && <Alert type="warning" showIcon message="回答未通过引用质量校验" description="当前回答已降级为证据不足提示，未将未验证结论展示为论文事实。" />}
      {!isUser && message.status === "completed" && message.source_check && !message.source_check.ok && <Typography.Text type="danger">检测到失效上下文来源标记：{message.source_check.broken.join("、")}，请核对来源。</Typography.Text>}
      {!isUser && message.status === "completed" && message.retrieval_audit?.graph && <ChatGraphAudit audit={message.retrieval_audit.graph} />}
      {!isUser && conversationId && (message.citations?.length ?? 0) > 0 && <ChatCitations conversationId={conversationId} messageId={message.id} citations={message.citations ?? []} />}
    </div>
    {message.status === "completed" && <div className="gm-chat-message-actions"><Tooltip title={copied ? "已复制" : "复制"}><Button type="text" size="small" aria-label="复制消息" icon={copied ? <CheckOutlined /> : <CopyOutlined />} onClick={() => void copy()} /></Tooltip></div>}
  </article>;
}
