import { useEffect, useState } from "react";
import { Avatar, Button, Dropdown, Layout, Menu, Space, Tag, Tooltip, Typography, theme } from "antd";
import {
  BarChartOutlined,
  AppstoreOutlined,
  LogoutOutlined,
  SettingOutlined,
  BulbOutlined,
  CodeOutlined,
  EditOutlined,
  ExperimentOutlined,
  FileSearchOutlined,
  FolderOpenOutlined,
  HomeOutlined,
  MessageOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  MoonOutlined,
  ProjectOutlined,
  ReadOutlined,
  ThunderboltOutlined,
  UserOutlined,
} from "@ant-design/icons";
import { Outlet, useLocation, useNavigate } from "react-router-dom";
import { useAppStore } from "../store/appStore";
import { readingLibraryPath, selectedGlobalKey } from "./layout/navigation";
import { useTheme } from "../state/theme";
import { useAuth } from "../state/auth";

const { Header, Sider, Content } = Layout;

const primaryNavigation = [
  { key: "/", icon: <HomeOutlined />, label: "首页" },
  { key: "/workspaces", icon: <ProjectOutlined />, label: "课题空间" },
  { key: "/reading", icon: <ReadOutlined />, label: "论文阅读" },
  { key: "/knowledge", icon: <FolderOpenOutlined />, label: "知识库" },
];

const lifecycleNavigation = [
  { key: "/discover", icon: <FileSearchOutlined />, label: "Discover" },
  { key: "/plan", icon: <ExperimentOutlined />, label: "Plan" },
  { key: "/execute", icon: <CodeOutlined />, label: "Execute" },
  { key: "/analyze", icon: <BarChartOutlined />, label: "Analyze" },
  { key: "/publish", icon: <EditOutlined />, label: "Publish" },
  { key: "/respond", icon: <MessageOutlined />, label: "Respond" },
];

