import { useEffect, useMemo, useState } from "react";
import { Alert, App, Button, Drawer, Empty, Space, Spin, Tag, Typography } from "antd";
import { DownloadOutlined, FileSearchOutlined } from "@ant-design/icons";
import apiClient from "../api/client";
import knowledgeApi from "../api/knowledge";
import type { EvidenceContext, EvidenceSpan } from "../api/types/knowledge";
import { discoverApi, type OpportunityEvidence } from "../api/discover";
import { evidenceLevelDisplayLabel, evidenceRelationLabel, gateMessageLabel } from "../state/discoverLabels";
import { buildEvidenceExcerpt, type EvidenceOffsetSpan } from "./evidenceExcerpt";

const { Text } = Typography;

interface Segment {
  text: string;
  highlighted: boolean;
  relation?: string;
}

function buildSegments(content: string, spans: EvidenceOffsetSpan[]): Segment[] {
  const valid = spans
    .filter((span) => span.start_char != null && span.end_char != null && (span.end_char ?? 0) > (span.start_char ?? 0))
    .map((span) => ({ start: Math.max(0, span.start_char ?? 0), end: Math.min(content.length, span.end_char ?? 0), relation: span.relation ?? undefined }))
    .filter((span) => span.end > span.start)
    .sort((a, b) => a.start - b.start);
  if (!valid.length) return [{ text: content, highlighted: false }];
  const boundaries = Array.from(new Set([0, content.length, ...valid.flatMap((span) => [span.start, span.end])])).sort((a, b) => a - b);
  return boundaries.slice(0, -1).map((start, index) => {
    const end = boundaries[index + 1];
    const match = valid.find((span) => start < span.end && end > span.start);
    return { text: content.slice(start, end), highlighted: Boolean(match), relation: match?.relation };
  });
}

export default function EvidenceViewer({
  workspaceId,
  itemId,
  span,
}: {
  workspaceId: string;
  itemId: string;
  span: EvidenceSpan;
}) {
  const { message } = App.useApp();
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [context, setContext] = useState<EvidenceContext | null>(null);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    setExpanded(false);
    setContext(null);
    knowledgeApi
      .evidenceContext(workspaceId, itemId)
      .then(setContext)
      .catch((error) => message.error(`加载证据原文失败：${(error as Error).message}`))
      .finally(() => setLoading(false));
  }, [itemId, message, open, workspaceId]);

  const sourceSpans = useMemo(
    () => (context?.spans?.length ? context.spans : [span]),
    [context?.spans, span],
  );
  const excerpt = useMemo(
    () => (context ? buildEvidenceExcerpt(context.content, sourceSpans, span) : null),
    [context, sourceSpans, span],
  );
  const displayedContent = context && excerpt ? (expanded ? context.content : excerpt.content) : "";
  const displayedSpans = useMemo(
    () => (context && excerpt ? (expanded ? sourceSpans : excerpt.spans) : []),
    [context, expanded, excerpt, sourceSpans],
  );
  const segments = useMemo(
    () => (context ? buildSegments(displayedContent, displayedSpans) : []),
    [context, displayedContent, displayedSpans],
  );
  const downloadUrl = context
    ? `${apiClient.defaults.baseURL}/workspaces/${workspaceId}/artifacts/${context.artifact_id}/download`
    : "#";

  return <>
    <Button size="small" icon={<FileSearchOutlined />} onClick={() => setOpen(true)}>定位原文</Button>
    <Drawer title="证据原文" open={open} width="760px" onClose={() => setOpen(false)}>
      {loading ? <div style={{ textAlign: "center", padding: 48 }}><Spin /></div> : !context ? <Empty description="没有可用的 Markdown 解析原文" /> : <>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 12, marginBottom: 12, flexWrap: "wrap" }}>
          <Text type="secondary">{context.filename ?? "parsed_markdown"} · 高亮字符范围 {span.start_char ?? "—"}–{span.end_char ?? "—"}</Text>
          <Space size={4}>
            {excerpt?.is_excerpt && <Button type="link" size="small" onClick={() => setExpanded((value) => !value)}>{expanded ? "收起全文" : "展开全文"}</Button>}
            <Button icon={<DownloadOutlined />} href={downloadUrl} target="_blank">下载解析后的 Markdown</Button>
          </Space>
        </div>
        <Alert type="info" showIcon message={expanded ? "已展开完整 parsed_markdown；黄色区域为当前 Knowledge Item 的证据原文。" : "默认显示证据位置附近的上下文；黄色区域为当前 Knowledge Item 的证据原文。"} style={{ marginBottom: 12 }} />
        <pre style={{ whiteSpace: "pre-wrap", wordBreak: "break-word", lineHeight: 1.75, background: "var(--gm-surface-3)", padding: 16, borderRadius: 8, maxHeight: "70vh", overflow: "auto" }}>
          {!expanded && excerpt?.omitted_before && <span>… 前文已省略 …{"\n"}</span>}
          {segments.map((segment, index) => segment.highlighted ? <mark key={index} style={{ background: segment.relation === "contradicts" ? "var(--gm-mark-danger)" : "var(--gm-mark)", padding: 0 }} title={segment.relation ? evidenceRelationLabel(segment.relation) : undefined}>{segment.text}</mark> : <span key={index}>{segment.text}</span>)}
          {!expanded && excerpt?.omitted_after && <span>{"\n"}… 后文已省略 …</span>}
        </pre>
        <Tag color={span.relation === "contradicts" ? "red" : "gold"}>{evidenceRelationLabel(span.relation)}</Tag>
      </>}
    </Drawer>
  </>;
}

