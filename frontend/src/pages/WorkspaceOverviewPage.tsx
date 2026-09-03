import { useCallback, useEffect, useState } from "react";
import { Alert, Button, Card, Col, Empty, List, Row, Space, Statistic, Tag, Typography } from "antd";
import { ArrowRightOutlined, BulbOutlined, FileSearchOutlined, ReadOutlined, SettingOutlined, UploadOutlined } from "@ant-design/icons";
import { Link, useNavigate } from "react-router-dom";
import paperApi from "../api/paper";
import taskApi from "../api/task";
import timelineApi from "../api/timeline";
import knowledgeApi from "../api/knowledge";
import workspaceApi from "../api/workspace";
import { discoverApi } from "../api/discover";
import type { Paper, Task, TimelineEvent } from "../api/types/domain";
import type { KnowledgeItem } from "../api/types/knowledge";
import type { WorkspaceReadiness } from "../api/types/workspace";
import type { DiscoverRun, ResearchOpportunity } from "../api/discover";
import WorkspaceReadinessCard from "../components/WorkspaceReadinessCard";
import { useWorkspaceLayout } from "../components/layout/WorkspaceLayout";
import PageHeader from "../components/common/PageHeader";
import StatusBadge from "../components/common/StatusBadge";
import ResearchRecommendations from "../components/ResearchRecommendations";
import { isTaskNeedingAttention } from "../state/taskAttention";
import { readingLibraryPath } from "../components/layout/navigation";

