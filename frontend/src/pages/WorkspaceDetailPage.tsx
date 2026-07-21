import { useCallback, useEffect, useState } from "react";
import {
  App,
  Button,
  Card,
  Descriptions,
  Form,
  Input,
  Modal,
  Space,
  Spin,
  Tag,
  Typography,
} from "antd";
import { ArrowLeftOutlined, EditOutlined, ReloadOutlined } from "@ant-design/icons";
import { Link, useParams } from "react-router-dom";
import workspaceApi from "../api/workspace";
import paperApi from "../api/paper";
import taskApi from "../api/task";
import timelineApi from "../api/timeline";
import type { Paper, Task, TimelineEvent } from "../api/types/domain";
import type { Workspace } from "../api/types/workspace";
import PapersSection from "../components/PapersSection";
import TasksSection from "../components/TasksSection";
import TimelineSection from "../components/TimelineSection";

const { Title, Paragraph } = Typography;
const { TextArea } = Input;

interface EditFormValues {
  name: string;
  description?: string;
  topic?: string;
  keywords?: string;
  goals?: string;
  constraints?: string;
  active_questions?: string;
}

function toEditValues(ws: Workspace): EditFormValues {
  return {
    name: ws.name,
    description: ws.description ?? "",
    topic: ws.topic ?? "",
    goals: ws.goals ?? "",
    constraints: ws.constraints ?? "",
    keywords: ws.keywords.join(", "),
    active_questions: ws.active_questions.join("\n"),
  };
}

