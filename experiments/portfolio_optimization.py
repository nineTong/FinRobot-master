"""
================================================================================
投资组合优化实验 — 三层多智能体协作系统入口脚本
================================================================================

【文件定位】
  本文件是一个"实验编排脚本"（Experiment Runner / Orchestration Script），
  不是 FinRobot 的库模块。它是用户直接运行的入口点。

【核心职责】
  1. 读取 investment_group.py 中的组织架构配置
  2. 构建一个三层多智能体层级：
       第一层 (CIO)            首席投资官 — 接收任务、分发子任务、汇总决策
          ├── 第二层 (Sub-Leaders)  三个分析小组的 Leader，各管理 2-3 名下属
          │     ├── 市场情绪分析组  (Senior Market Sentiment Analyst + 2 下属)
          │     ├── 风险评估分析组  (Senior Risk Analyst + 2 下属)
          │     └── 基本面分析组    (Senior Fundamental Analyst + 2 下属)
          └── 第三层 (Analysts)   具体执行分析的 Agent，调用数据工具
  3. 为所有 Agent 注册 RAG 检索工具（从 PDD 2022 年报检索信息）
  4. 向 CIO 发送投资分析任务，启动整个多智能体对话流程

【上下游关系】
  上游（本文件调用了什么）：
    - finrobot.agents.workflow.MultiAssistant           → 无 Leader 的群聊协作模式
    - finrobot.agents.workflow.MultiAssistantWithLeader → 有 Leader 的分派模式
    - finrobot.functional.rag.get_rag_function           → 创建 RAG 检索工具
    - finrobot.utils.register_keys_from_json             → 加载 API 密钥
    - investment_group.py.group_config                   → 所有 Agent 的角色定义
    - autogen (UserProxyAgent, register_function, etc.)  → AutoGen 框架原语

  下游（谁调用了本文件）：
    没有 — 本文件是叶子脚本，由用户直接运行：
      python experiments/portfolio_optimization.py

【执行流程图】
  ┌─────────────────────────────────────────────────────────────────────┐
  │ 1. 加载配置                                                          │
  │    ├─ llm_config: 从 OAI_CONFIG_LIST 读取 GPT-4 配置                 │
  │    └─ register_keys_from_json: 加载 Finnhub/FMP/SEC API 密钥        │
  │                                                                      │
  │ 2. 创建 UserProxyAgent（全局唯一的代码执行器）                         │
  │                                                                      │
  │ 3. 创建 RAG 检索函数（基于 PDD 2022 年报）                            │
  │    └─ get_rag_function() → (retrieve_content, RetrieveUserProxy)    │
  │                                                                      │
  │ 4. 构建三个分析小组（循环 group_config["groups"]）                     │
  │    ├─ 判断: 该组是否有 Leader?                                       │
  │    │   ├─ 有 Leader  → 创建 MultiAssistantWithLeader                 │
  │    │   └─ 无 Leader → 创建 MultiAssistant                           │
  │    ├─ 为组内每个 Agent 注册 RAG 工具                                  │
  │    └─ 将每个组的 representative 保存到 representatives 列表           │
  │                                                                      │
  │ 5. 构建 CIO 层                                                       │
  │    ├─ CIO 作为 Leader                                                │
  │    └─ 三个小组的 representative 作为下属                              │
  │    └─ 创建 MultiAssistantWithLeader (main_group)                     │
  │                                                                      │
  │ 6. 发送任务 → 启动对话                                               │
  │    └─ main_group.chat(message=task, use_cache=True)                  │
  └─────────────────────────────────────────────────────────────────────┘

【数据流向图 — 一次完整的任务执行】
  User Task (89-124行)
    │
    ▼
  main_group.chat(task)
    │  user_proxy.initiate_chat(CIO_Agent, message=task)
    ▼
  CIO_Agent (LLM 分析任务，决定先调用哪个小组)
    │  生成: "First, let me get market sentiment analysis.
    │          [Market_Sentiment_Analyst_1] Analyze recent market
    │          sentiment for PDD..."
    ▼
  order_trigger 检测到 "[Market_Sentiment_Analyst_1]" → 触发嵌套聊天
    │
    ▼
  嵌套聊天: User_Proxy → (Senior) Market_Sentiment_Analyst_1
    │  Leader Agent 调用工具 (FinnHubUtils, RedditUtils) 获取新闻/社交媒体数据
    │  生成分析报告
    │  结果经 "reflection_with_llm" 压缩为摘要
    ▼
  CIO_Agent 收到摘要 → 检查结果 → 分派下一个任务
    │  → [Risk_Analyst_1] Assess risks...
    │  → [Fundamental_Analyst_1] Analyze financials...
    │  → 汇总所有分析 → 生成最终投资建议
    ▼
  对话结束 (TERMINATE)

【Agent 交互层级图】
  ┌──────────────────────────────────────────────────────────┐
  │                    User Task (message=task)                │
  │                          │                                │
  │                          ▼                                │
  │  ┌──────────────────────────────────────────────────┐    │
  │  │  Level 1: CIO (MultiAssistantWithLeader)          │    │
  │  │  representative = Leader (Chief Investment Officer)│    │
  │  │                                                    │    │
  │  │  通过 [AgentName] 指令模式分派任务:                  │    │
  │  │    ┌──────────────────────┐                        │    │
  │  │    │ Market Sentiment     │ ← MultiAssistantWithLeader │
  │  │    │ Representative       │   (Level 2 sub-group) │    │
  │  │    │  ┌────────────────┐  │                        │    │
  │  │    │  │ Senior Market  │  │ ← Leader              │    │
  │  │    │  │ Sentiment      │──┼──→ dispatches to:      │    │
  │  │    │  │ Analyst        │  │   Market Sentiment    │    │
  │  │    │  └────────────────┘  │   Analyst (tools)     │    │
  │  │    │                      │   Junior Mkt Analyst  │    │
  │  │    └──────────────────────┘                        │    │
  │  │    ┌──────────────────────┐                        │    │
  │  │    │ Risk Assessment      │ ← MultiAssistantWithLeader │
  │  │    │ Representative       │                          │    │
  │  │    │  ┌────────────────┐  │                        │    │
  │  │    │  │ Senior Risk    │  │ ← Leader              │    │
  │  │    │  │ Analyst        │──┼──→ dispatches to:      │    │
  │  │    │  └────────────────┘  │   Risk Analyst        │    │
  │  │    │                      │   Junior Risk Analyst │    │
  │  │    └──────────────────────┘                        │    │
  │  │    ┌──────────────────────┐                        │    │
  │  │    │ Fundamental          │ ← MultiAssistantWithLeader │
  │  │    │ Representative       │                          │    │
  │  │    │  ┌────────────────┐  │                        │    │
  │  │    │  │ Senior Fundam  │  │ ← Leader              │    │
  │  │    │  │ Analyst        │──┼──→ dispatches to:      │    │
  │  │    │  └────────────────┘  │   Fundamental Analyst │    │
  │  │    │                      │   Junior Fund Analyst │    │
  │  │    └──────────────────────┘                        │    │
  │  └──────────────────────────────────────────────────┘    │
  └──────────────────────────────────────────────────────────┘

【嵌套聊天的触发链路】

  外层对话 (CIO ↔ User_Proxy)
  │
  │  CIO 生成消息: "... [Market_Sentiment_Analyst_1] Analyze sentiment..."
  │
  ├─ AutoGen 检查消息是否满足 trigger 条件
  │   └─ order_trigger(sender=CIO, pattern="[Market_Sentiment_Analyst_1]")
  │      → 检查 sender.name == "Chief_Investment_Officer" ✓
  │      → 检查 pattern in sender.last_message()["content"] ✓
  │      → 返回 True → 触发嵌套聊天
  │
  ├─ 嵌套聊天启动 (User_Proxy ↔ Market_Sentiment_Analyst_1)
  │   ├─ Turn 1: order_message 从 CIO 消息中提取指令
  │   │           → 用 order_template 包装为完整任务
  │   │           → 发送给 Market_Sentiment_Analyst_1
  │   ├─ Turn 2-N: Worker 调用工具、执行分析
  │   │             (最多 10 轮, 连续 3 次自动回复后强制终止)
  │   └─ 结束: summary_method="reflection_with_llm"
  │            → LLM 对完整对话做反思总结 → 摘要注入回外层
  │
  └─ CIO 收到摘要 → 继续分析或分派下一个任务
     → 循环直到所有子任务完成 → CIO 回复 "TERMINATE"
"""

