import { useEffect, useState } from "react";
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
  CloudDownloadOutlined,
  FilterOutlined,
  LinkOutlined,
  SearchOutlined,
} from "@ant-design/icons";
import semanticScholarApi, {
  type SemanticScholarPaper,
  type SemanticScholarSort,
} from "../api/semanticScholar";
import workspaceApi from "../api/workspace";
import type { Workspace } from "../api/types/workspace";

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

const SEARCH_CACHE_KEY = "gapmind.semantic-paper-search.v1";
const SEARCH_CACHE_TTL_MS = 30 * 60 * 1000;

type SearchSnapshot = {
  savedAt: number;
  query: string;
  searchedQuery: string;
  yearFrom: number | null;
  yearTo: number | null;
  minCitations: number | null;
  openAccess: boolean;
  fieldsOfStudy: string[];
  publicationTypes: string[];
  venue: string;
  sort: SemanticScholarSort;
  papers: SemanticScholarPaper[];
  total: number;
  nextOffset: number | null;
  nextToken: string | null;
};

function readSearchSnapshot(): SearchSnapshot | null {
  try {
    const raw = sessionStorage.getItem(SEARCH_CACHE_KEY);
    if (!raw) return null;

    const snapshot = JSON.parse(raw) as SearchSnapshot;
    if (
      !snapshot ||
      typeof snapshot.savedAt !== "number" ||
      Date.now() - snapshot.savedAt > SEARCH_CACHE_TTL_MS ||
      !Array.isArray(snapshot.papers)
    ) {
      sessionStorage.removeItem(SEARCH_CACHE_KEY);
      return null;
    }
    return snapshot;
  } catch {
    return null;
  }
}

function errorMessage(err: unknown): string {
  const detail = (
    err as {
      response?: { data?: { detail?: { message?: string } } };
    }
  ).response?.data?.detail;
  return detail?.message || (err as Error).message || "Request failed";
}

function authorsLabel(paper: SemanticScholarPaper): string {
  const names = paper.authors.map((author) => author.name).filter(Boolean) as string[];
  if (names.length <= 3) return names.join(", ") || "Unknown authors";
  return `${names.slice(0, 3).join(", ")} +${names.length - 3}`;
}

function paperYear(paper: SemanticScholarPaper): string {
  return paper.publicationDate?.slice(0, 4) || String(paper.year ?? "—");
}

