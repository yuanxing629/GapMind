import { useEffect, useState } from "react";
import {
  App,
  Button,
  Card,
  Empty,
  Form,
  Input,
  Modal,
  Space,
  Table,
  Tag,
  Typography,
  Upload,
  type UploadProps,
} from "antd";
import {
  DeleteOutlined,
  EditOutlined,
  InboxOutlined,
  PaperClipOutlined,
  PlusOutlined,
  ReadOutlined,
  ReloadOutlined,
} from "@ant-design/icons";
import type { UploadRequestOption } from "rc-upload/lib/interface";
import { useNavigate } from "react-router-dom";
import paperApi from "../api/paper";
import readingApi, { type ReadingPaper, type ReadingStatus } from "../api/reading";
import type { Paper, PaperUpdate, Task } from "../api/types/domain";
import { readingPaperPath } from "./layout/navigation";
import {
  isActivePaperTask,
  latestPaperTask,
  paperPipelineStatus,
  pipelineActionLabel,
  type PaperPipelineStatus,
} from "../state/paperProcessing";

const { Paragraph } = Typography;
const { TextArea } = Input;

const READING_STATUS_META: Record<ReadingStatus, { label: string; color: string }> = {
  unread: { label: "未开始", color: "default" },
  reading: { label: "阅读中", color: "processing" },
  completed: { label: "已读完", color: "success" },
};

interface Props {
  workspaceId: string;
  papers: Paper[];
  tasks: Task[];
  tasksAvailable: boolean;
  loading: boolean;
  onChanged: () => void | Promise<void>;
}

interface ManualFormValues {
  title: string;
  authors?: string;
  year?: number;
  abstract?: string;
  doi?: string;
  arxiv_id?: string;
}

interface EditFormValues {
  title: string;
  authors?: string;
  year?: number;
  abstract?: string;
  doi?: string;
  arxiv_id?: string;
}

function toEditValues(p: Paper): EditFormValues {
  return {
    title: p.title,
    authors: (p.authors ?? []).join(", "),
    year: p.year ?? undefined,
    abstract: p.abstract ?? "",
    doi: p.doi ?? "",
    arxiv_id: p.arxiv_id ?? "",
  };
}

