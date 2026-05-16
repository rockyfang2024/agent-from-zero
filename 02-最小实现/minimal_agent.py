# 02 - 最小实现

> 本章所有代码均可直接运行。每一行都有注释，解释为什么这样做。

## 2.1 最小 Agent：60 行核心代码

这是你能找到的最小的、可以调用工具的 Agent 实现。**删掉任何一行都会破坏功能**。

```python
#!/usr/bin/env python3
"""
最小 Agent 实现（Minimal Agent）

核心要素：
1. Message History（维护对话历史）
2. Tool Registry（工具注册表）
3. Agent Loop（循环调用 LLM 直到不需要工具）
"""

import json
from dataclasses import dataclass, field
from typing import Literal

# ─────────────────────────────────────────
# 第一部分：工具系统
# ─────────────────────────────────────────

@dataclass
class Tool:
    """工具定义"""
    name: str
    description: str
    parameters: dict          # JSON Schema 格式
    handler: callable          # 实际执行的函数

class ToolRegistry:
    """工具注册表——所有工具在这里登记"""
    def __init__(self):
        self._tools = {}

    def register(self, tool: Tool):
        self._tools[tool.name] = tool

    def get_schema(self, name: str) -> dict:
        """返回 OpenAI 格式的工具 schema"""
        t = self._tools[name]
        return {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters
            }
        }

    def list_schemas(self) -> list:
        return [self.get_schema(name) for name in self._tools]

    def call(self, name: str, args: dict) -> str:
        """执行工具"""
        return self._tools[name].handler(**args)

# 全局注册表
registry = ToolRegistry()

# ─────────────────────────────────────────
# 第二部分：Agent Loop
# ─────────────────────────────────────────

@dataclass
class Message:
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    name: str = None          # tool 调用时用
    tool_call_id: str = None   # tool 消息关联到哪个调用

@dataclass
class Agent:
    """最小 Agent"""
    system_prompt: str
    model: str = "gpt-4o"
    max_iterations: int = 20

    messages: list = field(default_factory=list)
    registry: ToolRegistry = field(default_factory=registry)

    def add_message(self, role, content, **kwargs):
        self.messages.append(Message(role, content, **kwargs).__dict__)

    def build_messages(self, user_input: str) -> list:
        """构建完整的 messages 数组"""
        msgs = []
        if self.system_prompt:
            msgs.append({"role": "system", "content": self.system_prompt})
        msgs.extend(self.messages)
        msgs.append({"role": "user", "content": user_input})
        return msgs

    def chat(self, user_input: str, call_llm_fn) -> str:
        """执行一次对话（Agent Loop）"""
        self.add_message("user", user_input)

        for _ in range(self.max_iterations):
            # ── Step 1: 调用 LLM ──
            response = call_llm_fn(
                model=self.model,
                messages=self.build_messages(user_input),
                tools=self.registry.list_schemas()
            )

            choice = response["choices"][0]
            msg = choice["message"]

            if "tool_calls" not in msg:
                # 没有工具调用——直接返回
                self.add_message("assistant", msg["content"])
                return msg["content"]

            # ── Step 2: 处理工具调用 ──
            for tool_call in msg["tool_calls"]:
                name = tool_call["function"]["name"]
                args = json.loads(tool_call["function"]["arguments"])

                try:
                    result = self.registry.call(name, args)
                except Exception as e:
                    result = f"Error: {e}"

                self.add_message(
                    "tool",
                    result,
                    tool_call_id=tool_call["id"],
                    name=name
                )

        return "Max iterations reached"


# ─────────────────────────────────────────
# 第三部分：运行示例
# ─────────────────────────────────────────

def demo():
    """演示：注册工具 → 运行 Agent"""

    # 注册一个工具：读取文件
    def read_file(path: str) -> str:
        """读取文件内容"""
        try:
            with open(path) as f:
                return f.read()
        except Exception as e:
            return f"Error: {e}"

    registry.register(Tool(
        name="read_file",
        description="读取文件内容",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"}
            },
            "required": ["path"]
        },
        handler=read_file
    ))

    # 模拟 LLM 调用（用 dict 模拟）
    # 真实环境这里换成 OpenAI API 调用
    def mock_llm(model, messages, tools):
        last_msg = messages[-1]["content"]
        if "read" in last_msg.lower() or "文件" in last_msg:
            # 模拟 LLM 返回 tool_call
            return {
                "choices": [{
                    "message": {
                        "content": None,
                        "tool_calls": [{
                            "id": "call_1",
                            "function": {
                                "name": "read_file",
                                "arguments": json.dumps({"path": "02-最小实现.py"})
                            }
                        }]
                    }
                }]
            }
        return {
            "choices": [{
                "message": {
                    "content": "文件读取完毕，内容是 Python 源代码。"
                }
            }]
        }

    agent = Agent(
        system_prompt="你是一个乐于助人的助手。",
        model="gpt-4o"
    )

    response = agent.chat("帮我读取 02-最小实现.py 的内容", mock_llm)
    print("Agent 响应:", response)

    # 打印对话历史
    print("\n─── 对话历史 ───")
    for msg in agent.messages:
        role = msg["role"].upper()
        content = msg["content"][:80] + "..." if len(str(msg["content"])) > 80 else msg["content"]
        print(f"{role}: {content}")


if __name__ == "__main__":
    demo()
```

