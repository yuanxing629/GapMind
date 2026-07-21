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
  Switch,
  Table,
  Tag,
  Typography,
} from "antd";
import { Link, useNavigate } from "react-router-dom";
import {
  DeleteOutlined,
  InboxOutlined,
  PlusOutlined,
  ReloadOutlined,
} from "@ant-design/icons";
import workspaceApi from "../api/workspace";
import type { Workspace } from "../api/types/workspace";

const { Title, Paragraph } = Typography;
const { TextArea } = Input;

interface CreateFormValues {
  name: string;
  description?: string;
  topic?: string;
  keywords?: string;
  goals?: string;
  constraints?: string;
}

export default function WorkspacesPage() {
  const navigate = useNavigate();
  const { message, modal } = App.useApp();
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [loading, setLoading] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [showArchived, setShowArchived] = useState(false);
  const [form] = Form.useForm<CreateFormValues>();

  const load = async () => {
    setLoading(true);
    try {
      const resp = await workspaceApi.list({ limit: 200, include_archived: showArchived });
      setWorkspaces(resp.items);
    } catch (err) {
      message.error(`Failed to load workspaces: ${(err as Error).message}`);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, [showArchived]);

  const handleCreate = async () => {
    const values = await form.validateFields();
    setSubmitting(true);
    try {
      const payload = {
        name: values.name,
        description: values.description || undefined,
        topic: values.topic || undefined,
        goals: values.goals || undefined,
        constraints: values.constraints || undefined,
        keywords: values.keywords
          ? values.keywords
              .split(/[,\n]/)
              .map((s) => s.trim())
              .filter(Boolean)
          : [],
      };
      const ws = await workspaceApi.create(payload);
      message.success(`Workspace "${ws.name}" created`);
      setCreateOpen(false);
      form.resetFields();
      navigate(`/workspaces/${ws.id}`);
    } catch (err) {
      message.error(`Failed to create workspace: ${(err as Error).message}`);
    } finally {
      setSubmitting(false);
    }
  };

  const handleArchive = async (ws: Workspace) => {
    try {
      const updated = await workspaceApi.archive(ws.id);
      message.success(`Archived "${updated.name}"`);
      load();
    } catch (err) {
      message.error(`Archive failed: ${(err as Error).message}`);
    }
  };

  const handleUnarchive = async (ws: Workspace) => {
    try {
      const updated = await workspaceApi.unarchive(ws.id);
      message.success(`Unarchived "${updated.name}"`);
      load();
    } catch (err) {
      message.error(`Unarchive failed: ${(err as Error).message}`);
    }
  };

  const handleDelete = (ws: Workspace) => {
    modal.confirm({
      title: `Delete workspace "${ws.name}"?`,
      content:
        "This is a soft delete - the workspace and all its data remain in the database for audit, but it will no longer appear in lists.",
      okText: "Delete",
      okType: "danger",
      cancelText: "Cancel",
      onOk: async () => {
        try {
          await workspaceApi.remove(ws.id);
          message.success(`Deleted "${ws.name}"`);
          load();
        } catch (err) {
          message.error(`Delete failed: ${(err as Error).message}`);
        }
      },
    });
  };

  return (
    <div>
      <Space
        style={{ width: "100%", justifyContent: "space-between", marginBottom: 16 }}
      >
        <div>
          <Title level={3} style={{ margin: 0 }}>
            Workspaces
          </Title>
          <Paragraph type="secondary" style={{ margin: 0 }}>
            A Workspace is the scope for one research thread - its topic, papers,
            knowledge graph, opportunities, and timeline.
          </Paragraph>
        </div>
        <Space>
          <Space>
            <Switch
              checked={showArchived}
              onChange={setShowArchived}
              checkedChildren="Archived"
              unCheckedChildren="Active"
            />
            <Typography.Text type="secondary">
              {showArchived ? "showing archived" : "hiding archived"}
            </Typography.Text>
          </Space>
          <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>
            Refresh
          </Button>
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={() => setCreateOpen(true)}
          >
            New Workspace
          </Button>
        </Space>
      </Space>

      <Card>
        {workspaces.length === 0 && !loading ? (
          <Empty description="No workspaces yet. Create your first one to get started.">
            <Button
              type="primary"
              icon={<PlusOutlined />}
              onClick={() => setCreateOpen(true)}
            >
              New Workspace
            </Button>
          </Empty>
        ) : (
          <Table<Workspace>
            rowKey="id"
            dataSource={workspaces}
            loading={loading}
            pagination={{ pageSize: 20, showSizeChanger: false }}
            columns={[
              {
                title: "Name",
                dataIndex: "name",
                key: "name",
                render: (name: string, ws) => (
                  <Link to={`/workspaces/${ws.id}`}>{name}</Link>
                ),
              },
              {
                title: "Topic",
                dataIndex: "topic",
                key: "topic",
                render: (v: string | null) =>
                  v ? <Typography.Text ellipsis>{v}</Typography.Text> : "—",
              },
              {
                title: "Keywords",
                dataIndex: "keywords",
                key: "keywords",
                render: (kws: string[]) => (
                  <Space size={[4, 4]} wrap>
                    {kws.slice(0, 4).map((k) => (
                      <Tag key={k}>{k}</Tag>
                    ))}
                    {kws.length > 4 && <Tag>+{kws.length - 4}</Tag>}
                    {kws.length === 0 && "—"}
                  </Space>
                ),
              },
              {
                title: "Status",
                key: "status",
                render: (_: unknown, ws) =>
                  ws.is_archived ? <Tag color="default">archived</Tag> : null,
                width: 100,
              },
              {
                title: "Created",
                dataIndex: "created_at",
                key: "created_at",
                width: 180,
                render: (v: string) =>
                  new Date(v).toLocaleString(undefined, {
                    year: "numeric",
                    month: "short",
                    day: "numeric",
                    hour: "2-digit",
                    minute: "2-digit",
                  }),
              },
              {
                title: "Actions",
                key: "actions",
                width: 180,
                render: (_: unknown, ws) => (
                  <Space>
                    {ws.is_archived ? (
                      <Button
                        size="small"
                        onClick={() => handleUnarchive(ws)}
                        title="Unarchive"
                      >
                        Unarchive
                      </Button>
                    ) : (
                      <Button
                        size="small"
                        icon={<InboxOutlined />}
                        onClick={() => handleArchive(ws)}
                        title="Archive"
                      />
                    )}
                    <Button
                      size="small"
                      danger
                      icon={<DeleteOutlined />}
                      onClick={() => handleDelete(ws)}
                      title="Delete"
                    />
                  </Space>
                ),
              },
            ]}
          />
        )}
      </Card>

      <Modal
        title="New Workspace"
        open={createOpen}
        onCancel={() => {
          setCreateOpen(false);
          form.resetFields();
        }}
        onOk={handleCreate}
        confirmLoading={submitting}
        okText="Create"
        cancelText="Cancel"
        width={640}
        destroyOnClose
      >
        <Form<CreateFormValues>
          form={form}
          layout="vertical"
          initialValues={{ keywords: "" }}
        >
          <Form.Item
            name="name"
            label="Name"
            rules={[
              { required: true, message: "Please enter a name" },
              { max: 255 },
            ]}
          >
            <Input placeholder="e.g. Self-Interpretable GNN" />
          </Form.Item>
          <Form.Item name="topic" label="Research Topic">
            <Input placeholder="e.g. Self-Interpretable Graph Neural Networks" />
          </Form.Item>
          <Form.Item
            name="keywords"
            label="Keywords"
            extra="Comma or newline separated"
          >
            <TextArea
              rows={2}
              placeholder={"GNN, explainability, interpretability"}
            />
          </Form.Item>
          <Form.Item name="goals" label="Goals">
            <TextArea
              rows={2}
              placeholder="What are you trying to achieve?"
            />
          </Form.Item>
          <Form.Item name="constraints" label="Constraints">
            <TextArea
              rows={2}
              placeholder="Compute budget, dataset limits, time, etc."
            />
          </Form.Item>
          <Form.Item name="description" label="Description">
            <TextArea
              rows={2}
              placeholder="Short description for this workspace"
            />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
