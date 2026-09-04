import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Alert, Button, Card, Col, List, Row, Space, Tag, Typography } from "antd";
import { BookOutlined, FileSearchOutlined, PlusOutlined, RightOutlined, SettingOutlined } from "@ant-design/icons";
import { Link, useNavigate } from "react-router-dom";
import workspaceApi from "../api/workspace";
import taskApi from "../api/task";
import { discoverApi, type DiscoverRun, type ResearchOpportunity } from "../api/discover";
import { recommendationsApi } from "../api/recommendations";
import type { Workspace, WorkspaceReadiness } from "../api/types/workspace";
import type { Task } from "../api/types/domain";
import PageHeader from "../components/common/PageHeader";
import EmptyGuide from "../components/common/EmptyGuide";
import LifecycleModules from "../components/LifecycleModules";
import ActiveWorkspacePanel from "../components/ActiveWorkspacePanel";
import { useAppStore } from "../store/appStore";
import StatusBadge from "../components/common/StatusBadge";
import { isTaskNeedingAttention } from "../state/taskAttention";
import {
  aggregateDashboardRecommendations,
  dashboardRecommendationEntries,
  type DashboardRecommendationEntry,
} from "../state/dashboardRecommendationState";

export interface WorkspaceSummary {
  workspace: Workspace;
  counts: WorkspaceReadiness["counts"] | null;
  pendingTasks: Task[] | null;
  waitingRuns: DiscoverRun[] | null;
  opportunities: ResearchOpportunity[] | null;
  pendingOpportunityCount: number | null;
}