export default function SemanticPaperSearch() {
  const { message } = App.useApp();
  const [hydrated, setHydrated] = useState(false);
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

  useEffect(() => {
    const snapshot = readSearchSnapshot();
    if (snapshot) {
      setQuery(snapshot.query);
      setSearchedQuery(snapshot.searchedQuery);
      setYearFrom(snapshot.yearFrom);
      setYearTo(snapshot.yearTo);
      setMinCitations(snapshot.minCitations);
      setOpenAccess(snapshot.openAccess);
      setFieldsOfStudy(snapshot.fieldsOfStudy);
      setPublicationTypes(snapshot.publicationTypes);
      setVenue(snapshot.venue);
      setSort(snapshot.sort);
      setPapers(snapshot.papers);
      setTotal(snapshot.total);
      setNextOffset(snapshot.nextOffset);
      setNextToken(snapshot.nextToken);
    }
    setHydrated(true);
  }, []);

  useEffect(() => {
    if (!hydrated) return;

    const snapshot: SearchSnapshot = {
      savedAt: Date.now(),
      query,
      searchedQuery,
      yearFrom,
      yearTo,
      minCitations,
      openAccess,
      fieldsOfStudy,
      publicationTypes,
      venue,
      sort,
      papers,
      total,
      nextOffset,
      nextToken,
    };

    try {
      sessionStorage.setItem(SEARCH_CACHE_KEY, JSON.stringify(snapshot));
    } catch {
      // Ignore storage failures; search remains fully functional in memory.
    }
  }, [
    hydrated,
    query,
    searchedQuery,
    yearFrom,
    yearTo,
    minCitations,
    openAccess,
    fieldsOfStudy,
    publicationTypes,
    venue,
    sort,
    papers,
    total,
    nextOffset,
    nextToken,
  ]);

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

    setLoading(true);
    setError(null);
    try {
      const response = await semanticScholarApi.search({
        query: activeQuery,
        year_from: yearFrom ?? undefined,
        year_to: yearTo ?? undefined,
        min_citation_count: minCitations ?? undefined,
        open_access: openAccess || undefined,
        fields_of_study: fieldsOfStudy.length ? fieldsOfStudy.join(",") : undefined,
        publication_types: publicationTypes.length ? publicationTypes.join(",") : undefined,
        venue: venue.trim() || undefined,
        sort,
        limit: 20,
        offset: append && sort === "relevance" ? nextOffset ?? papers.length : 0,
        token: append && sort !== "relevance" ? nextToken ?? undefined : undefined,
      });

      setSearchedQuery(activeQuery);
      setPapers((previous) => (append ? [...previous, ...response.data] : response.data));
      setTotal(response.total);
      setNextOffset(response.next ?? null);
      setNextToken(response.token ?? null);
    } catch (err) {
      setError(errorMessage(err));
      if (!append) setPapers([]);
    } finally {
      setLoading(false);
    }
  };

  const openImportModal = async (paper: SemanticScholarPaper) => {
    setImportPaper(paper);
    setImportWorkspaceId(undefined);
    try {
      const response = await workspaceApi.list({ limit: 200 });
      setWorkspaces(response.items);
    } catch (err) {
      message.error(`Failed to load workspaces: ${errorMessage(err)}`);
    }
  };

  const handleImport = async () => {
    if (!importPaper || !importWorkspaceId) {
      message.warning("Select a workspace first.");
      return;
    }
    setImportLoading(true);
    try {
      await semanticScholarApi.importToWorkspace(importWorkspaceId, importPaper.paperId);
      message.success("Paper metadata imported into the workspace.");
      setImportPaper(null);
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
          onChange={(event) => setQuery(event.target.value)}
          onPressEnter={() => runSearch(false)}
          placeholder="Search papers by topic, method, author, or keyword"
          prefix={<SearchOutlined />}
        />
        <Button type="primary" size="large" onClick={() => runSearch(false)} loading={loading}>
          Search
        </Button>
      </Space.Compact>

      <Space wrap size={[8, 8]} style={{ width: "100%" }}>
        <InputNumber
          min={1900}
          max={2100}
          placeholder="Year from"
          value={yearFrom}
          onChange={(value) => setYearFrom(value)}
        />
        <InputNumber
          min={1900}
          max={2100}
          placeholder="Year to"
          value={yearTo}
          onChange={(value) => setYearTo(value)}
        />
        <InputNumber
          min={0}
          placeholder="Min citations"
          value={minCitations}
          onChange={(value) => setMinCitations(value)}
        />
        <Select
          mode="multiple"
          allowClear
          style={{ minWidth: 210 }}
          placeholder="Fields of study"
          options={FIELD_OPTIONS}
          value={fieldsOfStudy}
          onChange={setFieldsOfStudy}
        />
        <Select
          mode="multiple"
          allowClear
          style={{ minWidth: 190 }}
          placeholder="Publication types"
          options={PUBLICATION_TYPE_OPTIONS}
          value={publicationTypes}
          onChange={setPublicationTypes}
        />
        <Input
          style={{ width: 180 }}
          placeholder="Venue"
          value={venue}
          onChange={(event) => setVenue(event.target.value)}
        />
        <Select
          style={{ width: 170 }}
          value={sort}
          options={SORT_OPTIONS}
          onChange={resetResultsForSort}
          suffixIcon={<FilterOutlined />}
        />
        <Button
          onClick={() => setOpenAccess((value) => !value)}
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
        title="Import paper into Workspace"
        open={importPaper !== null}
        onCancel={() => setImportPaper(null)}
        onOk={handleImport}
        confirmLoading={importLoading}
        okText="Import metadata"
      >
        <Paragraph>
          This imports the title, authors, abstract, year, DOI, and arXiv ID. You can attach a PDF later.
        </Paragraph>
        <Select
          showSearch
          style={{ width: "100%" }}
          placeholder="Select a workspace"
          optionFilterProp="label"
          value={importWorkspaceId}
          onChange={setImportWorkspaceId}
          options={workspaces.map((workspace) => ({
            value: workspace.id,
            label: workspace.name,
          }))}
        />
      </Modal>
    </Card>
  );
}
