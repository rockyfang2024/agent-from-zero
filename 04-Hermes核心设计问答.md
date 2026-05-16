# 04 - Hermes 核心设计问答

> 通过问题驱动理解 Hermes 为什么这样设计。

**阅读方式**：先自己思考每个问题，再看答案。不要急于跳过——思考本身就是学习的一部分。

---

## 第一章：Agent 是怎么"思考"的？

### Q1：当我对你说"帮我读取 config.yaml"，你（作为 AI）是怎么知道要调用哪个工具的？

**思考 3 秒**：LLM 本身只会说话，它怎么知道"读取文件"这件事是可以做的？

---

**答案**

LLM 本身并不知道自己能做什么。是你（开发者）告诉它的——通过在请求里携带**工具的描述（Schema）**。

```python
# 你告诉 LLM："你有这些能力"
tools = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取文件内容",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"}
                },
                "required": ["path"]
            }
        }
    }
]

# LLM 看到这些描述后，知道"读文件可以用 read_file"
response = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "帮我读取 config.yaml"}],
    tools=tools        # ← 把工具说明书传给它
)
```

**LLM 看到工具描述后**，如果用户请求涉及文件操作，它就知道说"我要调用 read_file"。

**为什么这样设计？**

LLM 是通用的，但它不知道自己连接了什么工具。通过 Schema 描述，Agent 系统把"能力清单"动态注入 LLM——你给什么工具，LLM 就认为它有什么能力。这是一种**依赖注入**的设计思想。

---

### Q2：为什么 Agent 要"循环"调用 LLM？一次调用不够吗？

**思考**：如果 LLM 一次就能完成任务，干嘛要循环？

---

**答案**

因为**工具的执行结果需要再交给 LLM**。

举个例子：

```
用户: "帮我读取 config.yaml，然后告诉我里面有多少行"

LLM 第1次调用:
  返回: tool_call { name: "read_file", args: { path: "config.yaml" } }
  
Agent 执行工具 → 返回: "共 42 行"

LLM 第2次调用:
  输入: 用户问题 + 工具结果 "共 42 行"
  返回: "config.yaml 共有 42 行"

结束
```

LLM 不会自动"看到"工具执行的结果——必须由 Agent 把结果塞回 messages 数组，下一次 LLM 调用才能用到这个结果。

**这引出了 Hermes 的核心——Agent Loop：**

```python
while True:
    response = llm(messages, tools)
    
    if 没有 tool_call:
        return response  # 完成了
    
    for tool_call in response.tool_calls:
        result = execute_tool(tool_call)
        messages.append(tool_result_message(result))  # 结果塞回去
    
    # 继续循环
```

这个循环会持续直到 LLM 说"不需要再调用工具了"。

**一个思考题**：如果 LLM 在循环里开始"胡说八道"（幻觉），调用不存在的工具名字，Agent 会怎样？翻翻 Hermes 的 `handle_function_call` 看看有没有防护。

---

### Q3：工具那么多，Agent 怎么知道该用哪一个？

**思考**：如果有 50 个工具，全传给 LLM 会发生什么？

---

**答案**

会出两个问题：

1. **Token 爆炸**：每个工具的 Schema 都是一段文字，50 个工具的描述可能占几千个 token，你的上下文窗口被浪费了
2. **LLM 选择困难**：工具太多，LLM 容易选错（选了"删除文件"而不是"读取文件"）

**Hermes 的解法：按需加载（Toolsets）**

```python
# Hermes 的工具集概念
ENABLED_TOOLSETS = ["terminal", "file", "web"]  # 按场景分组

# 你不会一次性加载所有工具
# 而是根据当前任务加载需要的
active_tools = []
for toolset_name in ENABLED_TOOLSETS:
    active_tools.extend(get_toolset(toolset_name).tools)

# 调用 LLM 时只传当前需要的工具
llm(messages, tools=active_tools)
```

这就像一个工具箱——木匠不会把所有工具都摆出来，而是根据任务选合适的工具。

