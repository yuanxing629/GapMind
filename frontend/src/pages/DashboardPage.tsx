import { useEffect, useState } from "react";
import { Button, Card, Col, Row, Space, Statistic, Typography } from "antd";
import { Link } from "react-router-dom";
import { PlusOutlined } from "@ant-design/icons";
import { healthCheck, type HealthResponse } from "../api/health";
import workspaceApi from "../api/workspace";
import SemanticPaperSearch from "../components/SemanticPaperSearch";

const { Title, Paragraph } = Typography;

export default function DashboardPage() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [workspaceCount, setWorkspaceCount] = useState<number>(0);

  useEffect(() => {
    let cancelled = false;
    const run = async () => {
      try {
        const data = await healthCheck();
        if (!cancelled) setHealth(data);
      } catch {
        if (!cancelled) setHealth(null);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    run();
    const timer = setInterval(run, 10000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    workspaceApi
      .list({ limit: 1 })
      .then((resp) => setWorkspaceCount(resp.total))
      .catch(() => setWorkspaceCount(0));
  }, []);

  const status = health?.status ?? "—";
  const env = health?.env ?? "—";

  return (
    <div>
      <Title level={3}>Dashboard</Title>
      <Paragraph type="secondary">
        Welcome to GapMind. Manage your research workspaces and track opportunity
        discovery progress.
      </Paragraph>

      <Row gutter={16} style={{ marginTop: 16 }}>
        <Col span={6}>
          <Card>
            <Statistic title="Workspaces" value={workspaceCount} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="Papers" value={0} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="Knowledge Items" value={0} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="Backend" value={status} loading={loading} />
          </Card>
        </Col>
      </Row>

      <Card style={{ marginTop: 16 }}>
        <Paragraph>
          <strong>Backend status:</strong> {status} ({env})
        </Paragraph>
        <Space>
          <Link to="/workspaces">
            <Button type="primary" icon={<PlusOutlined />}>
              Go to Workspaces
            </Button>
          </Link>
        </Space>
      </Card>

      <SemanticPaperSearch />
    </div>
  );
}