# ═══════════════════════════════════════════════════════════════════════════════
# 第 1 步：导入依赖
# ═══════════════════════════════════════════════════════════════════════════════

import autogen
# ↑ AutoGen 框架核心，提供：
#   - config_list_from_json(): 从 OAI_CONFIG_LIST 文件读取 LLM 配置
#   - UserProxyAgent: 代码执行代理，所有 Agent 的工具代码在此执行
#   - register_function: 将 Python 函数注册为 function calling 工具

from finrobot.agents.workflow import MultiAssistant, MultiAssistantWithLeader
# ↑ 两种群组协作模式：
#   - MultiAssistant: GroupChat 自由讨论模式（无 Leader，轮转发言）
#   - MultiAssistantWithLeader: Leader 主导的任务分派模式（通过 [AgentName] 指令分发）

from finrobot.functional import get_rag_function
# ↑ RAG 检索函数工厂。返回 (retrieve_content 函数, RetrieveUserProxyAgent 实例)。
#   retrieve_content 内部调用了 AutoGen 的 RetrieveUserProxyAgent（基于 ChromaDB 向量检索），
#   但对调用方表现为一个普通的 function calling 工具。

from finrobot.utils import register_keys_from_json
# ↑ 从 config_api_keys 文件加载第三方数据源 API 密钥：
#   - Finnhub API Key → FinnHubUtils 的新闻数据
#   - FMP API Key     → FMPUtils 的财务指标数据
#   - SEC API Key     → SEC 文件下载