**OpenClaw 也是类似的思路**，只是用"插件"而不是"工具集"来分组。

---

## 第二章：Agent 怎么"记住"之前的对话？

### Q4：为什么需要 Session？重启程序后 Agent 怎么知道"上次说了什么"？

**思考**：如果每次对话都是独立的，Agent 会变成什么样？

---

**答案**

假设没有 Session：

```
用户: "你好，我是 Rocky"
Agent: "你好 Rocky！很高兴认识你"

用户: "刚才你说的那个项目，能展开讲讲吗？"
Agent: "抱歉，我不知道你在说哪个项目..."
       ↑ Agent 失忆了，因为这是全新的对话
```

**Session 就是让 Agent 有"记忆"的东西。**

Hermes 用 SQLite 存储历史消息：

```python
class SessionStore:
    def __init__(self, db_path="~/.hermes/sessions.db"):
        self.db = sqlite3.connect(db_path)
        # 建表：session_id, role, content, created_at
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                session_id TEXT, role TEXT, content TEXT, created_at DATETIME
            )
        """)

    def save(self, session_id, role, content):
        self.db.execute(
            "INSERT INTO messages VALUES (?, ?, ?, ?)",
            (session_id, role, content, datetime.now())
        )
        self.db.commit()

    def load(self, session_id, limit=20):
        rows = self.db.execute(
            """SELECT role, content FROM messages
               WHERE session_id=? ORDER BY created_at LIMIT ?""",
            (session_id, limit)
        ).fetchall()
        return [{"role": r, "content": c} for r, c in reversed(rows)]
```

每次对话时：

```
1. 从数据库加载这个 session_id 的最近 20 条消息
2. 塞进 LLM 的 messages 数组
3. LLM "看到"历史对话，就知道上下文
4. 对话结束后，新消息再存回数据库
```

---

### Q5：对话越来越长，token 不够用了怎么办？

**思考**：假设一个 Session 有 100 轮对话，每轮 500 tokens，总共 50,000 tokens，但 LLM 只能处理 8,000 tokens。怎么办？

---

**答案**

四种策略，Hermes 组合使用：

**策略 1：只加载最近的消息（滑动窗口）**
```python
# 永远只加载最近 20 条
messages = session.load(session_id, limit=20)
```

**策略 2：压缩（Compress）**
```python
# 把 20 条消息压缩成 1 条摘要
compressed = compressor.compress(messages)
# "用户 Rocky，主要讨论了 Python 异步编程，包括 asyncio、await、async def 的用法..."
```

**策略 3：完全不加载历史，靠检索（Retrieval）**
```python
# 用 FTS5 全文搜索找到相关历史，而不是全量加载
relevant = session.search("Python 异步", limit=5)
# 只注入与当前问题相关的历史
```

**策略 4：截断（Truncate）**——最简单但最傻
```python
# 直接丢掉太旧的消息
messages = messages[-max_tokens:]
```

Hermes 默认用**策略 1 + 2 组合**：
- 最近 N 条消息完整保留
- 更早的消息压缩成摘要
- 只在必要时才触发压缩（节省 API 调用）

**为什么不用一种策略搞定？** 因为每种都有权衡：
- 滑动窗口简单但可能丢失重要上下文
- 压缩有延迟（要多调用一次 LLM 做摘要）
- 检索最精准，但实现最复杂

---

### Q6：Session 是怎么做到"多用户并发"的？

**思考**：两个用户同时跟 Agent 说话，Session 会混乱吗？

---

**答案**

不会——靠 **session_id 隔离**。

```python
# 每个用户/每个对话 = 不同的 session_id
session_rocky    = "session_rocky_001"
session_alice    = "session_alice_001"

# 数据库里完全隔离
SessionStore().load(session_rocky)    # 只读到 Rocky 的消息
SessionStore().load(session_alice)    # 只读到 Alice 的消息

# 并发请求到达时
messages_rocky = load_from_db("session_rocky_001")
messages_alice = load_from_db("session_alice_001")

# 各自独立，互不影响
```

