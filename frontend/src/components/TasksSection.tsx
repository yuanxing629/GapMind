import { Button, Card, Empty, Space, Table, Tag, Tooltip, Typography } from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import type { Task } from "../api/types/domain";
import taskApi from "../api/task";

const { Text } = Typography;

interface Props {
  tasks: Task[];
  loading: boolean;
  onChanged: () => void;
}

const STATUS_COLOR: Record<Task["status"], string> = {
  queued: "default",
  running: "processing",
  waiting_for_user: "warning",
  succeeded: "success",
  failed: "error",
  // cancel_requested is treated as terminal in the UI - MVP worker doesn't
  // monitor cancel signals, so this state never transitions to "cancelled".
  // Display it as cancelled (grey) to match user expectation. The raw status
  // is still in the DB for audit. See progress_and_roadmap.md for the
  // follow-up: implement real worker cancel monitoring in Phase 4.
  cancel_requested: "default",
  cancelled: "default",
};

// Map raw status to display label. cancel_requested is shown as "cancelled"
// since from the user's perspective the task is no longer active.
const STATUS_LABEL: Record<Task["status"], string> = {
  queued: "queued",
  running: "running",
  waiting_for_user: "waiting",
  succeeded: "succeeded",
  failed: "failed",
  cancel_requested: "cancelled",
  cancelled: "cancelled",
};

export default function TasksSection({ tasks, loading, onChanged }: Props) {
  const handleCancel = async (taskId: string) => {
    try {
      await taskApi.cancel(taskId);
      onChanged();
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: { message?: string } } } }).response?.data?.detail;
      window.alert(detail?.message || (err as Error).message);
    }
  };

  const handleRetry = async (taskId: string) => {
    try {
      await taskApi.retry(taskId);
      onChanged();
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: { message?: string } } } }).response?.data?.detail;
      window.alert(detail?.message || (err as Error).message);
    }
  };

  return (
    <Card
      title={
        <Space>
          <span>Tasks</span>
          <Tooltip title="Refresh">
            <Button size="small" icon={<ReloadOutlined />} onClick={onChanged} loading={loading} />
          </Tooltip>
        </Space>
      }
    >
      {tasks.length === 0 && !loading ? (
        <Empty description="No tasks yet. Tasks are created automatically when you upload papers (Phase 2) or run discovery (Phase 5)." />
      ) : (
        <Table<Task>
          rowKey="id"
          dataSource={tasks}
          loading={loading}
          pagination={{ pageSize: 10, showSizeChanger: false }}
          columns={[
            {
              title: "Type",
              dataIndex: "task_type",
              key: "task_type",
              render: (v: string) => <Text code>{v}</Text>,
            },
            {
              title: "Status",
              dataIndex: "status",
              key: "status",
              width: 140,
              render: (s: Task["status"]) => (
                <Tag color={STATUS_COLOR[s]}>{STATUS_LABEL[s]}</Tag>
              ),
            },
            {
              title: "Progress",
              dataIndex: "progress",
              key: "progress",
              width: 100,
              render: (p: number) => `${Math.round(p * 100)}%`,
            },
            {
              title: "Error",
              dataIndex: "error",
              key: "error",
              ellipsis: true,
              render: (e: string | null) =>
                e ? (
                  <Tooltip title={e}>
                    <Text type="danger" ellipsis>
                      {e}
                    </Text>
                  </Tooltip>
                ) : (
                  "—"
                ),
            },
            {
              title: "Created",
              dataIndex: "created_at",
              key: "created_at",
              width: 160,
              render: (v: string) =>
                new Date(v).toLocaleString(undefined, {
                  month: "short",
                  day: "numeric",
                  hour: "2-digit",
                  minute: "2-digit",
                }),
            },
            {
              title: "",
              key: "actions",
              width: 120,
              render: (_: unknown, t) => {
                if (t.status === "queued" || t.status === "running" || t.status === "waiting_for_user") {
                  return (
                    <Button size="small" onClick={() => handleCancel(t.id)}>
                      Cancel
                    </Button>
                  );
                }
                if (t.status === "failed") {
                  return (
                    <Button size="small" onClick={() => handleRetry(t.id)}>
                      Retry
                    </Button>
                  );
                }
                return null;
              },
            },
          ]}
        />
      )}
    </Card>
  );
}
