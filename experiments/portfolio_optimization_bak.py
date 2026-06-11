"""
投资组合优化实验 — 三层嵌套多 Agent 架构 (Portfolio Optimization Experiment)
================================================================================

┌─────────────────────────────────────────────────────────────────────────────┐
│                            模块定位与核心职责                                │
│                                                                             │
│  本文件是 FinRobot 项目中最复杂的多 Agent 编排示例。                           │
│  它构建了一个模拟真实投资公司的三层组织架构，用于分析拼多多(PDD)的投资价值：      │
│                                                                             │
│    Tier 1: CIO（首席投资官）— 总指挥，整合三个分析组的结论，做出最终决策         │
│    Tier 2: 3 个分析组组长 — 各自管理一个分析团队，向 CIO 汇报                  │
│    Tier 3: 各组 Worker Agent — 执行具体的数据采集、分析任务                    │
│                                                                             │
│  在 FinRobot 的架构层级中：                                                  │
│    investment_group.py (定义组织架构)                                         │
│      → portfolio_optimization.py (构建并运行 Agent 层级) ← 你在这里           │
│        → workflow.py (MultiAssistantWithLeader 类)                           │
│          → AutoGen (底层对话框架)                                             │
└─────────────────────────────────────────────────────────────────────────────┘

组织架构全景图
==============

    ┌─────────────────────────────────────────────────────────────────────┐
    │                    User Proxy (共享执行器)                            │
    │  所有 Agent 的工具调用都在这里实际执行                                   │
    │  ┌─────────────────────────────────────────────────────────────┐    │
    │  │ 工具集:                                                      │    │
    │  │  - FinnHubUtils.get_company_news (新闻数据)                  │    │
    │  │  - RedditUtils.get_reddit_posts (Reddit帖子)                 │    │
    │  │  - YFinanceUtils.get_stock_data (股价数据)                   │    │
    │  │  - FMPUtils.get_financial_metrics (财务指标)                 │    │
    │  │  - retrieve_content (RAG检索PDD年报)                         │    │
    │  └─────────────────────────────────────────────────────────────┘    │
    └─────────────────────────────────┬───────────────────────────────────┘
                                      │ initiate_chat
                                      ▼
    ┌─────────────────────────────────────────────────────────────────────┐
    │  Tier 1: CIO (Chief Investment Officer)                             │
    │  MultiAssistantWithLeader 的 Leader Agent                            │
    │  system_message 包含:                                                │
    │    - role_prompt: "As a Chief Investment Officer, ..."               │
    │    - leader_prompt: "You are the leader of: [三个组长信息]"           │
    │  通过 [AgentName] 指令向三个组长分派任务                                │
    └───────────┬─────────────────────┬───────────────────┬───────────────┘
                │                     │                   │
     [Senior_Market_...]   [Senior_Risk_...]   [Senior_Fundamental_...]
                │                     │                   │
                ▼                     ▼                   ▼
    ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────────┐
    │ Tier 2: 市场情绪  │  │ Tier 2: 风险评估  │  │ Tier 2: 基本面分析    │
    │ 分析组组长         │  │ 分析组组长         │  │ 分析组组长            │
    │                   │  │                   │  │                      │
    │ 下属:             │  │ 下属:             │  │ 下属:                │
    │  ├ Market_Senti-  │  │  ├ Risk_Analyst  │  │  ├ Fundamental_     │
    │  │  ment_Analyst  │  │  │               │  │  │  Analyst          │
    │  └ Junior_Market_ │  │  └ Junior_Risk_  │  │  └ Junior_          │
    │    Sentiment_     │  │    Analyst       │  │    Fundamental_     │
    │    Analyst        │  │                   │  │    Analyst           │
    │                   │  │                   │  │                      │
    │ 工具: FinnHub新闻  │  │ 工具: 无           │  │ 工具: YFinance股价    │
    │       Reddit帖子   │  │                   │  │       FMP财务指标     │
    └──────────────────┘  └──────────────────┘  └──────────────────────┘
       │                        │                         │
       │ 每个 Agent 还额外注册了 │                         │
       │ retrieve_content 工具  │                         │
       │ (RAG检索PDD 20-F年报)  │                         │
       └────────────────────────┴─────────────────────────┘

任务执行流程（一次完整的对话周期）
==================================

    User Proxy                  CIO                    市场情绪组长              Worker
       │                         │                        │                      │
       │── "评估PDD投资价值" ──→ │                        │                      │
       │                         │                        │                      │
       │                         │── 分析任务，决定先让     │                      │
       │                         │   市场情绪组分析         │                      │
       │                         │                        │                      │
       │  ┌─ 检测到 "[Senior_Market_Sentiment_Analyst] ..." ──┐                 │
       │  │ 触发嵌套聊天                                       │                 │
       │  │                                                    │                 │
       │  │  order_message() 提取指令:                          │                 │
       │  │  "Analyze recent market sentiment for PDD..."       │                 │
       │  │  套入 order_template 发给组长                        │                 │
       │  │                                                    │                 │
       │  ├─────────────────────────────────────────────────────┤                 │
       │  │                                                     ▼                 │
       │  │  组长收到指令，调用 FinnHub 获取新闻，分析情绪         │                 │
       │  │  组长可能进一步分派: "[Market_Sentiment_Analyst] ..." │                 │
       │  │  触发组长→Worker的嵌套聊天                            │                 │
       │  │                                                     │                 │
       │  │  Worker 执行工具调用，返回分析结果                      │                 │
       │  │  ◄─── reflection_with_llm 提炼摘要 ───►             │                 │
       │  │                                                     │                 │
       │  │  组长汇总 Worker 结果，返回给 CIO                     │                 │
       │  ◄──────────────────────────────────────────────────────┘                 │
       │                         │                        │                      │
       │                         │◄─ 收到市场情绪分析摘要 ──│                      │
       │                         │                        │                      │
       │                         │── 检查质量，决定下一个 ──→│                      │
       │                         │   分派风险评估任务 ...     │                      │
       │                         │                        │                      │
       │                         │   ... (循环，直到所有子任务完成)                  │
       │                         │                        │                      │
       │                         │── 整合所有分析 ──→ 最终报告 + "TERMINATE"        │
       │◄── 最终投资建议 ────────│                        │                      │

嵌套聊天的层级关系
==================

  portfolio_optimization.py 中存在两层嵌套聊天，形成俄罗斯套娃式的任务分派：

  外层对话: User Proxy ↔ CIO
    │
    ├─ 嵌套聊天 (Tier 1→2): User Proxy ↔ 市场情绪组长
    │    ├─ 嵌套聊天 (Tier 2→3): User Proxy ↔ Market_Sentiment_Analyst
    │    └─ 嵌套聊天 (Tier 2→3): User Proxy ↔ Junior_Market_Sentiment_Analyst
    │
    ├─ 嵌套聊天 (Tier 1→2): User Proxy ↔ 风险评估组长
    │    ├─ 嵌套聊天 (Tier 2→3): User Proxy ↔ Risk_Analyst
    │    └─ 嵌套聊天 (Tier 2→3): User Proxy ↔ Junior_Risk_Analyst
    │
    └─ 嵌套聊天 (Tier 1→2): User Proxy ↔ 基本面分析组长
         ├─ 嵌套聊天 (Tier 2→3): User Proxy ↔ Fundamental_Analyst
         └─ 嵌套聊天 (Tier 2→3): User Proxy ↔ Junior_Fundamental_Analyst

数据流向图（配置 → Agent 实例化）
==================================

  investment_group.py                    portfolio_optimization.py
  ┌───────────────────┐                  ┌──────────────────────────────────┐
  │ group_config = {  │                  │                                  │
  │   "CIO": {...},   │─────────────────→│ cio_config = group_config["CIO"] │
  │   "groups": {     │                  │                                  │
  │     "Market...": {│                  │ for group in groups:             │
  │       "with_      │─────────────────→│   MultiAssistantWithLeader(     │
  │        leader": { │                  │     group_members, llm_config)  │
  │         "leader": │                  │   representatives.append(       │
  │          {...},   │                  │     group.representative)       │
  │         "employees│                  │                                  │
  │          ": [...] │                  │ main_group = MultiAssistant-    │
  │       }           │                  │   WithLeader(                   │
  │     },            │                  │     {"leader": cio_config,      │
  │     "Risk...":... │                  │      "agents": representatives})│
  │     "Fund...":... │                  │                                  │
  │   }               │                  │ main_group.chat(task)           │
  │ }                 │                  │                                  │
  └───────────────────┘                  └──────────────────────────────────┘

上游调用方
==========
  本文件是一个顶层入口脚本，直接由用户运行：
    python experiments/portfolio_optimization.py
  没有其他模块调用本文件。

下游依赖（本文件调用了什么）
============================
  finrobot/agents/workflow.py
    ├── MultiAssistantWithLeader  — 构建 Leader-Worker 层级群组
    └── MultiAssistant            — 本文件未使用（when with_leader=False 时使用）
  finrobot/functional/rag.py
    └── get_rag_function()        — 创建 RAG 检索工具
  finrobot/utils.py
    └── register_keys_from_json() — 加载 API 密钥到环境变量
  experiments/investment_group.py
    └── group_config              — 组织架构配置字典
"""