这就像两个 Excel 文件——同时打开，修改互不干扰。

**那同一个用户开两个标签页呢？**

```python
# 标签页 A 和 B 都属于 Rocky，但对话是分开的
session_rocky_tab_a = "session_rocky_001_tabA"
session_rocky_tab_b = "session_rocky_001_tabB"
```

如果你希望同一个对话在多设备间同步（比如手机和电脑看到同一个对话），那是另一个问题——需要 Session 同步而不是 Session 隔离。

---

## 第三章：Agent 怎么"变成"一个有性格的助手？

### Q7：为什么要有 SOUL.md？直接在代码里写不好吗？

**思考**：如果你要改变 Agent 的性格（比如让"话痨"变"简洁"），改代码麻烦吗？

---

**答案**

直接在代码里写：
```python
# 改代码 → 需要改 Python 文件 → 需要理解代码逻辑 → 需要测试 → 发布
# 如果改错了，还要回滚
```

用 SOUL.md：
```markdown
# SOUL - Rocky 的 AI 助手
## 风格
- 简洁直接，不废话
- 技术问题从原理讲清楚
- 喜欢用类比解释复杂概念
```

**把人格定义从代码分离出来，好处是：**
1. **非程序员也能改**：运营、PM 直接改 SOUL.md，不用碰代码
2. **不需要发布**：改完文件，Agent 重启就生效
3. **版本控制**：SOUL.md 提交到 Git，看历史、改错能回滚
4. **多个 Agent 不同性格**：给 Rocky 的 Agent 和给 Alice 的 Agent 性格可以完全不同

**这就是"配置与代码分离"原则**——代码改的是"怎么做"，配置改的是"做什么/是什么样"。

---

### Q8：Hermes 怎么把 SOUL.md 和其他文件拼接成 System Prompt？

**思考**：一个 Agent 启动时，它的人格、知识、行为准则分别来自不同文件，这些是怎么组装成完整的 System Prompt 的？

---

**答案**

```python
def build_system_prompt():
    parts = []
    
    # 1. 身份定义（SOUL.md）
    parts.append(load("SOUL.md"))  # "你是一个技术极客..."
    
    # 2. 行为准则（AGENTS.md）
    parts.append(load("AGENTS.md"))  # "开发环境、测试要求..."
    
    # 3. 用户信息（USER.md）
    parts.append(load("USER.md"))  # "用户叫 Rocky，9年经验..."
    
    # 4. 环境提示（当前目录、平台等）
    parts.append(f"当前目录: {os.getcwd()}")
    parts.append(f"平台: {platform.platform()}")
    
    # 5. 已启用的 Skills 的提示词片段
    for skill in enabled_skills:
        parts.append(skill.prompt)
    
    return "\n\n---\n\n".join(parts)
```

最终注入 LLM 的 System Prompt 大致是：

```
你是一个技术极客，9年交易所开发经验，擅长 Python 和系统架构。（来自 SOUL.md）

## 开发准则
- 写代码前先解释思路
- 测试覆盖率要达到 80% 以上
- ...（来自 AGENTS.md）

用户叫 Rocky，目前在一家交易所工作...（来自 USER.md）

当前目录: /home/ubuntu/projects
平台: Linux-5.15.0-x86_64

## 启用的技能
[code-review skill 的提示词]
[git-helper skill 的提示词]
```

**为什么这样分层？**
- SOUL.md 定义"是什么人"
- AGENTS.md 定义"做事的方式"
- USER.md 定义"为谁服务"
- 任何一部分改动都不影响其他部分

---

## 第四章：工具系统是怎么设计的？

### Q9：为什么工具要"注册"而不是直接写死？

**思考**：如果你直接在代码里写 `def read_file(): ...`，有什么问题？

---

**答案**

写死的问题：

```python
# 硬编码 → 这个函数只能在当前文件用
def read_file(path):
    with open(path) as f:
        return f.read()
```

