# Agent From Zero

> 从零手写一个 Hermes / OpenClaw，理解核心设计。

## 背景

Hermes（Python）和 OpenClaw（TypeScript/Node.js）是两个自托管 AI Agent 运行时，本项目提取它们共同的核心理念，用最少的代码展示 Agent 的完整运行逻辑。

## 目录结构

```
01-核心架构/     核心模块的设计解析
02-最小实现/     可以直接运行的最小 Agent + Gateway
03-设计对比/     Hermes vs OpenClaw 设计哲学对比
04-Hermes核心设计问答.md  ← 问题驱动：16个问题理解 Hermes 为什么这样设计
05-源码导读.md             ← 带着问题读 Hermes 源码：15个源码问题 + 调试命令
```

## 适合谁

- 想深入理解 Agent 运行原理的开发者
- 想基于 Hermes/OpenClaw 做二次开发但不知从哪下手的开发者
- 对 AI Agent 架构感兴趣，想从"零件"层面理解它的工作方式的任何人

## 核心前提

无论 Hermes 还是 OpenClaw，它们的核心都是一个**消息循环**：

```
用户消息 → 构建 Prompt → 调用 LLM → 工具调用？→ 执行工具 → 继续调用 LLM → 返回结果
```

所有复杂的功能（Session、Skills、路由、多Agent）都是在这个循环基础上的扩展。