import autogen
from finrobot.agents.workflow import MultiAssistant, MultiAssistantWithLeader
# ↑ workflow.py 中的两个多 Agent 协作类：
#   - MultiAssistant: 群聊模式，Agent 自由对话，无 Leader
#   - MultiAssistantWithLeader: 层级模式，Leader 通过 [AgentName] 指令分派任务
#   本文件中，所有子组和顶层 CIO 组都使用 MultiAssistantWithLeader
from finrobot.functional import get_rag_function
# ↑ 来自 finrobot/functional/rag.py 的 RAG 工具工厂函数。
#   调用 get_rag_function(retrieve_config) 返回 (retrieve_content函数, rag_assistant实例)。
#   retrieve_content 被注册为每个 Agent 的 function-calling 工具，
#   内部通过 ChromaDB 向量检索从 SEC 年报中检索相关段落。
from finrobot.utils import register_keys_from_json
# ↑ 读取 config_api_keys JSON 文件，将 Finnhub/FMP/SEC 等 API 密钥注册到 os.environ，
#   使得 data_source/ 中的工具函数（如 FinnHubUtils、FMPUtils）可以读取密钥调用外部 API。
from textwrap import dedent
# ↑ 用于去除 task 字符串的公共缩进，保持代码可读性
from autogen import register_function
# ↑ AutoGen 的工具注册函数：register_function(func, caller=Agent, executor=Proxy, description=...)
#   caller: LLM 在此 Agent 的上下文中"看到"工具描述并决定调用
#   executor: 工具代码在此 UserProxyAgent 中实际执行
from investment_group import group_config
# ↑ 来自同目录的 investment_group.py，定义了三层组织架构的配置字典。
#   结构: {"CIO": {...}, "groups": {"Market Sentiment Analysts": {...}, "Risk ...": {...}, "Fundamental ...": {...}}}
#   每个 group 包含 "with_leader" 和 "without_leader" 两种配置变体。


