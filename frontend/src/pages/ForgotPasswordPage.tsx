import { App, Button, Form, Input, Typography } from "antd";
import { MailOutlined } from "@ant-design/icons";
import { Link } from "react-router-dom";
import authApi from "../api/auth";

const { Title, Paragraph, Text } = Typography;

export default function ForgotPasswordPage() {
  const { message } = App.useApp();
  const [form] = Form.useForm<{ email: string }>();
  const submit = async ({ email }: { email: string }) => {
    try {
      const response = await authApi.forgotPassword(email);
      message.success(response.debug_token ? `开发环境重置 token：${response.debug_token}` : response.message);
    } catch (error) {
      message.error(error instanceof Error ? error.message : "请求失败，请稍后再试");
    }
  };
  return (
    <main className="gm-auth-shell gm-auth-shell--compact">
      <section className="gm-auth-intro"><span className="gm-auth-kicker">ACCOUNT RECOVERY</span><Title>把研究上下文<br />安全地找回来。</Title><Paragraph>输入账号邮箱。正式部署会通过邮件发送一次性重置链接。</Paragraph></section>
      <section className="gm-auth-panel"><div className="gm-auth-card"><div className="gm-auth-card-heading"><Text type="secondary">FORGOT PASSWORD</Text><Title level={2}>重置密码</Title></div><Form form={form} layout="vertical" onFinish={submit} requiredMark={false} size="large"><Form.Item name="email" label="邮箱" rules={[{ required: true, message: "请输入邮箱" }, { type: "email", message: "请输入有效邮箱" }]}><Input prefix={<MailOutlined />} autoComplete="email" /></Form.Item><Button type="primary" htmlType="submit" block>发送重置链接</Button></Form><div className="gm-auth-links"><Link to="/login">返回登录</Link></div></div></section>
    </main>
  );
}