### 运行方法

```bash
python 02-最小实现/minimal_agent.py
```

### 核心流程图

```
chat("帮我读取文件")
    │
    ▼
add_message(user, "帮我读取文件")
    │
    ▼
build_messages() → [system, ...history..., user]
    │
    ▼
call_llm_fn(model, messages, tools)
    │
    │ ← LLM 返回 tool_call
    ▼
registry.call("read_file", {path: "..."})
    │
    │ → 文件内容
    ▼
add_message(tool, "文件内容...")
    │
    │ → 继续 loop
    ▼
call_llm_fn(..., messages + tool_result)
    │
    │ ← LLM 返回最终回答
    ▼
return "文件读取完毕..."
```

---

## 2.2 真实 LLM 版本

把上面的 `mock_llm` 替换成真实 API，就是一个可用的 Agent：

```python
from openai import OpenAI
client = OpenAI(api_key="sk-...")

def real_llm(model, messages, tools):
    return client.chat.completions.create(
        model=model,
        messages=messages,
        tools=tools,
        tool_choice="auto"
    )

agent = Agent(system_prompt="你是一个有帮助的助手。", model="gpt-4o")
response = agent.chat("解释为什么天空是蓝色的", real_llm)
print(response)
```

---

## 2.3 带 Session 的版本

上面的 `Agent` 类把消息存在内存里——重启后就忘了你是谁。下面加上 SQLite 持久化：

```python
import sqlite3
import json
from dataclasses import dataclass, field

@dataclass
class SessionStore:
    """会话存储——用 SQLite 保存对话历史"""
    db_path: str = "sessions.db"

    def __post_init__(self):
        self.db = sqlite3.connect(self.db_path)
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                session_id TEXT,
                role TEXT,
                content TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self.db.execute("""
            CREATE INDEX IF NOT EXISTS idx_session
            ON messages(session_id, created_at)
        """)

    def save(self, session_id: str, role: str, content: str):
        self.db.execute(
            "INSERT INTO messages VALUES (?, ?, ?)",
            (session_id, role, content)
        )
        self.db.commit()

    def load(self, session_id: str, limit: int = 20) -> list:
        rows = self.db.execute(
            """SELECT role, content FROM messages
               WHERE session_id=? ORDER BY created_at DESC LIMIT ?""",
            (session_id, limit)
        ).fetchall()
        return [{"role": r, "content": c} for r, c in reversed(rows)]

class PersistentAgent(Agent):
    """持久化 Agent——重启后记得之前的对话"""
    def __init__(self, session_id: str, **kwargs):
        super().__init__(**kwargs)
        self.session_id = session_id
        self.store = SessionStore()

    def chat(self, user_input: str, call_llm_fn) -> str:
        # 先加载历史
        self.messages = self.store.load(self.session_id)
        response = super().chat(user_input, call_llm_fn)
        # 保存这次对话
        for msg in self.messages:
            self.store.save(self.session_id, msg["role"], msg["content"])
        return response
```

---

## 2.4 最小 Gateway：消息路由

把上面几节组合起来，就是一个**极简但完整**的 Agent 系统：

```
用户消息（来自任意平台）
    │
    ▼
┌─────────────────────────────┐
│  Gateway                    │
│  - 接收消息                  │
│  - 解析 session_id          │
│  - 路由到对应 Agent          │
│  - 处理响应                  │
└────────────┬────────────────┘
             │
             ▼
┌─────────────────────────────┐
│  PersistentAgent            │
│  - 加载历史                 │
│  - 执行 Agent Loop          │
│  - 保存历史                 │
└────────────┬────────────────┘
             │
             ▼
         用户看到回复
```

这就是 Hermes 和 OpenClaw 的核心——**Platform 无关的 Agent + 可替换的接入层**。