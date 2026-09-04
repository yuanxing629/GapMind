import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  App,
  Button,
  Card,
  Empty,
  List,
  Space,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import {
  BookOutlined,
  BulbOutlined,
  CloudDownloadOutlined,
  CloseCircleOutlined,
  EyeOutlined,
  HeartOutlined,
  LinkOutlined,
  ReloadOutlined,
} from "@ant-design/icons";
import recommendationsApi, {
  type PaperRecommendation,
  type PaperRecommendationResponse,
} from "../api/recommendations";
import semanticScholarApi from "../api/semanticScholar";
import readingApi from "../api/reading";
import type { Paper } from "../api/types/domain";
import { recommendationErrorMessage } from "../state/recommendationState";

const { Paragraph, Text } = Typography;

function authorsLabel(paper: PaperRecommendation["paper"]): string {
  const names = (paper.authors ?? [])
    .map((author) => author?.name)
    .filter(Boolean) as string[];
  if (names.length <= 3) return names.join(", ") || "作者信息暂缺";
  return `${names.slice(0, 3).join(", ")} 等 ${names.length} 人`;
}

function yearLabel(paper: PaperRecommendation["paper"]): string {
  return paper.publicationDate?.slice(0, 4) || String(paper.year ?? "年份未知");
}

function abstractLabel(paper: PaperRecommendation["paper"]): string {
  return paper.tldr?.text || paper.abstract || "暂无摘要";
}

