import { useCallback, useEffect, useState } from "react";
import { Breadcrumb, Card, Menu, Spin, Tag, Typography } from "antd";
import { Link, Outlet, useLocation, useNavigate, useParams, useOutletContext } from "react-router-dom";
import workspaceApi from "../../api/workspace";
import type { Workspace } from "../../api/types/workspace";
import { useAppStore } from "../../store/appStore";
import { selectedWorkspaceKey, workspaceNavigationPath, WORKSPACE_NAVIGATION } from "./navigation";

export interface WorkspaceLayoutContext {
  workspace: Workspace;
  reloadWorkspace: () => Promise<void>;
}

export function useWorkspaceLayout() {
  return useOutletContext<WorkspaceLayoutContext>();
}

export default function WorkspaceLayout() {
  const { id } = useParams<{ id: string }>();
  const location = useLocation();
  const navigate = useNavigate();
  const setCurrentWorkspace = useAppStore((state) => state.setCurrentWorkspace);
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [loading, setLoading] = useState(true);
  const isAssistantPage = /^\/workspaces\/[^/]+\/assistant(?:\/[^/]+)?\/?$/.test(location.pathname);

  const reloadWorkspace = useCallback(async () => {
    if (!id) return;
    const next = await workspaceApi.get(id);
    setWorkspace(next);
    setCurrentWorkspace(next.id, next.name);
  }, [id, setCurrentWorkspace]);

  useEffect(() => {
    setLoading(true);
    reloadWorkspace()
      .catch(() => setWorkspace(null))
      .finally(() => setLoading(false));
  }, [reloadWorkspace, setCurrentWorkspace]);

  if (loading) return <div className="gm-loading"><Spin tip="正在加载课题" /></div>;
  if (!id || !workspace) {
    return <Card><Typography.Title level={4}>找不到这个课题</Typography.Title><Link to="/workspaces">返回课题空间</Link></Card>;
  }

  return (
    <div className={`gm-workspace-shell${isAssistantPage ? " gm-workspace-shell--assistant" : ""}`}>
      {!isAssistantPage && <Breadcrumb items={[{ title: <Link to="/">首页</Link> }, { title: <Link to="/workspaces">课题空间</Link> }, { title: workspace.name }]} />}
      {!isAssistantPage && <div className="gm-workspace-heading">
        <div>
          <Typography.Title level={3} style={{ margin: 0 }}>{workspace.name}</Typography.Title>
          <Typography.Paragraph type="secondary" ellipsis={{ rows: 1 }} style={{ margin: "4px 0 0" }}>
            {workspace.topic || workspace.description || "还没有填写研究主题"}
          </Typography.Paragraph>
        </div>
        <Tag color={workspace.is_archived ? "default" : "green"}>{workspace.is_archived ? "已归档" : "进行中"}</Tag>
      </div>}
      {!isAssistantPage && <Menu
        className="gm-workspace-nav"
        mode="horizontal"
        selectedKeys={[selectedWorkspaceKey(location.pathname)]}
        items={WORKSPACE_NAVIGATION.map((item) => ({ ...item }))}
        onClick={({ key }) => navigate(workspaceNavigationPath(workspace.id, key))}
      />}
      <Outlet context={{ workspace, reloadWorkspace }} />
    </div>
  );
}
