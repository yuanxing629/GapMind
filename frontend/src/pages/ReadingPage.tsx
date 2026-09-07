import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  App,
  Button,
  Card,
  Empty,
  Select,
  Space,
  Spin,
  Table,
  Tabs,
  Tag,
  Typography,
} from "antd";
import {
  BookOutlined,
  DeleteOutlined,
  FileSearchOutlined,
  PlayCircleOutlined,
  ReadOutlined,
  ReloadOutlined,
} from "@ant-design/icons";
import { useNavigate, useSearchParams } from "react-router-dom";
import PageHeader from "../components/common/PageHeader";
import SemanticPaperSearch from "../components/SemanticPaperSearch";
import paperApi from "../api/paper";
import readingApi, { type ReadingPaper, type ReadingStatus } from "../api/reading";
import taskApi from "../api/task";
import workspaceApi from "../api/workspace";
import type { Workspace } from "../api/types/workspace";
import type { Paper, Task } from "../api/types/domain";
import { useAppStore } from "../store/appStore";
import { readingPaperPath, resolveReadingWorkspace } from "../components/layout/navigation";
import {
  isActivePaperTask,
  latestPaperTask,
  paperPipelineStatus,
  pipelineActionLabel,
  type PaperPipelineStatus,
} from "../state/paperProcessing";

const { Text, Paragraph } = Typography;

const STATUS_META: Record<ReadingStatus, { label: string; color: string }> = {
  unread: { label: "未开始", color: "default" },
  reading: { label: "阅读中", color: "processing" },
  completed: { label: "已读完", color: "success" },
};

function errorMessage(error: unknown): string {
  const detail = (error as { response?: { data?: { detail?: { message?: string } } } }).response?.data?.detail;
  return detail?.message || (error as Error).message || "操作失败";
}

