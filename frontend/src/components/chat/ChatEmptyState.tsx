import { Card, Space, Typography } from "antd";

const examples = ["帮我梳理这个研究问题的思路", "分析一下实验结果", "帮我比较两种实验方案", "把下面这段研究描述改得更清楚:"];

export default function ChatEmptyState({ onExample, workspaceName, independent = false }: { onExample: (value: string) => void; workspaceName?: string; independent?: boolean }) {
  const description = independent
    ? "当前为独立模式：仅使用本次提供的材料，不会检索课题空间中的论文或知识库。"
    : workspaceName
      ? `回答将检索“${workspaceName}”中已解析并向量化的论文，并附上可定位的证据。`
      : "当前是普通 AI 对话，不会自动检索论文或知识库。可在右上角选择一个课题空间。";
  return <div className="gm-chat-empty-state"><Typography.Title level={3}>有什么研究问题想一起梳理？</Typography.Title><Typography.Paragraph type="secondary">{description}</Typography.Paragraph><Space wrap className="gm-chat-examples">{examples.map((example) => <Card hoverable size="small" key={example} onClick={() => onExample(example)}>{example}</Card>)}</Space></div>;
}