from textwrap import dedent
# ↑ 去除多行字符串的公共缩进，让 task 字符串保持整洁

from autogen import register_function
# ↑ 将 RAG 检索函数注册为所有 Agent 都可调用的工具

from investment_group import group_config
# ↑ 同目录下的投资小组配置文件，定义了整个组织的 Agent 层级结构：
#   - CIO（首席投资官）的职责定义
#   - 三个分析组的名称、职责和成员配置
#   - 每个成员的 toolkits（可调用的数据工具列表）


# ═══════════════════════════════════════════════════════════════════════════════
# 第 2 步：LLM 配置
# ═══════════════════════════════════════════════════════════════════════════════

llm_config = {
    "config_list": autogen.config_list_from_json(
        # ↑ 从 OAI_CONFIG_LIST 文件读取 API 配置
        #   文件路径相对于当前工作目录（experiments/ 的上级）
        "../OAI_CONFIG_LIST",
        filter_dict={
            "model": ["qwen-plus"],
            # ↑ 只选取指定模型的配置项。如果 OAI_CONFIG_LIST 中有多个模型，
            #   此过滤器确保只用 gpt-4-0125-preview
        },
    ),
    "cache_seed": 42,
    # ↑ 缓存种子。AutoGen 使用此值对 LLM 响应进行磁盘缓存。
    #   相同 seed 下相同输入会命中缓存，避免重复调用 LLM（节省 API 费用）。
    #   设为 None 可禁用缓存。

    "temperature": 0,
    # ↑ 温度参数。0 表示确定性输出（相同输入总是产生相同输出），
    #   这对金融分析场景至关重要——分析结果需要可复现、不被随机性干扰。
}


# ═══════════════════════════════════════════════════════════════════════════════
# 第 3 步：加载数据源 API 密钥
# ═══════════════════════════════════════════════════════════════════════════════

register_keys_from_json("../config_api_keys")
# ↑ 从 config_api_keys 文件加载第三方 API 密钥到环境变量：
#   - FINNHUB_API_KEY → FinnHubUtils.get_company_news 等
#   - FMP_API_KEY     → FMPUtils.get_financial_metrics 等
#   - SEC_API_KEY     → SEC 文件下载
#   这些密钥是 data_source/ 下各工具函数正常工作的前提条件。


# ═══════════════════════════════════════════════════════════════════════════════
# 第 4 步：创建 UserProxyAgent（全局唯一的工具执行器）
# ═══════════════════════════════════════════════════════════════════════════════