function formatDate(value: string) {
  return new Date(value).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export default function WorkspaceOverviewPage() {
  const { workspace } = useWorkspaceLayout();
  const navigate = useNavigate();
  const [papers, setPapers] = useState<Paper[] | null>(null);
  const [tasks, setTasks] = useState<Task[] | null>(null);
  const [knowledge, setKnowledge] = useState<KnowledgeItem[] | null>(null);
  const [runs, setRuns] = useState<DiscoverRun[] | null>(null);
  const [opportunities, setOpportunities] = useState<ResearchOpportunity[] | null>(null);
  const [pendingOpportunityCount, setPendingOpportunityCount] = useState<number | null>(null);
  const [timeline, setTimeline] = useState<TimelineEvent[] | null>(null);
  const [readiness, setReadiness] = useState<WorkspaceReadiness | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    const results = await Promise.allSettled([
      workspaceApi.readiness(workspace.id),
      paperApi.list(workspace.id, { limit: 100 }),
      taskApi.list(workspace.id, { limit: 100 }),
      knowledgeApi.listItems(workspace.id, { limit: 200 }),
      discoverApi.listRuns(workspace.id),
      discoverApi.listOpportunities(workspace.id, { pendingOnly: true, limit: 100 }),
      timelineApi.list(workspace.id, { limit: 8 }),
    ]);
    setReadiness(results[0].status === "fulfilled" ? results[0].value : null);
    setPapers(results[1].status === "fulfilled" ? results[1].value.items : null);
    setTasks(results[2].status === "fulfilled" ? results[2].value.items : null);
    setKnowledge(results[3].status === "fulfilled" ? results[3].value.items : null);
    setRuns(results[4].status === "fulfilled" ? results[4].value.items : null);
    setOpportunities(results[5].status === "fulfilled" ? results[5].value.items : null);
    setPendingOpportunityCount(results[5].status === "fulfilled" ? results[5].value.total : null);
    setTimeline(results[6].status === "fulfilled" ? results[6].value.items : null);
    setLoading(false);
  }, [workspace.id]);

  useEffect(() => { void load(); }, [load]);

  const activeTasks = tasks?.filter((task) => ["queued", "running", "waiting_for_user"].includes(task.status)) ?? [];
  const failedTasks = tasks?.filter((task) => task.status === "failed" && isTaskNeedingAttention(task)) ?? [];
  const reviewItems = knowledge?.filter((item) => ["candidate", "needs_review", "proposed"].includes(item.status)) ?? [];
  const waitingRuns = runs?.filter((run) => ["waiting_for_user", "waiting_for_fulltext"].includes(run.status)) ?? [];
  const reviewOpportunities = opportunities ?? [];
  const counts = readiness?.counts;
  const hasPapers = (counts?.papers ?? papers?.length ?? 0) > 0;

  const nextAction = readiness?.recommended_next_action
    ? {
        title: readiness.recommended_next_action.title,
        description: readiness.recommended_next_action.description,
        href: readiness.recommended_next_action.href,
        label: readiness.recommended_next_action.label,
        icon: <ArrowRightOutlined />,
      }
    : !papers?.length
      ? { title: "先收集几篇文献", description: "搜索论文或上传已有 PDF，建立这个课题的证据基础。", href: `/workspaces/${workspace.id}/papers`, label: "添加文献", icon: <FileSearchOutlined /> }
      : activeTasks.length
        ? { title: "查看正在处理的内容", description: "论文解析或知识提取还在进行，处理完成后即可继续审核。", href: `/workspaces/${workspace.id}/activity`, label: "打开处理中心", icon: <ArrowRightOutlined /> }
        : reviewItems.length
          ? { title: "审核提取的知识", description: `有 ${reviewItems.length} 条知识等待确认。`, href: `/workspaces/${workspace.id}/knowledge`, label: "打开知识工作台", icon: <ArrowRightOutlined /> }
          : !runs?.length
            ? { title: "开始发现研究机会", description: "从当前课题和已有知识出发，寻找可验证的研究缺口。", href: `/workspaces/${workspace.id}/discover`, label: "启动 Discover", icon: <BulbOutlined /> }
            : { title: "继续推进课题", description: "查看最新运行、研究机会和证据状态。", href: `/workspaces/${workspace.id}/discover`, label: "查看发现结果", icon: <ArrowRightOutlined /> };

  return (
    <div>
      <PageHeader
        eyebrow="课题概览"
        title="继续推进你的研究"
        description={workspace.description || "这里汇总当前课题的证据、知识和下一步行动。"}
        extra={<><Link to={`/workspaces/${workspace.id}/settings`}><Button icon={<SettingOutlined />}>课题设置</Button></Link><Button icon={<ArrowRightOutlined />} onClick={() => void load()} loading={loading}>刷新</Button></>}
      />

      {readiness && <WorkspaceReadinessCard readiness={readiness} />}

      <Card className="gm-action-card" style={{ marginBottom: 20 }} onClick={() => navigate(nextAction.href)}>
        <Space align="start" size="middle">
          <div className="gm-brand-mark">{nextAction.icon}</div>
          <div style={{ flex: 1 }}>
            <Typography.Title level={4} style={{ margin: 0 }}>{nextAction.title}</Typography.Title>
            <Typography.Paragraph type="secondary" style={{ margin: "5px 0 12px" }}>{nextAction.description}</Typography.Paragraph>
            <Link to={nextAction.href}><Button type="primary" icon={nextAction.icon}>{nextAction.label}</Button></Link>
          </div>
        </Space>
      </Card>

      <ResearchRecommendations workspaceId={workspace.id} onImported={() => void load()} />

      {failedTasks.length > 0 && <Alert type="error" showIcon message={`${failedTasks.length} 个近期后台任务处理失败`} description={<Link to={`/workspaces/${workspace.id}/activity`}>打开处理中心查看原因并重试；历史失败记录仍保留在处理中心。</Link>} style={{ marginBottom: 20 }} />}

      <Row gutter={[16, 16]}>
        <Col xs={12} md={6}><Card className="gm-section-card" extra={<Link to={readingLibraryPath(workspace.id)}>{hasPapers ? "继续阅读" : "进入阅读"}</Link>}><Statistic title={<span><ReadOutlined /> 课题文献</span>} value={counts?.papers ?? papers?.length ?? "—"} suffix={counts || papers ? "篇" : undefined} /><Typography.Text type="secondary">{counts ? `${counts.papers_with_pdf} 篇已有 PDF` : papers ? `${papers.filter((paper) => Boolean(paper.primary_artifact_id)).length} 篇已有 PDF` : "数据暂不可用"}</Typography.Text></Card></Col>
        <Col xs={12} md={6}><Card className="gm-section-card"><Statistic title="待审核知识" value={counts?.pending_knowledge ?? (knowledge ? reviewItems.length : "—")} suffix={counts || knowledge ? "条" : undefined} /><Typography.Text type="secondary">{counts || knowledge ? "优先处理候选内容" : "数据暂不可用"}</Typography.Text></Card></Col>
        <Col xs={12} md={6}><Card className="gm-section-card"><Statistic title="处理中" value={counts?.active_tasks ?? (tasks ? activeTasks.length : "—")} suffix={counts || tasks ? "项" : undefined} /><Typography.Text type="secondary">{counts || tasks ? "解析、提取或发现任务" : "数据暂不可用"}</Typography.Text></Card></Col>
        <Col xs={12} md={6}><Card className="gm-section-card"><Statistic title="待处理机会" value={counts?.pending_opportunities ?? pendingOpportunityCount ?? "—"} suffix={counts || pendingOpportunityCount !== null ? "项" : undefined} /><Typography.Text type="secondary">{counts || pendingOpportunityCount !== null ? "等待人工判断" : "数据暂不可用"}</Typography.Text></Card></Col>
      </Row>

      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24} lg={14}>
          <Card title="研究配置" extra={<Link to={`/workspaces/${workspace.id}/settings`}>编辑</Link>}>
            <Space direction="vertical" size="small" style={{ width: "100%" }}>
              <Typography.Text><strong>研究主题：</strong>{workspace.topic || "尚未设置"}</Typography.Text>
              <Typography.Text><strong>研究目标：</strong>{workspace.goals || "尚未设置"}</Typography.Text>
              <Typography.Text><strong>当前问题：</strong>{(workspace.active_questions ?? []).length ? (workspace.active_questions ?? [])[0] : "尚未设置"}</Typography.Text>
              <Space wrap>{(workspace.keywords ?? []).length ? (workspace.keywords ?? []).map((keyword) => <Tag key={keyword}>{keyword}</Tag>) : <Typography.Text type="secondary">还没有关键词</Typography.Text>}</Space>
            </Space>
          </Card>
        </Col>
        <Col xs={24} lg={10}>
          <Card title="需要你处理" extra={<Link to={`/workspaces/${workspace.id}/activity`}>处理中心</Link>}>
            {waitingRuns.length || reviewOpportunities.length || reviewItems.length ? <List size="small" dataSource={[...waitingRuns.slice(0, 2).map((run) => ({ key: run.id, label: "Discover 需要继续", status: run.status, href: `/workspaces/${workspace.id}/discover?run=${run.id}` })), ...reviewOpportunities.slice(0, 2).map((item) => ({ key: item.id, label: item.title, status: item.status, href: `/workspaces/${workspace.id}/discover?opportunity=${item.id}` })), ...reviewItems.slice(0, 2).map((item) => ({ key: item.id, label: item.canonical_name, status: item.status, href: `/workspaces/${workspace.id}/knowledge` }))]} renderItem={(item) => <List.Item><Link to={item.href}>{item.label}</Link><StatusBadge status={item.status} /></List.Item>} /> : <Empty description="目前没有待处理事项" image={Empty.PRESENTED_IMAGE_SIMPLE} />}
          </Card>
        </Col>
      </Row>

      <Card title="最近动态" style={{ marginTop: 16 }} extra={<Link to={`/workspaces/${workspace.id}/activity`}>查看全部</Link>}>
        {timeline?.length ? <List size="small" dataSource={timeline} renderItem={(event) => <List.Item><Space direction="vertical" size={0}><Typography.Text>{event.summary || event.event_type}</Typography.Text><Typography.Text type="secondary">{formatDate(event.created_at)}</Typography.Text></Space></List.Item>} /> : timeline === null ? <Typography.Text type="secondary">动态暂不可用</Typography.Text> : <Empty description="还没有课题动态" image={Empty.PRESENTED_IMAGE_SIMPLE}><Link to={`/workspaces/${workspace.id}/papers`}><Button icon={<UploadOutlined />}>添加第一篇文献</Button></Link></Empty>}
      </Card>
    </div>
  );
}
