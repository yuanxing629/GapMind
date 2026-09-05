import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  App,
  Button,
  Card,
  Empty,
  Form,
  Input,
  InputNumber,
  List,
  Select,
  Space,
  Spin,
  Tag,
  Typography,
} from "antd";
import {
  ArrowLeftOutlined,
  DeleteOutlined,
  FilePdfOutlined,
  HighlightOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  RobotOutlined,
  SaveOutlined,
  UploadOutlined,
} from "@ant-design/icons";
import { useNavigate, useParams } from "react-router-dom";
import PageHeader from "../components/common/PageHeader";
import { readingLibraryPath } from "../components/layout/navigation";
import paperApi from "../api/paper";
import readingApi, {
  paperArtifactViewUrl,
  type PaperAnnotation,
  type ReadingPaper,
  type ReadingStatus,
} from "../api/reading";

const { Paragraph, Text, Title } = Typography;
const { TextArea } = Input;

const STATUS_OPTIONS = [
  { value: "unread", label: "未开始" },
  { value: "reading", label: "阅读中" },
  { value: "completed", label: "已读完" },
];

function errorMessage(error: unknown): string {
  const detail = (error as { response?: { data?: { detail?: { message?: string } } } }).response?.data?.detail;
  return detail?.message || (error as Error).message || "操作失败";
}