export default function ReadingPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const { message, modal } = App.useApp();
  const currentWorkspaceId = useAppStore((state) => state.currentWorkspaceId);
  const requestedWorkspaceId = searchParams.get("workspace_id");
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState<string | undefined>(currentWorkspaceId ?? undefined);
  const [workspaceSelectionError, setWorkspaceSelectionError] = useState(false);
  const [workspaceSelectionResolved, setWorkspaceSelectionResolved] = useState(false);
  const [items, setItems] = useState<ReadingPaper[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [tasksAvailable, setTasksAvailable] = useState(true);
  const [loading, setLoading] = useState(true);
  const [workspacesLoading, setWorkspacesLoading] = useState(true);
  const [processingKey, setProcessingKey] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (workspacesLoading || !workspaceSelectionResolved) return;
    if (!workspaceId || workspaceSelectionError) {
      setItems([]);
      setLoading(false);
      return;
    }
    setLoading(true);
    setItems([]);
    setTasksAvailable(false);
    try {
      const [papersResult, readingResult, tasksResult] = await Promise.allSettled([
        paperApi.list(workspaceId, { limit: 100 }),
        readingApi.list({ workspace_id: workspaceId, limit: 100 }),
        taskApi.list(workspaceId, { limit: 200 }),
      ]);
      if (papersResult.status === "rejected") throw papersResult.reason;
      const readingItems = readingResult.status === "fulfilled" ? readingResult.value.items : [];
      if (readingResult.status === "rejected") {
        message.warning("阅读记录暂时不可用，已先展示课题空间中的论文");
      }
      if (tasksResult.status === "fulfilled") {
        setTasks(tasksResult.value.items);
        setTasksAvailable(true);
      } else {
        setTasks([]);
        message.warning("后台任务状态暂时不可用，论文处理按钮已暂停，请稍后刷新");
      }
      const readingByPaper = new Map(
        readingItems.map((item) => [item.paper_id, item]),
      );
      const workspaceName = workspaces.find((item) => item.id === workspaceId)?.name ?? null;
      setItems(
        papersResult.value.items.map((paper) => {
          const readingPaper = readingByPaper.get(paper.id);
          if (readingPaper) return readingPaper;
          return {
            reading_item_id: "",
            paper_id: paper.id,
            workspace_id: paper.workspace_id,
            workspace_name: workspaceName,
            title: paper.title,
            authors: paper.authors ?? [],
            year: paper.year ?? null,
            abstract: paper.abstract ?? null,
            doi: paper.doi ?? null,
            arxiv_id: paper.arxiv_id ?? null,
            source: paper.source ?? "manual",
            external_paper_id: paper.external_paper_id ?? null,
            primary_artifact_id: paper.primary_artifact_id ?? null,
            parse_status: paper.parse_status ?? "not_applicable",
            parsed_markdown_artifact_id: paper.parsed_markdown_artifact_id ?? null,
            chunk_index_artifact_id: paper.chunk_index_artifact_id ?? null,
            chunk_count: paper.chunk_count ?? 0,
            extract_status: paper.extract_status ?? "not_applicable",
            reading_status: "unread" as const,
            last_read_page: 1,
            last_read_at: null,
            added_at: paper.created_at,
            updated_at: paper.updated_at,
          } satisfies ReadingPaper;
        }),
      );
    } catch (error) {
      message.error(`阅读库加载失败：${(error as Error).message}`);
    } finally {
      setLoading(false);
    }
  }, [message, workspaceId, workspaceSelectionError, workspaceSelectionResolved, workspaces, workspacesLoading]);

  useEffect(() => {
    let cancelled = false;
    setWorkspacesLoading(true);
    workspaceApi
      .list({ limit: 200 })
      .then((response) => {
        if (cancelled) return;
        setWorkspaces(response.items);
      })
      .catch(() => {
        if (!cancelled) setWorkspaces([]);
      })
      .finally(() => {
        if (!cancelled) setWorkspacesLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (workspacesLoading) return;
    const resolved = resolveReadingWorkspace(
      requestedWorkspaceId,
      currentWorkspaceId,
      workspaces.map((workspace) => workspace.id),
    );
    setWorkspaceSelectionError(resolved.invalidRequested);
    setWorkspaceId(resolved.workspaceId);
    setWorkspaceSelectionResolved(true);
    if (resolved.workspaceId && requestedWorkspaceId !== resolved.workspaceId) {
      setSearchParams((previous) => {
        const next = new URLSearchParams(previous);
        next.set("workspace_id", resolved.workspaceId as string);
        return next;
      }, { replace: true });
    }
  }, [currentWorkspaceId, requestedWorkspaceId, setSearchParams, workspaces, workspacesLoading]);

  useEffect(() => {
    void load();
  }, [load]);

  const openImportedPaper = async (paper: Paper) => {
    try {
      const readingPaper = await readingApi.ensureReady(paper.id);
      navigate(readingPaperPath(readingPaper.paper_id));
    } catch (error) {
      message.error(`加入阅读库失败：${(error as Error).message}`);
    }
  };

  const openPaper = async (paper: ReadingPaper) => {
    try {
      const readingPaper = paper.reading_item_id
        ? paper
        : await readingApi.ensureReady(paper.paper_id);
      navigate(readingPaperPath(readingPaper.paper_id));
    } catch (error) {
      message.error(`打开论文失败：${(error as Error).message}`);
    }
  };

  const triggerProcessing = async (
    paper: ReadingPaper,
    kind: "parse" | "index" | "knowledge",
  ) => {
    if (!tasksAvailable) {
      message.warning("暂时无法确认后台任务状态，请刷新后重试");
      return;
    }
    const key = `${paper.paper_id}:${kind}`;
    setProcessingKey(key);
    try {
      if (kind === "parse") await paperApi.parse(paper.workspace_id, paper.paper_id);
      if (kind === "index") await paperApi.index(paper.workspace_id, paper.paper_id);
      if (kind === "knowledge") await paperApi.extract(paper.workspace_id, paper.paper_id);
      message.success(`${pipelineActionLabel(kind, "pending")}任务已提交`);
      await load();
    } catch (error) {
      message.error(`${pipelineActionLabel(kind, "pending")}失败：${errorMessage(error)}`);
    } finally {
      setProcessingKey(null);
    }
  };

  const handleWorkspaceChange = (nextWorkspaceId: string) => {
    setSearchParams((previous) => {
      const next = new URLSearchParams(previous);
      next.set("workspace_id", nextWorkspaceId);
      return next;
    }, { replace: true });
  };

  const remove = (paper: ReadingPaper) => {
    modal.confirm({
      title: "清除这篇论文的阅读记录？",
      content: "论文、PDF 和已有批注不会被删除；清除后仍会继续显示在当前课题空间的论文列表中。",
      okText: "清除记录",
      cancelText: "取消",
      okType: "danger",
      onOk: async () => {
        try {
          await readingApi.remove(paper.paper_id);
          message.success("已从阅读库移除");
          await load();
        } catch (error) {
          message.error(`移除失败：${(error as Error).message}`);
        }
      },
    });
  };

  return (
    <div>
      <PageHeader
        eyebrow="研究阅读"
        title="论文阅读"
        description="把值得精读的论文、PDF 原文和阅读批注集中到一个地方。"
        extra={
          <Space wrap>
            <Select
              showSearch
              optionFilterProp="label"
              loading={workspacesLoading}
              value={workspaceId}
              placeholder="选择课题空间"
              style={{ minWidth: 220 }}
              options={workspaces.map((workspace) => ({ value: workspace.id, label: workspace.name }))}
              onChange={handleWorkspaceChange}
            />
            <Button icon={<ReloadOutlined />} onClick={() => void load()} loading={loading}>
              刷新
            </Button>
          </Space>
        }
      />

      {workspaceSelectionError ? (
        <Alert
          showIcon
          type="error"
          message="无法打开这个课题空间"
          description="链接中的课题空间不存在、已被删除或你没有访问权限。为避免显示其他课题的数据，阅读库已停止加载。"
          action={<Button onClick={() => navigate("/workspaces")}>返回课题空间</Button>}
          style={{ marginBottom: 16 }}
        />
      ) : !workspaceId && (
        <Alert
          showIcon
          type="info"
          message="请先选择一个课题空间"
          description="论文需要归属到课题空间，才能进入现有的 PDF 解析、证据检索和 AI 对话流程。"
          style={{ marginBottom: 16 }}
        />
      )}

      <Tabs
        defaultActiveKey="library"
        items={[
          {
            key: "library",
            label: <span><BookOutlined /> 阅读库</span>,
            children: (
              <Card>
                {loading ? (
                  <div className="gm-loading"><Spin /></div>
                ) : items.length === 0 ? (
                  <Empty
                    image={<ReadOutlined style={{ fontSize: 42, color: "#9aa8ba" }} />}
                    description="当前课题空间还没有论文"
                  >
                    <Text type="secondary">可以切换到“搜索论文”，或先在课题空间的“文献”页面上传 PDF。</Text>
                  </Empty>
                ) : (
                  <Table<ReadingPaper>
                    rowKey="paper_id"
                    dataSource={items}
                    pagination={false}
                    scroll={{ x: 900 }}
                    columns={[
                      {
                        title: "论文",
                        key: "title",
                        width: 360,
                        render: (_: unknown, paper) => (
                          <div>
                            <Typography.Link strong onClick={() => void openPaper(paper)}>
                              {paper.title}
                            </Typography.Link>
                            <Paragraph type="secondary" ellipsis={{ rows: 1 }} style={{ margin: "4px 0 0" }}>
                              {(paper.authors ?? []).slice(0, 3).join(", ") || "作者信息暂缺"}
                            </Paragraph>
                          </div>
                        ),
                      },
                      {
                        title: "阅读状态",
                        key: "reading_status",
                        width: 110,
                        render: (_: unknown, paper) => {
                          const meta = STATUS_META[paper.reading_status];
                          return <Tag color={meta.color}>{meta.label}</Tag>;
                        },
                      },
                      {
                        title: "原文",
                        key: "pdf",
                        width: 260,
                        render: (_: unknown, paper) => {
                          const states = {
                            parse: paperPipelineStatus(paper, paper.paper_id, tasks, "parse"),
                            index: paperPipelineStatus(paper, paper.paper_id, tasks, "index"),
                            knowledge: paperPipelineStatus(paper, paper.paper_id, tasks, "knowledge"),
                          } as const;
                          return (
                          <Space direction="vertical" size={2}>
                            {paper.primary_artifact_id ? <Tag color="green">PDF 可读</Tag> : <Tag>待上传</Tag>}
                            <Space wrap size={2}>
                              {(Object.entries(states) as [keyof typeof states, PaperPipelineStatus][]).map(([kind, state]) => (
                                <Tag key={kind} color={state === "succeeded" ? "success" : state === "failed" ? "error" : state === "running" ? "processing" : "default"}>
                                  {kind === "parse" ? "解析" : kind === "index" ? "索引" : "知识"}：{state === "succeeded" ? "已完成" : state === "not_applicable" ? "待前置" : state === "running" ? "处理中" : state === "failed" ? "失败" : "待处理"}
                                </Tag>
                              ))}
                            </Space>
                          </Space>
                          );
                        },
                      },
                      {
                        title: "进度",
                        key: "progress",
                        width: 100,
                        render: (_: unknown, paper) => `第 ${paper.last_read_page} 页`,
                      },
                      {
                        title: "操作",
                        key: "actions",
                        width: 440,
                        render: (_: unknown, paper) => {
                          const states = {
                            parse: paperPipelineStatus(paper, paper.paper_id, tasks, "parse"),
                            index: paperPipelineStatus(paper, paper.paper_id, tasks, "index"),
                            knowledge: paperPipelineStatus(paper, paper.paper_id, tasks, "knowledge"),
                          } as const;
                          return (
                          <Space wrap size={4}>
                            <Button size="small" type="primary" icon={<PlayCircleOutlined />} onClick={() => void openPaper(paper)}>
                              阅读
                            </Button>
                            {paper.reading_item_id && (
                              <Button size="small" danger icon={<DeleteOutlined />} title="清除阅读记录" onClick={() => remove(paper)} />
                            )}
                            {(["parse", "index", "knowledge"] as const).map((kind) => {
                              const state = states[kind];
                              if (state === "succeeded" || state === "not_applicable") return null;
                              const taskType = kind === "parse" ? "parse_pdf" : kind === "index" ? "embed_chunks" : "extract_knowledge";
                              const task = latestPaperTask(tasks, paper.paper_id, taskType);
                              const active = isActivePaperTask(task);
                              if (kind === "parse" && !paper.primary_artifact_id) return null;
                              return (
                                <Button
                                  key={kind}
                                  size="small"
                                  icon={<ReloadOutlined />}
                                  disabled={!tasksAvailable || active}
                                  loading={processingKey === `${paper.paper_id}:${kind}` || active}
                                  onClick={() => void triggerProcessing(paper, kind)}
                                >
                                  {pipelineActionLabel(kind, state)}
                                </Button>
                              );
                            })}
                          </Space>
                          );
                        },
                      },
                    ]}
                  />
                )}
              </Card>
            ),
          },
          {
            key: "search",
            label: <span><FileSearchOutlined /> 搜索论文</span>,
            children: workspaceId ? (
              <SemanticPaperSearch workspaceId={workspaceId} onImported={openImportedPaper} />
            ) : (
              <Card><Empty description="选择课题空间后才能导入论文" /></Card>
            ),
          },
        ]}
      />
    </div>
  );
}
