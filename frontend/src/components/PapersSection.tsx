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
} from "@ant-design/icons";
import type { UploadRequestOption } from "rc-upload/lib/interface";
import paperApi from "../api/paper";
import type { Paper, PaperUpdate } from "../api/types/domain";

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
    authors: p.authors.join(", "),
    year: p.year ?? undefined,
    abstract: p.abstract ?? "",
    doi: p.doi ?? "",
    arxiv_id: p.arxiv_id ?? "",
  };
}

export default function PapersSection({ workspaceId, papers, loading, onChanged }: Props) {
  const { message, modal } = App.useApp();
  const [manualOpen, setManualOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [editingPaper, setEditingPaper] = useState<Paper | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [manualForm] = Form.useForm<ManualFormValues>();
  const [editForm] = Form.useForm<EditFormValues>();

  // ---------- upload (new paper with PDF) ----------
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
      message.success(`Uploaded "${paper.title}"`);
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

  // ---------- attach PDF to existing paper ----------
  const handleAttachPdf = (paper: Paper) => {
    // Triggered by clicking the "Upload PDF" action on a metadata-only paper.
    // We open a hidden file input via a ref-less pattern: antd Upload with
    // customRequest but no visible button - here we reuse a transient Upload.
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

  // ---------- manual create ----------
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

  // ---------- edit ----------
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

  // ---------- delete ----------
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

  return (
    <Card
      title="Papers"
      extra={
        <Space>
          <Upload {...uploadProps}>
            <Button icon={<InboxOutlined />} loading={submitting}>
              Upload PDF
            </Button>
          </Upload>
          <Button icon={<PlusOutlined />} onClick={() => setManualOpen(true)}>
            Add Manually
          </Button>
        </Space>
      }
    >
      {papers.length === 0 && !loading ? (
        <Empty description="No papers yet. Upload a PDF or add one manually." />
      ) : (
        <Table<Paper>
          rowKey="id"
          dataSource={papers}
          loading={loading}
          pagination={{ pageSize: 10, showSizeChanger: false }}
          columns={[
            {
              title: "Title",
              dataIndex: "title",
              key: "title",
              render: (v: string) => <Text strong>{v}</Text>,
            },
            {
              title: "Authors",
              dataIndex: "authors",
              key: "authors",
              render: (a: string[]) =>
                a.length > 0 ? (
                  <Text type="secondary">
                    {a.slice(0, 3).join("; ")}
                    {a.length > 3 ? `; +${a.length - 3} more` : ""}
                  </Text>
                ) : (
                  <Text type="secondary" italic>empty</Text>
                ),
            },
            {
              title: "Year",
              dataIndex: "year",
              key: "year",
              width: 80,
              render: (y: number | null) => y ?? <Text type="secondary">—</Text>,
            },
            {
              title: "PDF",
              key: "pdf",
              width: 80,
              render: (_: unknown, p) =>
                p.primary_artifact_id ? <Tag color="green">yes</Tag> : <Tag>no</Tag>,
            },
            {
              title: "Parse",
              key: "parse",
              width: 110,
              render: (_: unknown, p) => {
                const status = p.parse_status as string;
                const colorMap: Record<string, string> = {
                  not_applicable: "default",
                  pending: "default",
                  parsing: "processing",
                  parsed: "success",
                  failed: "error",
                };
                const labelMap: Record<string, string> = {
                  not_applicable: "—",
                  pending: "pending",
                  parsing: "parsing",
                  parsed: `parsed (${p.chunk_count})`,
                  failed: "failed",
                };
                return (
                  <Tag color={colorMap[status] ?? "default"}>
                    {labelMap[status] ?? status}
                  </Tag>
                );
              },
            },
            {
              title: "Source",
              dataIndex: "source",
              key: "source",
              width: 100,
              render: (s: string) => <Tag>{s}</Tag>,
            },
            {
              title: "Actions",
              key: "actions",
              width: 160,
              render: (_: unknown, p) => (
                <Space size={4}>
                  <Button
                    size="small"
                    icon={<EditOutlined />}
                    onClick={() => openEdit(p)}
                    title="Edit metadata"
                  />
                  {!p.primary_artifact_id && (
                    <Button
                      size="small"
                      icon={<PaperClipOutlined />}
                      onClick={() => handleAttachPdf(p)}
                      title="Attach PDF"
                      loading={submitting}
                    />
                  )}
                  <Button
                    size="small"
                    danger
                    icon={<DeleteOutlined />}
                    onClick={() => handleDelete(p)}
                    title="Delete"
                  />
                </Space>
              ),
            },
          ]}
        />
      )}

      {/* Manual create modal */}
      <Modal
        title="Add Paper Manually"
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
        title="Edit Paper"
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
