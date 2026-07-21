import { Outlet, useLocation } from "react-router-dom";
import { Layout, Menu, theme } from "antd";
import {
  DashboardOutlined,
  ProjectOutlined,
} from "@ant-design/icons";
import { Link } from "react-router-dom";

const { Header, Sider, Content } = Layout;

const menuItems = [
  { key: "/", icon: <DashboardOutlined />, label: <Link to="/">Dashboard</Link> },
  {
    key: "/workspaces",
    icon: <ProjectOutlined />,
    label: <Link to="/workspaces">Workspaces</Link>,
  },
];

export default function AppLayout() {
  const location = useLocation();
  const {
    token: { colorBgContainer },
  } = theme.useToken();

  const selectedKey =
    menuItems.find((m) => m.key === location.pathname)?.key ?? "/";

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Sider width={220} style={{ background: colorBgContainer }}>
        <div
          style={{
            height: 56,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontWeight: 700,
            fontSize: 18,
            borderBottom: "1px solid #f0f0f0",
          }}
        >
          GapMind
        </div>
        <Menu
          mode="inline"
          selectedKeys={[selectedKey]}
          items={menuItems}
          style={{ borderRight: 0 }}
        />
      </Sider>
      <Layout>
        <Header
          style={{
            background: colorBgContainer,
            padding: "0 24px",
            borderBottom: "1px solid #f0f0f0",
            fontSize: 14,
            color: "#666",
          }}
        >
          Evidence-grounded AI Research Workspace
        </Header>
        <Content style={{ padding: 24, overflow: "auto" }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
}