# ══════════════════════════════════════════════════════════════════════════════
# 第一部分：LLM 配置与 API 密钥注册
# ══════════════════════════════════════════════════════════════════════════════

llm_config = {
    "config_list": autogen.config_list_from_json(
        "../OAI_CONFIG_LIST",
        # ↑ 从项目根目录的 OAI_CONFIG_LIST 文件读取 OpenAI API 配置。
        #   该文件包含 [{"model": "gpt-4", "api_key": "sk-..."}] 格式的 JSON 数组。
        #   注意相对路径 "../"：因为本文件在 experiments/ 子目录中。
        filter_dict={
            "model": ["qwen-plus"],
            # ↑ 指定使用 GPT-4 Turbo (2025年1月版本)。
            #   多 Agent 场景对 LLM 的指令遵循能力要求很高：
            #   - Leader 需要准确生成 "[AgentName] 指令" 格式
            #   - Worker 需要理解并执行复杂的多步分析任务
            #   因此使用最强的 GPT-4 模型。
        },
    ),
    "cache_seed": 42,
    # ↑ 缓存种子。相同的 seed + 相同的请求 = 相同的响应（可复现）。
    #   设为固定值 42 使得实验结果可重复，便于调试。
    "temperature": 0,
    # ↑ 温度参数。0 = 完全确定性（同一输入总是产生相同输出）。
    #   在金融分析场景中需要确定性输出，不需要创意性。
}

register_keys_from_json("../config_api_keys")
# ↑ 读取 config_api_keys 文件（JSON 格式），将所有 API 密钥注入到 os.environ。
#   这些密钥被 data_source/ 中的工具函数使用：
#     - FINNHUB_API_KEY → FinnHubUtils.get_company_news() 获取新闻
#     - FMP_API_KEY     → FMPUtils.get_financial_metrics() 获取财务数据
#     - SEC_API_KEY     → SEC 相关数据获取
#   config_api_keys 文件格式: {"FINNHUB_API_KEY": "xxx", "FMP_API_KEY": "xxx", ...}


# ══════════════════════════════════════════════════════════════════════════════
# 第二部分：创建共享的 User Proxy Agent
# ══════════════════════════════════════════════════════════════════════════════
#
# ┌──────────────────────────────────────────────────────────────────┐
# │ 关键设计决策：所有 Agent 共享同一个 UserProxyAgent                │
# │                                                                  │
# │ 在整个三层架构中，只有一个 user_proxy 实例。                       │
# │ 这意味着：                                                       │
# │   1. 所有 Agent 的工具调用都在同一个执行环境中运行                  │
# │   2. 嵌套聊天中的 Worker 也使用这个 user_proxy 执行代码            │
# │   3. user_proxy 同时是外层对话和所有嵌套聊天的 executor            │
# │                                                                  │
# │ 为什么共享？                                                     │
# │   - 工具执行结果可以被所有层级的 Agent 访问（共享上下文）            │
# │   - 避免创建多个执行环境带来的状态同步问题                         │
# │   - 嵌套聊天的 register_nested_chats 注册在 user_proxy 上         │
# └──────────────────────────────────────────────────────────────────┘