export default function ReadingPaperPage() {
  const { paperId } = useParams<{ paperId: string }>();
  const navigate = useNavigate();
  const { message } = App.useApp();
  const [paper, setPaper] = useState<ReadingPaper | null>(null);
  const [annotations, setAnnotations] = useState<PaperAnnotation[]>([]);
  const [page, setPage] = useState(1);
  const [readingStatus, setReadingStatus] = useState<ReadingStatus>("unread");
  const [loading, setLoading] = useState(true);
  const [savingProgress, setSavingProgress] = useState(false);
  const [savingAnnotation, setSavingAnnotation] = useState(false);
  const [attachingPdf, setAttachingPdf] = useState(false);
  const [noteContent, setNoteContent] = useState("");
  const [selectedText, setSelectedText] = useState("");
  const [notePage, setNotePage] = useState(1);
  const [noteKind, setNoteKind] = useState<"note" | "highlight" | "underline">("note");
  const [annotationsOpen, setAnnotationsOpen] = useState(false);

  const load = useCallback(async () => {
    if (!paperId) return;
    setLoading(true);
    try {
      const paperResponse = await readingApi.ensure(paperId);
      const annotationResponse = await readingApi.listAnnotations(paperId);
      setPaper(paperResponse);
      setAnnotations(annotationResponse);
      setPage(paperResponse.last_read_page || 1);
      setNotePage(paperResponse.last_read_page || 1);
      setReadingStatus(paperResponse.reading_status);
    } catch (error) {
      message.error(`论文加载失败：${errorMessage(error)}`);
    } finally {
      setLoading(false);
    }
  }, [message, paperId]);

  useEffect(() => {
    void load();
  }, [load]);

  const viewerUrl = useMemo(() => {
    if (!paper?.primary_artifact_id) return "";
    return paperArtifactViewUrl(paper.workspace_id, paper.primary_artifact_id, page);
  }, [page, paper]);

  const saveProgress = async () => {
    if (!paper) return;
    setSavingProgress(true);
    try {
      const updated = await readingApi.updateProgress(paper.paper_id, {
        page_number: Math.max(1, page),
        status: readingStatus,
      });
      setPaper(updated);
      message.success("阅读进度已保存");
    } catch (error) {
      message.error(`进度保存失败：${errorMessage(error)}`);
    } finally {
      setSavingProgress(false);
    }
  };

  const saveAnnotation = async (draft?: {
    kind?: "note" | "highlight" | "underline";
    noteContent?: string;
  }) => {
    const nextKind = draft?.kind ?? noteKind;
    const nextNoteContent = (draft?.noteContent ?? noteContent).trim();
    if (!paper || !nextNoteContent) {
      message.warning("请先写下批注内容");
      return;
    }
    setSavingAnnotation(true);
    try {
      const annotation = await readingApi.createAnnotation(paper.paper_id, {
        kind: nextKind,
        page_number: Math.max(1, notePage),
        selected_text: selectedText.trim() || undefined,
        note_content: nextNoteContent,
        color: nextKind === "underline" ? "#f59e0b" : nextKind === "highlight" ? "#facc15" : "#60a5fa",
      });
      setAnnotations((current) => [...current, annotation].sort((a, b) => a.page_number - b.page_number));
      setNoteContent("");
      setSelectedText("");
      message.success("批注已保存");
    } catch (error) {
      message.error(`批注保存失败：${errorMessage(error)}`);
    } finally {
      setSavingAnnotation(false);
    }
  };

  const removeAnnotation = async (annotation: PaperAnnotation) => {
    try {
      await readingApi.removeAnnotation(annotation.id);
      setAnnotations((current) => current.filter((item) => item.id !== annotation.id));
      message.success("批注已删除");
    } catch (error) {
      message.error(`批注删除失败：${errorMessage(error)}`);
    }
  };

  const askAI = (annotation?: PaperAnnotation) => {
    if (!paper) return;
    const context = annotation
      ? `第 ${annotation.page_number} 页\n原文摘录：${annotation.selected_text || "（未填写原文摘录）"}\n我的批注：${annotation.note_content}`
      : `第 ${page} 页\n我的问题：`;
    const prompt = `请基于论文《${paper.title}》回答下面的问题。如果信息不足，请明确说明。\n\n${context}`;
    navigate(`/workspaces/${paper.workspace_id}/assistant?prompt=${encodeURIComponent(prompt)}`);
  };

  const attachPdf = () => {
    if (!paper) return;
    const input = document.createElement("input");
    input.type = "file";
    input.accept = ".pdf";
    input.onchange = async () => {
      const file = input.files?.[0];
      if (!file) return;
      if (!file.name.toLowerCase().endsWith(".pdf")) {
        message.error("只能上传 PDF 文件");
        return;
      }
      setAttachingPdf(true);
      try {
        await paperApi.attachPdf(paper.workspace_id, paper.paper_id, {
          filename: file.name,
          content: file,
          mime_type: file.type || "application/pdf",
        });
        message.success("PDF 已上传，解析任务已启动");
        await load();
      } catch (error) {
        message.error(`PDF 上传失败：${errorMessage(error)}`);
      } finally {
        setAttachingPdf(false);
      }
    };
    input.click();
  };

  if (loading) return <div className="gm-loading"><Spin /></div>;
  if (!paper) return <Card><Empty description="找不到这篇阅读论文" /></Card>;

  return (
    <div className="gm-reader-page">
      <PageHeader
        eyebrow="论文阅读"
        title={paper.title}
        description={paper.authors.slice(0, 5).join(", ") || "作者信息暂缺"}
        extra={
          <Space wrap>
            <Button icon={<ArrowLeftOutlined />} onClick={() => navigate(readingLibraryPath(paper.workspace_id))}>返回阅读库</Button>
            <Button
              icon={annotationsOpen ? <MenuFoldOutlined /> : <MenuUnfoldOutlined />}
              aria-controls="paper-annotation-sidebar"
              aria-expanded={annotationsOpen}
              onClick={() => setAnnotationsOpen((open) => !open)}
            >
              {annotationsOpen ? "隐藏批注栏" : "显示批注栏"}
            </Button>
            {paper.doi && <Typography.Link href={`https://doi.org/${paper.doi}`} target="_blank" rel="noreferrer">DOI</Typography.Link>}
            {paper.arxiv_id && <Typography.Link href={`https://arxiv.org/abs/${paper.arxiv_id}`} target="_blank" rel="noreferrer">arXiv</Typography.Link>}
          </Space>
        }
      />

      <div className={`gm-reader-layout${annotationsOpen ? "" : " gm-reader-layout--annotations-hidden"}`}>
        <Card
          className="gm-reader-pdf"
          title={<span><FilePdfOutlined /> PDF 原文</span>}
          extra={
            <Space wrap>
              <InputNumber min={1} value={page} onChange={(value) => setPage(Math.max(1, value ?? 1))} addonBefore="页" />
              <Select value={readingStatus} options={STATUS_OPTIONS} onChange={setReadingStatus} />
              <Button icon={<SaveOutlined />} loading={savingProgress} onClick={() => void saveProgress()}>保存进度</Button>
            </Space>
          }
        >
          {paper.primary_artifact_id ? (
            <iframe key={viewerUrl} className="gm-reader-pdf-frame" src={viewerUrl} title={`阅读 ${paper.title}`} />
          ) : (
            <div className="gm-reader-no-pdf">
              <Empty image={<FilePdfOutlined style={{ fontSize: 48, color: "#9aa8ba" }} />} description="这篇论文还没有 PDF 原文" />
              <Button icon={<UploadOutlined />} type="primary" loading={attachingPdf} onClick={attachPdf}>上传 PDF 开始阅读</Button>
              <Paragraph type="secondary">也可以先通过 DOI 或 arXiv 链接打开外部原文。</Paragraph>
            </div>
          )}
          {paper.primary_artifact_id && paper.parse_status !== "parsed" && (
            <Alert
              type="info"
              showIcon
              message={`PDF 当前状态：${paper.parse_status === "pending" ? "等待解析" : paper.parse_status === "parsing" ? "解析中" : "尚未完成全文索引"}`}
              description="原文可以先阅读；解析完成后，这篇论文才会参与课题空间检索和有依据的 AI 回答。"
              style={{ marginTop: 12 }}
            />
          )}
        </Card>

        <div id="paper-annotation-sidebar" className="gm-reader-side">
          <Card title={<span><HighlightOutlined /> 添加批注</span>} size="small">
            <Form layout="vertical">
              <Form.Item label="页码">
                <InputNumber min={1} value={notePage} onChange={(value) => setNotePage(Math.max(1, value ?? 1))} style={{ width: "100%" }} />
              </Form.Item>
              <Form.Item label="类型">
                <Select value={noteKind} style={{ width: "100%" }} options={[{ value: "note", label: "普通批注" }, { value: "highlight", label: "重点内容" }, { value: "underline", label: "待核实内容" }]} onChange={setNoteKind} />
              </Form.Item>
              <Form.Item label="原文摘录" extra="可以从 PDF 中复制一段文字粘贴到这里">
                <TextArea rows={3} value={selectedText} onChange={(event) => setSelectedText(event.target.value)} placeholder="可选" />
              </Form.Item>
              <Form.Item label="我的批注" required>
                <TextArea rows={4} value={noteContent} onChange={(event) => setNoteContent(event.target.value)} placeholder="写下你的理解、疑问或待验证结论" />
              </Form.Item>
              <Button block type="primary" icon={<SaveOutlined />} loading={savingAnnotation} onClick={() => void saveAnnotation()}>保存批注</Button>
            </Form>
          </Card>

          <Card title={`我的批注（${annotations.length}）`} size="small" style={{ marginTop: 12 }}>
            {annotations.length === 0 ? (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有批注" />
            ) : (
              <List
                dataSource={annotations}
                renderItem={(annotation) => (
                  <List.Item
                    actions={[
                      <Button key="ask" type="link" size="small" icon={<RobotOutlined />} onClick={() => askAI(annotation)}>问 AI</Button>,
                      <Button key="delete" type="text" danger size="small" icon={<DeleteOutlined />} onClick={() => void removeAnnotation(annotation)} />,
                    ]}
                  >
                    <List.Item.Meta
                      avatar={<Tag color={annotation.kind === "highlight" ? "gold" : annotation.kind === "underline" ? "orange" : "blue"}>P{annotation.page_number}</Tag>}
                      title={annotation.note_content}
                      description={annotation.selected_text ? <Text type="secondary" ellipsis={{ tooltip: annotation.selected_text }}>{annotation.selected_text}</Text> : "未记录原文摘录"}
                    />
                  </List.Item>
                )}
              />
            )}
          </Card>

          <Card size="small" style={{ marginTop: 12 }}>
            <Title level={5} style={{ marginTop: 0 }}>下一步</Title>
            <Paragraph type="secondary" style={{ marginBottom: 8 }}>可以把当前页或某条批注直接带入课题空间 AI 助手。</Paragraph>
            <Button block icon={<RobotOutlined />} onClick={() => askAI()}>询问这篇论文</Button>
          </Card>
        </div>
      </div>
    </div>
  );
}
