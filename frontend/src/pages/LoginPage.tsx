import { App, Button, Form, Input, Typography } from "antd";
import { LockOutlined, MailOutlined } from "@ant-design/icons";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../state/auth";

const { Title, Paragraph, Text } = Typography;

interface LoginValues {
  email: string;
  password: string;
}

export default function LoginPage() {
  const { message } = App.useApp();
  const { login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [form] = Form.useForm<LoginValues>();
  const from = (location.state as { from?: string } | null)?.from || "/";

  const submit = async (values: LoginValues) => {
    try {
      await login(values.email, values.password);
      navigate(from, { replace: true });
    } catch (error) {
      message.error(error instanceof Error ? error.message : "登录失败，请检查邮箱和密码");
    }
  };

  return (
    <main className="gm-auth-shell">
      <section className="gm-auth-intro">
        <span className="gm-auth-kicker">GAPMIND / RESEARCH WORKSPACE</span>
        <Title>让每个研究判断，<br />都能回到证据。</Title>
        <Paragraph>
          从文献、知识与研究机会，到可验证的计划。登录后继续你的研究上下文，邀请成员共同推进同一个课题空间。
        </Paragraph>
        <div className="gm-auth-proof">
          <span>01</span><span>Evidence-linked workspace</span>
        </div>
      </section>
      <section className="gm-auth-panel">
        <div className="gm-auth-card">
          <div className="gm-auth-card-heading">
            <Text type="secondary">WELCOME BACK</Text>
            <Title level={2}>登录 GapMind</Title>
          </div>
          <Form form={form} layout="vertical" onFinish={submit} requiredMark={false} size="large">
            <Form.Item name="email" label="邮箱" rules={[{ required: true, message: "请输入邮箱" }, { type: "email", message: "请输入有效邮箱" }]}>
              <Input prefix={<MailOutlined />} placeholder="you@example.com" autoComplete="email" />
            </Form.Item>
            <Form.Item name="password" label="密码" rules={[{ required: true, message: "请输入密码" }]}>
              <Input.Password prefix={<LockOutlined />} placeholder="输入密码" autoComplete="current-password" />
            </Form.Item>
            <Button type="primary" htmlType="submit" block>登录</Button>
          </Form>
          <div className="gm-auth-links">
            <Link to="/forgot-password">忘记密码？</Link>
            <span>账号需要由管理员邀请</span>
          </div>
        </div>
      </section>
    </main>
  );
}
