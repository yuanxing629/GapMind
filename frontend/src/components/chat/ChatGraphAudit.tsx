import { useState, type ReactNode } from "react";
import { Alert, Button, Collapse, Drawer, Empty, List, Space, Spin, Tag, Typography, message } from "antd";
import { FileSearchOutlined } from "@ant-design/icons";
import knowledgeApi from "../../api/knowledge";
import type { GraphRAGAudit, GraphRAGPath } from "../../api/types/knowledge";
import { buildEvidenceExcerpt, type EvidenceOffsetSpan } from "../evidenceExcerpt";

const { Paragraph, Text } = Typography;
type GraphRAGEvidence = NonNullable<GraphRAGPath["evidence"]>[number];

function reviewStatusLabel(status: string): { label: string; color: string } {
  if (status === "confirmed") return { label: "人工确认", color: "green" };
  if (status === "rejected") return { label: "已拒绝", color: "red" };
  return { label: "候选关联", color: "gold" };
}

function fallbackLabel(reason: string | null | undefined): string {
  if (reason === "insufficient_evidence") return "没有可回链的证据，未将图路径加入回答上下文。";
  if (reason === "graph_timeout") return "图投影超时，已安全保留 dense 检索回答。";
  if (reason === "projection_version_mismatch") return "图投影版本不一致，已安全保留 dense 检索回答。";
  if (reason === "graph_query_failed") return "图投影失败，已安全保留 dense 检索回答。";
  return "GraphRAG 仅用于诊断，当前回答仍使用 dense 论文证据。";
}

function highlightedSource(
  content: string,
  evidence: GraphRAGEvidence,
  spans: Array<{ id: string; start_char?: number | null; end_char?: number | null }>,
  expanded: boolean,
): { node: ReactNode; valid: boolean; canExpand: boolean } {
  const span = spans.find((item) => item.id === evidence.evidence_span_id);
  const start = span?.start_char ?? evidence.start_char;
  const end = span?.end_char ?? evidence.end_char;
  if (start == null || end == null || !Number.isInteger(start) || !Number.isInteger(end) || start < 0 || end <= start || end > content.length) {
    return { node: <span>{content}</span>, valid: false, canExpand: false };
  }
  const excerpt = (evidence.excerpt || "").replace(/\x00/g, "");
  if (excerpt && (start + excerpt.length > end || content.slice(start, start + excerpt.length).replace(/\x00/g, "") !== excerpt)) {
    return { node: <span>{content}</span>, valid: false, canExpand: false };
  }
  const range: EvidenceOffsetSpan = { start_char: start, end_char: end };
  const excerptView = buildEvidenceExcerpt(content, [range], range);
  const viewContent = expanded ? content : excerptView.content;
  const viewSpan = expanded ? range : excerptView.spans[0];
  const viewStart = viewSpan?.start_char;
  const viewEnd = viewSpan?.end_char;
  if (viewStart == null || viewEnd == null || viewEnd <= viewStart) {
    return { node: <span>{viewContent}</span>, valid: true, canExpand: excerptView.is_excerpt };
  }
  return {
    node: <>
      {!expanded && excerptView.omitted_before && <span>… 前文已省略 …{"\n"}</span>}
      <span>{viewContent.slice(0, viewStart)}</span>
      <mark>{viewContent.slice(viewStart, viewEnd)}</mark>
      <span>{viewContent.slice(viewEnd)}</span>
      {!expanded && excerptView.omitted_after && <span>{"\n"}… 后文已省略 …</span>}
    </>,
    valid: true,
    canExpand: excerptView.is_excerpt,
  };
}