export function OpportunityEvidenceViewer({
  workspaceId,
  evidence,
}: {
  workspaceId: string;
  evidence: OpportunityEvidence;
}) {
  const { message } = App.useApp();
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [context, setContext] = useState<Awaited<ReturnType<typeof discoverApi.getEvidenceContext>> | null>(null);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    setExpanded(false);
    setContext(null);
    discoverApi
      .getEvidenceContext(workspaceId, evidence.id)
      .then(setContext)
      .catch((error) => message.error(`加载研究机会证据原文失败：${(error as Error).message}`))
      .finally(() => setLoading(false));
  }, [evidence.id, message, open, workspaceId]);

  const evidenceSpan = useMemo<EvidenceOffsetSpan>(() => ({
    start_char: context?.start_char,
    end_char: context?.end_char,
    relation: evidence.relation,
  }), [context?.end_char, context?.start_char, evidence.relation]);
  const excerpt = useMemo(() => {
    if (!context?.available || !context.content) return null;
    return buildEvidenceExcerpt(context.content, [evidenceSpan], evidenceSpan);
  }, [context, evidenceSpan]);
  const displayedContent = context?.content && excerpt ? (expanded ? context.content : excerpt.content) : "";
  const displayedSpans = useMemo(
    () => (excerpt ? (expanded ? [evidenceSpan] : excerpt.spans) : []),
    [evidenceSpan, expanded, excerpt],
  );
  const segments = useMemo(
    () => (context ? buildSegments(displayedContent, displayedSpans) : []),
    [context, displayedContent, displayedSpans],
  );

  return (
    <>
      <Button size="small" icon={<FileSearchOutlined />} onClick={() => setOpen(true)}>
        查看证据原文
      </Button>
      <Drawer title="研究机会证据" open={open} width="min(760px, 100vw)" onClose={() => setOpen(false)}>
        {loading ? <div style={{ textAlign: "center", padding: 48 }}><Spin /></div> : !context ? <Empty description="尚未加载证据" /> : !context.available ? (
          <Alert type="warning" showIcon message="仅有元数据" description={gateMessageLabel(context.message ?? "本地没有可定位的全文证据。")} />
        ) : (
          <>
            <div style={{ display: "flex", justifyContent: "space-between", gap: 12, marginBottom: 12, flexWrap: "wrap" }}>
              <Text type="secondary">{context.filename ?? "parsed_markdown"} · {context.start_char ?? "—"}–{context.end_char ?? "—"}</Text>
              <Space size={4}>
                {excerpt?.is_excerpt && <Button type="link" size="small" onClick={() => setExpanded((value) => !value)}>{expanded ? "收起全文" : "展开全文"}</Button>}
                <Tag color="green">{evidenceLevelDisplayLabel("full_text")} · {evidenceRelationLabel(evidence.relation)}</Tag>
              </Space>
            </div>
            <Alert type="info" showIcon message={expanded ? "已展开完整 parsed_markdown；黄色区域为当前证据。" : "默认显示证据位置附近的上下文；黄色区域为当前证据。"} style={{ marginBottom: 12 }} />
            <pre style={{ whiteSpace: "pre-wrap", wordBreak: "break-word", lineHeight: 1.75, background: "var(--gm-surface-3)", padding: 16, borderRadius: 8, maxHeight: "70vh", overflow: "auto" }}>
              {!expanded && excerpt?.omitted_before && <span>… 前文已省略 …{"\n"}</span>}
              {segments.map((segment, index) => segment.highlighted ? <mark key={index} style={{ background: segment.relation === "contradicts" ? "#ffccc7" : "#fff566", padding: 0 }} title={segment.relation ? evidenceRelationLabel(segment.relation) : undefined}>{segment.text}</mark> : <span key={index}>{segment.text}</span>)}
              {!expanded && excerpt?.omitted_after && <span>{"\n"}… 后文已省略 …</span>}
            </pre>
          </>
        )}
      </Drawer>
    </>
  );
}
