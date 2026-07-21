import { Card, Empty, List, Tag, Typography } from "antd";
import type { TimelineEvent } from "../api/types/domain";

const { Text, Paragraph } = Typography;

interface Props {
  events: TimelineEvent[];
  loading: boolean;
}

const ACTOR_COLOR: Record<string, string> = {
  system: "blue",
  agent: "purple",
  user: "green",
};

function summarize(e: TimelineEvent): string {
  // Human-friendly label for common event types; falls back to event_type.
  const map: Record<string, string> = {
    "paper.created": "Paper created",
    "paper.uploaded": "Paper uploaded",
    "paper.updated": "Paper updated",
    "paper.deleted": "Paper deleted",
    "task.created": "Task created",
    "task.running": "Task started",
    "task.succeeded": "Task succeeded",
    "task.failed": "Task failed",
    "task.cancelled": "Task cancelled",
    "task.queued": "Task queued",
  };
  return map[e.event_type] ?? e.event_type;
}

export default function TimelineSection({ events, loading }: Props) {
  return (
    <Card title="Timeline">
      {events.length === 0 && !loading ? (
        <Empty description="No timeline events yet. They appear automatically as you work." />
      ) : (
        <List
          loading={loading}
          dataSource={events}
          renderItem={(e) => (
            <List.Item>
              <List.Item.Meta
                title={
                  <span>
                    <Text strong>{summarize(e)}</Text>{" "}
                    <Tag color={ACTOR_COLOR[e.actor] ?? "default"}>{e.actor}</Tag>
                    {e.subject_type && <Tag>{e.subject_type}</Tag>}
                  </span>
                }
                description={
                  <span>
                    <Text type="secondary">
                      {new Date(e.created_at).toLocaleString()}
                    </Text>
                    {e.payload && Object.keys(e.payload).length > 0 && (
                      <Paragraph
                        type="secondary"
                        style={{ margin: "4px 0 0", fontSize: 12 }}
                      >
                        <code>{JSON.stringify(e.payload)}</code>
                      </Paragraph>
                    )}
                  </span>
                }
              />
            </List.Item>
          )}
        />
      )}
    </Card>
  );
}
