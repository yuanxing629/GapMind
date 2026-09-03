import { useEffect, useRef, useState } from "react";
import {
  Alert,
  App,
  Button,
  Card,
  Empty,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from "antd";
import {
  ClockCircleOutlined,
  CloudDownloadOutlined,
  FilterOutlined,
  LinkOutlined,
  SearchOutlined,
  StarFilled,
  StarOutlined,
} from "@ant-design/icons";
import semanticScholarApi, {
  type SemanticScholarPaper,
  type SemanticScholarSearchHistory,
  type SemanticScholarSort,
} from "../api/semanticScholar";
import workspaceApi from "../api/workspace";
import type { Workspace } from "../api/types/workspace";
import type { Paper } from "../api/types/domain";

const { Paragraph, Text } = Typography;

const FIELD_OPTIONS = [
  "Computer Science",
  "Mathematics",
  "Engineering",
  "Medicine",
  "Physics",
  "Biology",
  "Chemistry",
  "Psychology",
  "Economics",
  "Environmental Science",
].map((value) => ({ label: value, value }));

const PUBLICATION_TYPE_OPTIONS = [
  "Review",
  "JournalArticle",
  "Conference",
  "Dataset",
  "Book",
  "BookSection",
  "MetaAnalysis",
  "Study",
].map((value) => ({ label: value, value }));

const SORT_OPTIONS: Array<{ label: string; value: SemanticScholarSort }> = [
  { label: "Relevance", value: "relevance" },
  { label: "Newest first", value: "publicationDate:desc" },
  { label: "Oldest first", value: "publicationDate:asc" },
  { label: "Most citations", value: "citationCount:desc" },
  { label: "Fewest citations", value: "citationCount:asc" },
];

function errorMessage(err: unknown): string {
  const detail = (
    err as {
      response?: { data?: { detail?: { message?: string } } };
    }
  ).response?.data?.detail;
  return detail?.message || (err as Error).message || "Request failed";
}

function authorsLabel(paper: SemanticScholarPaper): string {
  const names = (paper.authors ?? [])
    .map((author) => author?.name)
    .filter(Boolean) as string[];
  if (names.length <= 3) return names.join(", ") || "Unknown authors";
  return `${names.slice(0, 3).join(", ")} +${names.length - 3}`;
}

function paperYear(paper: SemanticScholarPaper): string {
  return paper.publicationDate?.slice(0, 4) || String(paper.year ?? "—");
}

export default function SemanticPaperSearch({
  workspaceId,
  onImported,
}: {
  workspaceId?: string;
  onImported?: (paper: Paper) => void | Promise<void>;
} = {}) {
  const { message } = App.useApp();
  const searchGeneration = useRef(0);
  const [query, setQuery] = useState("");
  const [searchedQuery, setSearchedQuery] = useState("");
  const [yearFrom, setYearFrom] = useState<number | null>(null);
  const [yearTo, setYearTo] = useState<number | null>(null);
  const [minCitations, setMinCitations] = useState<number | null>(null);
  const [openAccess, setOpenAccess] = useState(false);
  const [fieldsOfStudy, setFieldsOfStudy] = useState<string[]>([]);
  const [publicationTypes, setPublicationTypes] = useState<string[]>([]);
  const [venue, setVenue] = useState("");
  const [sort, setSort] = useState<SemanticScholarSort>("relevance");
  const [papers, setPapers] = useState<SemanticScholarPaper[]>([]);
  const [total, setTotal] = useState(0);
  const [nextOffset, setNextOffset] = useState<number | null>(null);
  const [nextToken, setNextToken] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [detailsPaper, setDetailsPaper] = useState<SemanticScholarPaper | null>(null);
  const [importPaper, setImportPaper] = useState<SemanticScholarPaper | null>(null);
  const [importWorkspaceId, setImportWorkspaceId] = useState<string>();
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [importLoading, setImportLoading] = useState(false);
  const [searchHistory, setSearchHistory] = useState<SemanticScholarSearchHistory[]>([]);
  const [favoritePapers, setFavoritePapers] = useState<SemanticScholarPaper[]>([]);
  const [searchStateDirty, setSearchStateDirty] = useState(false);

  useEffect(() => {
    searchGeneration.current += 1;
    setQuery("");
    setSearchedQuery("");
    setYearFrom(null);
    setYearTo(null);
    setMinCitations(null);
    setOpenAccess(false);
    setFieldsOfStudy([]);
    setPublicationTypes([]);
    setVenue("");
    setSort("relevance");
    setPapers([]);
    setTotal(0);
    setNextOffset(null);
    setNextToken(null);
    setLoading(false);
    setError(null);
    setDetailsPaper(null);
    setImportPaper(null);
    setImportWorkspaceId(undefined);
    setSearchStateDirty(false);
  }, [workspaceId]);

  useEffect(() => {
    void Promise.all([semanticScholarApi.listHistory(), semanticScholarApi.listFavorites()])
      .then(([history, favorites]) => {
        setSearchHistory(history);
        setFavoritePapers(favorites.map((favorite) => favorite.paper));
      })
      .catch(() => {
        // Search remains usable when history/favorites are unavailable.
      });
  }, []);

  const favoriteIds = new Set(favoritePapers.map((paper) => paper.paperId));

  const applyHistory = (historyId: string) => {
    const record = searchHistory.find((item) => item.id === historyId);
    if (!record) return;
    const filters = record.filters;
    setQuery(record.query);
    setYearFrom(typeof filters.year_from === "number" ? filters.year_from : null);
    setYearTo(typeof filters.year_to === "number" ? filters.year_to : null);
    setMinCitations(
      typeof filters.min_citation_count === "number" ? filters.min_citation_count : null,
    );
    setOpenAccess(filters.open_access === true);
    setFieldsOfStudy(Array.isArray(filters.fields_of_study) ? filters.fields_of_study as string[] : []);
    setPublicationTypes(Array.isArray(filters.publication_types) ? filters.publication_types as string[] : []);
    setVenue(typeof filters.venue === "string" ? filters.venue : "");
    setSort(record.sort);
    setPapers([]);
    setTotal(0);
    setNextOffset(null);
    setNextToken(null);
    setSearchStateDirty(true);
  };

  const toggleFavorite = async (paper: SemanticScholarPaper) => {
    try {
      if (favoriteIds.has(paper.paperId)) {
        await semanticScholarApi.deleteFavorite(paper.paperId);
        setFavoritePapers((current) => current.filter((item) => item.paperId !== paper.paperId));
        message.success("Removed from favorites");
      } else {
        await semanticScholarApi.saveFavorite(paper);
        setFavoritePapers((current) => [...current, paper]);
        message.success("Added to favorites");
      }
    } catch (err) {
      message.error(`Favorite update failed: ${errorMessage(err)}`);
    }
  };

  const runSearch = async (append: boolean) => {
    const activeQuery = (append ? searchedQuery : query).trim();
    if (!activeQuery) {
      message.warning("Enter a topic or keywords first.");
      return;
    }
    if (!append && yearFrom !== null && yearTo !== null && yearFrom > yearTo) {
      message.warning("The start year must not be later than the end year.");
      return;
    }

    const requestGeneration = ++searchGeneration.current;
    setLoading(true);
    setError(null);
    try {
      const response = await semanticScholarApi.search({
        query: activeQuery,
        year_from: yearFrom ?? undefined,
        year_to: yearTo ?? undefined,
        min_citation_count: minCitations ?? undefined,
        open_access: openAccess || undefined,
        fields_of_study: fieldsOfStudy?.length ? fieldsOfStudy.join(",") : undefined,
        publication_types: publicationTypes?.length ? publicationTypes.join(",") : undefined,
        venue: venue.trim() || undefined,
        sort,
        limit: 20,
        offset: append && sort === "relevance" ? nextOffset ?? papers.length : 0,
        token: append && sort !== "relevance" ? nextToken ?? undefined : undefined,
      });

      if (requestGeneration !== searchGeneration.current) return;
      setSearchedQuery(activeQuery);
      setPapers((previous) => (append ? [...previous, ...response.data] : response.data));
      setTotal(response.total);
      setNextOffset(response.next ?? null);
      setNextToken(response.token ?? null);
      setSearchStateDirty(false);
      if (!append) {
        void semanticScholarApi.listHistory().then(setSearchHistory).catch(() => {
          // Search results remain usable when history refresh is unavailable.
        });
      }
    } catch (err) {
      if (requestGeneration !== searchGeneration.current) return;
      setError(errorMessage(err));
      if (!append) setPapers([]);
    } finally {
      if (requestGeneration === searchGeneration.current) setLoading(false);
    }
  };

  const openImportModal = async (paper: SemanticScholarPaper) => {
    setImportPaper(paper);
    setImportWorkspaceId(workspaceId);
    if (workspaceId) return;
    try {
      const response = await workspaceApi.list({ limit: 200 });
      setWorkspaces(response.items);
    } catch (err) {
      message.error(`Failed to load workspaces: ${errorMessage(err)}`);
    }
  };

  const handleImport = async () => {
    const targetWorkspaceId = workspaceId || importWorkspaceId;
    if (!importPaper || !targetWorkspaceId) {
      message.warning("请先选择目标课题。");
      return;
    }
    setImportLoading(true);
    try {
      const imported = await semanticScholarApi.importToWorkspace(targetWorkspaceId, importPaper.paperId);
      message.success(
        imported.primary_artifact_id
          ? "Paper metadata imported; open-access PDF processing was queued."
          : "Paper metadata imported into the workspace.",
      );
      setImportPaper(null);
      await onImported?.(imported);
    } catch (err) {
      message.error(`Import failed: ${errorMessage(err)}`);
    } finally {
      setImportLoading(false);
    }
  };

  const resetResultsForSort = (value: SemanticScholarSort) => {
    setSort(value);
    setPapers([]);
    setTotal(0);
    setNextOffset(null);
    setNextToken(null);
    setSearchStateDirty(true);
  };

  const hasMore = sort === "relevance" ? nextOffset !== null : nextToken !== null;

  return (
    <Card
      title="Search Semantic Scholar"
      style={{ marginTop: 16 }}
      extra={<Tag color="blue">External papers</Tag>}
    >
      <Space.Compact style={{ width: "100%", marginBottom: 12 }}>
        <Input
          size="large"
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
            setSearchStateDirty(true);
          }}
          onPressEnter={() => runSearch(false)}
          placeholder="Search papers by topic, method, author, or keyword"
          prefix={<SearchOutlined />}
        />
        <Button type="primary" size="large" onClick={() => runSearch(false)} loading={loading}>
          Search
        </Button>
      </Space.Compact>

      <Space wrap style={{ marginBottom: 12 }}>
        <Select
          allowClear
          style={{ minWidth: 240 }}
          placeholder="Recent searches"
          suffixIcon={<ClockCircleOutlined />}
          options={searchHistory.map((item) => ({
            value: item.id,
            label: `${item.query} · ${item.result_count}`,
          }))}
          onChange={(value) => value && applyHistory(value)}
        />
        <Select
          allowClear
          style={{ minWidth: 240 }}
          placeholder="Favorites"
          suffixIcon={<StarFilled />}
          options={favoritePapers.map((paper) => ({
            value: paper.paperId,
            label: paper.title || paper.paperId,
          }))}
          onChange={(value) => {
            const paper = favoritePapers.find((item) => item.paperId === value);
            if (paper) setDetailsPaper(paper);
          }}
        />
      </Space>

      {searchStateDirty && (
        <Alert
          type="info"
          showIcon
          message="Search conditions changed. Click Search to apply them."
          style={{ marginBottom: 12 }}
        />
      )}

      <Space wrap size={[8, 8]} style={{ width: "100%" }}>
        <InputNumber
          min={1900}
          max={2100}
          placeholder="Year from"
          value={yearFrom}
          onChange={(value) => {
            setYearFrom(value);
            setSearchStateDirty(true);
          }}
        />
        <InputNumber
          min={1900}
          max={2100}
          placeholder="Year to"
          value={yearTo}
          onChange={(value) => {
            setYearTo(value);
            setSearchStateDirty(true);
          }}
        />
        <InputNumber
          min={0}
          placeholder="Min citations"
          value={minCitations}
          onChange={(value) => {
            setMinCitations(value);
            setSearchStateDirty(true);
          }}
        />
        <Select
          mode="multiple"
          allowClear
          style={{ minWidth: 210 }}
          placeholder="Fields of study"
          options={FIELD_OPTIONS}
          value={fieldsOfStudy}
          onChange={(value) => {
            setFieldsOfStudy(value);
            setSearchStateDirty(true);
          }}
        />
        <Select
          mode="multiple"
          allowClear
          style={{ minWidth: 190 }}
          placeholder="Publication types"
          options={PUBLICATION_TYPE_OPTIONS}
          value={publicationTypes}
          onChange={(value) => {
            setPublicationTypes(value);
            setSearchStateDirty(true);
          }}
        />
        <Input
          style={{ width: 180 }}
          placeholder="Venue"
          value={venue}
          onChange={(event) => {
            setVenue(event.target.value);
            setSearchStateDirty(true);
          }}
        />
        <Select
          style={{ width: 170 }}
          value={sort}
          options={SORT_OPTIONS}
          onChange={resetResultsForSort}
          suffixIcon={<FilterOutlined />}
        />
        <Button
          onClick={() => {
            setOpenAccess((value) => !value);
            setSearchStateDirty(true);
          }}
          type={openAccess ? "primary" : "default"}
        >
          Open access only
        </Button>
      </Space>

      {error && <Alert type="error" showIcon message={error} style={{ marginTop: 16 }} />}

      {searchedQuery && !loading && !error && (
        <Text type="secondary" style={{ display: "block", marginTop: 16 }}>
          {total.toLocaleString()} results for “{searchedQuery}”
        </Text>
      )}

      {!loading && papers.length === 0 && !error && (
        <Empty description={searchedQuery ? "No papers found" : "Search for papers to get started"} style={{ margin: 32 }} />
      )}

      {papers.length > 0 && (
        <Table<SemanticScholarPaper>
          style={{ marginTop: 12 }}
          rowKey="paperId"
          dataSource={papers}
          loading={loading}
          pagination={false}
          scroll={{ x: 1000 }}
          columns={[
            {
              title: "Title",
              key: "title",
              width: 340,
              render: (_: unknown, paper) => (
                <div>
                  <Typography.Link onClick={() => setDetailsPaper(paper)}>
                    {paper.title || "Untitled paper"}
                  </Typography.Link>
                  <Text type="secondary" ellipsis style={{ display: "block", maxWidth: 320 }}>
                    {paper.abstract || "No abstract available."}
                  </Text>
                </div>
              ),
            },
            {
              title: "Authors",
              key: "authors",
              width: 210,
              render: (_: unknown, paper) => authorsLabel(paper),
            },
            {
              title: "Year",
              key: "year",
              width: 80,
              render: (_: unknown, paper) => paperYear(paper),
            },
            {
              title: "Citations",
              dataIndex: "citationCount",
              key: "citationCount",
              width: 100,
              render: (value: number | null) => value ?? "—",
            },
            {
              title: "Venue",
              dataIndex: "venue",
              key: "venue",
              width: 150,
              render: (value: string | null) => value || "—",
            },
            {
              title: "Actions",
              key: "actions",
              width: 180,
              render: (_: unknown, paper) => (
                <Space size={4}>
                  <Button size="small" onClick={() => setDetailsPaper(paper)}>
                    Details
                  </Button>
                  <Button
                    size="small"
                    icon={favoriteIds.has(paper.paperId) ? <StarFilled /> : <StarOutlined />}
                    onClick={() => void toggleFavorite(paper)}
                    title={favoriteIds.has(paper.paperId) ? "Remove favorite" : "Add favorite"}
                  />
                  <Button size="small" icon={<CloudDownloadOutlined />} onClick={() => openImportModal(paper)}>
                    Import
                  </Button>
                  {paper.url && (
                    <Button
                      size="small"
                      icon={<LinkOutlined />}
                      href={paper.url}
                      target="_blank"
                      rel="noreferrer"
                    />
                  )}
                </Space>
              ),
            },
          ]}
        />
      )}

      {hasMore && (
        <Button block style={{ marginTop: 16 }} onClick={() => runSearch(true)} loading={loading}>
          Load more
        </Button>
      )}

      <Modal
        title="Paper details"
        open={detailsPaper !== null}
        onCancel={() => setDetailsPaper(null)}
        footer={null}
        width={720}
      >
        {detailsPaper && (
          <>
            <Typography.Title level={4}>{detailsPaper.title || "Untitled paper"}</Typography.Title>
            <Paragraph type="secondary">{authorsLabel(detailsPaper)}</Paragraph>
            <Space wrap>
              <Tag>{paperYear(detailsPaper)}</Tag>
              <Tag>Citations: {detailsPaper.citationCount ?? "—"}</Tag>
              <Tag>References: {detailsPaper.referenceCount ?? "—"}</Tag>
              {detailsPaper.isOpenAccess && <Tag color="green">Open access</Tag>}
            </Space>
            <Paragraph style={{ marginTop: 16 }}>
              {detailsPaper.abstract || "No abstract available."}
            </Paragraph>
            <Space>
              {detailsPaper.url && (
                <Button href={detailsPaper.url} target="_blank" rel="noreferrer" icon={<LinkOutlined />}>
                  Semantic Scholar
                </Button>
              )}
              {detailsPaper.openAccessPdf?.url && (
                <Button href={detailsPaper.openAccessPdf.url} target="_blank" rel="noreferrer">
                  Open PDF
                </Button>
              )}
            </Space>
          </>
        )}
      </Modal>

      <Modal
          title={workspaceId ? "导入到当前课题" : "导入论文到课题"}
        open={importPaper !== null}
        onCancel={() => setImportPaper(null)}
        onOk={handleImport}
        confirmLoading={importLoading}
          okText="导入论文"
      >
          <Paragraph>
            {workspaceId ? "论文会直接添加到当前课题，随后按已有流程尝试下载、解析和建立全文索引。" : "这会导入标题、作者、摘要、年份、DOI 和 arXiv ID；请先选择目标课题。"}
          </Paragraph>
          {workspaceId ? <Tag color="blue">当前课题已绑定</Tag> : <Select showSearch style={{ width: "100%" }} placeholder="选择目标课题" optionFilterProp="label" value={importWorkspaceId} onChange={setImportWorkspaceId} options={workspaces.map((workspace) => ({ value: workspace.id, label: workspace.name }))} />}
      </Modal>
    </Card>
  );
}