export default function DashboardPage() {
  const navigate = useNavigate();
  const currentWorkspaceId = useAppStore((state) => state.currentWorkspaceId);
  const [summaries, setSummaries] = useState<WorkspaceSummary[]>([]);
  const [recommendations, setRecommendations] = useState<DashboardRecommendationEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const recommendationRequestIdRef = useRef(0);
  const recommendationEntriesRef = useRef(new Map<string, DashboardRecommendationEntry[]>());

  const activeSummary = useMemo(
    () => {
      const eligible = summaries.filter(
        (summary) => !summary.workspace.is_archived && summary.workspace.name !== "__independent__",
      );
      return eligible.find((summary) => summary.workspace.id === currentWorkspaceId)
        ?? eligible.sort(
          (a, b) => new Date(b.workspace.updated_at).getTime() - new Date(a.workspace.updated_at).getTime(),
        )[0]
        ?? null;
    },
    [currentWorkspaceId, summaries],
  );
  const setCurrentWorkspace = useAppStore((state) => state.setCurrentWorkspace);

  useEffect(() => {
    setCurrentWorkspace(activeSummary?.workspace.id ?? null, activeSummary?.workspace.name ?? null);
  }, [activeSummary, setCurrentWorkspace]);

  const loadRecommendations = useCallback((workspaces: Workspace[]) => {
// 来源到达后立即渲染。冷 workspace 可以等待 S2，同时 Demo workspace 的
// 持久化推荐仍可立即使用。
    const sources = workspaces.filter((workspace) => workspace.name !== "__independent__");
    const requestId = ++recommendationRequestIdRef.current;
    recommendationEntriesRef.current.clear();
    setRecommendations([]);
    for (const workspace of sources) {
      void recommendationsApi.list(workspace.id)
        .then((response) => {
          if (requestId !== recommendationRequestIdRef.current) return;
          recommendationEntriesRef.current.set(
            workspace.id,
            dashboardRecommendationEntries(workspace, response),
          );
          setRecommendations(
            aggregateDashboardRecommendations(sources, recommendationEntriesRef.current),
          );
        })
        .catch(() => {
// 概览卡片负责可操作的 S2 错误状态。冷来源不可用时，首页预览仍不能阻塞。
        });
    }
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const workspaces = (await workspaceApi.list({ limit: 8 })).items;
      const next = await Promise.all(workspaces.map(async (workspace) => {
// 单一来源的 readiness 提供精确计数；对象级请求只保留给“需要关注”的操作列表。
        const [readiness, tasks, runs, opportunities] = await Promise.allSettled([
          workspaceApi.readiness(workspace.id),
          taskApi.list(workspace.id, { limit: 100 }),
          discoverApi.listRuns(workspace.id),
          discoverApi.listOpportunities(workspace.id, { pendingOnly: true, limit: 100 }),
        ]);
        const taskItems = tasks.status === "fulfilled" ? tasks.value.items : null;
        const runItems = runs.status === "fulfilled" ? runs.value.items : null;
        return {
          workspace,
          counts: readiness.status === "fulfilled" ? readiness.value.counts : null,
          pendingTasks: taskItems?.filter((task) => isTaskNeedingAttention(task)) ?? taskItems,
          waitingRuns: runItems?.filter((run) => ["waiting_for_user", "waiting_for_fulltext"].includes(run.status)) ?? runItems,
          opportunities: opportunities.status === "fulfilled" ? opportunities.value.items : null,
          pendingOpportunityCount: opportunities.status === "fulfilled" ? opportunities.value.total : null,
        } satisfies WorkspaceSummary;
      }));
      setSummaries(next);
// 两者解耦：未缓存 workspace 的推荐调用可以等待 S2 上游（约 5 秒），
// 主视图不能因此阻塞。
      void loadRecommendations(workspaces);
    } catch {
      setSummaries([]);
      setRecommendations([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const actions = summaries.flatMap((summary) => [
    ...(summary.pendingTasks ?? []).map((task) => ({ key: `task-${task.id}`, workspace: summary.workspace, title: task.status === "failed" ? "近期后台任务处理失败" : "后台任务正在处理", status: task.status, href: `/workspaces/${summary.workspace.id}/activity` })),
    ...(summary.waitingRuns ?? []).map((run) => ({ key: `run-${run.id}`, workspace: summary.workspace, title: "Discover 等待继续", status: run.status, href: `/workspaces/${summary.workspace.id}/discover?run=${run.id}` })),
    ...(summary.opportunities ?? []).map((opportunity) => ({ key: `opportunity-${opportunity.id}`, workspace: summary.workspace, title: opportunity.title, status: opportunity.status, href: `/workspaces/${summary.workspace.id}/discover?opportunity=${opportunity.id}` })),
  ]).slice(0, 8);

  return (
    <div className="gm-dashboard-page">
      <PageHeader
        eyebrow="GapMind"
        title="继续你的研究"
        description="从课题、文献和证据出发，把下一步行动变得清晰。"
        extra={<><Link to="/workspaces"><Button type="primary" icon={<PlusOutlined />}>新建课题</Button></Link><Link to="/search"><Button icon={<FileSearchOutlined />}>全局检索</Button></Link></>}
      />

      <LifecycleModules workspaceId={currentWorkspaceId ?? undefined} />

      {summaries.length === 0 && !loading ? (
        <Card><EmptyGuide description="还没有建立课题。先创建一个课题，再开始收集文献和证据。" actionText="创建第一个课题" actionIcon={<PlusOutlined />} onAction={() => navigate("/workspaces")} /></Card>
      ) : (
        <>
          <ActiveWorkspacePanel
            workspace={activeSummary?.workspace ?? null}
            counts={activeSummary?.counts ?? null}
            pendingTasks={activeSummary?.pendingTasks ?? null}
            waitingRuns={activeSummary?.waitingRuns ?? null}
            opportunities={activeSummary?.opportunities ?? null}
            pendingOpportunityCount={activeSummary?.pendingOpportunityCount ?? null}
            loading={loading}
          />
          {actions.length > 0 && <Card title="需要你处理" extra={<Link to="/workspaces">查看课题</Link>} style={{ marginBottom: 20 }}><List size="small" dataSource={actions} renderItem={(item) => <List.Item actions={[<Link key="open" to={item.href}><RightOutlined /></Link>]}><Space><Tag>{item.workspace.name}</Tag><Typography.Text>{item.title}</Typography.Text><StatusBadge status={item.status} /></Space></List.Item>} /></Card>}
          {recommendations.length > 0 && <Card title={<Space size={6}><BookOutlined />论文推荐</Space>} extra={<Typography.Text type="secondary">来自各课题语料画像 · 完整操作见课题概览</Typography.Text>} style={{ marginBottom: 20 }}><List size="small" dataSource={recommendations} renderItem={({ workspace, item }) => <List.Item actions={[<Link key="open" to={`/workspaces/${workspace.id}/overview`}>查看课题 <RightOutlined /></Link>]}><Space direction="vertical" size={2} style={{ width: "100%" }}><Space wrap><Tag color="blue">{workspace.name}</Tag><Typography.Text strong ellipsis={{ tooltip: item.paper.title }} style={{ maxWidth: 480 }}>{item.paper.title || "未命名论文"}</Typography.Text><Typography.Text type="secondary">{item.paper.publicationDate?.slice(0, 4) || (item.paper.year ?? "年份未知")}</Typography.Text><Tag>相关度 {Math.round(item.score * 100)}%</Tag></Space>{item.reasons[0] && <Typography.Text type="secondary" style={{ fontSize: 12 }} ellipsis={{ tooltip: item.reasons[0] }}>{item.reasons[0]}</Typography.Text>}</Space></List.Item>} /></Card>}
          <Typography.Title level={4}>最近课题</Typography.Title>
          <Row gutter={[16, 16]}>
            {summaries.map((summary) => <Col xs={24} md={12} xl={8} key={summary.workspace.id}><Card className="gm-action-card" title={<Link to={`/workspaces/${summary.workspace.id}/overview`}>{summary.workspace.name}</Link>} extra={<Link to={`/workspaces/${summary.workspace.id}/settings`}><SettingOutlined /></Link>}><Typography.Paragraph type="secondary" ellipsis={{ rows: 2 }}>{summary.workspace.description || summary.workspace.topic || "尚未填写课题描述"}</Typography.Paragraph><Space wrap><Tag>{summary.counts ? `文献 ${summary.counts.papers} 篇` : "文献：暂不可用"}</Tag><Tag>{summary.counts ? `待审核知识 ${summary.counts.pending_knowledge}` : "知识：暂不可用"}</Tag><Tag color={(summary.counts?.pending_opportunities ?? summary.pendingOpportunityCount) ? "orange" : "default"}>{summary.counts ? `待处理机会 ${summary.counts.pending_opportunities}` : summary.pendingOpportunityCount === null ? "机会：暂不可用" : `待处理机会 ${summary.pendingOpportunityCount}`}</Tag></Space><div style={{ marginTop: 16 }}><Link to={`/workspaces/${summary.workspace.id}/overview`}>继续课题 <RightOutlined /></Link></div></Card></Col>)}
          </Row>
        </>
      )}
      {loading && <Alert type="info" showIcon message="正在加载最近课题和待处理事项…" style={{ marginTop: 16 }} />}
    </div>
  );
}
