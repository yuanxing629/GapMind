import { useCallback, useEffect, useMemo, useState } from "react";
import { App, Alert, Button, Card, Empty, Popconfirm, Select, Space, Statistic, Table, Typography } from "antd";
import {
  ArrowRightOutlined,
  CheckCircleFilled,
  CompassOutlined,
  ExperimentOutlined,
  FireFilled,
  LinkOutlined,
  ReloadOutlined,
  RobotOutlined,
  SwapOutlined,
} from "@ant-design/icons";
import { useNavigate, useParams } from "react-router-dom";
import gapApi, { type GapAnnotation, type GapBoard, type GapBoardCell } from "../api/gap";
import paperApi from "../api/paper";
import { isRemoteGapFallback } from "../state/gapAnnotation";

const { Paragraph, Text, Title } = Typography;

function errorMessage(error: unknown): string {
  const value = error as {
    response?: { status?: number; data?: { detail?: string | { message?: string } } };
    message?: string;
  };
  const detail = value.response?.data?.detail;
  if (typeof detail === "string") return detail;
  return detail?.message || value.message || "请求失败";
}

const candidatePresentation: Record<
  GapBoardCell["candidate_tier"],
  { label: string; tone: string; shortDescription: string }
> = {
  covered: { label: "已有方法解决", tone: "covered", shortDescription: "已有直接关联证据" },
  explicit_limitation: { label: "明确剩余局限", tone: "limitation", shortDescription: "论文明确指出仍未解决" },
  same_paper_unlinked: { label: "同篇共现待核验", tone: "same-paper", shortDescription: "同篇出现但尚无直接关联" },
  cross_paper_transfer: { label: "跨论文迁移候选", tone: "transfer", shortDescription: "跨论文组合形成的候选" },
  corpus_only: { label: "语料库未覆盖", tone: "uncovered", shortDescription: "仅由方法与问题轴组合产生" },
};

type GapTierFilter = GapBoardCell["candidate_tier"] | "all" | "recommended";

const TIER_FILTER_OPTIONS: Array<{ value: GapTierFilter; label: string }> = [
  { value: "recommended", label: "推荐核验（优先）" },
  { value: "all", label: "全部机会" },
  { value: "explicit_limitation", label: "明确剩余局限" },
  { value: "same_paper_unlinked", label: "同篇共现待核验" },
  { value: "cross_paper_transfer", label: "跨论文迁移候选" },
  { value: "covered", label: "已有研究覆盖" },
  { value: "corpus_only", label: "低证据组合" },
];

function cellIcon(tier: GapBoardCell["candidate_tier"]) {
  if (tier === "covered") return <CheckCircleFilled />;
  if (tier === "explicit_limitation") return <FireFilled />;
  if (tier === "same_paper_unlinked") return <LinkOutlined />;
  if (tier === "cross_paper_transfer") return <SwapOutlined />;
  return <CompassOutlined />;
}

