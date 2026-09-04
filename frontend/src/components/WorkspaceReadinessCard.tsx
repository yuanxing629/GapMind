import { useMemo } from "react";
import { Button, Card, Steps, Typography } from "antd";
import { ArrowRightOutlined, LoadingOutlined } from "@ant-design/icons";
import { Link, useNavigate } from "react-router-dom";
import type { ReadinessDimension, WorkspaceReadiness } from "../api/types/workspace";

/**
 * W0 研究就绪度进度条。
 *
 * 唯一数据源进度条：文献 → 知识 → 发现 → 确认 → 计划 → 执行。
 * 数据由后端 /readiness 文档驱动（不使用重复的前端聚合）。每个步骤都可点击，
 * 并跳转到推进该维度的页面。
 */
interface StepDef {
  title: string;
  done: boolean;
  waiting: boolean;
  href: string;
}

function findDimension(r: WorkspaceReadiness, key: string): ReadinessDimension | undefined {
  return r.dimensions.find((d) => d.key === key);
}

export default function WorkspaceReadinessCard({ readiness }: { readiness: WorkspaceReadiness }) {
  const navigate = useNavigate();
  const wid = readiness.workspace_id;
  const { steps, current } = useMemo(() => {
    const c = readiness.counts;
    const corpus = findDimension(readiness, "corpus");
    const knowledge = findDimension(readiness, "knowledge");
    const discover = findDimension(readiness, "discover");
    const defs: StepDef[] = [
      { title: "文献", done: corpus?.ready ?? false, waiting: corpus?.waiting ?? false, href: `/workspaces/${wid}/papers` },
      { title: "知识", done: knowledge?.ready ?? false, waiting: knowledge?.waiting ?? false, href: `/workspaces/${wid}/knowledge` },
      { title: "发现", done: discover?.ready ?? false, waiting: discover?.waiting ?? false, href: `/workspaces/${wid}/discover` },
      { title: "确认", done: c.confirmed_opportunities >= 1, waiting: false, href: `/workspaces/${wid}/discover` },
      { title: "计划", done: c.research_plans >= 1, waiting: false, href: `/workspaces/${wid}/plans` },
      { title: "执行", done: c.research_plans >= 1, waiting: false, href: `/workspaces/${wid}/plans` },
    ];
    const firstNotDone = defs.findIndex((s) => !s.done);
    return { steps: defs, current: firstNotDone === -1 ? defs.length : firstNotDone };
  }, [readiness, wid]);

  const rec = readiness.recommended_next_action;

  return (
    <Card className="gm-section-card" title="研究准备度" style={{ marginBottom: 20 }}>
      <Steps
        size="small"
        current={current}
        items={steps.map((s, i) => ({
          title: s.title,
          status: i < current ? "finish" : i === current ? (s.waiting ? "wait" : "process") : "wait",
          icon: i === current && s.waiting ? <LoadingOutlined /> : undefined,
          onClick: () => navigate(s.href),
        }))}
      />
      <div style={{ marginTop: 14, display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        <Typography.Text type="secondary" style={{ flex: 1, minWidth: 200 }}>
          {rec.description}
        </Typography.Text>
        <Link to={rec.href}>
          <Button type="primary" size="small" icon={<ArrowRightOutlined />}>{rec.label}</Button>
        </Link>
      </div>
    </Card>
  );
}