export default function PapersSection({ workspaceId, papers, tasks, tasksAvailable, loading, onChanged }: Props) {
  const { message, modal } = App.useApp();
  const navigate = useNavigate();
  const [manualOpen, setManualOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [editingPaper, setEditingPaper] = useState<Paper | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [openingPaperId, setOpeningPaperId] = useState<string | null>(null);
  const [processingKey, setProcessingKey] = useState<string | null>(null);
  const [readingByPaper, setReadingByPaper] = useState<Map<string, Pick<ReadingPaper, "reading_status" | "last_read_page">>>(new Map());
  const [manualForm] = Form.useForm<ManualFormValues>();
  const [editForm] = Form.useForm<EditFormValues>();

  useEffect(() => {
    let cancelled = false;
    setReadingByPaper(new Map());
    void readingApi.list({ workspace_id: workspaceId, limit: 100 })
      .then((result) => {
        if (cancelled) return;
        setReadingByPaper(new Map(
          result.items.map((item) => [item.paper_id, {
            reading_status: item.reading_status,
            last_read_page: item.last_read_page,
          }]),
        ));
      })
      .catch(() => {
        if (!cancelled) setReadingByPaper(new Map());
      });
    return () => {
      cancelled = true;
    };
  }, [workspaceId]);

  // ---------- 上传（带 PDF 的新论文） ----------
  const handleUpload = async (req: UploadRequestOption) => {
    const file = req.file as File;
    if (!file.name.toLowerCase().endsWith(".pdf")) {
      message.error("Only .pdf files are accepted");
      req.onError?.(new Error("invalid file"));
      return;
    }
    setSubmitting(true);
    try {
      const paper = await paperApi.upload(workspaceId, {
        filename: file.name,
        content: file,
        mime_type: file.type || "application/pdf",
      });
      message.success(`已上传“${paper.title}”，解析任务已排队；请查看解析列的质量反馈`);
      await onChanged();
    } catch (err) {
      const msg = (err as { response?: { data?: { detail?: { message?: string } } } }).response?.data?.detail?.message
        || (err as Error).message;
      message.error(`Upload failed: ${msg}`);
      req.onError?.(new Error(msg));
    } finally {
      setSubmitting(false);
    }
  };

  const uploadProps: UploadProps = {
    customRequest: handleUpload,
    showUploadList: false,
    accept: ".pdf",
    multiple: false,
  };

  // ---------- 为已有论文附加 PDF ----------
  const handleAttachPdf = (paper: Paper) => {
    // 点击仅元数据论文的“Upload PDF”操作后触发。
    // 使用无 ref 模式打开隐藏文件输入：通过 customRequest 配置 antd Upload，
    // 不显示按钮，这里复用临时 Upload。
    const input = document.createElement("input");
    input.type = "file";
    input.accept = ".pdf";
    input.onchange = async () => {
      const file = input.files?.[0];
      if (!file) return;
      if (!file.name.toLowerCase().endsWith(".pdf")) {
        message.error("Only .pdf files are accepted");
        return;
      }
      setSubmitting(true);
      try {
        const updated = await paperApi.attachPdf(workspaceId, paper.id, {
          filename: file.name,
          content: file,
          mime_type: file.type || "application/pdf",
        });
        message.success(`PDF attached to "${updated.title}"`);
        await onChanged();
      } catch (err) {
        const detail = (err as { response?: { data?: { detail?: { message?: string; error?: string } } } }).response?.data?.detail;
        if (detail?.error === "paper_already_has_pdf") {
          message.warning("This paper already has a PDF.");
        } else {
          message.error(`Attach failed: ${detail?.message || (err as Error).message}`);
        }
      } finally {
        setSubmitting(false);
      }
    };
    input.click();
  };

  // ---------- 手动创建 ----------
  const handleManualCreate = async () => {
    const values = await manualForm.validateFields();
    setSubmitting(true);
    try {
      await paperApi.create(workspaceId, {
        title: values.title,
        authors: values.authors
          ? values.authors.split(/[,\n]/).map((s) => s.trim()).filter(Boolean)
          : [],
        year: values.year,
        abstract: values.abstract,
        doi: values.doi,
        arxiv_id: values.arxiv_id,
      });
      message.success("Paper created");
      setManualOpen(false);
      manualForm.resetFields();
      await onChanged();
    } catch (err) {
      message.error(`Create failed: ${(err as Error).message}`);
    } finally {
      setSubmitting(false);
    }
  };

  // ---------- 编辑 ----------
  const openEdit = (paper: Paper) => {
    setEditingPaper(paper);
    editForm.setFieldsValue(toEditValues(paper));
    setEditOpen(true);
  };

  const handleEditSave = async () => {
    if (!editingPaper) return;
    const values = await editForm.validateFields();
    setSubmitting(true);
    try {
      const payload: PaperUpdate = {
        title: values.title,
        authors: values.authors
          ? values.authors.split(/[,\n]/).map((s) => s.trim()).filter(Boolean)
          : [],
        year: values.year || undefined,
        abstract: values.abstract || undefined,
        doi: values.doi || undefined,
        arxiv_id: values.arxiv_id || undefined,
      };
      await paperApi.update(workspaceId, editingPaper.id, payload);
      message.success("Paper updated");
      setEditOpen(false);
      setEditingPaper(null);
      await onChanged();
    } catch (err) {
      message.error(`Update failed: ${(err as Error).message}`);
    } finally {
      setSubmitting(false);
    }
  };

  // ---------- 删除 ----------
  const handleDelete = (paper: Paper) => {
    modal.confirm({
      title: `Delete paper "${paper.title}"?`,
      content: "Soft delete - the row stays for audit but disappears from lists.",
      okText: "Delete",
      okType: "danger",
      cancelText: "Cancel",
      onOk: async () => {
        try {
          await paperApi.remove(workspaceId, paper.id);
          message.success("Paper deleted");
          await onChanged();
        } catch (err) {
          message.error(`Delete failed: ${(err as Error).message}`);
        }
      },
    });
  };

  const openPaper = async (paper: Paper) => {
    if (openingPaperId) return;
    setOpeningPaperId(paper.id);
    try {
      const readingPaper = await readingApi.ensureReady(paper.id);
      navigate(readingPaperPath(readingPaper.paper_id));
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: { message?: string } } } }).response?.data?.detail;
      message.error(`打开论文失败：${detail?.message || (err as Error).message}`);
    } finally {
      setOpeningPaperId(null);
    }
  };

  const triggerProcessing = async (
    paper: Paper,
    kind: "parse" | "index" | "knowledge",
  ) => {
    if (!tasksAvailable) {
      message.warning("暂时无法确认后台任务状态，请刷新后重试");
      return;
    }
    const key = `${paper.id}:${kind}`;
    setProcessingKey(key);
    try {
      if (kind === "parse") await paperApi.parse(workspaceId, paper.id);
      if (kind === "index") await paperApi.index(workspaceId, paper.id);
      if (kind === "knowledge") await paperApi.extract(workspaceId, paper.id);
      message.success(`${pipelineActionLabel(kind, "pending")}任务已提交`);
      await onChanged();
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: { message?: string } } } }).response?.data?.detail;
      message.error(`${pipelineActionLabel(kind, "pending")}失败：${detail?.message || (err as Error).message}`);
    } finally {
      setProcessingKey(null);
    }
  };

  return (
    <Card
      title="文献"
      extra={
        <Space>
          <Upload {...uploadProps}>
            <Button icon={<InboxOutlined />} loading={submitting}>
              上传 PDF
            </Button>
          </Upload>
          <Button icon={<PlusOutlined />} onClick={() => setManualOpen(true)}>
            手动添加
          </Button>
        </Space>
      }
    >
      {papers.length === 0 && !loading ? (
        <Empty description="还没有文献。可以搜索导入、上传 PDF 或手动添加。" />
      ) : (
        <Table<Paper>
          rowKey="id"
          dataSource={papers}
          loading={loading}
          pagination={false}
          scroll={{ x: 900 }}
          columns={[
            {
              title: "论文",
              key: "title",
              width: 360,
              render: (_: unknown, p) => {
                const authors = (p.authors ?? []).slice(0, 3).join(", ") || "作者信息暂缺";
                return (
                  <div>
                    <Typography.Link strong onClick={() => void openPaper(p)}>
                      {p.title}
                    </Typography.Link>
                    <Paragraph type="secondary" ellipsis={{ rows: 1 }} style={{ margin: "4px 0 0" }}>
                      {authors}
                    </Paragraph>
                  </div>
                );
              },
            },
            {
              title: "阅读状态",
              key: "reading_status",
              width: 110,
              render: (_: unknown, p) => {
                const status = readingByPaper.get(p.id)?.reading_status ?? "unread";
                const meta = READING_STATUS_META[status];
                return <Tag color={meta.color}>{meta.label}</Tag>;
              },
            },
            {
              title: "原文",
              key: "pdf",
              width: 260,
              render: (_: unknown, p) => {
                const states = {
                  parse: paperPipelineStatus(p, p.id, tasks, "parse"),
                  index: paperPipelineStatus(p, p.id, tasks, "index"),
                  knowledge: paperPipelineStatus(p, p.id, tasks, "knowledge"),
                } as const;
                return (
                  <Space direction="vertical" size={2}>
                    {p.primary_artifact_id ? <Tag color="green">PDF 可读</Tag> : <Tag>待上传</Tag>}
                    <Space wrap size={2}>
                      {(Object.entries(states) as [keyof typeof states, PaperPipelineStatus][]).map(([kind, state]) => (
                        <Tag key={kind} color={state === "succeeded" ? "success" : state === "failed" ? "error" : state === "running" ? "processing" : "default"}>
                          {kind === "parse" ? "解析" : kind === "index" ? "索引" : "知识"}：{state === "succeeded" ? "已完成" : state === "not_applicable" ? "待前置" : state === "running" ? "处理中" : state === "failed" ? "失败" : "待处理"}
                        </Tag>
                      ))}
                    </Space>
                  </Space>
                );
              },
            },
            {
              title: "进度",
              key: "progress",
              width: 100,
              render: (_: unknown, p) => `第 ${readingByPaper.get(p.id)?.last_read_page ?? 1} 页`,
            },
            {
              title: "操作",
              key: "actions",
              width: 440,
              render: (_: unknown, p) => {
                const states = {
                  parse: paperPipelineStatus(p, p.id, tasks, "parse"),
                  index: paperPipelineStatus(p, p.id, tasks, "index"),
                  knowledge: paperPipelineStatus(p, p.id, tasks, "knowledge"),
                } as const;
                return (
                <Space wrap size={4}>
                  <Button
                    size="small"
                    type="primary"
                    icon={<ReadOutlined />}
                    loading={openingPaperId === p.id}
                    onClick={() => void openPaper(p)}
                  >
                    阅读
                  </Button>
                  <Button
                    size="small"
                    icon={<EditOutlined />}
                    onClick={() => openEdit(p)}
                      title="编辑信息"
                  />
                  {!p.primary_artifact_id && (
                    <Button
                      size="small"
                      icon={<PaperClipOutlined />}
                      onClick={() => handleAttachPdf(p)}
                      title="上传 PDF"
                      loading={submitting}
                    />
                  )}
                  <Button
                    size="small"
                    danger
                    icon={<DeleteOutlined />}
                    onClick={() => handleDelete(p)}
                    title="删除"
                  />
                  {(["parse", "index", "knowledge"] as const).map((kind) => {
                    const state = states[kind];
                    if (state === "succeeded" || state === "not_applicable") return null;
                    const taskType = kind === "parse" ? "parse_pdf" : kind === "index" ? "embed_chunks" : "extract_knowledge";
                    const task = latestPaperTask(tasks, p.id, taskType);
                    const active = isActivePaperTask(task);
                    if (kind === "parse" && !p.primary_artifact_id) return null;
                    return (
                      <Button
                        key={kind}
                        size="small"
                        icon={<ReloadOutlined />}
                        disabled={!tasksAvailable || active}
                        loading={processingKey === `${p.id}:${kind}` || active}
                        onClick={() => void triggerProcessing(p, kind)}
                      >
                        {pipelineActionLabel(kind, state)}
                      </Button>
                    );
                  })}
                </Space>
                );
              },
            },
          ]}
        />
      )}

      {/* Manual create modal */}
      <Modal
        title="手动添加文献"
        open={manualOpen}
        onCancel={() => {
          setManualOpen(false);
          manualForm.resetFields();
        }}
        onOk={handleManualCreate}
        confirmLoading={submitting}
        okText="Create"
        cancelText="Cancel"
        width={600}
        destroyOnClose
      >
        <Form<ManualFormValues> form={manualForm} layout="vertical">
          <Form.Item
            name="title"
            label="Title"
            rules={[{ required: true, message: "Please enter a title" }]}
          >
            <Input />
          </Form.Item>
          <Form.Item name="authors" label="Authors" extra="Comma separated">
            <Input placeholder="Alice, Bob, Carol" />
          </Form.Item>
          <Form.Item name="year" label="Year">
            <Input type="number" />
          </Form.Item>
          <Form.Item name="abstract" label="Abstract">
            <TextArea rows={3} />
          </Form.Item>
          <Form.Item name="doi" label="DOI">
            <Input />
          </Form.Item>
          <Form.Item name="arxiv_id" label="arXiv ID">
            <Input />
          </Form.Item>
        </Form>
      </Modal>

      {/* Edit modal */}
      <Modal
        title="编辑文献"
        open={editOpen}
        onCancel={() => {
          setEditOpen(false);
          setEditingPaper(null);
        }}
        onOk={handleEditSave}
        confirmLoading={submitting}
        okText="Save"
        cancelText="Cancel"
        width={600}
        destroyOnClose
      >
        <Form<EditFormValues> form={editForm} layout="vertical">
          <Form.Item
            name="title"
            label="Title"
            rules={[{ required: true, message: "Please enter a title" }]}
          >
            <Input />
          </Form.Item>
          <Form.Item name="authors" label="Authors" extra="Comma separated">
            <Input />
          </Form.Item>
          <Form.Item name="year" label="Year">
            <Input type="number" />
          </Form.Item>
          <Form.Item name="abstract" label="Abstract">
            <TextArea rows={4} />
          </Form.Item>
          <Form.Item name="doi" label="DOI">
            <Input />
          </Form.Item>
          <Form.Item name="arxiv_id" label="arXiv ID">
            <Input />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
}