user_proxy = autogen.UserProxyAgent(
    name="User",
    # ↑ 代理名称，在对话日志中显示为 "User"

    human_input_mode="NEVER",
    # ↑ 人类输入模式：
    #   - "NEVER": 全自动 — Agent 的每次回复都不等待人类输入
    #   - "ALWAYS": 每轮都等待人类输入（调试用，已注释掉）
    #   - "TERMINATE": 只在终止时等待人类确认

    is_termination_msg=lambda x: x.get("content", "")
    and "TERMINATE" in x.get("content", ""),
    # ↑ 终止条件判断函数：
    #   当收到的消息内容中包含 "TERMINATE" 字符串时，对话自动结束。
    #   注意：这里用的是 "in" 而非 "endswith"（workflow.py 里用的是 "endswith"），
    #   所以只要消息中任意位置出现 TERMINATE 就会终止。

    code_execution_config={
        "last_n_messages": 3,
        # ↑ 代码执行上下文：执行 Python 代码时，向子进程发送
        #   最近 3 轮消息作为执行上下文（让代码能引用之前的结果）

        "work_dir": "quant",
        # ↑ 代码执行的工作目录。Agent 生成的 Python 脚本在此目录下创建和执行。

        "use_docker": False,
        # ↑ 不使用 Docker 隔离。代码直接在本地 Python 环境中执行。
        #   设为 True 时会在 Docker 容器中执行（更安全但需要 Docker 环境）。
    },
)


# ═══════════════════════════════════════════════════════════════════════════════
# 第 5 步：创建 RAG 检索函数（基于 PDD 2022 年报）
# ═══════════════════════════════════════════════════════════════════════════════

rag_func = get_rag_function(
    retrieve_config={
        "task": "qa",
        # ↑ 任务类型："qa" 表示问答模式（区别于 "code" 代码生成模式）

        "docs_path": "https://www.sec.gov/Archives/edgar/data/1737806/000110465923049927/pdd-20231231x20f.htm",
        # ↑ 要检索的文档路径：PDD (Pinduoduo) 2022 财年的 20-F 年报（SEC 文件）
        #   直接使用 SEC 官网的 HTML 链接。get_rag_function 内部会：
        #     1. 下载该文件
        #     2. 将内容分块（chunk）
        #     3. 通过 ChromaDB 建立向量索引

        "chunk_token_size": 1000,
        # ↑ 文档分块大小（token 数）。每个 chunk 最多 1000 token。
        #   较小的 chunk 提高检索精度，但可能丢失上下文；
        #   较大的 chunk 保留更多上下文，但检索精度下降。

        "collection_name": "pdd2022",
        # ↑ ChromaDB 中的 collection 名称。用于持久化和复用向量索引。
        #   如果 collection 已存在且 get_or_create=True，直接加载而不重建。

        "get_or_create": True,
        # ↑ 如果 collection "pdd2022" 已存在（上次运行时创建的），直接加载；
        #   如果不存在，从 docs_path 下载文档并创建新 collection。
        #   这避免了每次运行都重新下载和索引文档。
    },
)
# ↑ rag_func 是一个包含 (retrieve_content 函数, RetrieveUserProxyAgent 实例) 的元组。
#   - rag_func[0]: retrieve_content 函数 — 将被注册为 Agent 的工具
#   - rag_func[1]: RetrieveUserProxyAgent — RAG 代理实例（用于 reset 管理）


# ═══════════════════════════════════════════════════════════════════════════════
# 第 6 步：定义哪些小组使用 Leader 模式
# ═══════════════════════════════════════════════════════════════════════════════

with_leader_config = {
    "Market Sentiment Analysts": True,
    # ↑ 市场情绪分析组 → 使用 MultiAssistantWithLeader
    #   Leader: Senior Market Sentiment Analyst
    #   Workers: Market Sentiment Analyst, Junior Market Sentiment Analyst

    "Risk Assessment Analysts": True,
    # ↑ 风险评估分析组 → 使用 MultiAssistantWithLeader
    #   Leader: Senior Risk Analyst
    #   Workers: Risk Analyst, Junior Risk Analyst

    "Fundamental Analysts": True,
    # ↑ 基本面分析组 → 使用 MultiAssistantWithLeader
    #   Leader: Senior Fundamental Analyst
    #   Workers: Fundamental Analyst, Junior Fundamental Analyst
}
# ↑ 通过这个字典控制每个小组使用哪种协作模式：
#   - True: 该组有 Leader-Agent，使用 MultiAssistantWithLeader 层级分派模式
#   - False: 该组没有 Leader，使用 MultiAssistant 群聊自由讨论模式
#   （当前三个组都使用 Leader 模式）


# ═══════════════════════════════════════════════════════════════════════════════
# 第 7 步：构建三个分析小组（Level 2）
# ═══════════════════════════════════════════════════════════════════════════════