**注册制的优势：**

```python
# 1. 工具可以被"发现"
registry.list_tools()  # ["read_file", "write_file", "terminal", ...]

# 2. 工具可以被"过滤"
active = registry.list_tools(toolsets=["file"])  # 只要文件相关工具

# 3. 工具可以被"替换"
registry.unregister("read_file")
registry.register("read_file", schema, new_handler)  # 换实现，不改调用方

# 4. 工具可以被"热插拔"
# 启动时加载插件，插件注册自己的工具，不用改主程序
```

**核心思想：控制反转（IoC）**
- 写死：主程序依赖具体函数
- 注册：工具主动注册，主程序只依赖接口

---

### Q10：工具执行出错了怎么办？Agent 会不会卡住？

**思考**：如果工具执行超时、文件不存在、权限不足，Agent 会怎样？

---

**答案**

Hermes 有完整的错误处理链：

```python
def handle_function_call(tool_name, tool_args, context):
    try:
        # 参数校验
        validate_args(tool_name, tool_args)
        
        # 执行
        result = registry.call(tool_name, tool_args)
        return {"status": "ok", "result": result}
    
    except ToolNotFoundError:
        return {"status": "error", "message": f"工具 {tool_name} 不存在"}
    
    except PermissionError:
        return {"status": "error", "message": "权限不足，请检查文件权限"}
    
    except TimeoutError:
        return {"status": "error", "message": "执行超时，已终止"}
    
    except Exception as e:
        # 未知错误 → 记录 + 返回（不让 Agent 卡死）
        logger.error(f"工具执行异常: {e}")
        return {"status": "error", "message": f"执行出错: {e}"}
```

**关键是：工具出错不能杀死 Agent Loop**——把错误格式化成消息返回给 LLM，让 LLM 决定下一步怎么办。

```python
# Hermes 的实际处理逻辑（简化）
tool_result = handle_function_call(...)
messages.append(tool_result_message(tool_result))
# ↑ 把错误包装成消息，LLM 会看到这个"工具执行失败了"
// LLM 可以选择：换工具、重试、告诉用户做不到
```

**这比"报错就停止"好在哪？**

| 方式 | LLM 能否恢复 | 用户体验 |
|------|------------|---------|
| 报错停止 | ❌ 需要人工介入 | "抱歉出错了，请重试" |
| 错误转消息 | ✅ 看到错误后可换策略 | "我用另一个方法试了一下，这次成功了" |

---

### Q11：危险工具（如 rm -rf）怎么管控？

**思考**：如果用户让 Agent "删除根目录"，你愿意让它真的执行吗？

---

**答案**

Hermes 有**审批链（Approval Chain）**：

```
用户输入: "删除根目录"
    │
    ▼
检测到 dangerous tool call: terminal
    │
    ▼
approval.check(tool_name, args, session_id)
    │
    ├─ 如果是已知安全模式（如已认证的 session）→ 执行
    │
    ├─ 如果是危险模式 → 暂停，等待人工审批
    │   → 弹出提示: "即将执行 rm -rf /，是否确认？"
    │
    └─ 如果是黑名单模式 → 直接拒绝
```

```python
# Hermes 的 approval.py 大致逻辑
DANGEROUS_PATTERNS = [
    (r"rm\s+-rf\s+/", "极危险：递归删除根目录"),
    (r"curl\s+.*\s+|\s+sh", "危险：远程代码执行"),
    (r"shutdown|reboot", "危险：系统关机"),
]

def check_dangerous(command: str) -> ApprovalResult:
    for pattern, description in DANGEROUS_PATTERNS:
        if re.search(pattern, command):
            return ApprovalResult(block=True, reason=description)
    return ApprovalResult(block=False)
```

**这引出一个设计哲学**：

> 给 Agent 越强大能力的同时，越需要给它装上"安全带"。

工具能力越强，错误的后果越严重。审批链就是在强大能力和安全之间取得平衡。

---

## 第五章：系统怎么应对真实环境的各种问题？