user_proxy = autogen.UserProxyAgent(
    name="User",
    # ↑ Agent 名称。在对话日志中显示为 "User"。
    #   注意这里叫 "User" 而非 "User_Proxy"（workflow.py 中默认叫 "User_Proxy"），
    #   因为 portfolio_optimization.py 手动创建了 user_proxy 而非让 workflow 自动创建。

    # human_input_mode="ALWAYS",
    human_input_mode="NEVER",
    # ↑ 全自动模式，不等待人类输入。
    #   如果设为 "ALWAYS"，每轮对话都会暂停等待人类回复，适合调试和观察。
    #   设为 "NEVER" 后，整个三层对话完全自动运行直到 CIO 输出 TERMINATE。

    is_termination_msg=lambda x: x.get("content", "")
    and "TERMINATE" in x.get("content", ""),
    # ↑ 终止条件判断函数。当任何 Agent 的消息内容中包含 "TERMINATE" 时，对话终止。
    #   注意这里用的是 "in" 而非 endswith，比 workflow.py 中的条件更宽松。
    #   这是因为 CIO 的最终报告可能在末尾有额外文本，但中间已包含 TERMINATE 标记。

    code_execution_config={
        "last_n_messages": 3,
        # ↑ 代码执行时只考虑最近 3 条消息的上下文。
        #   避免在多轮对话中代码执行器看到过时的历史消息。
        "work_dir": "quant",
        # ↑ 代码执行的工作目录。Agent 生成的 Python 脚本会在 quant/ 目录下执行，
        #   中间数据文件也保存在这里（如 FMPUtils 返回的财务数据 JSON）。
        "use_docker": False,
        # ↑ 不使用 Docker 隔离。Agent 生成的代码直接在本地运行。
        #   生产环境建议设为 True 以隔离代码执行风险。
    },
)


# ══════════════════════════════════════════════════════════════════════════════
# 第三部分：创建 RAG 检索工具
# ══════════════════════════════════════════════════════════════════════════════
#
# RAG 工具在本文件中的作用：
#   让所有 Agent（不仅是某个特定的 Analyst）都能从 PDD 的 2022 年 20-F 年报中
#   检索信息。例如：
#   - 风险评估组的 Agent 可以检索 "risk factors" 相关段落
#   - 基本面分析组可以检索 "revenue breakdown" 相关段落
#   - 市场情绪组可以检索 "management discussion" 相关段落
#
# ┌───────────────────────────────────────────────────────────────┐
# │ RAG 检索的底层机制:                                           │
# │                                                               │
# │   retrieve_content("PDD risk factors")                        │
# │     ↓                                                         │
# │   RAG_Assistant (RetrieveUserProxyAgent)                      │
# │     ↓  ChromaDB 向量检索                                      │
# │   返回最相关的 3 个文档片段 (每个 ~1000 tokens)                  │
# │     ↓                                                         │
# │   Agent 基于检索到的年报原文 + 自身角色，生成分析结论             │
# └───────────────────────────────────────────────────────────────┘

rag_func = get_rag_function(
    retrieve_config={
        "task": "qa",
        # ↑ 任务类型。"qa" 表示问答模式（通用文档检索）。
        #   另一个选项是 "code"（代码生成场景的检索），这里不适用。

        "docs_path": "https://www.sec.gov/Archives/edgar/data/1737806/000110465923049927/pdd-20221231x20f.htm",
        # ↑ 文档来源：PDD（拼多多）2022 年 20-F 年报的 SEC 官方 URL。
        #   20-F 是在美国上市的外国公司（FPI）的年度报告表格，
        #   等同于美国本土公司的 10-K。包含完整的财务报表、风险因素、MD&A 等。
        #   RAG 系统会自动下载此 HTML 文件，分块后存入 ChromaDB 向量库。

        "chunk_token_size": 1000,
        # ↑ 文档分块大小。每个 chunk 约 1000 个 token。
        #   20-F 年报通常有数十万 token，必须分块才能有效检索。
        #   1000 token 是一个平衡值：太大会引入噪音，太小会丢失上下文。

        "collection_name": "pdd2022",
        # ↑ ChromaDB 中的 collection 名称。用于标识这批文档的向量索引。
        #   如果下次用相同名称，可以复用已建好的索引（配合 get_or_create=True）。

        "get_or_create": True,
        # ↑ 如果 "pdd2022" collection 已存在，直接加载（不重建索引）。
        #   首次运行时会下载文档、分块、向量化（耗时较长），
        #   后续运行直接加载已有索引，大幅提升启动速度。
    },
)
# ↑ rag_func 是一个 retrieve_content(message, n_results=3) → str 函数。
#   它将被注册为每个 Agent 的 function-calling 工具。
#   内部持有一个 RAG_Assistant (RetrieveUserProxyAgent) 实例。


