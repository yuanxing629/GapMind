import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  App,
  Button,
  Card,
  Checkbox,
  Descriptions,
  Divider,
  Drawer,
  Empty,
  Form,
  Grid,
  Input,
  List,
  Modal,
  Popconfirm,
  Progress,
  Select,
  Space,
  Steps,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import { BulbOutlined, CloseCircleOutlined, DeleteOutlined, PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import { useParams, useSearchParams } from "react-router-dom";
import { discoverApi, type DiscoverExternalCandidate, type DiscoverRun, type EvidenceManifest, type OpportunityDetail, type ResearchOpportunity } from "../api/discover";
import { OpportunityEvidenceViewer } from "../components/EvidenceViewer";
import { getStatusMeta } from "../components/common/StatusBadge";
import { currentRunStage, currentRunStatus, DISCOVER_STAGE_LABELS, DISCOVER_STAGES, pollingInterval, selectedOpportunityCount, stageIndex, stageSummaryMessage, stageSummaryStatus, TERMINAL_RUN_STATUSES } from "../state/discoverState";
import { canSelectExternalCandidate, externalCandidateActionLabel, externalSelectionIsOpen } from "../state/externalSelection";
import { discoverRunVerificationStatusLabel, discoverStageLabel, evidenceLevelDisplayLabel, evidenceRelationLabel, evidenceSourceScopeLabel, gateMessageLabel, localizedGeneratedText, opportunityStatusLabel, verificationStatusLabel as verificationDisplayLabel } from "../state/discoverLabels";

const { Text, Title, Paragraph } = Typography;

function errorMessage(error: unknown): string {
  const response = error as { response?: { status?: number; data?: { detail?: { message?: string; error?: string } } } };
  const detail = response.response?.data?.detail;
  if (response.response?.status === 409) return detail?.message || "该内容已在其他位置发生变化，请刷新后重试。";
  return detail?.message || (error as Error).message || "请求失败";
}

function statusColor(status: string): string {
  if (["succeeded", "confirmed", "edited_confirmed", "verified"].includes(status)) return "green";
  if (["failed", "cancelled", "rejected", "verification_failed"].includes(status)) return "red";
  if (["waiting_for_user", "waiting_for_fulltext", "needs_more_evidence", "reviewable_with_warning", "verification_incomplete", "verified_with_warnings", "deferred"].includes(status)) return "orange";
  return "blue";
}

function agentStepColor(status: string): string {
  if (status === "completed") return "green";
  if (status === "waiting") return "orange";
  if (status === "failed") return "red";
  if (status === "skipped") return "default";
  return "processing";
}

function verificationStatusLabel(status: string): string {
  switch (status) {
    case "selected": return "已选择，正在启动核验";
    case "imported_pending_parse": return "PDF 已下载，解析流程运行中";
    case "verified": return "全文核验完成";
    case "no_pdf": return "没有可用 PDF";
    case "import_failed": return "PDF 下载失败";
    case "verification_failed": return "全文核验失败";
    default: return "未选择";
  }
}

function evidenceLevelLabel(level: string): string {
  if (level === "full_text") return "全文证据";
  if (level === "metadata_only") return "仅有元数据";
  return level;
}

function externalRoleLabel(role: string): string {
  switch (role) {
    case "similar": return "相似工作";
    case "overlap": return "部分重合";
    case "qualifies": return "限定／反证线索";
    case "contradicts": return "可能反驳";
    case "supporting": return "可能支持";
    default: return "关系未确定";
  }
}

function pdfAcquisitionLabel(candidate: DiscoverExternalCandidate): string | null {
  const acquisition = candidate.snapshot_payload?.pdf_acquisition;
  if (!acquisition || typeof acquisition !== "object" || Array.isArray(acquisition)) return null;
  const status = (acquisition as { status?: unknown }).status;
  if (status === "no_pdf") return "未找到开放获取 PDF 地址";
  if (status === "retryable_failure") return "PDF 下载暂时失败，可重试";
  if (status === "unavailable") return "可用来源均未返回有效 PDF";
  if (status === "local_import_failed") return "PDF 已获取，但本地导入失败";
  return null;
}

function gateDetails(sourcePayload: Record<string, unknown>): { verified: boolean; confirmable: boolean; blockingMissing: string[]; warnings: string[]; missing: string[]; reason?: string } | null {
  const value = sourcePayload.gate;
  if (!value || typeof value !== "object") return null;
  const gate = value as { verified?: unknown; confirmable?: unknown; blocking_missing?: unknown; warnings?: unknown; missing?: unknown; reason?: unknown };
  const missing = Array.isArray(gate.missing) ? gate.missing.filter((item): item is string => typeof item === "string") : [];
  const blockingMissing = Array.isArray(gate.blocking_missing)
    ? gate.blocking_missing.filter((item): item is string => typeof item === "string")
    : missing.filter((item) => item !== "external verification did not complete");
  const warnings = Array.isArray(gate.warnings)
    ? gate.warnings.filter((item): item is string => typeof item === "string")
    : missing.filter((item) => item === "external verification did not complete");
  return {
    verified: gate.verified === true,
    confirmable: gate.confirmable === true || (blockingMissing.length === 0 && (gate.verified === true || warnings.length > 0)),
    blockingMissing,
    warnings,
    missing,
    reason: typeof gate.reason === "string" ? gate.reason : undefined,
  };
}

function opportunityStatus(item: ResearchOpportunity): string {
  const gate = gateDetails(item.source_payload);
  if (item.status === "needs_more_evidence" && gate?.confirmable) return "reviewable_with_warning";
  return item.status;
}

export default function DiscoverPage() {
  const { id: workspaceId, runId, opportunityId } = useParams<{ id: string; runId?: string; opportunityId?: string }>();
  const [searchParams, setSearchParams] = useSearchParams();
  const { message, modal } = App.useApp();
  const screens = Grid.useBreakpoint();
  const [form] = Form.useForm();
  const [decisionForm] = Form.useForm();
  const [editForm] = Form.useForm();
  const [runs, setRuns] = useState<DiscoverRun[]>([]);
  const [runDetail, setRunDetail] = useState<Awaited<ReturnType<typeof discoverApi.getRun>> | null>(null);
  const [opportunities, setOpportunities] = useState<ResearchOpportunity[]>([]);
  const [selectedOpportunity, setSelectedOpportunity] = useState<OpportunityDetail | null>(null);
  const [runModalOpen, setRunModalOpen] = useState(false);
  const [decisionModalOpen, setDecisionModalOpen] = useState(false);
  const [editModalOpen, setEditModalOpen] = useState(false);
  const [decisionAction, setDecisionAction] = useState<"confirm" | "reject" | "defer">("confirm");
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [actionLoading, setActionLoading] = useState(false);
  const [selectedExternalCandidateIds, setSelectedExternalCandidateIds] = useState<string[]>([]);
  const selectedRunId = searchParams.get("run") ?? runId ?? null;
  const selectedOpportunityId = searchParams.get("opportunity") ?? opportunityId ?? null;
  const selectedRun = useMemo(() => runs.find((run) => run.id === selectedRunId) ?? runs[0] ?? null, [runs, selectedRunId]);

  const mergeDetail = useCallback((detail: Awaited<ReturnType<typeof discoverApi.getRun>>) => {
    setRunDetail(detail);
    setRuns((current) => current.map((run) => run.id === detail.id ? { ...run, ...detail } : run));
    setOpportunities((current) => {
      const merged = new Map(current.map((item) => [item.id, item]));
      detail.opportunities.forEach((item) => merged.set(item.id, item));
      return Array.from(merged.values());
    });
  }, []);

  const load = useCallback(async () => {
    if (!workspaceId) return;
    setLoading(true);
    try {
      const [runResponse, opportunityResponse] = await Promise.all([
        discoverApi.listRuns(workspaceId),
        discoverApi.listOpportunities(workspaceId),
      ]);
      setRuns(runResponse.items);
      setOpportunities(opportunityResponse.items);
      const current = selectedRunId ? runResponse.items.find((item) => item.id === selectedRunId) : runResponse.items[0];
      if (current && current.id !== selectedRunId) {
        const next = new URLSearchParams(searchParams);
        next.set("run", current.id);
        setSearchParams(next, { replace: true });
      }
      if (current) mergeDetail(await discoverApi.getRun(workspaceId, current.id));
    } catch (error) {
      message.error(`加载研究机会发现任务失败：${errorMessage(error)}`);
    } finally {
      setLoading(false);
    }
  }, [mergeDetail, message, searchParams, selectedRunId, setSearchParams, workspaceId]);

  useEffect(() => { void load(); }, [load]);

  const currentStatus = currentRunStatus(runDetail, selectedRun);
  const currentStage = currentRunStage(runDetail, selectedRun);
  useEffect(() => {
    const interval = pollingInterval(currentStatus);
    if (!interval) return undefined;
    const timer = window.setInterval(() => { void load(); }, interval);
    return () => window.clearInterval(timer);
  }, [currentStatus, load]);

  useEffect(() => {
    setSelectedExternalCandidateIds([]);
  }, [selectedRunId, currentStatus, currentStage]);

  useEffect(() => {
    const claimText = searchParams.get("claim_text");
    if (searchParams.get("claim_item_id")) {
      setRunModalOpen(true);
      form.setFieldsValue({ topic: claimText ?? "" });
    }
  }, [form, searchParams]);

  const openRun = (runId: string) => {
    const next = new URLSearchParams(searchParams);
    next.set("run", runId);
    setSearchParams(next);
  };

  const submitRun = async (values: Record<string, unknown>) => {
    if (!workspaceId) return;
    setSubmitting(true);
    try {
      const sourcePaperId = searchParams.get("source_paper_id") || undefined;
      const response = await discoverApi.createRun(workspaceId, {
        input: {
          topic: String(values.topic || "").trim() || undefined,
          claim_item_id: searchParams.get("claim_item_id") || undefined,
          paper_ids: sourcePaperId ? [sourcePaperId] : [],
          constraints: String(values.constraints || "").trim() || undefined,
          keywords: String(values.keywords || "").split(/[,\n]/).map((value) => value.trim()).filter(Boolean),
        },
        scope: { year_from: values.year_from ? Number(values.year_from) : undefined, year_to: values.year_to ? Number(values.year_to) : undefined, open_access_preferred: Boolean(values.open_access_preferred) },
        config: { max_opportunities: Number(values.max_opportunities || 3), top_k: 10, include_counter_evidence: true, use_reranker: true, use_judge: true },
      });
      message.success(`研究机会发现任务已启动（${response.run_id.slice(0, 8)}）`);
      setRunModalOpen(false);
      form.resetFields();
      const next = new URLSearchParams(searchParams);
      next.delete("claim_item_id"); next.delete("claim_text"); next.delete("source_paper_id"); next.set("run", response.run_id);
      setSearchParams(next);
      await load();
    } catch (error) { message.error(`无法启动研究机会发现任务：${errorMessage(error)}`); }
    finally { setSubmitting(false); }
  };

  const openOpportunity = async (opportunityId: string) => {
    if (!workspaceId) return;
    try { setSelectedOpportunity(await discoverApi.getOpportunity(workspaceId, opportunityId)); }
    catch (error) { message.error(`加载研究机会候选失败：${errorMessage(error)}`); }
  };

  useEffect(() => {
    if (selectedOpportunityId) void openOpportunity(selectedOpportunityId);
  }, [selectedOpportunityId, workspaceId]);

  const openDecision = (action: "confirm" | "reject" | "defer") => {
    setDecisionAction(action);
    decisionForm.resetFields();
    setDecisionModalOpen(true);
  };

  const submitDecision = async (values: { note?: string; defer_condition?: string }) => {
    if (!workspaceId || !selectedOpportunity) return;
    setActionLoading(true);
    try {
      const item = selectedOpportunity.opportunity;
      if (decisionAction === "confirm") await discoverApi.confirm(workspaceId, item.id, selectedOpportunity.current_version?.id, values.note);
      if (decisionAction === "reject") await discoverApi.reject(workspaceId, item.id, values.note);
      if (decisionAction === "defer") await discoverApi.defer(workspaceId, item.id, values.note, values.defer_condition);
      setDecisionModalOpen(false);
      message.success(decisionAction === "confirm" ? "研究机会已确认" : decisionAction === "reject" ? "研究机会已驳回" : "研究机会已暂缓");
      await openOpportunity(item.id); await load();
    } catch (error) { message.error(`操作失败：${errorMessage(error)}`); }
    finally { setActionLoading(false); }
  };

  const submitEditConfirm = async (values: Record<string, unknown>) => {
    if (!workspaceId || !selectedOpportunity?.current_version) return;
    setActionLoading(true);
    try {
      const item = selectedOpportunity.opportunity;
      const { note, ...changes } = values;
      await discoverApi.editConfirm(workspaceId, item.id, { base_version_id: selectedOpportunity.current_version.id, changes, note: String(note || "") || undefined });
      setEditModalOpen(false);
      message.success("研究机会已编辑并确认");
      await openOpportunity(item.id); await load();
    } catch (error) { message.error(`编辑失败：${errorMessage(error)}`); }
    finally { setActionLoading(false); }
  };

  const reassessOpportunity = async () => {
    if (!workspaceId || !selectedOpportunity) return;
    setActionLoading(true);
    try {
      const item = selectedOpportunity.opportunity;
      await discoverApi.reassess(workspaceId, item.id);
      message.success("已根据保存的全文证据重新计算覆盖率与确认门槛");
      await openOpportunity(item.id);
      await load();
    } catch (error) { message.error(`证据重新评估失败：${errorMessage(error)}`); }
    finally { setActionLoading(false); }
  };

  const convert = async () => {
    if (!workspaceId || !selectedOpportunity) return;
    setActionLoading(true);
    try { await discoverApi.convert(workspaceId, selectedOpportunity.opportunity.id); message.success("研究计划已生成"); await openOpportunity(selectedOpportunity.opportunity.id); await load(); }
    catch (error) { message.error(`研究计划生成失败：${errorMessage(error)}`); }
    finally { setActionLoading(false); }
  };

  const submitExternalSelection = async (candidateIds: string[]) => {
    if (!workspaceId || !runDetail || candidateIds.length === 0) return;
    if (!externalSelectionIsOpen(currentStatus, currentStage)) {
      message.warning("当前任务已不处于外部论文选择阶段，请刷新页面查看最新进度。");
      return;
    }
    setActionLoading(true);
    try {
      await discoverApi.selectExternal(workspaceId, runDetail.id, candidateIds);
      setSelectedExternalCandidateIds([]);
      message.success(`已提交 ${candidateIds.length} 篇论文，正在下载并执行全文核验`);
      await load();
    } catch (error) {
      message.error(`外部论文提交失败：${errorMessage(error)}`);
      await load();
    } finally {
      setActionLoading(false);
    }
  };

  const selectExternal = async (candidate: DiscoverExternalCandidate) => {
    await submitExternalSelection([candidate.id]);
  };

  const toggleExternalCandidate = (candidateId: string, checked: boolean) => {
    setSelectedExternalCandidateIds((current) => checked
      ? Array.from(new Set([...current, candidateId]))
      : current.filter((id) => id !== candidateId));
  };

  const skipExternalSelection = () => {
    if (!workspaceId || !runDetail) return;
    modal.confirm({
      title: "是否跳过外部论文核验？",
      content: "系统将只使用当前工作区已有的证据继续运行。外部候选论文不会被导入或解析，因此对创新性和已有工作的核验可能不够完整。",
      okText: "跳过并继续",
      cancelText: "继续选择",
      onOk: async () => {
        setActionLoading(true);
        try {
          await discoverApi.skipExternalSelection(workspaceId, runDetail.id);
          message.success("已跳过外部论文选择，系统将使用工作区现有证据继续运行");
          await load();
        } catch (error) {
          message.error(`无法跳过外部论文选择：${errorMessage(error)}`);
        } finally {
          setActionLoading(false);
        }
      },
    });
  };

  const cancelRun = async () => {
    if (!workspaceId || !selectedRun) return;
    try { await discoverApi.cancelRun(workspaceId, selectedRun.id); message.success("研究机会发现任务已取消"); await load(); }
    catch (error) { message.error(`取消失败：${errorMessage(error)}`); }
  };
  const deleteRun = async (run: DiscoverRun) => {
    if (!workspaceId) return;
    try {
      await discoverApi.deleteRun(workspaceId, run.id);
      if (selectedRunId === run.id) {
        const next = new URLSearchParams(searchParams);
        next.delete("run");
        setSearchParams(next, { replace: true });
        setRunDetail(null);
      }
      message.success("该任务已从运行历史中删除");
      await load();
    } catch (error) {
      message.error(`删除失败：${errorMessage(error)}`);
    }
  };

  if (!workspaceId) return <Empty description="未找到课题空间" />;
  const stage = currentStage;
  const stagePosition = stageIndex(stage);
  const activeRun = runDetail?.id === selectedRun?.id ? runDetail : selectedRun;
  const externalSelectionOpen = externalSelectionIsOpen(currentStatus, stage);
  const stageIssues = DISCOVER_STAGES.flatMap((item) => {
    if (item === "external_search") return [];
    const status = stageSummaryStatus(activeRun?.stage_summaries, item);
    const detail = stageSummaryMessage(activeRun?.stage_summaries, item);
    return status && detail && ["failed", "succeeded_partial", "succeeded_empty"].includes(status)
      ? [{ stage: item, status, detail }]
      : [];
  });
  const selectedOpportunities = opportunities.filter((item) => !selectedRun || item.discover_run_id === selectedRun.id);

  return (
    <div style={{ padding: screens.md ? 24 : 12 }}>
      <Space direction="vertical" size={20} style={{ width: "100%" }}>
        <Space style={{ width: "100%", justifyContent: "space-between" }} wrap>
          <div><Title level={2} style={{ margin: 0 }}>研究机会发现工作台</Title><Text type="secondary">基于证据的研究机会发现与核验</Text></div>
          <Space wrap><Button icon={<ReloadOutlined />} onClick={() => void load()} loading={loading}>刷新</Button>{selectedRun && !TERMINAL_RUN_STATUSES.has(selectedRun.status) && <Button danger icon={<CloseCircleOutlined />} onClick={() => void cancelRun()}>取消本次运行</Button>}<Button type="primary" icon={<PlusOutlined />} onClick={() => setRunModalOpen(true)}>新建发现任务</Button></Space>
        </Space>
        <div style={{ display: "grid", gridTemplateColumns: screens.md ? "minmax(230px, 0.28fr) minmax(0, 0.72fr)" : "minmax(0, 1fr)", gap: 20 }}>
          <Card title={`运行历史（${runs.length}）`} bodyStyle={{ padding: 0 }}>
            <List dataSource={runs} locale={{ emptyText: "暂无发现任务" }} renderItem={(run) => <List.Item onClick={() => openRun(run.id)} actions={[<Popconfirm key="delete" title="删除这次发现任务？" description="该任务将从运行历史中隐藏；工作区论文、PDF、研究机会和研究计划均会保留。" okText="删除" cancelText="取消" okButtonProps={{ danger: true }} onConfirm={() => void deleteRun(run)}><Button type="text" danger disabled={!TERMINAL_RUN_STATUSES.has(run.status)} title={TERMINAL_RUN_STATUSES.has(run.status) ? "删除任务" : "请先取消任务再删除"} aria-label={`删除 ${run.input_topic || "发现任务"}`} icon={<DeleteOutlined />} onClick={(event) => event.stopPropagation()} /></Popconfirm>]} style={{ cursor: "pointer", padding: "14px 16px", background: selectedRun?.id === run.id ? "var(--gm-hover)" : undefined }}><List.Item.Meta avatar={<BulbOutlined />} title={<Text ellipsis>{run.input_topic || "基于论断的研究机会发现"}</Text>} description={<Space wrap><Tag color={statusColor(run.status)}>{getStatusMeta(run.status).label}</Tag><Text type="secondary">{Math.round(run.progress * 100)}%</Text><Text type="secondary">{discoverStageLabel(run.stage)}</Text></Space>} /></List.Item>} />
          </Card>
          <Space direction="vertical" size={16} style={{ width: "100%" }}>
            <Card title="运行概览" extra={selectedRun && <Space wrap><Tag color={statusColor(selectedRun.status)}>{getStatusMeta(selectedRun.status).label}</Tag>{selectedRun.status === "waiting_for_fulltext" && <Tag color="orange">等待 PDF 流水线</Tag>}</Space>}>
              {!selectedRun ? <Empty description="新建任务后可在这里查看进度和候选结果" /> : <>
                <Descriptions size="small" column={{ xs: 1, sm: 2 }}><Descriptions.Item label="研究主题">{selectedRun.input_topic || "基于论断的发现任务"}</Descriptions.Item><Descriptions.Item label="核验状态">{discoverRunVerificationStatusLabel(selectedRun.verification_status, selectedRun.status, stage, activeRun?.stage_summaries)}</Descriptions.Item><Descriptions.Item label="当前阶段">{discoverStageLabel(stage)}</Descriptions.Item><Descriptions.Item label="研究机会候选数">{selectedOpportunityCount(opportunities, selectedRun.id)}</Descriptions.Item></Descriptions>
                <Progress percent={Math.round((runDetail?.id === selectedRun.id ? runDetail.progress : selectedRun.progress) * 100)} status={selectedRun.status === "failed" ? "exception" : undefined} />
                {stagePosition < 0 ? <Tag color="red">未知阶段：{stage || "未提供"}</Tag> : <Steps size="small" current={stagePosition} responsive items={DISCOVER_STAGES.map((item, index) => { const summaryStatus = stageSummaryStatus(activeRun?.stage_summaries, item); const detail = stageSummaryMessage(activeRun?.stage_summaries, item); const failed = summaryStatus === "failed"; const partial = summaryStatus === "succeeded_partial" || summaryStatus === "succeeded_empty"; const visualStatus = failed ? "error" : index < stagePosition || (index === stagePosition && TERMINAL_RUN_STATUSES.has(activeRun?.status || "")) ? "finish" : index === stagePosition ? "process" : "wait"; return { status: visualStatus as "error" | "finish" | "process" | "wait", title: <Tooltip title={detail || DISCOVER_STAGE_LABELS[item]}><span style={partial ? { color: "#d48806" } : undefined}>{DISCOVER_STAGE_LABELS[item]}{partial ? " ⚠" : ""}</span></Tooltip> }; })} />}
                {stageIssues.length > 0 && <Alert style={{ marginTop: 16 }} type={stageIssues.some((item) => item.status === "failed") ? "error" : "warning"} showIcon message="其他核验阶段需要注意" description={<Space direction="vertical" size={2}>{stageIssues.map((item) => <Text key={item.stage}><Text strong>{DISCOVER_STAGE_LABELS[item.stage]}：</Text>{item.detail}</Text>)}</Space>} />}
                {selectedRun.status === "waiting_for_fulltext" && <Paragraph type="warning" style={{ marginTop: 16 }}>已选论文正在进行 PDF 解析、知识抽取和向量索引。流水线完成前，候选综合将暂停。</Paragraph>}
                {externalSelectionOpen && <Alert style={{ marginTop: 16 }} type="info" showIcon message="外部候选论文已准备好" description={<Space direction="vertical" size={8}><Text>可以勾选一篇或多篇论文统一导入并执行全文核验，也可以只使用当前工作区已有证据继续。</Text><Button onClick={skipExternalSelection} loading={actionLoading}>跳过外部论文核验并继续</Button></Space>} />}
                {selectedRun.error_message && <Paragraph type="danger" style={{ marginTop: 16 }}>{selectedRun.error_message}</Paragraph>}
              </>}
            </Card>
            <Card size="small" title="Multi-agent handoff">
              {!runDetail?.agent_steps?.length ? <Empty description="The run records Planner → Evidence → External → Critic → Gate as it executes" image={Empty.PRESENTED_IMAGE_SIMPLE} /> : <List size="small" dataSource={runDetail.agent_steps} renderItem={(step) => { const verdicts = step.stage === "critic" ? (step.details?.verdicts as Record<string, number> | undefined) : undefined; const narrowing = step.stage === "narrowing" ? (step.details?.narrowed as number | undefined) : undefined; return <List.Item><Space direction="vertical" size={2} style={{ width: "100%" }}><Space wrap><Tag color={agentStepColor(step.status)}>{step.status}</Tag><Text strong style={{ textTransform: "capitalize" }}>{step.stage.replaceAll("_", " ")}</Text><Text type="secondary">step {step.sequence}</Text></Space><Text type="secondary">{step.summary}</Text>{verdicts ? <Space wrap>{(["keep", "narrow", "reject"] as const).map((key) => <Tag key={key} color={key === "reject" ? "red" : key === "narrow" ? "orange" : "green"}>{key}: {verdicts[key] ?? 0}</Tag>)}</Space> : null}{narrowing ? <Text type="secondary">Focused counter-evidence pass narrowed {narrowing} candidate(s)</Text> : null}</Space></List.Item>; }} />}
            </Card>
            <Card title={`本次运行的研究机会候选（${selectedOpportunities.length}）`}>
      {selectedOpportunities.length === 0 ? <Empty description={selectedRun?.status === "waiting_for_fulltext" ? "完成全文核验后将生成候选结果" : "候选综合完成后将在这里显示结果"} /> : <List dataSource={selectedOpportunities} renderItem={(item) => { const displayStatus = opportunityStatus(item); return <List.Item actions={[<Button key="open" type="link" onClick={() => void openOpportunity(item.id)}>查看详情</Button>]}><List.Item.Meta title={<Space wrap><Text strong>{localizedGeneratedText(item.title)}</Text><Tag color={statusColor(displayStatus)}>{opportunityStatusLabel(displayStatus)}</Tag></Space>} description={<Paragraph ellipsis={{ rows: 2 }} style={{ margin: 0 }}>{localizedGeneratedText(item.summary)}</Paragraph>} /><Tag>智能体置信度 {Math.round(item.confidence * 100)}%</Tag></List.Item>; }} />}
            </Card>
            {runDetail?.external_candidates?.length ? (
              <Card
                title="待核验的外部候选论文"
                extra={externalSelectionOpen ? (
                  <Space wrap>
                    <Text type="secondary">已选 {selectedExternalCandidateIds.length} 篇</Text>
                    <Button
                      type="primary"
                      size="small"
                      disabled={selectedExternalCandidateIds.length === 0}
                      loading={actionLoading}
                      onClick={() => void submitExternalSelection(selectedExternalCandidateIds)}
                    >
                      导入并核验所选论文
                    </Button>
                    <Button size="small" onClick={skipExternalSelection} loading={actionLoading}>跳过选择</Button>
                  </Space>
                ) : <Text type="secondary">当前阶段不可再选择论文</Text>}
              >
                <List
                  size="small"
                  dataSource={runDetail.external_candidates}
                  renderItem={(candidate) => {
                    const selectable = canSelectExternalCandidate(currentStatus, stage, candidate.verification_status);
                    const pdfStatus = pdfAcquisitionLabel(candidate);
                    return (
                      <List.Item
                        actions={[
                          <Button
                            key="select"
                            size="small"
                            onClick={() => void selectExternal(candidate)}
                            disabled={!selectable || actionLoading || selectedExternalCandidateIds.length > 0}
                            title={selectedExternalCandidateIds.length > 0 ? "已进入批量选择，请使用上方按钮统一提交" : undefined}
                          >
                            {externalCandidateActionLabel(currentStatus, stage, candidate.verification_status)}
                          </Button>,
                        ]}
                      >
                        <List.Item.Meta
                          avatar={(
                            <Checkbox
                              checked={selectedExternalCandidateIds.includes(candidate.id)}
                              disabled={!selectable || actionLoading}
                              onChange={(event) => toggleExternalCandidate(candidate.id, event.target.checked)}
                              aria-label={`选择外部论文 ${candidate.title}`}
                            />
                          )}
                          title={`${candidate.rank}. ${candidate.title}`}
                          description={(
                            <Space wrap>
                              <Tag>{candidate.year || "年份未知"}</Tag>
                              <Tag>{evidenceLevelLabel(candidate.evidence_level)}</Tag>
                              <Tag color={candidate.verification_status === "verified" ? "green" : ["verification_failed", "import_failed", "no_pdf"].includes(candidate.verification_status) ? "red" : "processing"}>
                                {verificationStatusLabel(candidate.verification_status)}
                              </Tag>
                              <Tag>{externalRoleLabel(candidate.role)}</Tag>
                              {pdfStatus ? <Text type="secondary">{pdfStatus}</Text> : null}
                            </Space>
                          )}
                        />
                      </List.Item>
                    );
                  }}
                />
              </Card>
            ) : null}
          </Space>
        </div>
      </Space>

      <Modal title="新建研究机会发现任务" open={runModalOpen} onCancel={() => setRunModalOpen(false)} onOk={() => void form.submit()} okText="开始运行" cancelText="取消" confirmLoading={submitting} width={640}>
        <Form form={form} layout="vertical" onFinish={(values) => void submitRun(values)} initialValues={{ max_opportunities: 3, topic: searchParams.get("claim_text") || undefined }}>
          {searchParams.get("claim_item_id") && <AlertText text={`种子论断：${searchParams.get("claim_text") || "已选择的论断"}。其来源论文将不会被当作反证。`} />}
          <Form.Item name="topic" label="研究主题或问题" rules={[{ required: true, message: "请输入研究主题或问题" }]}><Input.TextArea rows={4} placeholder="例如：分布偏移条件下稳健的自解释图神经网络" /></Form.Item>
          <Form.Item name="keywords" label="关键词"><Input placeholder="多个关键词请用逗号分隔" /></Form.Item>
          <Space wrap style={{ width: "100%" }}><Form.Item name="year_from" label="起始年份"><Input type="number" placeholder="2020" /></Form.Item><Form.Item name="year_to" label="截止年份"><Input type="number" placeholder="2026" /></Form.Item><Form.Item name="max_opportunities" label="最大候选数"><Select options={[1, 2, 3, 5].map((value) => ({ value, label: String(value) }))} /></Form.Item></Space>
          <Form.Item name="constraints" label="研究约束"><Input.TextArea rows={3} placeholder="数据集、算力、时间或领域约束" /></Form.Item>
          <Form.Item name="open_access_preferred" valuePropName="checked"><Checkbox>优先选择可开放获取的论文进行核验</Checkbox></Form.Item>
          <Text type="secondary">任务将在后台异步运行；仅有元数据的论文不能计为全文支持证据。</Text>
        </Form>
      </Modal>

      <Drawer title={localizedGeneratedText(selectedOpportunity?.opportunity.title)} open={selectedOpportunity !== null} width="min(760px, 100vw)" onClose={() => setSelectedOpportunity(null)}>
        {selectedOpportunity && <OpportunityPanel workspaceId={workspaceId} detail={selectedOpportunity} loading={actionLoading} onAction={openDecision} onEdit={() => { const version = selectedOpportunity.current_version; if (version) { editForm.setFieldsValue({ ...version, note: "" }); setEditModalOpen(true); } }} onConvert={() => void convert()} onReassess={() => void reassessOpportunity()} />}
      </Drawer>

      <Modal title={decisionAction === "confirm" ? "确认研究机会" : decisionAction === "reject" ? "驳回研究机会" : "暂缓研究机会"} open={decisionModalOpen} confirmLoading={actionLoading} okText="提交" cancelText="取消" onCancel={() => setDecisionModalOpen(false)} onOk={() => void decisionForm.submit()}>
        <Form form={decisionForm} layout="vertical" onFinish={(values) => void submitDecision(values)}><Form.Item name="note" label={decisionAction === "confirm" ? "审阅备注" : "处理原因"}><Input.TextArea rows={3} placeholder={decisionAction === "confirm" ? "可选备注" : "请说明本次决定的原因"} /></Form.Item>{decisionAction === "defer" && <Form.Item name="defer_condition" label="重新审阅条件" rules={[{ required: true, message: "请说明何时应再次审阅" }]}><Input.TextArea rows={3} placeholder="例如：再导入两篇全文论文后重新审阅" /></Form.Item>}</Form>
      </Modal>
      <Modal title="编辑并确认研究机会" open={editModalOpen} width={720} confirmLoading={actionLoading} okText="保存并确认" cancelText="取消" onCancel={() => setEditModalOpen(false)} onOk={() => void editForm.submit()}>
        <Form form={editForm} layout="vertical" onFinish={(values) => void submitEditConfirm(values)}><Form.Item name="title" label="标题" rules={[{ required: true }]}><Input /></Form.Item><Form.Item name="problem_statement" label="问题陈述" rules={[{ required: true }]}><Input.TextArea rows={3} /></Form.Item><Form.Item name="research_scope" label="研究范围"><Input.TextArea rows={2} /></Form.Item><Form.Item name="why_existing_work_is_insufficient" label="现有工作为何不足"><Input.TextArea rows={3} /></Form.Item><Form.Item name="candidate_research_question" label="研究问题"><Input.TextArea rows={2} /></Form.Item><Form.Item name="candidate_hypothesis" label="可证伪假设"><Input.TextArea rows={2} /></Form.Item><Form.Item name="note" label="编辑备注"><Input.TextArea rows={2} /></Form.Item></Form>
      </Modal>
    </div>
  );
}

function AlertText({ text }: { text: string }) {
  return <Paragraph type="secondary" style={{ background: "#f5f5f5", padding: 10, borderRadius: 6 }}>{text}</Paragraph>;
}

function OpportunityPanel({ workspaceId, detail, loading, onAction, onEdit, onConvert, onReassess }: { workspaceId: string; detail: OpportunityDetail; loading: boolean; onAction: (action: "confirm" | "reject" | "defer") => void; onEdit: () => void; onConvert: () => void; onReassess: () => void }) {
  const version = detail.current_version;
  const gate = gateDetails(detail.opportunity.source_payload);
  const confirmable = gate?.confirmable ?? detail.opportunity.status !== "needs_more_evidence";
  const supporting = detail.evidence.filter((item) => item.relation === "supports");
  const similar = detail.evidence.filter((item) => item.relation === "similar");
  const counter = detail.evidence.filter((item) => ["contradicts", "qualifies", "overlaps", "unknown"].includes(item.relation));
  const versionById = new Map(detail.versions.map((item) => [item.id, item.version_number]));
  const decisionLabel = (action: string) => ({ confirm: "确认", edit_confirm: "编辑确认", reject: "驳回", defer: "暂缓" }[action] ?? action);
  const decisionColor = (action: string) => ({ confirm: "green", edit_confirm: "blue", reject: "red", defer: "orange" }[action] ?? "default");
  return <Space direction="vertical" style={{ width: "100%" }}>
    <Space wrap><Tag color={statusColor(opportunityStatus(detail.opportunity))}>{opportunityStatusLabel(opportunityStatus(detail.opportunity))}</Tag><Tag color={statusColor(version?.verification_status || "unverified")}>{verificationDisplayLabel(version?.verification_status)}</Tag><Tag>证据覆盖率 {Math.round((version?.evidence_coverage || 0) * 100)}%</Tag><Tag>智能体置信度 {Math.round(detail.opportunity.confidence * 100)}%</Tag></Space>
    <Alert type="info" showIcon message="置信度不等于证据覆盖率" description="智能体置信度只是候选排序信号；是否可以确认，以独立全文证据、证据门、核验状态和人工决策为准。" />
    {!confirmable && <Alert type="warning" showIcon message="该研究机会目前还不能确认" description={gate?.blockingMissing.length ? <List size="small" dataSource={gate.blockingMissing} renderItem={(item) => <List.Item>{gateMessageLabel(item)}</List.Item>} /> : gateMessageLabel(gate?.reason || "核心证据门槛尚未满足。") } action={supporting.length ? <Button size="small" onClick={onReassess} loading={loading}>重新评估证据</Button> : undefined} />}
    {confirmable && gate?.warnings.length ? <Alert type="info" showIcon message="可以确认，但仍有核验警告" description={<List size="small" dataSource={gate.warnings} renderItem={(item) => <List.Item>{gateMessageLabel(item)}</List.Item>} />} /> : null}
    <EvidencePassportCard manifest={detail.evidence_manifest} />
    <Divider orientation="left">概述</Divider><Paragraph>{localizedGeneratedText(version?.problem_statement || detail.opportunity.summary)}</Paragraph>
    <Descriptions column={1} size="small"><Descriptions.Item label="研究范围">{localizedGeneratedText(version?.research_scope)}</Descriptions.Item><Descriptions.Item label="现有工作为何不足">{localizedGeneratedText(version?.why_existing_work_is_insufficient || detail.opportunity.rationale)}</Descriptions.Item><Descriptions.Item label="研究问题">{localizedGeneratedText(version?.candidate_research_question)}</Descriptions.Item><Descriptions.Item label="候选假设">{localizedGeneratedText(version?.candidate_hypothesis)}</Descriptions.Item></Descriptions>
    {gate && <Alert type="info" showIcon message={`界面展示 ${supporting.length} 条支持类证据；其中 ${Number((detail.opportunity.source_payload.gate as Record<string, unknown> | undefined)?.supporting_evidence_count || 0)} 篇独立全文证据通过当前确认门槛`} description="支持类证据还可能包含反证检索阶段被判定为支持的片段；确认门槛只统计候选问题专门检索得到、具有全文锚点且来自不同论文的证据。" />}
    <EvidenceGroup workspaceId={workspaceId} title={`支持证据（${supporting.length}）`} items={supporting} empty="暂无可定位到原文的支持证据" />
    <EvidenceGroup workspaceId={workspaceId} title={`相似工作（${similar.length}）`} items={similar} empty="暂无已保存的相似工作" />
    <EvidenceGroup workspaceId={workspaceId} title={`反证／限定性证据（${counter.length}）`} items={counter} empty="暂无已保存的反证或限定性证据" />
    <Divider orientation="left">验证方案</Divider><List size="small" dataSource={(version?.candidate_validation_plan?.steps as string[]) || []} renderItem={(step) => <List.Item>{localizedGeneratedText(step)}</List.Item>} locale={{ emptyText: "暂无结构化验证步骤" }} />
    <Divider orientation="left">人工决策</Divider><Space wrap><Button danger onClick={() => onAction("reject")} loading={loading}>驳回</Button><Button onClick={() => onAction("defer")} loading={loading}>暂缓</Button><Button onClick={onEdit} loading={loading} disabled={!confirmable}>编辑并确认</Button><Button type="primary" onClick={() => onAction("confirm")} loading={loading} disabled={!confirmable}>确认</Button>{["confirmed", "edited_confirmed"].includes(detail.opportunity.status) && <Button onClick={onConvert} loading={loading}>生成研究计划</Button>}</Space>
    <Divider orientation="left">决策历史（HITL 追溯）</Divider>
    {detail.decisions.length === 0
      ? <Text type="secondary">暂无人工决策记录；每次确认 / 编辑确认 / 驳回 / 暂缓都会在此留痕，并同步写入工作区时间线。</Text>
      : <List size="small" dataSource={detail.decisions} renderItem={(decision) => (
        <List.Item>
          <Space direction="vertical" size={2} style={{ width: "100%" }}>
            <Space wrap>
              <Tag color={decisionColor(decision.action)}>{decisionLabel(decision.action)}</Tag>
              <Text type="secondary">{new Date(decision.created_at).toLocaleString()}</Text>
              <Text type="secondary">执行人 {decision.actor}</Text>
              <Text type="secondary">版本 v{versionById.get(decision.to_version_id) ?? "—"}</Text>
            </Space>
            {decision.reason && <Text type="secondary">备注：{decision.reason}</Text>}
            {decision.defer_condition && <Text type="secondary">重新审阅条件：{decision.defer_condition}</Text>}
          </Space>
        </List.Item>
      )} />}
    {detail.versions.length > 1 && (
      <Card size="small" title="版本历史（不可变）">
        <List size="small" dataSource={[...detail.versions].sort((a, b) => b.version_number - a.version_number)} renderItem={(item) => (
          <List.Item><Space wrap>
            <Tag>v{item.version_number}</Tag>
            <Text>{item.created_by === "user" ? "人工编辑确认" : "AI 生成"}</Text>
            <Text type="secondary">{new Date(item.created_at).toLocaleString()}</Text>
            {item.id === detail.current_version?.id && <Tag color="blue">当前</Tag>}
          </Space></List.Item>
        )} />
      </Card>
    )}
    {detail.plan && <Card size="small" title="已生成研究计划"><Paragraph>{detail.plan.research_question}</Paragraph></Card>}
  </Space>;
}

function EvidenceGroup({ workspaceId, title, items, empty }: { workspaceId: string; title: string; items: OpportunityDetail["evidence"]; empty: string }) {
  return <Card size="small" title={title}><List size="small" dataSource={items} locale={{ emptyText: empty }} renderItem={(evidence) => <List.Item><Space direction="vertical" style={{ width: "100%" }}><Space wrap><Tag color={evidence.relation === "contradicts" ? "red" : evidence.relation === "supports" ? "green" : "blue"}>{evidenceRelationLabel(evidence.relation)}</Tag><Tag>{evidenceSourceScopeLabel(evidence.source_scope)}</Tag><Tag color={evidence.evidence_level === "full_text" ? "green" : "orange"}>{evidenceLevelDisplayLabel(evidence.evidence_level)}</Tag></Space><Text>{evidence.display_excerpt || "暂无证据摘录"}</Text><OpportunityEvidenceViewer workspaceId={workspaceId} evidence={evidence} /></Space></List.Item>} /></Card>;
}

function EvidencePassportCard({ manifest }: { manifest: EvidenceManifest | null }) {
  if (!manifest) return null;
  const gateLabel = manifest.gate_verified ? "已通过" : manifest.gate_confirmable ? "可确认（带警告）" : "未通过";
  const criticLabel = manifest.critic_verdict === "reject" ? <Tag color="red">reject</Tag> : manifest.critic_verdict === "narrow" ? <Tag color="orange">narrow</Tag> : manifest.critic_verdict ? <Tag color="green">{manifest.critic_verdict}</Tag> : null;
  const narrowingLabel = manifest.narrowing_outcome === "obstacle_found" ? "发现反证障碍" : manifest.narrowing_outcome === "direction_clear" ? "收窄方向可行" : null;
  const freshnessLabel = manifest.evidence_freshness === "current" ? "当前快照" : manifest.evidence_freshness === "stale" ? "版本较旧" : manifest.evidence_freshness === "expired" ? "需要重新核验" : "未记录";
  const freshnessColor = manifest.evidence_freshness === "current" ? "green" : manifest.evidence_freshness === "expired" ? "red" : manifest.evidence_freshness === "stale" ? "orange" : "default";
  return (
    <Card size="small" title="证据可信度（Evidence Passport）">
      <Descriptions column={{ xs: 1, sm: 2 }} size="small">
        <Descriptions.Item label="证据覆盖">{manifest.evidence_coverage != null ? `${Math.round(manifest.evidence_coverage * 100)}%` : "—"}（{manifest.total} 条）</Descriptions.Item>
        <Descriptions.Item label="独立论文">{manifest.independent_papers} 篇</Descriptions.Item>
        <Descriptions.Item label="全文来源">{manifest.full_text_papers} 篇</Descriptions.Item>
        <Descriptions.Item label="元数据来源">{manifest.metadata_only_papers} 篇</Descriptions.Item>
        <Descriptions.Item label="支持 / 相似 / 反证">{manifest.supports} / {manifest.similar} / {manifest.counter}</Descriptions.Item>
        <Descriptions.Item label="外部来源">{manifest.external_sources} 条</Descriptions.Item>
        <Descriptions.Item label="证据门">{gateLabel}</Descriptions.Item>
        <Descriptions.Item label="核验状态">{verificationDisplayLabel(manifest.verification_status ?? "")}</Descriptions.Item>
        {criticLabel && <Descriptions.Item label="Critic 判定">{criticLabel}</Descriptions.Item>}
        {narrowingLabel && <Descriptions.Item label="收窄结果">{narrowingLabel}</Descriptions.Item>}
        <Descriptions.Item label="人工状态">{opportunityStatusLabel(manifest.human_status ?? "")}</Descriptions.Item>
        <Descriptions.Item label="证据新鲜度"><Tag color={freshnessColor}>{freshnessLabel}</Tag>{manifest.evidence_checked_at ? `（截至 ${new Date(manifest.evidence_checked_at).toLocaleString()}）` : ""}</Descriptions.Item>
        <Descriptions.Item label="Prompt / 模型">{manifest.prompt_version || "—"} / {manifest.model_name || "—"}</Descriptions.Item>
      </Descriptions>
    </Card>
  );
}