representatives = []
# ↑ 存储三个小组的代表 Agent（representative）。
#   每个代表 Agent 是：
#     - 有 Leader 的组 → Leader Agent（负责接收 CIO 分派的任务并协调内部成员）
#     - 无 Leader 的组 → GroupChatManager（负责管理群聊发言顺序）
#   这些代表最终会被注册为 CIO 的下属。

for group_name, single_group_config in group_config["groups"].items():
    # ↑ 遍历 investment_group.py 中定义的三个组：
    #   "Market Sentiment Analysts"
    #   "Risk Assessment Analysts"
    #   "Fundamental Analysts"
    #   每次迭代：
    #     single_group_config = {
    #       "responsibilities": [...],       ← 该组的整体职责（未直接使用）
    #       "with_leader": {                  ← Leader 模式的成员配置
    #         "leader": {...},
    #         "employees": [{...}, {...}]
    #       },
    #       "without_leader": {               ← 非 Leader 模式的成员配置
    #         "employees": [{...}, {...}, {...}]
    #       }
    #     }

    with_leader = with_leader_config.get(group_name)
    # ↑ 从 with_leader_config 判断当前组是否使用 Leader 模式

    if with_leader:
        # ── 分支 A：有 Leader ──
        # 使用 "with_leader" 配置，创建 MultiAssistantWithLeader 实例
        group_members = single_group_config["with_leader"]
        # ↑ group_members = {
        #     "leader": {"title": "Senior ...", "responsibilities": [...]},
        #     "employees": [{"title": "...", "responsibilities": [...], "toolkits": [...]}, ...]
        #   }

        group_members["agents"] = group_members.pop("employees")
        # ↑ 关键转换！将 "employees" key 重命名为 "agents"。
        #   investment_group.py 用 "employees" 命名（语义清晰），
        #   但 MultiAssistantWithLeader 期望 "agents" key。
        #   此重命名让配置与 workflow.py 的预期格式对齐。

        group = MultiAssistantWithLeader(
            group_members, llm_config=llm_config, user_proxy=user_proxy
        )
        # ↑ 创建有 Leader 的群组。内部流程：
        #   1. MultiAssistantWithLeader.__init__()
        #   2. → _init_agents(): 创建 Leader + 所有 Workers（每个都是 FinRobot 实例）
        #   3. → _get_representative():
        #        a. 构建 group_desc（所有 Workers 的 Name + Responsibility 汇总）
        #        b. 将 group_desc 注入 Leader 配置
        #        c. 初始化 Leader Agent（其 system_message 中包含 team 成员信息）
        #        d. 注册嵌套聊天: 当 Leader 消息中出现 "[WorkerName] 任务" 时，
        #           order_trigger 触发 User_Proxy → Worker 的嵌套对话
        #        e. 返回 Leader 作为 representative

    else:
        # ── 分支 B：没有 Leader ──
        # 使用 "without_leader" 配置，创建 MultiAssistant（GroupChat）实例
        group_members = single_group_config["without_leader"]
        # ↑ group_members = {
        #     "employees": [{"title": "...", ...}, {"title": "...", ...}, ...]
        #   }

        group_members["agents"] = group_members.pop("employees")
        # ↑ 同样的 key 重命名：employees → agents

        group = MultiAssistant(
            group_members, llm_config=llm_config, user_proxy=user_proxy
        )
        # ↑ 创建无 Leader 的群聊组。内部流程：
        #   1. MultiAssistant.__init__()
        #   2. → _init_agents(): 创建所有 Agent（都是平级的）
        #   3. → _get_representative():
        #        a. 创建 GroupChat（所有 Agent + UserProxy 参与）
        #        b. 设置 custom_speaker_selection_func 控制发言顺序
        #        c. 创建 GroupChatManager 作为群聊管理者
        #        d. 返回 GroupChatManager 作为 representative

    # ── 为当前组的所有 Agent 注册 RAG 检索工具 ──
    # group.agents 是在 _init_agents() 中创建的所有 Agent 列表。
    # 注意：这里用 register_function() 直接注册，而非通过 toolkits 配置，
    # 因为 RAG 函数是在运行时动态创建的（依赖于 retrieve_config），
    # 无法在 investment_group.py 中静态定义。
    for agent in group.agents:
        register_function(
            rag_func,
            # ↑ rag_func 是 get_rag_function 返回的元组的第一个元素，
            #   即 retrieve_content 函数。
            #   实际上这里有点问题——rag_func 应该是一个可调用对象，
            #   但 get_rag_function 返回的是 (retrieve_content, rag_assistant) 元组。
            #   查看 rag.py:366 → return retrieve_content, rag_assistant
            #   查看 workflow.py:570 → rag_func, rag_assistant = get_rag_function(...)
            #   所以 rag_func 确实是 retrieve_content 函数本身 ✓

            caller=agent,
            # ↑ caller: LLM 在 agent 的上下文中"看到"这个工具的描述，
            #   并判断何时该调用它来检索年报内容

            executor=group.user_proxy,
            # ↑ executor: 工具的代码在 user_proxy 中实际执行。
            #   与 agent 注册工具时的 executor 是同一个 user_proxy，
            #   确保所有工具调用都在同一执行上下文中。

            description="retrieve content from PDD's 2022 20-F Sec Filing for QA",
            # ↑ 工具描述：LLM 通过此描述理解工具的用途和适用场景。
            #   当 Agent 需要查找 PDD 年报中的具体数据时，会调用此工具。
        )

    # ── 保存当前组的 representative ──
    representatives.append(group.representative)
    # ↑ 将每个组的代表 Agent 加入列表。
    #   对于有 Leader 的组：representative = Leader Agent
    #   对于无 Leader 的组：representative = GroupChatManager
    #   这些代表将在下一步成为 CIO 的下属。
    #
    #   重要的架构设计：这里传的是 Agent 实例（ConversableAgent），而非配置字典。
    #   当 MultiAssistantWithLeader 的 _get_representative() 构建 group_desc 时，
    #   检查 isinstance(c, ConversableAgent) → 直接取 c.description 属性
    #   （见 workflow.py 第 972-976 行），避免了重复解析配置。