export default function AppLayout() {
  const location = useLocation();
  const navigate = useNavigate();
  const isChatSurface =
    location.pathname === "/chat" ||
    location.pathname.startsWith("/chat/") ||
    /\/assistant(?:\/|$)/.test(location.pathname);
  const { token } = theme.useToken();
  const { isDark, toggleTheme } = useTheme();
  const [collapsed, setCollapsed] = useState(false);
  const [mobile, setMobile] = useState(false);
  const currentWorkspaceId = useAppStore((state) => state.currentWorkspaceId);
  const currentWorkspaceName = useAppStore((state) => state.currentWorkspaceName);
  const { user, logout } = useAuth();

  useEffect(() => {
    setCollapsed(mobile);
  }, [mobile]);

  const openNavigation = (key: string) => {
    const workspaceId = currentWorkspaceId;
    const workspaceRoute = workspaceId ? `/workspaces/${workspaceId}` : null;
    const target = key === "/knowledge"
      ? workspaceRoute ? `${workspaceRoute}/knowledge` : "/workspaces"
      : key === "/discover"
        ? workspaceRoute ? `${workspaceRoute}/discover` : "/workspaces"
        : key === "/plan"
          ? workspaceRoute ? `${workspaceRoute}/plans` : "/workspaces"
          : key === "/execute"
            ? workspaceRoute ? `${workspaceRoute}/assistant?mode=code_generation` : "/workspaces"
            : key === "/analyze"
              ? workspaceRoute ? `${workspaceRoute}/assistant?mode=analyze` : "/chat/new?mode=analyze"
              : key === "/publish"
                ? workspaceRoute ? `${workspaceRoute}/assistant?mode=write` : "/chat/new?mode=write"
                : key === "/respond"
                  ? workspaceRoute ? `${workspaceRoute}/assistant?mode=respond` : "/chat/new?mode=respond"
                  : key === "/reading"
                    ? readingLibraryPath(workspaceId)
                    : key;
    navigate(target);
    if (mobile) setCollapsed(true);
  };

  const items = [
    ...primaryNavigation.map((item) => ({ ...item, onClick: () => openNavigation(item.key) })),
    { type: "divider" as const, key: "lifecycle-divider" },
    {
      type: "group" as const,
      key: "lifecycle",
      label: "研究生命周期",
      children: lifecycleNavigation.map((item) => ({ ...item, onClick: () => openNavigation(item.key) })),
    },
  ];

  const profileItems = [
    {
      key: "identity",
      label: user?.email || user?.display_name || "本地开发用户",
      disabled: true,
    },
    ...(user?.is_platform_admin ? [{ key: "admin", icon: <SettingOutlined />, label: "管理员控制台" }] : []),
    { type: "divider" as const, key: "profile-divider" },
    { key: "logout", icon: <LogoutOutlined />, label: "退出登录", danger: true },
  ];

  const handleProfileAction = async ({ key }: { key: string }) => {
    if (key === "admin") navigate("/admin");
    if (key === "logout") {
      await logout();
      navigate("/login", { replace: true });
    }
  };

  return (
    <Layout className={`gm-app-layout${isChatSurface ? " gm-app-layout--chat" : ""}`}>
      <Sider
        width={232}
        breakpoint="lg"
        collapsedWidth={mobile ? 0 : 64}
        collapsed={collapsed}
        trigger={null}
        onBreakpoint={(broken) => {
          setMobile(broken);
          if (broken) setCollapsed(true);
        }}
        className="gm-sider"
        style={{ background: token.colorBgContainer }}
      >
        <div className="gm-brand">
          <div className="gm-brand-mark"><AppstoreOutlined /></div>
          <div>
            <strong>GapMind</strong>
            <span>Research workspace</span>
          </div>
        </div>
        <button className="gm-workspace-selector" type="button" onClick={() => navigate("/workspaces")}>
          <span className="gm-workspace-selector-icon">
            {(currentWorkspaceName || "课").slice(0, 1).toUpperCase()}
          </span>
          <span className="gm-workspace-selector-copy">
            <strong>{currentWorkspaceName || "选择课题空间"}</strong>
            <small>{currentWorkspaceName ? "当前活跃课题" : "从一个课题开始"}</small>
          </span>
          <span className="gm-workspace-selector-chevron">⌄</span>
        </button>
        <Menu
          mode="inline"
          selectedKeys={[selectedGlobalKey(location.pathname, location.search)]}
          items={items}
          style={{ borderRight: 0 }}
        />
        <div className="gm-sider-footer">
          <div className="gm-sider-footer-copy">
            <Tag color="blue">证据驱动研究</Tag>
            <TypographyFooter />
          </div>
          <Tooltip title={collapsed ? "展开导航" : "收起导航"} placement="right">
            <Button
              type="text"
              className="gm-sider-collapse"
              aria-label={collapsed ? "展开导航" : "收起导航"}
              icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
              onClick={() => setCollapsed((value) => !value)}
            />
          </Tooltip>
        </div>
      </Sider>
      <Layout>
        <Header className="gm-topbar" style={{ background: token.colorBgContainer }}>
          <Space size="middle">
            {mobile && (
              <Button
                type="text"
                aria-label={collapsed ? "打开导航" : "关闭导航"}
                icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
                onClick={() => setCollapsed((value) => !value)}
              />
            )}
            <span className="gm-topbar-title">围绕课题推进研究</span>
          </Space>
          <Space size="small" wrap>
            <Tooltip title={currentWorkspaceName || "进入课题空间后查看处理任务"}>
              <Button
                type="text"
                icon={<ThunderboltOutlined />}
                onClick={() => navigate(currentWorkspaceId ? `/workspaces/${currentWorkspaceId}/activity` : "/workspaces")}
              >
                处理中心
              </Button>
            </Tooltip>
            <Button type="text" icon={<ProjectOutlined />} onClick={() => navigate("/workspaces")}>
              切换课题
            </Button>
            <Tooltip title={isDark ? "切换到浅色模式" : "切换到深色模式"}>
              <Button type="text" aria-label="切换主题" icon={isDark ? <BulbOutlined /> : <MoonOutlined />} onClick={toggleTheme} />
            </Tooltip>
            <Dropdown menu={{ items: profileItems, onClick: handleProfileAction }} placement="bottomRight">
              <Button type="text" className="gm-profile-trigger">
                <Avatar size="small" icon={<UserOutlined />} />
                <Typography.Text className="gm-profile-name">{user?.display_name || user?.email || "本地用户"}</Typography.Text>
              </Button>
            </Dropdown>
          </Space>
        </Header>
        <Content className="gm-content">
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
}

function TypographyFooter() {
  return <span className="gm-sider-caption">从文献到可验证的研究机会</span>;
}