# ══════════════════════════════════════════════════════════════════════════════
# 第四部分：构建三层 Agent 组织架构（自底向上构建）
# ══════════════════════════════════════════════════════════════════════════════
#
# ┌─────────────────────────────────────────────────────────────────────────┐
# │ 构建策略：自底向上（Bottom-Up）                                         │
# │                                                                         │
# │ portfolio_optimization.py 不是一次性创建所有 Agent，而是分层构建：        │
# │                                                                         │
# │  Step 1: 遍历 3 个分析组，为每个组创建一个 MultiAssistantWithLeader      │
# │          → 得到 3 个独立的子群组（Tier 2 组长 + Tier 3 Workers）          │
# │          → 收集每个子群组的 representative（即组长 Agent）                │
# │                                                                         │
# │  Step 2: 用 CIO 配置 + 3 个组长 representative 创建顶层                   │
# │          MultiAssistantWithLeader                                       │
# │          → 得到完整的三层架构（Tier 1 CIO → Tier 2 组长 → Tier 3 Worker） │
# │                                                                         │
# │  这种设计的精妙之处：                                                    │
# │    子群组的 representative 是一个普通的 FinRobot Agent 实例               │
# │    当它被传入顶层 MultiAssistantWithLeader 的 agents 列表时               │
# │    workflow.py 的 _init_single_agent() 会检测到它是 ConversableAgent     │
# │    直接复用，不会重新创建。                                               │
# │    但它已经在子群组中注册了嵌套聊天（组长→Worker），                       │
# │    所以当 CIO 给它分派任务时，它能自动向下级分派。                         │
# └─────────────────────────────────────────────────────────────────────────┘

with_leader_config = {
    "Market Sentiment Analysts": True,
    "Risk Assessment Analysts": True,
    "Fundamental Analysts": True,
}
# ↑ 控制每个分析组是否启用 Leader。
#   True  → 使用 MultiAssistantWithLeader（有组长，组长可向下分派任务）
#   False → 使用 MultiAssistant（群聊模式，Agent 之间自由对话，无组长）
#   本文件中三个组全部启用 Leader，形成完整的层级结构。
#   如果某个组设为 False，该组变成扁平的群聊模式，CIO 直接与组内所有 Worker 对话。

representatives = []
# ↑ 收集每个子群组的"代表 Agent"（即组长）。
#   这些代表稍后会被作为 CIO 的下属，组成顶层 MultiAssistantWithLeader。
#   在 workflow.py 中，MultiAssistantWithLeader._get_representative() 返回
#   leader Agent 作为 representative。所以 representatives 里存的是 3 个组长 Agent。