export default function GapBoardPage() {
  const { id: workspaceId } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { message } = App.useApp();
  const [board, setBoard] = useState<GapBoard | null>(null);
  const [annotations, setAnnotations] = useState<GapAnnotation[]>([]);
  const [loading, setLoading] = useState(false);
  const [extracting, setExtracting] = useState(false);
  const [rebuilding, setRebuilding] = useState(false);
  const [discovering, setDiscovering] = useState<string | null>(null);
  // P0-5: 矩阵可按机会类型筛选（只看某一类，如"明确剩余局限"），矩阵变大后不至于难读。
  const [tierFilter, setTierFilter] = useState<GapTierFilter>("recommended");

  const load = useCallback(async () => {
    if (!workspaceId) return;
    setLoading(true);
    try {
      const annotationResponse = await gapApi.listAnnotations(workspaceId);
      setAnnotations(annotationResponse.items);
      try {
        setBoard(await gapApi.getBoard(workspaceId));
      } catch (error) {
        const status = (error as { response?: { status?: number } }).response?.status;
        if (status === 404) setBoard(null);
        else throw error;
      }
    } catch (error) {
      message.error(`加载研究空白棋盘失败：${errorMessage(error)}`);
    } finally {
      setLoading(false);
    }
  }, [message, workspaceId]);

  useEffect(() => {
    void load();
  }, [load]);

  const runExtraction = async () => {
    if (!workspaceId) return;
    setExtracting(true);
    try {
      const allPapers: Awaited<ReturnType<typeof paperApi.list>>["items"] = [];
      let offset = 0;
      let total = 1;
      while (offset < total) {
        const page = await paperApi.list(workspaceId, { limit: 100, offset });
        allPapers.push(...page.items);
        total = page.total;
        offset += page.items.length;
        if (!page.items.length) break;
      }
      const eligible = allPapers
        .filter((paper) => paper.parse_status === "parsed" && paper.parsed_markdown_artifact_id)
        .filter((paper) => !["pending", "extracting"].includes(paper.extract_status))
        .map((paper) => paper.id);
      const waitingForKnowledge = allPapers.filter(
        (paper) =>
          paper.parse_status === "parsed" &&
          paper.parsed_markdown_artifact_id &&
          ["pending", "extracting"].includes(paper.extract_status),
      ).length;
      if (!eligible.length) {
        message.warning(
          waitingForKnowledge
            ? `有 ${waitingForKnowledge} 篇论文正在完成知识抽取，请完成后再运行研究空白抽取。`
            : "当前没有已完成 Markdown 解析的论文。",
        );
        return;
      }
      let submitted = 0;
      let skipped = 0;
      let legacyFallback = 0;
      for (let index = 0; index < eligible.length; index += 200) {
        const response = await gapApi.extract(
          workspaceId,
          eligible.slice(index, index + 200),
          false,
        );
        for (const task of response.tasks) {
          if (task.skipped) skipped += 1;
          else submitted += 1;
          if (task.dependency_status === "legacy_fallback") legacyFallback += 1;
        }
      }
      if (submitted === 0) {
        message.info(
          waitingForKnowledge
            ? `已跳过 ${waitingForKnowledge} 篇知识抽取中的论文；其余 ${skipped} 篇无需重复抽取。`
            : `全部 ${skipped} 篇论文已完成专项标注，无需重复抽取。`,
        );
      } else if (skipped > 0) {
        message.success(
          `已提交 ${submitted} 篇论文抽取，跳过 ${skipped} 篇已完成标注${
            legacyFallback ? `，其中 ${legacyFallback} 篇将使用兼容 Markdown 降级` : ""
          }；可在“课题活动 → 处理中心”查看进度或失败原因。`,
        );
      } else {
        message.success(
          `已提交 ${submitted} 篇论文${
            legacyFallback ? `，其中 ${legacyFallback} 篇将使用兼容 Markdown 降级` : ""
          }；可在“课题活动 → 处理中心”查看进度，完成后刷新并重建棋盘。`,
        );
      }
    } catch (error) {
      message.error(`提交专项抽取失败：${errorMessage(error)}`);
    } finally {
      setExtracting(false);
    }
  };

  const rebuild = async () => {
    if (!workspaceId) return;
    setRebuilding(true);
    try {
      const next = await gapApi.rebuildBoard(workspaceId);
      setBoard(next);
      message.success(`棋盘 v${next.version} 已生成。`);
    } catch (error) {
      message.error(`重建棋盘失败：${errorMessage(error)}`);
    } finally {
      setRebuilding(false);
    }
  };

  const verifyCandidate = async (cell: GapBoardCell, exploratory = false) => {
    if (!workspaceId) return;
    const key = `${cell.method_concept_id}:${cell.problem_concept_id}`;
    setDiscovering(key);
    try {
      const result = await gapApi.discoverCandidate(
        workspaceId,
        cell.method_concept_id,
        cell.problem_concept_id,
        exploratory,
      );
      message.success(
        exploratory
          ? "已发起探索性核验；系统会先检查机制兼容性与已有工作，不会直接认定研究空白。"
          : "已交给 Discover 进行相似工作、外部论文与反证核验。",
      );
      navigate(`/workspaces/${workspaceId}/discover?run=${result.run_id}`);
    } catch (error) {
      message.error(`启动候选核验失败：${errorMessage(error)}`);
    } finally {
      setDiscovering(null);
    }
  };

  const filteredCells = useMemo(() => {
    const all = board?.cells ?? [];
    if (tierFilter === "all") return all;
    if (tierFilter === "recommended") {
      return all.filter((cell) => !cell.addressed && cell.eligible_for_discovery);
    }
    return all.filter((cell) => (cell.addressed ? "covered" : cell.candidate_tier) === tierFilter);
  }, [board, tierFilter]);

  const cellIndex = useMemo(
    () =>
      new Map(
        filteredCells.map((cell) => [
          `${cell.method_concept_id}:${cell.problem_concept_id}`,
          cell,
        ]),
      ),
    [filteredCells],
  );

  // 筛选后只保留包含匹配机会的行/列，让矩阵收缩到可读大小。
  const visibleMethods = useMemo(() => {
    const ids = new Set(filteredCells.map((cell) => cell.method_concept_id));
    return (board?.method_axes ?? []).filter((method) => ids.has(method.concept_id));
  }, [board, filteredCells]);
  const visibleProblems = useMemo(() => {
    const ids = new Set(filteredCells.map((cell) => cell.problem_concept_id));
    return (board?.problem_axes ?? []).filter((problem) => ids.has(problem.concept_id));
  }, [board, filteredCells]);

  const columns = useMemo(() => {
    if (!board) return [];
    return [
      {
        title: "方法策略（纵轴）",
        dataIndex: "label",
        key: "method",
        fixed: "left" as const,
        width: 230,
        render: (label: string, method: GapBoard["method_axes"][number]) => (
          <div className="gm-gap-axis gm-gap-axis--method">
            <span className="gm-gap-axis-kicker">方法策略</span>
            <Text strong>{label}</Text>
            <span className="gm-gap-axis-count">{method.paper_count} 篇论文支持</span>
          </div>
        ),
      },
      ...visibleProblems.map((problem) => ({
        title: (
          <div className="gm-gap-axis gm-gap-axis--problem">
            <span className="gm-gap-axis-kicker">核心问题</span>
            <Text strong>{problem.label}</Text>
            <span className="gm-gap-axis-count">{problem.paper_count} 篇论文涉及</span>
          </div>
        ),
        key: problem.concept_id,
        width: 228,
        render: (_: unknown, method: GapBoard["method_axes"][number]) => {
          const cell = cellIndex.get(`${method.concept_id}:${problem.concept_id}`);
          if (!cell) return <div className="gm-gap-cell gm-gap-cell--empty">—</div>;
          const key = `${cell.method_concept_id}:${cell.problem_concept_id}`;
          const tier = cell.addressed ? "covered" : cell.candidate_tier;
          const presentation = candidatePresentation[tier] || candidatePresentation.corpus_only;
          const score = Math.max(0, Math.min(100, Math.round(cell.candidate_score * 100)));
          const isHot = !cell.addressed && score >= 70;
          return (
            <div className={`gm-gap-cell gm-gap-cell--${presentation.tone}${isHot ? " gm-gap-cell--hot" : ""}`}>
              <div className="gm-gap-cell-heading">
                <span className="gm-gap-cell-icon">{cellIcon(tier)}</span>
                <span className="gm-gap-cell-label">{presentation.label}</span>
                {(tier === "explicit_limitation" || isHot) ? <span className="gm-gap-hot-badge">重点</span> : null}
              </div>
              <span className="gm-gap-cell-caption">{presentation.shortDescription}</span>

              {cell.addressed ? (
                <div className="gm-gap-support-count">
                  <strong>{cell.addressed_paper_ids.length}</strong> 篇直接支持
                </div>
              ) : (
                <>
                  <div className="gm-gap-priority-row">
                    <span>核验优先级</span>
                    <strong>{score}</strong>
                  </div>
                  <div className="gm-gap-priority-track" aria-label={`核验优先级 ${score}`}>
                    <span style={{ width: `${score}%` }} />
                  </div>
                  {cell.candidate_reasons[0] ? (
                    <span className="gm-gap-cell-reason">{cell.candidate_reasons[0]}</span>
                  ) : null}
                  {cell.eligible_for_discovery ? (
                    <Button
                      className="gm-gap-cell-action"
                      size="small"
                      type="link"
                      icon={<ArrowRightOutlined />}
                      loading={discovering === key}
                      onClick={() => void verifyCandidate(cell)}
                    >
                      进入 Discover 核验
                    </Button>
                  ) : (
                    <Popconfirm
                      title="发起低证据探索性核验？"
                      description="该组合目前仅来自横纵轴配对。Discover 会检索机制兼容性、相似工作和反证，但不会把空格直接当作研究空白。"
                      okText="继续核验"
                      cancelText="取消"
                      onConfirm={() => void verifyCandidate(cell, true)}
                    >
                      <Button
                        className="gm-gap-cell-action"
                        size="small"
                        type="link"
                        icon={<CompassOutlined />}
                        loading={discovering === key}
                      >
                        探索性核验
                      </Button>
                    </Popconfirm>
                  )}
                </>
              )}
            </div>
          );
        },
      })),
    ];
  }, [visibleProblems, cellIndex, discovering]);

  if (!workspaceId) return <Empty description="工作区不存在" />;
  const validCount = annotations.filter((item) => item.status === "valid").length;
  const invalidCount = annotations.filter((item) => item.status === "invalid").length;
  const remoteFallbackCount = annotations.filter(isRemoteGapFallback).length;
  const legacyContextCount = annotations.filter(
    (item) => item.input_mode === "core_markdown_legacy_v1",
  ).length;
  const staleAnnotationCount = annotations.filter((item) => item.stale).length;
  const uncoveredCount = board?.cells.filter((item) => !item.addressed).length || 0;
  const explicitLimitationCount = board?.cells.filter(
    (item) => !item.addressed && item.candidate_tier === "explicit_limitation",
  ).length || 0;
  const coveredCount = board?.cells.filter((item) => item.addressed).length || 0;
  const visibleUncoveredCount = filteredCells.filter((item) => !item.addressed).length;

  return (
    <div className="gm-gap-board-page">
      <section className="gm-gap-board-hero">
        <div className="gm-gap-board-hero-copy">
          <span className="gm-gap-board-eyebrow">RESEARCH OPPORTUNITY MAP</span>
          <Title level={2}>研究空白棋盘</Title>
          <Paragraph>
            用“方法策略 × 核心问题”观察已有研究覆盖，并优先发现值得进一步核验的跨论文机会。
          </Paragraph>
          <div className="gm-gap-board-meta">
            <span>棋盘版本 v{board?.version || 0}</span>
            <span>{board?.method_axes.length || 0} 个方法族</span>
            <span>{board?.problem_axes.length || 0} 个问题族</span>
          </div>
        </div>
        <Space wrap className="gm-gap-board-actions">
          <Button icon={<ReloadOutlined />} loading={loading} onClick={() => void load()}>
            刷新
          </Button>
          <Button icon={<RobotOutlined />} loading={extracting} onClick={() => void runExtraction()}>
            抽取已解析论文
          </Button>
          <Button type="primary" icon={<ExperimentOutlined />} loading={rebuilding} onClick={() => void rebuild()}>
            重建棋盘
          </Button>
        </Space>
      </section>

      <div className="gm-gap-summary-grid">
        <Card className="gm-gap-summary-card gm-gap-summary-card--limitation" size="small">
          <Statistic prefix={<FireFilled />} title="明确剩余局限" value={explicitLimitationCount} suffix="格" />
          <span>论文直接指出的高价值线索</span>
        </Card>
        <Card className="gm-gap-summary-card gm-gap-summary-card--candidate" size="small">
          <Statistic prefix={<ExperimentOutlined />} title="推荐核验候选" value={board?.candidate_count || 0} suffix="格" />
          <span>建议进入 Discover 深度核验</span>
        </Card>
        <Card className="gm-gap-summary-card gm-gap-summary-card--covered" size="small">
          <Statistic prefix={<CheckCircleFilled />} title="已有研究覆盖" value={coveredCount} suffix="格" />
          <span>已有直接方法—问题关联</span>
        </Card>
        <Card className="gm-gap-summary-card gm-gap-summary-card--evidence" size="small">
          <Statistic prefix={<RobotOutlined />} title="有效专项标注" value={validCount} suffix="篇" />
          <span>
            {remoteFallbackCount
              ? `${remoteFallbackCount} 篇远程降级候选，${invalidCount ? `${invalidCount} 篇无效标注已隔离` : "其余标注已通过校验"}`
              : invalidCount
                ? `${invalidCount} 篇无效标注已隔离`
                : "当前标注均已通过校验"}
            {legacyContextCount ? `；${legacyContextCount} 篇使用兼容 Markdown 上下文` : ""}
            {staleAnnotationCount ? `；${staleAnnotationCount} 篇待按最新知识结果重跑` : ""}
          </span>
        </Card>
      </div>

      <Alert
        className="gm-gap-board-alert"
        type="warning"
        showIcon
        message="棋盘空格不等于真实研究空白，优先级也不是成功概率"
        description="系统保留全部未覆盖格用于观察，但只有明确局限、同篇共现未连接，或方法族与问题族均获得多篇论文支持的格子会进入推荐核验。之后仍须经过相似工作、外部论文、反证与 Evidence Gate。"
      />

      <Card className="gm-gap-board-card" loading={loading}>
        <div className="gm-gap-board-card-heading">
          <div>
            <Title level={4}>方法 × 问题机会矩阵</Title>
            <Text type="secondary">{tierFilter === "recommended" ? `优先显示 ${visibleUncoveredCount} 个推荐核验候选；可切换查看全部 ${uncoveredCount} 个未覆盖组合。` : `共 ${uncoveredCount} 个未覆盖组合，颜色越醒目代表越值得优先核验。`}</Text>
          </div>
          <Space wrap align="center">
            <Select
              aria-label="按机会类型筛选矩阵"
              value={tierFilter}
              onChange={(value) => setTierFilter(value)}
              options={TIER_FILTER_OPTIONS}
              style={{ minWidth: 160 }}
            />
            <div className="gm-gap-legend" aria-label="棋盘图例">
              <span className="gm-gap-legend-item gm-gap-legend-item--limitation"><i />明确局限</span>
              <span className="gm-gap-legend-item gm-gap-legend-item--transfer"><i />跨论文候选</span>
              <span className="gm-gap-legend-item gm-gap-legend-item--same-paper"><i />同篇待核验</span>
              <span className="gm-gap-legend-item gm-gap-legend-item--covered"><i />已有覆盖</span>
              <span className="gm-gap-legend-item gm-gap-legend-item--uncovered"><i />低证据组合</span>
            </div>
          </Space>
        </div>
        {!board || !board.method_axes.length || !board.problem_axes.length ? (
          <Empty description="先完成专项抽取，再重建棋盘。" />
        ) : visibleMethods.length === 0 || visibleProblems.length === 0 ? (
          <Empty description="当前筛选条件下没有此类机会。" />
        ) : (
          <div className="gm-gap-table-shell">
            <Table
              className="gm-gap-board-table"
              rowKey="concept_id"
              dataSource={visibleMethods}
              columns={columns}
              pagination={false}
              scroll={{ x: Math.max(920, 230 + visibleProblems.length * 228) }}
              sticky
            />
          </div>
        )}
      </Card>
    </div>
  );
}
