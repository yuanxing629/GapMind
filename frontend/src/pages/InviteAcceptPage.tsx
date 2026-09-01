import { useEffect, useState } from "react";
import { Alert, App, Button, Form, Input, Spin, Typography } from "antd";
import { LockOutlined, UserOutlined } from "@ant-design/icons";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import authApi, { type InviteValidation } from "../api/auth";
import { useAuth } from "../state/auth";

const { Title, Paragraph, Text } = Typography;

interface AcceptValues {
  displayName?: string;
  password: string;
  passwordAgain: string;
}

export default function InviteAcceptPage() {
  const [params] = useSearchParams();
  const token = params.get("token") || "";
  const { message } = App.useApp();
  const { acceptInvite } = useAuth();
  const navigate = useNavigate();
  const [invite, setInvite] = useState<InviteValidation | null>(null);
  const [checking, setChecking] = useState(true);
  const [form] = Form.useForm<AcceptValues>();

  useEffect(() => {
    if (!token) {
      setInvite({ valid: false, email: null, expires_at: null, message: "邀请链接缺少 token" });
      setChecking(false);
      return;
    }
    void authApi.validateInvite(token).then(setInvite).catch(() => {
      setInvite({ valid: false, email: null, expires_at: null, message: "邀请链接暂时无法验证" });
    }).finally(() => setChecking(false));
  }, [token]);

  const submit = async (values: AcceptValues) => {
    try {
      await acceptInvite(token, values.password, values.displayName);
      message.success("账号已创建，欢迎加入 GapMind");
      navigate("/", { replace: true });
    } catch (error) {
      message.error(error instanceof Error ? error.message : "激活失败，请重新打开邀请链接");
    }
  };

  return (
    <main className="gm-auth-shell gm-auth-shell--compact">
      <section className="gm-auth-intro">
        <span className="gm-auth-kicker">INVITATION / FIRST SETUP</span>
        <Title>加入一个<br />正在生长的课题。</Title>
        <Paragraph>设置你的显示名称和密码，之后即可创建属于自己的 Workspace。</Paragraph>
      </section>
      <section className="gm-auth-panel">
        <div className="gm-auth-card">
          <div className="gm-auth-card-heading">
            <Text type="secondary">ACCEPT INVITATION</Text>
            <Title level={2}>设置账号</Title>
          </div>
          {checking ? <div className="gm-auth-loading"><Spin /></div> : !invite?.valid ? (
            <Alert type="error" showIcon message="邀请不可用" description={invite?.message || "链接可能已过期或已被使用"} />
          ) : (
            <>
              <Alert type="info" showIcon message={`邀请邮箱：${invite.email}`} description="密码至少需要包含一个字符；系统不会限制密码长度。" />
              <Form form={form} layout="vertical" onFinish={submit} requiredMark={false} size="large" style={{ marginTop: 18 }}>
                <Form.Item name="displayName" label="显示名称"><Input prefix={<UserOutlined />} placeholder="例如：张三" autoComplete="name" /></Form.Item>
                <Form.Item name="password" label="设置密码" rules={[{ required: true, message: "请输入密码" }]}><Input.Password prefix={<LockOutlined />} autoComplete="new-password" /></Form.Item>
                <Form.Item name="passwordAgain" label="确认密码" dependencies={["password"]} rules={[{ required: true, message: "请再次输入密码" }, ({ getFieldValue }) => ({ validator(_, value) { return !value || getFieldValue("password") === value ? Promise.resolve() : Promise.reject(new Error("两次密码不一致")); } })]}>
                  <Input.Password prefix={<LockOutlined />} autoComplete="new-password" />
                </Form.Item>
                <Button type="primary" htmlType="submit" block>完成设置并进入</Button>
              </Form>
            </>
          )}
          <div className="gm-auth-links"><Link to="/login">返回登录</Link></div>
        </div>
      </section>
    </main>
  );
}