for group_name, single_group_config in group_config["groups"].items():
    # ↑ 遍历 investment_group.py 中定义的 3 个分析组：
    #   "Market Sentiment Analysts"  — 市场情绪分析组
    #   "Risk Assessment Analysts"   — 风险评估分析组
    #   "Fundamental Analysts"       — 基本面分析组
    #
    # single_group_config 结构（以 Market Sentiment 为例）：
    #   {
    #     "responsibilities": ["Track and interpret...", ...],
    #     "with_leader": {
    #       "leader": {"title": "Senior Market Sentiment Analyst", "responsibilities": [...]},
    #       "employees": [
    #         {"title": "Market Sentiment Analyst", "responsibilities": [...], "toolkits": [...]},
    #         {"title": "Junior Market Sentiment Analyst", "responsibilities": [...], "toolkits": [...]}
    #       ]
    #     },
    #     "without_leader": {
    #       "employees": [...]
    #     }
    #   }

    with_leader = with_leader_config.get(group_name)
    # ↑ 根据 with_leader_config 决定当前组是否启用组长

    if with_leader:
        # ═══════════════════════════════════════════════════════════════
        # 分支 A：启用组长 → 使用 MultiAssistantWithLeader
        # ═══════════════════════════════════════════════════════════════
        group_members = single_group_config["with_leader"]
        # ↑ 获取有组长模式的配置，包含 "leader" 和 "employees" 两个 key

        group_members["agents"] = group_members.pop("employees")
        # ↑ 【关键重命名】将 "employees" key 重命名为 "agents"。
        #   原因：investment_group.py 中使用 "employees" 是为了配置文件的语义清晰，
        #   但 MultiAssistantWithLeader.__init__ 期望的配置格式是
        #   {"leader": {...}, "agents": [...]}，其中 "agents" 是下属列表。
        #   pop("employees") 同时完成了重命名和删除旧 key 两个操作。
        #
        #   重命名后的 group_members 结构：
        #   {
        #     "leader": {"title": "Senior Market Sentiment Analyst", ...},
        #     "agents": [
        #       {"title": "Market Sentiment Analyst", "toolkits": [...]},
        #       {"title": "Junior Market Sentiment Analyst", "toolkits": [...]}
        #     ]
        #   }

        group = MultiAssistantWithLeader(
            group_members, llm_config=llm_config, user_proxy=user_proxy
        )
        # ↑ 创建子群组。MultiAssistantWithLeader.__init__ 内部会：
        #   1. 调用 _init_agents() 为 "agents" 中的每个配置创建 FinRobot Agent
        #   2. 调用 _get_representative()：
        #      a. 遍历所有 worker agent，构建 group_desc（汇总所有 worker 信息）
        #      b. 将 group_desc 注入 leader_config
        #      c. 创建 Leader FinRobot Agent（system_message 包含 role_prompt + leader_prompt）
        #      d. 在 user_proxy 上为每个 worker 注册嵌套聊天 trigger
        #      e. 返回 leader Agent 作为 group.representative
        #
        #   传入已有的 user_proxy 而非让 workflow 创建新的，确保所有层级共享同一个执行器。

    else:
        # ═══════════════════════════════════════════════════════════════
        # 分支 B：不启用组长 → 使用 MultiAssistant（群聊模式）
        # ═══════════════════════════════════════════════════════════════
        group_members = single_group_config["without_leader"]
        group_members["agents"] = group_members.pop("employees")
        # ↑ 同样的重命名操作

        group = MultiAssistant(
            group_members, llm_config=llm_config, user_proxy=user_proxy
        )
        # ↑ MultiAssistant 创建的是扁平的 GroupChat，没有 Leader。
        #   Agent 之间按 round_robin 轮流发言，自由讨论。
        #   group.representative 是 GroupChatManager（非某个具体的 Agent）。
        #   CIO 会直接与 GroupChatManager 对话，消息被路由到组内各 Agent。

    # ── 为子群组中的每个 Agent 注册 RAG 检索工具 ──
    # 这是一个重要的步骤：不仅组长需要 RAG，每个 Worker 也需要。
    # 因为实际的数据检索和分析工作是由 Worker 执行的，
    # Worker 需要从 PDD 年报中检索具体段落来支撑分析结论。
    for agent in group.agents:
        # ↑ group.agents 包含该组内所有 Worker Agent（不含 Leader）。
        #   Leader 在 _get_representative() 中单独创建，不在 group.agents 列表中。
        #   例如市场情绪组的 group.agents = [Market_Sentiment_Analyst, Junior_Market_Sentiment_Analyst]

        register_function(
            rag_func,
            # ↑ retrieve_content 函数（所有 Agent 共享同一个函数引用，
            #   但每个 Agent 独立调用，每次调用触发独立的 ChromaDB 检索）

            caller=agent,
            # ↑ 将工具描述注册到此 Agent 的 function schema 中。
            #   当 LLM 在此 Agent 的上下文中生成对话时，它"看到"这个工具，
            #   可以根据需要决定是否调用。

            executor=group.user_proxy,
            # ↑ 工具代码在此 UserProxyAgent 中执行。
            #   这里 group.user_proxy 就是前面创建的共享 user_proxy。
            #   caller 和 executor 分离是 AutoGen 的核心设计：
            #   - caller（Agent）：LLM 决定"要调用什么工具、传什么参数"
            #   - executor（Proxy）：实际执行工具函数，返回结果

            description="retrieve content from PDD's 2022 20-F Sec Filing for QA",
            # ↑ 工具描述。LLM 通过此描述判断何时调用该工具。
            #   明确说明了数据来源（PDD 2022 年 20-F 年报）和用途（QA 问答），
            #   帮助 Agent 在需要年报数据时主动调用此工具。
        )

    representatives.append(group.representative)
    # ↑ 收集该子群组的 representative。
    #   对于 MultiAssistantWithLeader：representative = Leader Agent (FinRobot 实例)
    #   对于 MultiAssistant：representative = GroupChatManager 实例
    #   本文件中三个组都启用了 Leader，所以 representatives 里是 3 个组长 Agent：
    #     [Senior_Market_Sentiment_Analyst, Senior_Risk_Analyst, Senior_Fundamental_Analyst]


# ══════════════════════════════════════════════════════════════════════════════
# 第五部分：构建顶层 CIO 群组
# ══════════════════════════════════════════════════════════════════════════════
#
# ┌───────────────────────────────────────────────────────────────────────┐
# │ 顶层群组的特殊之处：                                                   │
# │                                                                       │
# │ agents 列表中的元素不是配置字典，而是已创建的 Agent 实例（representatives）│
# │                                                                       │
# │ workflow.py 的 _init_single_agent() 方法检测到 ConversableAgent 类型后 │
# │ 直接复用已有实例，不会重新创建。                                        │
# │                                                                       │
# │ 但这些已有实例的 description 属性仍然会被读取，用于构建 group_desc。     │
# │ group_desc 被注入 CIO 的 system_message，让 CIO "看到"三个组长是谁。    │
# │                                                                       │
# │ _get_representative() 中处理 ConversableAgent 的分支（第 972-976 行）： │
# │   if isinstance(c, ConversableAgent):                                 │
# │       group_desc += c.description + "\n\n"  # 直接取已有 description  │
# └───────────────────────────────────────────────────────────────────────┘

