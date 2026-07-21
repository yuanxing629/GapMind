import { Result, Typography } from "antd";

const { Title } = Typography;

export default function NotFoundPage() {
  return (
    <div>
      <Title level={3}>Not Found</Title>
      <Result
        status="404"
        title="404"
        subTitle="Sorry, the page you visited does not exist."
      />
    </div>
  );
}
