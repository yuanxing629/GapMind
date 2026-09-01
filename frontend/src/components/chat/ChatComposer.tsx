import { useRef, useState } from "react";
import { Button, Dropdown, Input, Select, Space, Tag, Typography, message } from "antd";
import type { MenuProps } from "antd";
import { CloseOutlined, CodeOutlined, ExperimentOutlined, FileSearchOutlined, FileTextOutlined, MoreOutlined, PictureOutlined, SendOutlined, SettingOutlined } from "@ant-design/icons";
import type { ChatImageInput } from "../../api/chat";
import { shouldSendOnEnter } from "../../state/chatState";

export type ChatMode = "chat" | "research_plan" | "code_generation" | "analyze" | "write" | "respond";

interface Props {
  loading: boolean;
  onSend: (value: string, images: ChatImageInput[]) => void;
  value: string;
  onChange: (value: string) => void;
  workspaceEnabled: boolean;
  mode: ChatMode;
  onModeChange: (mode: ChatMode) => void;
  planOptions: { label: string; value: string; title?: string }[];
  researchPlanId?: string;
  onResearchPlanChange: (value?: string) => void;
  sourceOptions: { label: string; value: string; title?: string }[];
  sourceArtifactIds: string[];
  onSourceArtifactChange: (value: string[]) => void;
  imageInputs: ChatImageInput[];
  onImageInputsChange: (value: ChatImageInput[]) => void;
}

const MAX_IMAGE_COUNT = 3;
const MAX_IMAGE_BYTES = 8 * 1024 * 1024;
const ACCEPTED_IMAGE_TYPES = new Set(["image/jpeg", "image/png", "image/gif", "image/webp"]);

const modeLabels: Record<ChatMode, string> = {
  chat: "直接提问",
  research_plan: "生成研究计划",
  code_generation: "代码生成",
  analyze: "结果分析",
  write: "论文写作",
  respond: "审稿回复",
};

const modeIcons: Record<Exclude<ChatMode, "chat">, React.ReactNode> = {
  research_plan: <ExperimentOutlined />,
  code_generation: <CodeOutlined />,
  analyze: <FileSearchOutlined />,
  write: <FileTextOutlined />,
  respond: <FileTextOutlined />,
};

