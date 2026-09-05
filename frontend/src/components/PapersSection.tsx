import { useState } from "react";
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
  Tooltip,
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
} from "@ant-design/icons";
import type { UploadRequestOption } from "rc-upload/lib/interface";
import { useNavigate } from "react-router-dom";
import paperApi from "../api/paper";
import readingApi from "../api/reading";
import type { Paper, PaperUpdate } from "../api/types/domain";
import StatusBadge from "./common/StatusBadge";
import { readingPaperPath } from "./layout/navigation";

const { Text } = Typography;
const { TextArea } = Input;

interface Props {
  workspaceId: string;
  papers: Paper[];
  loading: boolean;
  onChanged: () => void;
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

export default function PapersSection({ workspaceId, papers, loading, onChanged }: Props) {
  const { message, modal } = App.useApp();
  const navigate = useNavigate();
  const [manualOpen, setManualOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [editingPaper, setEditingPaper] = useState<Paper | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [openingPaperId, setOpeningPaperId] = useState<string | null>(null);
  const [manualForm] = Form.useForm<ManualFormValues>();
  const [editForm] = Form.useForm<EditFormValues>();

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
      onChanged();
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
        onChanged();
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
      onChanged();
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
      onChanged();
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
          onChanged();
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
      const readingPaper = await readingApi.add(paper.id);
      navigate(readingPaperPath(readingPaper.paper_id));
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: { message?: string } } } }).response?.data?.detail;
      message.error(`打开论文失败：${detail?.message || (err as Error).message}`);
    } finally {
      setOpeningPaperId(null);
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
          pagination={{ pageSize: 10, showSizeChanger: false }}
          scroll={{ x: 820 }}
          columns={[
            {
              title: "标题",
              dataIndex: "title",
              key: "title",
              render: (v: string) => <Text strong>{v}</Text>,
            },
            {
              title: "作者",
              dataIndex: "authors",
              key: "authors",
              render: (a: string[]) =>
                a.length > 0 ? (
                  <Text type="secondary">
                    {a.slice(0, 3).join("; ")}
                    {a.length > 3 ? `; +${a.length - 3} more` : ""}
                  </Text>
                ) : (
                  <Text type="secondary" italic>未填写</Text>
                ),
            },
            {
              title: "年份",
              dataIndex: "year",
              key: "year",
              width: 80,
              render: (y: number | null) => y ?? <Text type="secondary">—</Text>,
            },
            {
              title: "全文",
              key: "pdf",
              width: 80,
              render: (_: unknown, p) =>
                p.primary_artifact_id ? <Tag color="green">已上传</Tag> : <Tag>缺失</Tag>,
            },
            {
              title: "解析",
              key: "parse",
              width: 210,
              render: (_: unknown, p) => {
                const status = p.parse_status as string;
                const flags = p.quality_flags ?? [];
                const detail = status === "failed"
                  ? p.parse_error || "解析失败，请重试或更换文件"
                  : status === "parsed"
                    ? `${p.page_count ?? 0} 页 · ${p.parsed_text_chars ?? 0} 字符 · ${p.chunk_count ?? 0} 块${flags.length ? ` · ${flags.length} 个提示` : ""}`
                    : "解析完成后才会进入证据检索和知识抽取";
                return (
                  <Tooltip title={detail}>
                    <Space size={4}>
                      <StatusBadge status={status} />
                      {status === "parsed" && <Text type="secondary">{p.chunk_count} 块</Text>}
                      {flags.length > 0 && <Tag color="gold">{flags.length} 个提示</Tag>}
                    </Space>
                  </Tooltip>
                );
              },
            },
            {
              title: "来源",
              dataIndex: "source",
              key: "source",
              width: 100,
              render: (s: string) => <Tag>{s}</Tag>,
            },
            {
              title: "操作",
              key: "actions",
              width: 220,
              render: (_: unknown, p) => (
                <Space size={4}>
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
                </Space>
              ),
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