# ═══════════════════════════════════════════════════════════════════════════════
# 第 8 步：构建 CIO 层（Level 1 — 顶层 Leader）
# ═══════════════════════════════════════════════════════════════════════════════

cio_config = group_config["CIO"]
# ↑ 从 investment_group.py 中读取 CIO 配置：
#   {
#     "title": "Chief Investment Officer",
#     "responsibilities": [
#       "Oversee the entire investment analysis process.",
#       "Integrate insights from various groups.",
#       "Make the final decision on portfolio composition and adjustments.",
#     ]
#   }
#   注意：CIO 没有 toolkits（不需要直接调用数据工具），
#   它的核心能力是分析、整合和决策。

main_group_config = {
    "leader": cio_config,
    # ↑ CIO 是 Leader Agent

    "agents": representatives,
    # ↑ 三个子小组的代表 Agent 是 CIO 的"下属"
    #   representatives[0] = Market Sentiment Analysts 组的 Leader
    #                       (Senior Market Sentiment Analyst)
    #   representatives[1] = Risk Assessment Analysts 组的 Leader
    #                       (Senior Risk Analyst)
    #   representatives[2] = Fundamental Analysts 组的 Leader
    #                       (Senior Fundamental Analyst)
}

main_group = MultiAssistantWithLeader(
    main_group_config, llm_config=llm_config, user_proxy=user_proxy
)
# ↑ 创建 CIO 层的 MultiAssistantWithLeader。
#   内部流程：
#     1. _init_agents(): 创建 CIO (Leader) + 三个小组代表已存在（ConversableAgent 实例）
#     2. _get_representative():
#        a. 构建 group_desc：三个小组代表的 Name + Responsibility 信息
#           （因为已经是 ConversableAgent 实例，直接取 .description 属性）
#        b. 将 group_desc 注入 CIO 配置
#        c. 初始化 CIO Agent：
#           - system_message 包含：
#             "As a Chief Investment Officer, your responsibilities are:
#               - Oversee the entire investment analysis process.
#               - Integrate insights from various groups.
#               - Make the final decision.
#              You are the leader of the following group members:
#              Name: Market_Sentiment_Analyst_1
#              Responsibility:
#               - Track and interpret market trends and news.
#               - ...
#              Name: Risk_Analyst_1
#              Responsibility:
#               - ..."
#        d. 对每个子组代表注册嵌套聊天 trigger：
#           trigger = order_trigger(name=CIO.name, pattern="[Market_Sentiment_Analyst_1]")
#           当 CIO 消息中出现 "[Market_Sentiment_Analyst_1] 任务描述" 时，
#           User_Proxy → Market_Sentiment_Analyst_1 的嵌套对话被触发
#        e. 返回 CIO Agent 作为 representative