export default function ChatComposer({
  loading,
  onSend,
  value,
  onChange,
  workspaceEnabled,
  mode,
  onModeChange,
  planOptions,
  researchPlanId,
  onResearchPlanChange,
  sourceOptions,
  sourceArtifactIds,
  onSourceArtifactChange,
  imageInputs,
  onImageInputsChange,
}: Props) {
  const [focused, setFocused] = useState(false);
  const [contextOpen, setContextOpen] = useState(Boolean(researchPlanId));
  const hasSelectedContext = Boolean(researchPlanId) || sourceArtifactIds.length > 0;
  const fileInputRef = useRef<HTMLInputElement>(null);
  const sendDisabled = (!value.trim() && imageInputs.length === 0) || loading || (mode === "code_generation" && !researchPlanId);
  const send = () => {
    const content = value.trim() || (imageInputs.length > 0 ? "请分析我上传的图片。" : "");
    if (sendDisabled) return;
    onSend(content, imageInputs);
  };
  const readImage = (file: File): Promise<ChatImageInput> => new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const dataUrl = typeof reader.result === "string" ? reader.result : "";
      if (!dataUrl) reject(new Error("图片读取失败"));
      else resolve({ filename: file.name || "image", mime_type: file.type, data_url: dataUrl });
    };
    reader.onerror = () => reject(new Error("图片读取失败"));
    reader.readAsDataURL(file);
  });
  const addImages = async (files: File[]) => {
    const imageFiles = files.filter((file) => ACCEPTED_IMAGE_TYPES.has(file.type));
    if (imageFiles.length !== files.length) message.error("仅支持 JPEG、PNG、GIF 或 WebP 图片");
    const remaining = MAX_IMAGE_COUNT - imageInputs.length;
    if (remaining <= 0) {
      message.warning(`每次最多添加 ${MAX_IMAGE_COUNT} 张图片`);
      return;
    }
    const acceptedFiles = imageFiles.slice(0, remaining);
    if (acceptedFiles.length < imageFiles.length) message.warning(`每次最多添加 ${MAX_IMAGE_COUNT} 张图片`);
    const next: ChatImageInput[] = [];
    for (const file of acceptedFiles) {
      if (file.size > MAX_IMAGE_BYTES) {
        message.error(`图片不能超过 ${MAX_IMAGE_BYTES / (1024 * 1024)} MB：${file.name}`);
        continue;
      }
      try { next.push(await readImage(file)); }
      catch { message.error(`图片读取失败：${file.name}`); }
    }
    if (next.length > 0) onImageInputsChange([...imageInputs, ...next]);
  };
  const removeImage = (index: number) => onImageInputsChange(imageInputs.filter((_, itemIndex) => itemIndex !== index));
  const placeholder = mode === "research_plan" ? "描述研究目标、资源约束或希望验证的假设…"
    : mode === "code_generation" ? "描述希望生成的实验代码、框架或运行约束…"
    : mode === "analyze" ? "粘贴实验结果（JSON）或描述实验结论…"
    : mode === "write" ? "描述论文主题、目标章节或写作重点…"
    : mode === "respond" ? "粘贴审稿意见，逐条回应…"
    : "输入问题，不必先选择功能…";
  const hint = mode === "research_plan" ? "这是一次操作建议，确认后才会创建 AgentRun 和研究计划草案。"
    : mode === "code_generation" ? "这是一次操作建议，确认后才会创建 AgentRun；代码只生成候选文件，不会自动执行。"
    : mode === "analyze" ? (workspaceEnabled ? "确认后才会创建结果分析 Agent；可选绑定研究计划。" : "独立模式：确认后分析本次提供的实验材料。")
    : mode === "write" ? (workspaceEnabled ? "确认后才会创建论文写作 Agent；可选绑定研究计划。" : "独立模式：确认后生成论文草稿。")
    : mode === "respond" ? (workspaceEnabled ? "确认后才会创建审稿回复 Agent；可选绑定研究计划。" : "独立模式：确认后生成逐条回复。")
    : "工作区论文是 E 来源；计划、报告和代码草案会以独立来源标注。";
  const operationItems: MenuProps["items"] = (Object.keys(modeLabels) as ChatMode[])
    .filter((item) => item !== "chat")
    .map((item) => ({
      key: item,
      icon: modeIcons[item as Exclude<ChatMode, "chat">],
      label: modeLabels[item],
      disabled: !workspaceEnabled && (item === "research_plan" || item === "code_generation"),
    }));

  return <div className={`gm-chat-composer ${focused ? "is-focused" : ""}`}>
    <div className="gm-chat-agent-toolbar">
      <Space className="gm-chat-agent-actions" wrap size={[8, 8]}>
        <Tag color={mode === "chat" ? "blue" : "gold"}>{mode === "chat" ? "直接提问" : `操作建议：${modeLabels[mode]}`}</Tag>
        <Dropdown
          menu={{
            items: operationItems,
            onClick: ({ key }) => onModeChange(key as ChatMode),
          }}
          trigger={["click"]}
        >
          <Button size="small" icon={<MoreOutlined />} aria-label="更多研究操作">更多研究操作</Button>
        </Dropdown>
        <Button
          size="small"
          type={imageInputs.length > 0 ? "default" : "text"}
          icon={<PictureOutlined />}
          disabled={loading || mode !== "chat"}
          onClick={() => fileInputRef.current?.click()}
          title={mode === "chat" ? "上传图片或粘贴图片" : "图片输入目前仅支持直接提问"}
        >
          图片
        </Button>
        <input
          ref={fileInputRef}
          type="file"
          accept="image/jpeg,image/png,image/gif,image/webp"
          multiple
          hidden
          onChange={(event) => {
            void addImages(Array.from(event.target.files ?? []));
            event.currentTarget.value = "";
          }}
        />
        {workspaceEnabled && <Button
          size="small"
          type={contextOpen || hasSelectedContext ? "default" : "text"}
          icon={<SettingOutlined />}
          onClick={() => setContextOpen((open) => !open)}
        >
          上下文
        </Button>}
        {mode !== "chat" && <Button size="small" type="link" onClick={() => onModeChange("chat")}>回到提问</Button>}
      </Space>
      {workspaceEnabled && contextOpen && <Space className="gm-chat-context-controls" wrap size={[8, 8]}>
        <Select
          allowClear
          showSearch
          value={researchPlanId}
          onChange={onResearchPlanChange}
          options={planOptions}
          placeholder="研究计划（可选）"
          notFoundContent="暂无已确认研究计划"
          style={{ minWidth: 250, maxWidth: 360 }}
          optionFilterProp="label"
          aria-label="研究计划上下文"
        />
        {researchPlanId && sourceOptions.length > 0 && <Select
          mode="multiple"
          allowClear
          showSearch
          value={sourceArtifactIds}
          onChange={onSourceArtifactChange}
          options={sourceOptions}
          placeholder="补充报告或代码草案（可选）"
          maxTagCount={2}
          style={{ minWidth: 250, maxWidth: 360 }}
          optionFilterProp="label"
          aria-label="补充研究来源"
        />}
      </Space>}
    </div>
    {imageInputs.length > 0 && <div className="gm-chat-image-previews" aria-label="已添加图片">
      {imageInputs.map((image, index) => <div className="gm-chat-image-preview" key={`${image.filename}-${index}`}>
        <img src={image.data_url} alt={image.filename} />
        <Button type="text" size="small" icon={<CloseOutlined />} aria-label={`移除 ${image.filename}`} onClick={() => removeImage(index)} />
      </div>)}
    </div>}
    {mode !== "chat" && <Typography.Text className="gm-chat-agent-hint" type="secondary">{hint}</Typography.Text>}
    <Input.TextArea
      value={value}
      autoSize={{ minRows: 1, maxRows: 4 }}
      maxLength={12000}
      placeholder={placeholder}
      aria-label="输入消息"
      onFocus={() => setFocused(true)}
      onBlur={() => setFocused(false)}
      onChange={(event) => onChange(event.target.value)}
      onPaste={(event) => {
        const pastedImages = Array.from(event.clipboardData.files).filter((file) => file.type.startsWith("image/"));
        if (pastedImages.length > 0) {
          event.preventDefault();
          void addImages(pastedImages);
        }
      }}
      onKeyDown={(event) => {
        if (shouldSendOnEnter(event)) {
          event.preventDefault();
          send();
        }
      }}
    />
    <div className="gm-chat-composer-footer">
      {focused ? <Typography.Text type="secondary">Enter 发送，Shift + Enter 换行</Typography.Text> : <span aria-hidden="true" />}
      <Space>
        {(focused || value.length > 0) && <Typography.Text type="secondary">{value.length}/12000</Typography.Text>}
        <Button type="primary" icon={<SendOutlined />} loading={loading} disabled={sendDisabled} onClick={send}>
          {mode === "chat" ? "发送" : "查看启动建议"}
        </Button>
      </Space>
    </div>
  </div>;
}