export default function ResearchRecommendations({
  workspaceId,
  onImported,
}: {
  workspaceId: string;
  onImported?: (paper: Paper) => void | Promise<void>;
}) {
  const { message } = App.useApp();
  const [data, setData] = useState<PaperRecommendationResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [actionId, setActionId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await recommendationsApi.list(workspaceId));
    } catch (requestError) {
      setError(recommendationErrorMessage(requestError));
    } finally {
      setLoading(false);
    }
  }, [workspaceId]);

  useEffect(() => {
    void load();
  }, [load]);

  const refresh = async () => {
    setRefreshing(true);
    setError(null);
    try {
      setData(await recommendationsApi.refresh(workspaceId));
      message.success("论文推荐已更新");
    } catch (requestError) {
      const displayError = recommendationErrorMessage(requestError);
      setError(displayError);
      message.error(`刷新推荐失败：${displayError}`);
    } finally {
      setRefreshing(false);
    }
  };

  const importPaper = async (item: PaperRecommendation, addToReading = false) => {
    setActionId(item.external_paper_id);
    try {
      const paper = await semanticScholarApi.importToWorkspace(
        workspaceId,
        item.external_paper_id,
      );
      await recommendationsApi.feedback(workspaceId, item.external_paper_id, addToReading ? "reading" : "imported");
      if (addToReading) await readingApi.add(paper.id);
      setData((current) =>
        current
          ? {
              ...current,
              items: current.items.map((candidate) =>
                candidate.external_paper_id === item.external_paper_id
                  ? { ...candidate, status: addToReading ? "reading" : "imported" }
                  : candidate,
              ),
            }
          : current,
      );
      await onImported?.(paper);
      message.success(addToReading ? "论文已导入并加入阅读库" : "论文已导入当前课题");
    } catch (requestError) {
      message.error(`导入论文失败：${recommendationErrorMessage(requestError)}`);
    } finally {
      setActionId(null);
    }
  };

  const favorite = async (item: PaperRecommendation) => {
    setActionId(item.external_paper_id);
    try {
      await semanticScholarApi.saveFavorite(item.paper);
      await recommendationsApi.feedback(workspaceId, item.external_paper_id, "favorite");
      message.success("已加入收藏");
    } catch (requestError) {
      message.error(`收藏失败：${recommendationErrorMessage(requestError)}`);
    } finally {
      setActionId(null);
    }
  };

  const dismiss = async (item: PaperRecommendation) => {
    try {
      await recommendationsApi.feedback(workspaceId, item.external_paper_id, "dismiss");
      setData((current) =>
        current
          ? { ...current, items: current.items.filter((candidate) => candidate.id !== item.id) }
          : current,
      );
      message.success("已减少此类推荐");
    } catch (requestError) {
      message.error(`操作失败：${recommendationErrorMessage(requestError)}`);
    }
  };

  const openPaper = async (item: PaperRecommendation) => {
    try {
      await recommendationsApi.feedback(workspaceId, item.external_paper_id, "open");
    } catch {
// 即使 feedback 失败，也应保持打开外部页面的能力。
    }
    if (item.paper.url) window.open(item.paper.url, "_blank", "noopener,noreferrer");
  };

  return (
    <Card
      title={
        <Space>
          <BulbOutlined style={{ color: "#1677ff" }} />
          <span>为你推荐的论文</span>
        </Space>
      }
      extra={
        <Button
          size="small"
          icon={<ReloadOutlined />}
          onClick={() => void refresh()}
          loading={refreshing}
        >
          刷新推荐
        </Button>
      }
      style={{ marginTop: 16 }}
    >
      {error && (
        <Alert
          type="warning"
          showIcon
          message={data ? "实时刷新失败，正在展示上次生成的推荐" : "暂时无法生成论文推荐"}
          description={error}
          action={<Button size="small" onClick={() => void load()}>重试</Button>}
          style={{ marginBottom: 16 }}
        />
      )}

      {data && (
        <>
          <Space wrap size={[6, 6]} style={{ marginBottom: 14 }}>
            <Text type="secondary">基于当前课题主题</Text>
            {data.profile_topics.map((topic) => <Tag key={topic} color="blue">{topic}</Tag>)}
            {data.stale && <Tag color="orange">推荐已超过 24 小时</Tag>}
          </Space>
          {!data.has_profile && (
            <Alert
              type="info"
              showIcon
              message="建议补充研究主题或关键词"
              description="当前推荐主要依据课题名称。完善课题设置后，推荐结果会更贴合你的研究方向。"
              style={{ marginBottom: 14 }}
            />
          )}
          {data.items.length ? (
            <List
              loading={loading}
              dataSource={data.items.slice(0, 10)}
              renderItem={(item) => {
                const paper = item.paper;
                const isBusy = actionId === item.external_paper_id;
                return (
                  <List.Item
                    key={item.id}
                    actions={[
                      <Tooltip key="dismiss" title="不感兴趣">
                        <Button type="text" icon={<CloseCircleOutlined />} onClick={() => void dismiss(item)} />
                      </Tooltip>,
                      <Button key="favorite" type="text" icon={<HeartOutlined />} onClick={() => void favorite(item)} disabled={isBusy}>收藏</Button>,
                      <Button key="open" type="text" icon={<EyeOutlined />} onClick={() => void openPaper(item)}>查看</Button>,
                    ]}
                  >
                    <List.Item.Meta
                      avatar={<BulbOutlined style={{ color: "#1677ff", fontSize: 18, marginTop: 4 }} />}
                      title={
                        <Space wrap>
                          <Typography.Link onClick={() => void openPaper(item)}>{paper.title || "未命名论文"}</Typography.Link>
                          <Tag color={item.status === "reading" ? "green" : item.status === "imported" ? "blue" : undefined}>
                            {item.status === "reading" ? "已加入阅读" : item.status === "imported" ? "已导入" : `相关度 ${Math.round(item.score * 100)}%`}
                          </Tag>
                        </Space>
                      }
                      description={
                        <Space direction="vertical" size={4} style={{ width: "100%" }}>
                          <Text type="secondary">{authorsLabel(paper)} · {yearLabel(paper)} · 引用 {paper.citationCount ?? "—"}</Text>
                          <Paragraph ellipsis={{ rows: 2 }} style={{ margin: 0 }}>{abstractLabel(paper)}</Paragraph>
                          <Space wrap size={[4, 4]}>
                            {item.topics.map((topic) => <Tag key={topic}>{topic}</Tag>)}
                            {paper.isOpenAccess && <Tag color="green">开放获取</Tag>}
                            {item.reasons.slice(0, 2).map((reason) => <Text key={reason} type="secondary">{reason}</Text>)}
                          </Space>
                          <Space wrap>
                            <Button size="small" type="primary" icon={<CloudDownloadOutlined />} loading={isBusy} onClick={() => void importPaper(item)}>导入课题</Button>
                            <Button size="small" icon={<BookOutlined />} loading={isBusy} onClick={() => void importPaper(item, true)}>导入并加入阅读</Button>
                            {paper.openAccessPdf?.url && <Button size="small" type="link" icon={<LinkOutlined />} href={paper.openAccessPdf.url} target="_blank" rel="noreferrer">PDF</Button>}
                          </Space>
                        </Space>
                      }
                    />
                  </List.Item>
                );
              }}
            />
          ) : loading ? <List loading dataSource={[1, 2, 3]} renderItem={() => <List.Item />} /> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂时没有找到新的论文" />}
        </>
      )}
      {!data && loading && <List loading dataSource={[1, 2, 3]} renderItem={() => <List.Item />} />}
    </Card>
  );
}
