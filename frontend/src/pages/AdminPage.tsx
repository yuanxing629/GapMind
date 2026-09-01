import { useCallback, useEffect, useState } from "react";
import { Alert, App, Button, Card, Form, Input, Space, Table, Tag, Typography } from "antd";
import { CopyOutlined, ReloadOutlined, SendOutlined } from "@ant-design/icons";
import { Navigate } from "react-router-dom";
import authApi, { type AuditEventItem, type InviteCreated, type InviteListItem, type UserListItem } from "../api/auth";
import { useAuth } from "../state/auth";

const { Title, Paragraph, Text } = Typography;

interface InviteFormValues {
  email: string;
}

export default function AdminPage() {
  const { user } = useAuth();
  const { message } = App.useApp();
  const [invites, setInvites] = useState<InviteListItem[]>([]);
  const [users, setUsers] = useState<UserListItem[]>([]);
  const [audit, setAudit] = useState<AuditEventItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [created, setCreated] = useState<InviteCreated | null>(null);
  const [form] = Form.useForm<InviteFormValues>();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [nextInvites, nextUsers, nextAudit] = await Promise.all([authApi.listInvites(), authApi.listUsers(), authApi.listAudit()]);
      setInvites(nextInvites);
      setUsers(nextUsers);
      setAudit(nextAudit);
    } catch (error) {
      message.error(error instanceof Error ? error.message : "邀请列表加载失败");
    } finally {
      setLoading(false);
    }
  }, [message]);

  useEffect(() => {
    if (user?.is_platform_admin) void load();
  }, [load, user?.is_platform_admin]);

  if (!user?.is_platform_admin) return <Navigate to="/" replace />;

  const create = async (values: InviteFormValues) => {
    setSubmitting(true);
    try {
      const invite = await authApi.createInvite({
        email: values.email,
      });
      setCreated(invite);
      form.resetFields();
      await load();
      message.success("邀请已创建；请复制链接发送给用户");
    } catch (error) {
      message.error(error instanceof Error ? error.message : "邀请创建失败");
    } finally {
      setSubmitting(false);
    }
  };

  const inviteUrl = created ? `${window.location.origin}/invite/accept?token=${encodeURIComponent(created.token)}` : "";
  const copy = async () => {
    if (!inviteUrl) return;
    await navigator.clipboard.writeText(inviteUrl);
    message.success("邀请链接已复制");
  };

  const changeUserStatus = async (item: UserListItem) => {
    try {
      if (item.status === "active") await authApi.disableUser(item.id);
      else await authApi.enableUser(item.id);
      await load();
      message.success(item.status === "active" ? "账号已禁用" : "账号已启用");
    } catch (error) {
      message.error(error instanceof Error ? error.message : "账号状态更新失败");
    }
  };

  return (
    <div className="gm-admin-page gm-workspace-shell">
      <div className="gm-page-header">
        <div><span className="gm-eyebrow">PLATFORM ADMINISTRATION</span><Title level={2}>管理员控制台</Title><Paragraph type="secondary">邀请用户创建 GapMind 账号。每个 Workspace 只属于创建它的用户，管理员不自动拥有任何研究内容。</Paragraph></div>
        <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>刷新</Button>
      </div>
      <Card title="创建邀请" className="gm-section-card">
        <Form form={form} layout="vertical" onFinish={create}>
          <Space align="start" size="middle" wrap style={{ width: "100%" }}>
            <Form.Item name="email" label="用户邮箱" rules={[{ required: true, message: "请输入邮箱" }, { type: "email", message: "请输入有效邮箱" }]} style={{ minWidth: 280 }}><Input placeholder="researcher@example.com" /></Form.Item>
            <Form.Item label=" "><Button type="primary" icon={<SendOutlined />} htmlType="submit" loading={submitting}>生成邀请</Button></Form.Item>
          </Space>
        </Form>
        {created && <Alert type="success" showIcon message={`邀请已生成：${created.email}`} description={<Space direction="vertical" style={{ width: "100%" }}><Text type="secondary">链接仅在本次创建结果中显示，发送后不要把 token 写入日志。</Text><Input value={inviteUrl} readOnly addonAfter={<Button type="text" icon={<CopyOutlined />} onClick={copy}>复制</Button>} /></Space>} />}
      </Card>
      <Card title="最近邀请" style={{ marginTop: 16 }}>
        <Table<InviteListItem> rowKey="id" loading={loading} dataSource={invites} pagination={{ pageSize: 10 }} columns={[
          { title: "邮箱", dataIndex: "email", key: "email" },
          { title: "状态", key: "status", render: (_: unknown, item) => item.accepted_at ? <Tag color="green">已接受</Tag> : item.revoked_at ? <Tag color="red">已撤销</Tag> : new Date(item.expires_at) <= new Date() ? <Tag>已过期</Tag> : <Tag color="blue">待接受</Tag> },
          { title: "过期时间", dataIndex: "expires_at", key: "expires_at", render: (value: string) => new Date(value).toLocaleString() },
        ]} />
      </Card>
      <Card title="用户状态" style={{ marginTop: 16 }}>
        <Table<UserListItem> rowKey="id" loading={loading} dataSource={users} pagination={{ pageSize: 10 }} columns={[
          { title: "用户", key: "user", render: (_: unknown, item) => <Space direction="vertical" size={0}><Text>{item.display_name || "未设置名称"}</Text><Text type="secondary">{item.email}</Text></Space> },
          { title: "平台角色", dataIndex: "roles", key: "roles", render: (roles: string[]) => roles.map((role) => <Tag key={role}>{role}</Tag>) },
          { title: "状态", dataIndex: "status", key: "status", render: (value: string) => <Tag color={value === "active" ? "green" : "red"}>{value}</Tag> },
          { title: "最近登录", dataIndex: "last_login_at", key: "last_login_at", render: (value: string | null) => value ? new Date(value).toLocaleString() : "从未登录" },
          { title: "操作", key: "action", render: (_: unknown, item) => item.id === user.id ? <Text type="secondary">当前账号</Text> : <Button size="small" danger={item.status === "active"} onClick={() => void changeUserStatus(item)}>{item.status === "active" ? "禁用" : "启用"}</Button> },
        ]} />
      </Card>
      <Card title="最近鉴权审计" style={{ marginTop: 16 }}>
        <Table<AuditEventItem> rowKey="id" loading={loading} dataSource={audit} pagination={{ pageSize: 8 }} columns={[
          { title: "时间", dataIndex: "created_at", key: "created_at", render: (value: string) => new Date(value).toLocaleString() },
          { title: "事件", dataIndex: "event_type", key: "event_type" },
          { title: "用户", dataIndex: "user_id", key: "user_id", render: (value: string | null) => value || "系统" },
          { title: "目标", dataIndex: "target_id", key: "target_id", render: (value: string | null) => value || "—" },
        ]} />
      </Card>
    </div>
  );
}
