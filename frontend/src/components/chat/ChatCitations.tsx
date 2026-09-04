import { useMemo, useState, type ReactNode } from "react";
import { Alert, Button, Collapse, Drawer, Empty, List, Space, Spin, Tag, Typography, message } from "antd";
import { FileSearchOutlined } from "@ant-design/icons";
import chatApi, { type ChatEvidenceContext, type ChatMessageEvidence } from "../../api/chat";
import { buildEvidenceExcerpt, type EvidenceOffsetSpan } from "../evidenceExcerpt";

const { Paragraph, Text } = Typography;

// P0.5-2: keep the evidence list compact — show a few by default, the rest on demand.
const MAX_VISIBLE = 3;

function highlightedSource(context: ChatEvidenceContext, expanded: boolean): { node: ReactNode; canExpand: boolean } {
  const content = context.content ?? "";
  const evidence: EvidenceOffsetSpan = context.evidence;
  const excerpt = buildEvidenceExcerpt(content, [evidence], evidence);
  const viewContent = expanded ? content : excerpt.content;
  const viewSpan = expanded ? evidence : excerpt.spans[0];
  const start = viewSpan?.start_char;
  const end = viewSpan?.end_char;
  if (start == null || end == null || end <= start) {
    return { node: <span>{viewContent}</span>, canExpand: excerpt.is_excerpt };
  }
  return {
    node: <>
      {!expanded && excerpt.omitted_before && <span>… 前文已省略 …{"\n"}</span>}
      <span>{viewContent.slice(0, start)}</span>
      <mark>{viewContent.slice(start, end)}</mark>
      <span>{viewContent.slice(end)}</span>
      {!expanded && excerpt.omitted_after && <span>{"\n"}… 后文已省略 …</span>}
    </>,
    canExpand: excerpt.is_excerpt,
  };
}

export default function ChatCitations({ conversationId, messageId, citations }: { conversationId: string; messageId: string; citations: ChatMessageEvidence[] }) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [context, setContext] = useState<ChatEvidenceContext | null>(null);
  const [expandedSource, setExpandedSource] = useState(false);
  // Per-citation expanded state so "展开" can toggle back to "收起".
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [showAll, setShowAll] = useState(false);
  const sorted = useMemo(() => [...citations].sort((a, b) => a.rank - b.rank), [citations]);

  const toggleExpand = (id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const openEvidence = async (citation: ChatMessageEvidence) => {
    setOpen(true);
    setLoading(true);
    setContext(null);
    setExpandedSource(false);
    try {
      setContext(await chatApi.getEvidenceContext(conversationId, messageId, citation.id));
    } catch (error) {
      message.error(`原文加载失败：${(error as Error).message}`);
    } finally {
      setLoading(false);
    }
  };

  if (!sorted.length) return null;
  const visible = showAll ? sorted : sorted.slice(0, MAX_VISIBLE);
  const highlighted = context?.content ? highlightedSource(context, expandedSource) : null;
  return <>
    <Collapse
      className="gm-chat-citations"
      size="small"
      items={[{
        key: "citations",
        label: <Text strong>检索证据（{sorted.length} 条）</Text>,
        children: <>
          <List
            size="small"
            dataSource={visible}
            renderItem={(citation) => <List.Item actions={[<Button key="source" type="link" size="small" icon={<FileSearchOutlined />} onClick={() => void openEvidence(citation)}>定位原文</Button>]}>
              <List.Item.Meta
                title={<Space size={6} wrap><Tag color="blue">E{citation.rank}</Tag><Text>{citation.paper_title ?? "未命名论文"}</Text>{citation.section && <Tag>{citation.section}</Tag>}</Space>}
                description={<Paragraph
                  ellipsis={{
                    rows: 2,
                    expandable: true,
                    expanded: expanded.has(citation.id),
                    onExpand: () => toggleExpand(citation.id),
                    symbol: (isExpanded: boolean) => (isExpanded ? "收起" : "展开"),
                  }}
                  style={{ margin: 0 }}
                >{citation.excerpt}</Paragraph>}
              />
            </List.Item>}
          />
          {!showAll && sorted.length > MAX_VISIBLE && <Button type="link" size="small" onClick={() => setShowAll(true)}>查看全部 {sorted.length} 条</Button>}
        </>,
      }]}
    />
    <Drawer title="论文证据原文" width="min(820px, 100vw)" open={open} onClose={() => setOpen(false)}>
      {loading ? <div className="gm-chat-source-loading"><Spin /></div> : !context ? <Empty description="未加载证据" /> : !context.available || !context.content ? <Alert type="warning" showIcon message="暂时无法定位原文" description={context.message ?? "原文文件不可用"} /> : <>
        <Space wrap style={{ marginBottom: 12 }}><Tag color="blue">E{context.evidence.rank}</Tag><Text strong>{context.evidence.paper_title ?? "未命名论文"}</Text>{context.evidence.section && <Tag>{context.evidence.section}</Tag>}<Text type="secondary">字符 {context.evidence.start_char ?? "—"}–{context.evidence.end_char ?? "—"}</Text>{highlighted?.canExpand && <Button type="link" size="small" onClick={() => setExpandedSource((value) => !value)}>{expandedSource ? "收起全文" : "展开全文"}</Button>}</Space>
        <Alert type="info" showIcon message={expandedSource ? "已展开完整原文；黄色部分是本次回答检索到的证据分块。" : "默认显示证据位置附近的上下文；黄色部分是本次回答检索到的证据分块。"} style={{ marginBottom: 12 }} />
        <pre className="gm-chat-source-text">{highlighted?.node ?? context.content}</pre>
      </>}
    </Drawer>
  </>;
}