### Q12：LLM API 调用失败了怎么办？直接重试吗？

**思考**：网络抖动、超时、服务器 500 错误——简单的 `while True: retry()` 够用吗？

---

**答案**

不够。Hermes 有**智能重试策略**：

```python
# Hermes 的重试逻辑（简化自 retry_utils.py）
def call_with_retry(call_llm_fn, *args, **kwargs):
    max_attempts = 3
    
    for attempt in range(max_attempts):
        try:
            return call_llm_fn(*args, **kwargs)
        
        except RateLimitError:
            # 429 → 等待一段时间后重试
            wait_time = calculate_backoff(attempt)
            time.sleep(wait_time)
        
        except ServerError:
            # 500/502/503 → 稍等重试
            time.sleep(1 * (attempt + 1))
        
        except ContextLengthError:
            # context 超出 → 压缩上下文，不要重试
            raise  # 压缩也失败，直接放弃
        
        except NetworkError:
            # 网络问题 → 重试，但有超时保护
            if attempt == max_attempts - 1:
                raise
        
    raise MaxRetriesExceeded()
```

**为什么重试也分不同策略？**

因为不同错误的原因不同：
- **Rate Limit（429）**：服务端限流，重试等一等
- **Server Error（500）**：服务端问题，很可能短暂，重试可能好
- **Context Length（400）**：请求本身有问题，重试也会失败，浪费时间
- **Network Error**：可能是网络抖动，可以重试

**这叫"差异化重试策略"——不是眉毛胡子一把抓。**

---

### Q13：Hermes 支持同时连接多个模型供应商，为什么？

**思考**：用一个 API Key 不就够了吗？

---

**答案**

多供应商 = 多重保险 + 成本优化：

```
单一供应商的问题：
├── API Key 泄露 → 所有请求都暴露
├── 供应商宕机 → Agent 完全不可用
├── 某些模型某个供应商更强 → 无法选择最好的
└── 成本差异 → 无法用便宜的模型处理简单任务
```

**Hermes 的多供应商架构：**

```python
# 配置文件
providers:
  anthropic:
    api_key: $ANTHROPIC_API_KEY
    models: [claude-opus-4.6, claude-sonnet-4.6]
  
  openrouter:
    api_key: $OPENROUTER_API_KEY
    models: [gpt-4o, gemini-pro, llama-3]
```

```python
# 路由策略（伪代码）
def route_model(task_type, preferred=None):
    if preferred and model_available(preferred):
        return preferred  # 优先用指定的
    
    if "代码生成" in task_type:
        return "anthropic/claude-opus-4.6"  # Claude 代码更强
    
    if "快速问答" in task_type:
        return "openrouter/gpt-4o-mini"    # 便宜快速
    
    return "openrouter/gpt-4o"             # 默认
```

**好处汇总：**

| 场景 | 解决方案 |
|------|---------|
| Anthropic 宕机 | 自动切换到 OpenRouter |
| 简单任务 | 用便宜模型省成本 |
| 复杂推理 | 切换到 Opus |
| API 限流 | 分流到多个供应商 |

---

### Q14：Anthropic 的 Prompt Caching 是什么？为什么重要？

**思考**：每次对话都要把 System Prompt + 历史消息发给 LLM，这些内容每次都重新计费吗？

---

**答案**

传统方式：每次请求都传完整的 messages
```
第1次请求: [system_prompt(4KB) + history(50KB)] → $0.05
第2次请求: [system_prompt(4KB) + history(50KB)] → $0.05  ← 重复传输 system_prompt
第3次请求: [system_prompt(4KB) + history(50KB)] → $0.05  ← 又传一遍
```

Anthropic 的 Prompt Caching 把"不变的"部分（System Prompt）缓存起来，只传变化的部分：
```
System Prompt 被缓存 → 只传一次
第1次请求: [cache_tag + history(50KB)] → $0.05
第2次请求: [cache_hit + history(50KB)] → ~$0.005  ← 系统复用缓存
第3次请求: [cache_hit + history(50KB)] → ~$0.005
```

