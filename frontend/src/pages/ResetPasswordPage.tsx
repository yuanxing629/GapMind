import { App, Button, Form, Input, Typography } from "antd";
import { LockOutlined } from "@ant-design/icons";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import authApi from "../api/auth";

const { Title, Paragraph, Text } = Typography;

export default function ResetPasswordPage() {
  const [params] = useSearchParams();
  const token = params.get("token") || "";
  const navigate = useNavigate();
  const { message } = App.useApp();
  const [form] = Form.useForm<{ password: string; passwordAgain: string }>();
  const submit = async (values: { password: string; passwordAgain: string }) => {
    try {
      await authApi.resetPassword(token, values.password);
      message.success("密码已重置，请重新登录");
      navigate("/login", { replace: true });
    } catch (error) {
      message.error(error instanceof Error ? error.message : "重置失败，请重新申请链接");
    }
  };
  return <main className="gm-auth-shell gm-auth-shell--compact"><section className="gm-auth-intro"><span className="gm-auth-kicker">NEW CREDENTIAL</span><Title>为下一次<br />研究会话设定入口。</Title><Paragraph>重置链接只可使用一次，并会让现有登录状态失效。</Paragraph></section><section className="gm-auth-panel"><div className="gm-auth-card"><div className="gm-auth-card-heading"><Text type="secondary">RESET PASSWORD</Text><Title level={2}>设置新密码</Title></div>{!token ? <Paragraph type="danger">链接缺少 token，请从邮件重新打开。</Paragraph> : <Form form={form} layout="vertical" onFinish={submit} requiredMark={false} size="large"><Form.Item name="password" label="新密码" rules={[{ required: true, message: "请输入新密码" }]}><Input.Password prefix={<LockOutlined />} autoComplete="new-password" /></Form.Item><Form.Item name="passwordAgain" label="确认密码" dependencies={["password"]} rules={[{ required: true, message: "请再次输入密码" }, ({ getFieldValue }) => ({ validator(_, value) { return !value || getFieldValue("password") === value ? Promise.resolve() : Promise.reject(new Error("两次密码不一致")); } })]}><Input.Password prefix={<LockOutlined />} autoComplete="new-password" /></Form.Item><Button type="primary" htmlType="submit" block>保存新密码</Button></Form>}<div className="gm-auth-links"><Link to="/login">返回登录</Link></div></div></section></main>;
}