export default function ChatGraphAudit({ audit }: { audit: GraphRAGAudit }) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<GraphRAGEvidence | null>(null);
  const [context, setContext] = useState<Awaited<ReturnType<typeof knowledgeApi.evidenceContext>> | null>(null);
  const [expandedSource, setExpandedSource] = useState(false);

  if (audit.mode !== "shadow") return null;
  const paths = audit.paths ?? [];
  const droppedReasons = Object.entries(audit.dropped_path_reasons ?? {})
    .map(([reason, count]) => `${reason} ${count}`)
    .join("、");

  const openEvidence = async (evidence: GraphRAGEvidence) => {
    setSelected(evidence);
    setOpen(true);
    setLoading(true);
    setContext(null);
    setExpandedSource(false);
    try {
      setContext(await knowledgeApi.evidenceContext(evidence.workspace_id, evidence.item_id, evidence.evidence_span_id));
    } catch (error) {
      message.error(`原文加载失败：${(error as Error).message}`);
    } finally {
      setLoading(false);
    }
  };
  const highlighted = context && selected && context.content
    ? highlightedSource(context.content, selected, context.spans ?? [], expandedSource)
    : null;

  return <>
    <Collapse
      className="gm-chat-citations"
      size="small"
      items={[{
        key: "graph-audit",
        label: <Space size={6}><Text strong>GraphRAG shadow 诊断</Text><Tag color="blue">不改变回答</Tag></Space>,
        children: <Space direction="vertical" size={8} style={{ width: "100%" }}>
          <Text type="secondary">
            seed {audit.seed_count} · 节点 {audit.expanded_node_count} · 边 {audit.expanded_edge_count} · 路径 {audit.path_count} · {audit.latency_ms.toFixed(1)} ms
          </Text>
          <Text type="secondary">
            候选 {audit.candidate_path_count ?? 0} · 发出 {audit.emitted_path_count ?? audit.path_count} · 预算丢弃 {audit.dropped_path_count ?? 0}{droppedReasons ? `（${droppedReasons}）` : ""}
          </Text>
          {audit.fallback && <Alert type="warning" showIcon message={fallbackLabel(audit.fallback_reason)} />}
          {audit.truncated && <Alert type="info" showIcon message={`图扩展已截断：${audit.truncation_reason ?? "达到上限"}。`} />}
          {!paths.length ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="本次没有可展示的图路径" /> : <List
            size="small"
            dataSource={paths}
            renderItem={(path) => {
              const status = reviewStatusLabel(path.review_status);
              const nodes = path.nodes ?? [];
              const evidenceItems = path.evidence ?? [];
              const papers = nodes.filter((node) => node.kind === "paper").map((node) => node.label);
              return <List.Item actions={evidenceItems.slice(0, 2).map((evidence) => (
                <Button key={evidence.evidence_span_id} type="link" size="small" icon={<FileSearchOutlined />} onClick={() => void openEvidence(evidence)}>定位原文</Button>
              ))}>
                <List.Item.Meta
                  title={<Space size={6} wrap><Tag color={status.color}>{status.label}</Tag><Text>路径 {path.path_id.split(":").at(-1) ?? path.path_id}</Text></Space>}
                  description={<Space direction="vertical" size={2}>
                    <Text type="secondary">论文：{papers.length ? papers.join("、") : "未找到论文节点"}；证据 {evidenceItems.length} 条</Text>
                    {evidenceItems.slice(0, 2).map((evidence) => <Paragraph key={evidence.evidence_span_id} ellipsis={{ rows: 2 }} style={{ margin: 0 }}><Tag color="blue">相关性 {Math.round(evidence.query_relevance_score * 100)}%</Tag>{evidence.excerpt || "证据片段为空"}</Paragraph>)}
                  </Space>}
                />
              </List.Item>;
            }}
          />}
        </Space>,
      }]}
    />
    <Drawer title="GraphRAG 证据原文" width="min(820px, 100vw)" open={open} onClose={() => setOpen(false)}>
      {loading ? <div className="gm-chat-source-loading"><Spin /></div> : !context || !selected ? <Empty description="未加载证据" /> : !context.content ? <Alert type="warning" showIcon message="暂时无法定位原文" description="原文文件不可用" /> : <>
        <Space wrap style={{ marginBottom: 12 }}><Tag color="blue">{selected.evidence_span_id}</Tag><Text type="secondary">字符 {selected.start_char ?? "—"}–{selected.end_char ?? "—"}</Text>{highlighted?.canExpand && <Button type="link" size="small" onClick={() => setExpandedSource((value) => !value)}>{expandedSource ? "收起全文" : "展开全文"}</Button>}</Space>
        <Alert type="info" showIcon message={`${expandedSource ? "已展开完整原文" : "默认显示证据位置附近的上下文"}；黄色部分是图路径回检到的 EvidenceSpan；候选关联不等于人工确认事实。`} style={{ marginBottom: 12 }} />
        {!highlighted?.valid && <Alert type="warning" showIcon message="证据定位未通过校验，已取消黄色高亮。" description="EvidenceSpan 的文本或字符范围与当前 artifact 不一致，不能可靠地把该段原文当作定位结果。" style={{ marginBottom: 12 }} />}
        <pre className="gm-chat-source-text">{highlighted?.node ?? context.content}</pre>
      </>}
    </Drawer>
  </>;
}