# ═══════════════════════════════════════════════════════════════════════════════
# 第 9 步：定义投资分析任务
# ═══════════════════════════════════════════════════════════════════════════════

task = dedent(
    """
    Subject: Evaluate Investment Potential and Determine 6-Month Target Price for Pinduoduo (PDD)

    Task Description:

    Today is 2023-04-26. As the Chief Investment Officer, your task is to evaluate the potential investment in Pinduoduo (PDD) based on the newly released 2022 annual report and recent market news. You will need to coordinate with the Market Sentiment Analysts, Risk Assessment Analysts, and Fundamental Analysts to gather and analyze the relevant information. Your final deliverable should include a comprehensive evaluation, a 6-month target price for PDD's stock, and a recommendation on whether to invest in Pinduoduo.

    Notes:

    All members in your group should be informed:
    - Do not use any data after 2023-04-26, which is cheating.


    Specific Instructions:

    [Coordinate with Market Sentiment Analysts]:
    Task: Analyze recent market sentiment surrounding PDD based on social media, news articles, and investor behavior.
    Deliverable: Provide a sentiment score based on positive and negative mentions in the past few months.

    [Coordinate with Risk Assessment Analysts]:
    Task: Assess the financial and operational risks highlighted in PDD's 2022 annual report (Form 20-F).
    Deliverable: Provide a risk score considering factors such as debt levels, liquidity, market volatility, regulatory risks, and any legal proceedings.

    [Coordinate with Fundamental Analysts]:
    Task: Perform a detailed analysis of PDD's financial health based on the 2022 annual report.
    Deliverable: Calculate key financial metrics such as Profit Margin, Return on Assets (ROA), and other relevant ratios.

    [Determine 6-Month Target Price]:
    Task: Based on the integrated analysis from all three groups, calculate a 6-month target price for PDD's stock.
    Considerations: Current stock price, market sentiment, risk assessment, and financial health as indicated in the annual report.

    [Final Deliverable]:
    Integrate Findings: Compile the insights from all three groups to get a holistic view of Pinduoduo's potential.
    Evaluation and 6-Month Target Price: Provide a 6-month target price for PDD's stock and a recommendation on whether to invest in Pinduoduo, including the rationale behind your decision.
    """
    # ↑ dedent(): 去除公共缩进。虽然这里使用三引号直接写了内容，
    #   但 dedent 确保了即使字符串有缩进也不会产生多余的前导空白。
)

# ── 以下是被注释掉的简单测试任务（不使用 RAG 和真实数据）──
# 这段注释掉的代码是一个简化版的测试用例，用于验证多 Agent 协调流程是否正确
# 而不依赖 RAG 检索和真实 API 调用。使用虚构的硬编码数值：
#   - 80 条正向提及, 20 条负向提及 → 情绪分数 = (80-20)/100 = 60%
#   - 负债权益比 1.5, 流动比率 2.0, ROE 10% → 风险分数
#   - 收入 $1M, 净收入 $100K, 总资产 $500K → 利润率 10%, ROA 20%
# task = dedent(
#     """
#     As the Chief Investment Officer, your task is to evaluate the potential investment in Company ABC based on the provided data...
#     """
# )


# ═══════════════════════════════════════════════════════════════════════════════
# 第 10 步：启动多智能体对话
# ═══════════════════════════════════════════════════════════════════════════════

main_group.chat(message=task, use_cache=True)
# ↑ 启动整个多智能体系统的对话流程。
#   调用链：
#     main_group.chat(message=task, use_cache=True)
#       → MultiAssistantBase.chat() (workflow.py:821)
#         → user_proxy.initiate_chat(self.representative, message=task, cache=cache)
#           → self.representative = CIO Agent
#             → CIO 收到任务消息 → LLM 分析任务
#             → CIO 生成消息（包含 [AgentName] 指令格式）
#             → order_trigger 检测到指令 → 嵌套聊天被触发
#             → 子 Agent 执行具体分析
#             → 分析结果经 reflection_with_llm 压缩为摘要
#             → CIO 收到摘要 → 继续分派或汇总
#             → 循环直到 CIO 回复 "TERMINATE"
#
#   use_cache=True:
#     - 启用 AutoGen 磁盘缓存
#     - 如果之前运行过相同任务且 LLM 响应已缓存，可直接复用
#     - 节省 API 调用费用，加快调试迭代速度
#     - 注意：修改了 task 内容后缓存将不命中（prompt 改变了）