cio_config = group_config["CIO"]
# ↑ 获取 CIO 的配置：
#   {
#     "title": "Chief Investment Officer",
#     "responsibilities": [
#       "Oversee the entire investment analysis process.",
#       "Integrate insights from various groups.",
#       "Make the final decision on portfolio composition and adjustments."
#     ]
#   }
#   注意 CIO 没有 "toolkits" 字段——CIO 不直接调用工具，它只负责协调和决策。
#   所有数据采集和分析工作由下级 Agent 完成。

main_group_config = {"leader": cio_config, "agents": representatives}
# ↑ 构建顶层 MultiAssistantWithLeader 的配置：
#   {
#     "leader": {"title": "Chief Investment Officer", "responsibilities": [...]},
#     "agents": [
#       Senior_Market_Sentiment_Analyst (FinRobot 实例),
#       Senior_Risk_Analyst (FinRobot 实例),
#       Senior_Fundamental_Analyst (FinRobot 实例)
#     ]
#   }
#   agents 列表中存的是已创建的 Agent 对象而非配置字典，
#   这是 portfolio_optimization.py 实现"层级嵌套"的关键手法。

main_group = MultiAssistantWithLeader(
    main_group_config, llm_config=llm_config, user_proxy=user_proxy
)
# ↑ 创建顶层 CIO 群组。_get_representative() 内部流程：
#   1. 检测到 agents 是 ConversableAgent 实例 → 直接复用
#   2. 读取每个 agent 的 description 属性，拼接 group_desc
#      （group_desc 包含三个组长的名称和职责信息）
#   3. 将 group_desc 注入 cio_config → FinRobot(cio_config) 创建 CIO Agent
#      CIO 的 system_message 最终包含：
#        - role_prompt: "As a Chief Investment Officer, your responsibilities are..."
#        - leader_prompt: "You are the leader of: Senior_Market_Sentiment_Analyst..."
#   4. 在 user_proxy 上为每个组长注册嵌套聊天 trigger
#      trigger 条件: sender.name == "Chief_Investment_Officer" && "[Senior_Market_Sentiment_Analyst]" in message
#   5. 返回 CIO Agent 作为 main_group.representative
#
#   此时整个三层架构已完全构建完毕，嵌套聊天链路已就绪。


# ══════════════════════════════════════════════════════════════════════════════
# 第六部分：定义投资分析任务
# ══════════════════════════════════════════════════════════════════════════════
#
# ┌─────────────────────────────────────────────────────────────────────────┐
# │ 任务设计的精妙之处：                                                    │
# │                                                                         │
# │ 1. 任务直接发给 CIO，CIO 自行决定如何分解和分派                          │
# │    task 中虽然写了 "[Coordinate with Market Sentiment Analysts]"，       │
# │    但这只是提示性文本。实际的任务分派由 CIO 的 LLM 自主决定。              │
# │    CIO 看到 leader_prompt 中的三个组长名称后，会主动生成                  │
# │    "[Senior_Market_Sentiment_Analyst] ..." 格式的指令来分派任务。         │
# │                                                                         │
# │ 2. 设定了明确的时间锚点（2023-04-26）                                    │
# │    防止 Agent 使用未来数据（LLM 的训练数据可能包含 2023-04-26 之后的信息） │
# │    这是金融分析中的"防止前瞻偏差"（look-ahead bias）设计                  │
# │                                                                         │
# │ 3. 最终交付物要求具体且可量化                                            │
# │    - 6个月目标价格（定量）                                               │
# │    - 投资建议（定性）                                                    │
# │    - 综合三个分析组的结论（集成）                                         │
# └─────────────────────────────────────────────────────────────────────────┘

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
)
# ↑ dedent() 去除公共缩进，使 task 字符串没有多余的前导空格。
#
# 这段 task 文本是发给 CIO 的初始消息。CIO 的 LLM 会：
#   1. 阅读 task 中的任务描述
#   2. 对照自己 system_message 中的三个组长信息
#   3. 按顺序生成类似以下的指令来分派任务：
#      "I'll start by coordinating with the Market Sentiment team.
#       [Senior_Market_Sentiment_Analyst] Please analyze recent market sentiment
#       for PDD based on social media and news. Provide a sentiment score."
#   4. order_trigger 检测到 "[Senior_Market_Sentiment_Analyst]" → 触发嵌套聊天
#   5. 市场情绪组长收到任务，可能进一步分派给自己的 Worker
#   6. Worker 调用 FinnHub/Reddit 工具获取数据，RAG 检索年报，生成分析
#   7. 结果通过 reflection_with_llm 提炼后返回给 CIO
#   8. CIO 检查结果，继续分派下一个组的任务
#   9. 所有组完成后，CIO 整合结论，输出最终报告 + TERMINATE


