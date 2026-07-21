# Role

请你扮演一位 Principal AI Architect、Senior Backend Engineer 和 AI Agent System Designer。

你的目标不是写代码，而是帮助我们完成整个系统的后端架构设计。

请站在：

- AI Native Product
- Multi-Agent System
- 可扩展架构
- 比赛 MVP
- 后续科研论文

几个角度进行设计。

请尽量避免一开始就陷入数据库设计或代码实现。

我们希望先确定整个系统应该有哪些 Backend Services、Agent、数据流以及模块边界。

---

# 项目背景

项目名称：

GapMind

定位：

> 一个面向科研全生命周期（Research Lifecycle）的 AI Workspace。

注意：

Workspace

不是 Workflow。

用户：

不会按照：

Discover

↓

Plan

↓

Execute

↓

Analyze

↓

Publish

↓

Respond

一步一步使用。

任何模块：

都应该能够独立进入。

系统：

自动沉淀所有数据。

---

# 产品目标

GapMind 希望帮助科研人员完成整个科研生命周期。

包括：

Research Discovery

Research Planning

Baseline Execution

Experiment Analysis

Writing Assistant

Reviewer Rebuttal

Knowledge Management

Workspace Management

整个系统：

既支持：

一个功能单独使用。

也支持：

Workspace 自动串联。

---

# 当前产品模块

目前前端规划如下：

Dashboard

Workspace

Discover

Plan

Execute

Analyze

Publish

Respond

Knowledge Base

Settings

---

# 我们目前的技术背景

团队背景：

主要研究方向：

Graph Neural Network

熟悉：

PyTorch Geometric

Python

FastAPI

LLM Agent

RAG

Embedding

知识图谱

Multi-Agent

未来：

计划加入：

GNN

但：

第一版 MVP

不一定全部实现。

---

# 我们希望采用 AI Native Architecture

希望整个系统：

不是：

很多 API。

而是：

多个 Agent 协同完成任务。

例如：

Discover：

可以由：

多个 Agent

共同完成。

Analyze：

也可以：

多个 Agent。

但：

不要为了 Agent 而 Agent。

请合理拆分。

---

# 请重点讨论以下问题

## 第一部分

Backend 应该有哪些核心 Service？

例如：

Workspace Service

Knowledge Service

LLM Service

Task Service

Experiment Service

Paper Service

Auth Service

Storage Service

......

请重新设计。

说明：

每个 Service：

负责什么。

边界在哪里。

哪些可以独立部署。

哪些最好不要拆。

---

## 第二部分

整个系统应该有哪些 Agent？

例如：

Discover Agent

Planning Agent

Execution Agent

Writing Agent

Review Agent

Experiment Agent

Knowledge Agent

......

请说明：

哪些是真正需要 Agent。

哪些：

其实：

普通 Service

即可。

不要：

为了 Agent 而 Agent。

---

## 第三部分

Backend 的数据流应该是什么？

例如：

用户：

上传：

论文。

系统：

如何：

处理？

需要：

哪些 Service？

哪些 Agent？

哪些缓存？

哪些向量数据库？

哪些 Workspace 更新？

希望：

画出完整的数据流。

---

## 第四部分

Workspace 在 Backend 应该如何设计？

我认为：

Workspace

应该成为：

整个系统：

最核心的数据对象。

例如：

Workspace

关联：

Paper

Idea

Experiment

Draft

Reviewer

Knowledge

Timeline

Task

Agent History

请讨论：

是否合理？

还有哪些对象？

---

## 第五部分

Knowledge Base

如何设计？

目前：

Knowledge

不仅包含：

Paper。

还包括：

Method

Dataset

Code

Prompt

Experiment

Idea

Future Work

Reviewer

Question

Citation

请设计：

Knowledge Graph

或者：

统一的数据结构。

---

## 第六部分

Timeline

Backend

如何实现？

Timeline：

不是：

用户填写。

而是：

Agent

自动生成。

例如：

导入论文。

分析实验。

生成综述。

Reviewer。

全部：

自动记录。

请讨论：

Timeline

事件模型。

---

## 第七部分

Task

如何设计？

很多操作：

可能需要：

几分钟。

例如：

Discover。

Writing。

Experiment。

因此：

是否需要：

Task Queue？

Celery？

Redis？

Workflow？

请讨论。

---

## 第八部分

Memory

如何设计？

不同 Agent

是否：

共享：

Memory？

Workspace

是否：

应该拥有：

长期记忆？

Conversation

如何：

保存？

Knowledge

如何：

检索？

请讨论。

---

## 第九部分

MCP

是否：

需要？

哪些模块：

适合：

MCP？

哪些：

直接：

API？

请分析。

---

## 第十部分

GNN

未来：

应该接入哪里？

目前：

我们计划：

Research Opportunity Discovery：

LLM

↓

Fact Extraction

↓

Knowledge Graph

↓

GNN

↓

Opportunity Ranking

请分析：

未来：

GNN：

作为：

独立 Service？

独立 Agent？

还是：

Model Service？

如何：

最容易扩展？

---

# MVP 原则

请牢记：

我们是比赛。

不是：

商业产品。

请不断提醒：

哪些：

必须实现。

哪些：

第二阶段。

哪些：

未来。

不要：

一开始：

设计：

微服务集群。

希望：

先完成：

MVP。

---

# 输出要求

不要写代码。

不要写 FastAPI。

不要写数据库DDL。

请输出：

一个完整的：

Backend Architecture Proposal。

包括：

1.

整体架构图。

2.

Service 划分。

3.

Agent 划分。

4.

数据流。

5.

Workspace 数据模型。

6.

Knowledge 数据模型。

7.

Timeline。

8.

Task。

9.

Memory。

10.

MVP 开发优先级。

最后：

请站在：

比赛评委、

开源项目、

后期科研论文、

后续商业化

四个角度，

评价整个架构是否合理。

如果存在：

过度设计、

Agent 滥用、

微服务滥用、

GNN 滥用、

请直接指出。

# 注意事项

请不要一开始就设计6个Agent，建议的结构如下：

```
                 Frontend (Workspace)

                        │

                 GapMind Core

                        │

      ┌──────────────────────────────────┐
      │                                  │
 Workspace Service                Knowledge Service
      │                                  │
 Timeline Service                 Retrieval Service
      │                                  │
      └──────────────┬───────────────────┘
                     │
              AI Orchestrator
                     │
     ┌─────────┬─────────┬─────────┐
     │         │         │         │
 Discover   Analyze   Publish   Respond
   Agent      Agent      Agent      Agent
                     │
               Model Service
          (LLM / Embedding / GNN)
```

也就是说，

- **Workspace** 是整个系统的核心数据对象。
- **Knowledge** 是整个系统的知识中心。
- **AI Orchestrator** 负责任务编排。
- **Agent** 只是能力插件，而不是系统主体。

你可以按照上面的结构来设计，也可以有自己的不同意见，但需要讲清楚理由。