export default function WorkspaceDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { message } = App.useApp();
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [loading, setLoading] = useState(true);
  const [editOpen, setEditOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [form] = Form.useForm<EditFormValues>();

  const [papers, setPapers] = useState<Paper[]>([]);
  const [papersLoading, setPapersLoading] = useState(false);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [tasksLoading, setTasksLoading] = useState(false);
  const [timeline, setTimeline] = useState<TimelineEvent[]>([]);
  const [timelineLoading, setTimelineLoading] = useState(false);

  const loadWorkspace = useCallback(async () => {
    if (!id) return;
    try {
      const ws = await workspaceApi.get(id);
      setWorkspace(ws);
    } catch (err) {
      message.error(`Failed to load workspace: ${(err as Error).message}`);
    } finally {
      setLoading(false);
    }
  }, [id, message]);

  const loadPapers = useCallback(async () => {
    if (!id) return;
    setPapersLoading(true);
    try {
      const resp = await paperApi.list(id, { limit: 100 });
      setPapers(resp.items);
    } catch {
      setPapers([]);
    } finally {
      setPapersLoading(false);
    }
  }, [id]);

  const loadTasks = useCallback(async () => {
    if (!id) return;
    setTasksLoading(true);
    try {
      const resp = await taskApi.list(id, { limit: 100 });
      setTasks(resp.items);
    } catch {
      setTasks([]);
    } finally {
      setTasksLoading(false);
    }
  }, [id]);

  const loadTimeline = useCallback(async () => {
    if (!id) return;
    setTimelineLoading(true);
    try {
      const resp = await timelineApi.list(id, { limit: 50 });
      setTimeline(resp.items);
    } catch {
      setTimeline([]);
    } finally {
      setTimelineLoading(false);
    }
  }, [id]);

  const loadAll = useCallback(() => {
    loadWorkspace();
    loadPapers();
    loadTasks();
    loadTimeline();
  }, [loadWorkspace, loadPapers, loadTasks, loadTimeline]);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  // Auto-refresh papers + tasks + timeline while any task is in an active
  // state. NOTE: `cancel_requested` is intentionally excluded - in MVP the
  // worker doesn't monitor cancel signals, so a task in `cancel_requested`
  // stays there forever and shouldn't keep polling alive. We treat it as
  // effectively terminal from the UI's perspective.
  //
  // We use a 1.5s interval so the user sees pending -> parsing -> parsed
  // transitions smoothly (a typical PDF parses in 2-5s).
  const hasActiveTask = tasks.some(
    (t) =>
      t.status === "queued" ||
      t.status === "running" ||
      t.status === "waiting_for_user"
  );

  useEffect(() => {
    if (!hasActiveTask) return;
    const timer = setInterval(() => {
      loadPapers();
      loadTasks();
      loadTimeline();
    }, 1500);
    return () => clearInterval(timer);
  }, [hasActiveTask, loadPapers, loadTasks, loadTimeline]);

  const handleEdit = async () => {
    if (!workspace) return;
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
          ? values.keywords.split(/[,\n]/).map((s) => s.trim()).filter(Boolean)
          : [],
        active_questions: values.active_questions
          ? values.active_questions.split(/\n/).map((s) => s.trim()).filter(Boolean)
          : [],
      };
      const updated = await workspaceApi.update(workspace.id, payload);
      setWorkspace(updated);
      message.success("Workspace updated");
      setEditOpen(false);
    } catch (err) {
      message.error(`Update failed: ${(err as Error).message}`);
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) {
    return (
      <div style={{ padding: 48, textAlign: "center" }}>
        <Spin />
      </div>
    );
  }

  if (!workspace) {
    return (
      <div>
        <Title level={4}>Workspace not found</Title>
        <Link to="/workspaces">← Back to workspaces</Link>
      </div>
    );
  }

  return (
    <div>
      <Space style={{ marginBottom: 16 }}>
        <Link to="/workspaces">
          <Button icon={<ArrowLeftOutlined />}>Back</Button>
        </Link>
        <Button
          type="primary"
          icon={<EditOutlined />}
          onClick={() => {
            form.setFieldsValue(toEditValues(workspace));
            setEditOpen(true);
          }}
        >
          Edit
        </Button>
        <Button icon={<ReloadOutlined />} onClick={loadAll}>
          Refresh
        </Button>
      </Space>

      <Title level={3} style={{ marginBottom: 4 }}>
        {workspace.name}
        {workspace.is_archived && (
          <Tag color="default" style={{ marginLeft: 12 }}>
            archived
          </Tag>
        )}
      </Title>
      <Paragraph type="secondary" style={{ marginBottom: 16 }}>
        {workspace.description || "No description."}
      </Paragraph>

      <Card title="Research Profile" style={{ marginBottom: 16 }}>
        <Descriptions column={1} bordered size="small">
          <Descriptions.Item label="Topic">
            {workspace.topic || "—"}
          </Descriptions.Item>
          <Descriptions.Item label="Keywords">
            <Space size={[4, 4]} wrap>
              {workspace.keywords.length > 0
                ? workspace.keywords.map((k: string) => <Tag key={k}>{k}</Tag>)
                : "—"}
            </Space>
          </Descriptions.Item>
          <Descriptions.Item label="Goals">
            {workspace.goals || "—"}
          </Descriptions.Item>
          <Descriptions.Item label="Constraints">
            {workspace.constraints || "—"}
          </Descriptions.Item>
          <Descriptions.Item label="Active Questions">
            {workspace.active_questions.length > 0 ? (
              <ul style={{ margin: 0, paddingLeft: 20 }}>
                {workspace.active_questions.map((q: string, i: number) => (
                  <li key={i}>{q}</li>
                ))}
              </ul>
            ) : (
              "—"
            )}
          </Descriptions.Item>
        </Descriptions>
      </Card>

      <div style={{ marginBottom: 16 }}>
        <PapersSection
          workspaceId={workspace.id}
          papers={papers}
          loading={papersLoading}
          onChanged={loadAll}
        />
      </div>

      <div style={{ marginBottom: 16 }}>
        <TasksSection
          tasks={tasks}
          loading={tasksLoading}
          onChanged={loadAll}
        />
      </div>

      <div style={{ marginBottom: 16 }}>
        <TimelineSection events={timeline} loading={timelineLoading} />
      </div>

      <Card title="Metadata">
        <Descriptions column={2} size="small">
          <Descriptions.Item label="Workspace ID">
            <Typography.Text code copyable>
              {workspace.id}
            </Typography.Text>
          </Descriptions.Item>
          <Descriptions.Item label="Created">
            {new Date(workspace.created_at).toLocaleString()}
          </Descriptions.Item>
          <Descriptions.Item label="Updated">
            {new Date(workspace.updated_at).toLocaleString()}
          </Descriptions.Item>
          <Descriptions.Item label="Status">
            {workspace.is_archived ? "Archived" : "Active"}
          </Descriptions.Item>
        </Descriptions>
      </Card>

      <Modal
        title="Edit Workspace"
        open={editOpen}
        onCancel={() => setEditOpen(false)}
        onOk={handleEdit}
        confirmLoading={submitting}
        okText="Save"
        cancelText="Cancel"
        width={640}
        destroyOnClose
      >
        <Form<EditFormValues> form={form} layout="vertical">
          <Form.Item
            name="name"
            label="Name"
            rules={[
              { required: true, message: "Please enter a name" },
              { max: 255 },
            ]}
          >
            <Input />
          </Form.Item>
          <Form.Item name="topic" label="Research Topic">
            <Input />
          </Form.Item>
          <Form.Item name="keywords" label="Keywords" extra="Comma separated">
            <Input />
          </Form.Item>
          <Form.Item name="goals" label="Goals">
            <TextArea rows={2} />
          </Form.Item>
          <Form.Item name="constraints" label="Constraints">
            <TextArea rows={2} />
          </Form.Item>
          <Form.Item
            name="active_questions"
            label="Active Questions"
            extra="One per line"
          >
            <TextArea rows={3} />
          </Form.Item>
          <Form.Item name="description" label="Description">
            <TextArea rows={2} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