**Hermes 的实现：**

```python
# 1. 构建 System Prompt 时标记缓存边界
def build_messages(...):
    messages = []
    # ... 前面部分会被缓存
    messages.append({"role": "system", "content": system_prompt,
                     "cache_control": {"type": "ephemeral"}})
    # ... 后面部分每次变化，不缓存
    messages.append({"role": "user", "content": user_input})
    return messages

# 2. 检测 context 是否复用上次
existing_cache = detect_existing_cache(system_prompt)
if existing_cache:
    # 告诉 LLM 用这个缓存
    messages[0]["cache_control"] = existing_cache
```

**实际效果**：System Prompt 有 4KB，history 每轮 1KB，100 轮对话：
- 不用缓存：100 × 5KB = 500KB 的 system prompt 传输
- 用缓存：4KB（只传一次）+ 100 × 1KB = 104KB，**省了 80%**

---

## 第六章：从全局看，为什么这样架构？

### Q15： Hermes 为什么用"Gateway + Agent"分离的架构？

**思考**：如果把消息接收、路由、Agent 执行全写在一个进程里，有什么问题？

---

**答案**

单进程的问题：

```
聊天应用 ──┐
          ├──→ 单一服务 ──→ Agent（忙时排队）
Telegram  ──┤
          │
Discord   ──┘

问题：
1. 一个通道挂了，其他也受影响
2. 无法独立扩展"接入层"和"Agent 层"
3. 重启一个通道要重启整个服务
4. 多开发者协作时，互相干扰
```

**分离架构：**

```
Channel Plugins (各自独立进程)
    │     │     │
    ▼     ▼     ▼
   API   API   API
    │     │     │
    └─────┼─────┘  ← Gateway 统一接收
          │
          ▼
      Agent Runtime
```

**好处：**
- Gateway 挂了 → Channel Plugins 还活着，只是没消息
- Agent 要更新 → Gateway 和通道都不用动
- 某通道限流 → Gateway 可以单独熔断它
- 未来加新通道 → 只需写新 Plugin，不碰核心

**这其实就是"高内聚低耦合"原则的体现。**

---

### Q16：综合前面的所有问题，Hermes 的核心设计哲学是什么？

**思考**：看完所有设计，有没有一条主线可以把它们串起来？

---

**答案**

**一条主线：把"变的"和"不变的"分开。**

| 分离点 | 不变（稳定） | 变（灵活） |
|--------|------------|---------|
| 人格 vs 逻辑 | SOUL.md 定义人格 | Agent Loop 代码逻辑 |
| Schema vs 实现 | 工具的 name/description | 工具的具体函数 |
| 通道 vs 核心 | Agent Loop | Telegram/Discord 适配器 |
| 配置 vs 代码 | config.yaml / .env | Python 源代码 |
| 错误处理 vs 业务逻辑 | try/except 框架 | 具体报错信息 |

**为什么这样做？**

- 变的部分要容易改（不改代码，改配置/文件）
- 不变的部分要稳定（严格测试，确保核心不出错）
- 新功能通过扩展实现（加 Tool，加 Skill，加 Plugin），不修改已有代码

**这六章问答的核心收获：**

```
1. Agent 的本质是"循环调用 LLM + 执行工具"
2. Session 让 Agent 有记忆（SQLite 持久化 + 滑动窗口）
3. System Prompt 分层拼接（SOUL/AGENTS/USER/环境）
4. 工具通过注册制实现可发现、可替换、可热插拔
5. 错误不是终点，是 LLM 可以响应的信息
6. 多供应商 + 智能路由 = 稳定 + 省钱
7. Prompt Caching 把重复传输省掉 80%
8. 架构哲学：把"变的"和"不变的"分开
```

**下一步**：带着这些问题去看 Hermes 源码（`run_agent.py`、`model_tools.py`、`tools/registry.py`），你会发现每一行代码都在呼应上面的答案。