# ══════════════════════════════════════════════════════════════════════════════
# 附：被注释掉的简化版任务（用于理解 task 的演进过程）
# ══════════════════════════════════════════════════════════════════════════════

# task = dedent(
#     """
#     As the Chief Investment Officer, your task is to evaluate the potential investment in Company ABC based on the provided data. You will need to coordinate with the Market Sentiment Analysts, Risk Assessment Analysts, and Fundamental Analysts to gather and analyze the relevant information. Your final deliverable should include a comprehensive evaluation and a recommendation on whether to invest in Company ABC.

#     Specific Instructions:

#     Coordinate with Market Sentiment Analysts:

#     Task: Calculate the sentiment score based on the provided market sentiment data.
#     Data: Positive mentions (80), Negative mentions (20)
#     Formula: Sentiment Score = (Positive Mentions - Negative Mentions) / Total Mentions
#     Expected Output: Sentiment Score (percentage)

#     Coordinate with Risk Assessment Analysts:

#     Task: Calculate the risk score using the provided financial ratios.
#     Data:
#     Debt-to-Equity Ratio: 1.5
#     Current Ratio: 2.0
#     Return on Equity (ROE): 0.1 (10%)
#     Weights: Debt-to-Equity (0.5), Current Ratio (0.3), ROE (0.2)
#     Formula: Risk Score = 0.5 * Debt-to-Equity + 0.3 * (1 / Current Ratio) - 0.2 * ROE
#     Expected Output: Risk Score

#     Coordinate with Fundamental Analysts:

#     Task: Calculate the Profit Margin and Return on Assets (ROA) based on the provided financial data.
#     Data:
#     Revenue: $1,000,000
#     Net Income: $100,000
#     Total Assets: $500,000
#     Formulas:
#     Profit Margin = (Net Income / Revenue) * 100
#     ROA = (Net Income / Total Assets) * 100
#     Expected Outputs: Profit Margin (percentage) and ROA (percentage)

#     Final Deliverable:
#     Integrate Findings: Compile the insights from all three groups to get a holistic view of Company ABC's potential.
#     Evaluation and Recommendation: Based on the integrated analysis, provide a recommendation on whether to invest in Company ABC, including the rationale behind your decision.
# """
# )
# ↑ 这是一个简化的测试版任务，数据全部内联在 task 文本中（不需要调用外部 API）。
#   与上方真实任务的区别：
#     - 使用虚构的 "Company ABC" 而非真实的 PDD
#     - 所有数据直接给出（不需要 Agent 调用工具获取）
#     - 提供了明确的计算公式（降低了 LLM 的决策难度）
#     - 没有 RAG 检索需求
#   这个简化版适合在没有 API 密钥的情况下测试多 Agent 编排是否正常工作。


# ══════════════════════════════════════════════════════════════════════════════
# 第七部分：启动对话
# ══════════════════════════════════════════════════════════════════════════════
#
# ┌───────────────────────────────────────────────────────────────────────┐
# │ 启动入口：main_group.chat(task, use_cache=True)                       │
# │                                                                       │
# │ 调用链：                                                               │
# │   chat() → Cache.disk() 上下文 → user_proxy.initiate_chat(            │
# │     representative=CIO,                                               │
# │     message=task,                                                     │
# │     cache=disk_cache                                                  │
# │   )                                                                   │
# │                                                                       │
# │ 整个对话的完整生命周期：                                               │
# │   1. user_proxy 发送 task 给 CIO                                      │
# │   2. CIO (LLM) 分析任务，生成第一条回复（包含 [组长名] 指令）            │
# │   3. order_trigger 检测到指令 → 触发嵌套聊天                           │
# │   4. 组长收到任务 → 可能进一步分派给 Worker → Worker 调用工具          │
# │   5. Worker 结果 → 组长汇总 → reflection_with_llm 提炼 → 返回 CIO     │
# │   6. CIO 检查 → 分派下一个组 → 重复 2-5                               │
# │   7. 所有组完成 → CIO 整合分析 → 生成最终报告 + "TERMINATE"            │
# │   8. user_proxy 检测到 TERMINATE → 对话结束                            │
# │   9. chat() 调用 main_group.reset() 清除所有 Agent 的对话历史          │
# │                                                                       │
# │ use_cache=True 的作用：                                                │
# │   启用 AutoGen 的磁盘缓存。相同的 LLM 请求（相同的 prompt + model）    │
# │   会直接从缓存返回结果，避免重复 API 调用。                             │
# │   对于调试和迭代非常有用——修改某个 Agent 的配置后，未修改的 Agent       │
# │   仍然走缓存，大幅减少等待时间和 API 费用。                             │
# └───────────────────────────────────────────────────────────────────────┘

main_group.chat(message=task, use_cache=True)
