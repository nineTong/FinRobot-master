"""
RAG 工具工厂模块 (RAG Tool Factory Module)
============================================

┌─────────────────────────────────────────────────────────────────────┐
│                         模块定位与职责                                │
│                                                                     │
│  本模块是 FinRobot 的 RAG（检索增强生成）工具工厂。                      │
│  它不定义 Agent、不管理状态、不做路由 —— 它只做一件事：                   │
│    → 生成一个可注册为 AutoGen function-calling 工具的 retrieve_content  │
│      函数，让 Agent 在对话中能够从外部知识库检索文档内容。                 │
│                                                                     │
│  在整个架构中的位置：                                                  │
│    data_source/ (数据) → ragquery.py (构建向量库) → rag.py (工具工厂)    │
│      → toolkits.py (注册工具) → workflow.py (Agent 使用工具)            │
└─────────────────────────────────────────────────────────────────────┘

调用链路
========

    consumer (上游调用方):
    ├── finrobot/agents/workflow.py::SingleAssistantRAG.__init__()
    │       ↓ 调用
    │   get_rag_function(retrieve_config, rag_description)
    │       返回 → (retrieve_content 函数, RetrieveUserProxyAgent 实例)
    │
    └── experiments/portfolio_optimization.py
            ↓ 调用 (同上)

    rag.py 内部执行流程:
    ┌────────────────────────────────────────────────────────────┐
    │  get_rag_function(retrieve_config, description)            │
    │                                                            │
    │  1. 定义 termination_msg() — 判断对话终止条件               │
    │     ┌─────────────────────────────────────────┐            │
    │     │ 检查消息是否为 dict 且以 "TERMINATE" 结尾  │            │
    │     └─────────────────────────────────────────┘            │
    │                                                            │
    │  2. 设置默认 RAG 提示词模板 (PROMPT_RAG_FUNC)               │
    │     ┌─────────────────────────────────────────┐            │
    │     │ "Below is the context retrieved from...  │            │
    │     │  Your current query is: {input_question}  │            │
    │     │  Retrieved context is: {input_context}"   │            │
    │     └─────────────────────────────────────────┘            │
    │                                                            │
    │  3. 创建 RetrieveUserProxyAgent (AutoGen 内置 RAG Agent)   │
    │     ┌─────────────────────────────────────────┐            │
    │     │ name="RAG_Assistant"                    │            │
    │     │ human_input_mode="NEVER" (全自动)        │            │
    │     │ max_consecutive_auto_reply=3 (防死循环)  │            │
    │     │ code_execution_config=False (不执行代码)  │            │
    │     │ retrieve_config=用户传入的检索配置        │            │
    │     └─────────────────────────────────────────┘            │
    │                                                            │
    │  4. 闭包创建 retrieve_content() 函数                       │
    │     ┌─────────────────────────────────────────┐            │
    │     │ 参数: message (检索查询), n_results (结果数) │         │
    │     │ 逻辑:                                     │            │
    │     │   a. 设置 n_results                       │            │
    │     │   b. _check_update_context() 判断是否需要   │            │
    │     │      更新检索上下文                        │            │
    │     │   c. 若需更新 → _generate_retrieve_user_reply()│        │
    │     │      若不需要 → message_generator()        │            │
    │     │   d. 返回检索结果                          │            │
    │     └─────────────────────────────────────────┘            │
    │                                                            │
    │  5. 设置函数描述 (__doc__)                                  │
    │                                                            │
    │  6. 返回 (retrieve_content, rag_assistant)                 │
    └────────────────────────────────────────────────────────────┘

    retrieve_content 被注册为 Agent 工具后的调用流程:
    ┌────────────────────────────────────────────────────────────┐
    │  外层 Agent 对话                                            │
    │     │  LLM 决定调用 retrieve_content                       │
    │     ↓                                                      │
    │  UserProxyAgent 执行 retrieve_content(message, n_results)  │
    │     │                                                      │
    │     ↓  内部调用                                             │
    │  rag_assistant (RetrieveUserProxyAgent)                    │
    │     │  向量检索 (ChromaDB)                                  │
    │     ↓                                                      │
    │  返回检索到的文档片段 (str)                                  │
    │     │                                                      │
    │     ↓  注入回外层对话                                       │
    │  LLM 基于检索结果 + 原始问题，生成最终答案                    │
    └────────────────────────────────────────────────────────────┘

下游依赖 (本模块调用了什么):
  本模块只依赖一个外部库：
    autogen.agentchat.contrib.retrieve_user_proxy_agent.RetrieveUserProxyAgent
    — AutoGen 框架内置的 RAG 代理，内部封装了 ChromaDB 向量数据库操作。

  本模块不直接调用 finrobot 中的其他模块（注意 import 在函数内部，延迟加载）。

上游调用方 (谁调用了本模块):
  - finrobot/agents/workflow.py::SingleAssistantRAG.__init__()
    在初始化带 RAG 能力的 Agent 时，用 get_rag_function() 获取检索工具
  - finrobot/functional/__init__.py  统一导出 get_rag_function
  - experiments/portfolio_optimization.py  实验中的投资组合优化也使用 RAG
"""

from typing import Annotated


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                    RAG 提示词模板 (Prompt Template)                           ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

PROMPT_RAG_FUNC = """Below is the context retrieved from the required file based on your query.
If you can't answer the question with or without the current context, you should try using a more refined search query according to your requirements, or ask for more contexts.

Your current query is: {input_question}

Retrieved context is: {input_context}
"""
# ↑ 这是注入到 RetrieveUserProxyAgent 中的召回提示词模板。
#   当 rag_assistant 完成检索后，检索到的文档内容会填充到 {input_context}，
#   用户的原始查询填充到 {input_question}，然后作为新的上下文发给 LLM。
#   模板引导 LLM：优先基于检索结果回答；如果检索结果不足，LLM 应尝试
#   重新组织查询或要求更多上下文，而不是编造答案（对抗幻觉的重要设计）。


def get_rag_function(retrieve_config, description=""):
    """
    创建并返回一个 RAG 检索函数，供 Agent 在对话中调用。

    这是本模块唯一的公开函数，也是整个 RAG 工具系统的入口。
    调用方通过此函数获得一个可注册为 AutoGen function-calling 工具的
    retrieve_content 函数，以及底层的 RetrieveUserProxyAgent 实例。

    返回值的使用方式（在 workflow.py 的 SingleAssistantRAG 中）:
        rag_func, rag_assistant = get_rag_function(retrieve_config, rag_description)
        register_function(
            rag_func,
            caller=self.assistant,   # ← LLM 看到工具描述，决定何时调用
            executor=self.user_proxy, # ← 工具代码在此执行
            description=rag_description,
        )

    Args:
        retrieve_config: dict，RAG 检索配置字典。其 key-value 直接传给
            RetrieveUserProxyAgent 的 retrieve_config 参数。典型配置包括：

            - "task": str (默认 "default")
                任务类型，影响 RetrieveUserProxyAgent 的内部检索策略。
                "default" 用于通用 QA，"code" 用于代码生成场景。

            - "docs_path": str 或 List[str]
                文档路径列表，指向要检索的文档（txt/md/pdf）。
                可以是文件路径或目录路径。支持以下格式：
                  - 本地文件: "path/to/doc.txt"
                  - 目录: "path/to/docs_dir/"
                  - URL: "https://example.com/report.pdf"

            - "vector_db": str (可选，默认 "chromadb")
                向量数据库类型。目前仅支持 ChromaDB。

            - "collection_name": str (可选)
                ChromaDB 中的 collection 名称。

            - "chunk_token_size": int (可选，默认根据模型自动设置)
                文档分块大小（token 数）。

            - "customized_prompt": str (可选)
                自定义 RAG 提示词。若不提供，则使用本模块的 PROMPT_RAG_FUNC。

            - "get_or_create": bool (可选)
                是否复用已有的向量数据库。True 表示如果已存在则直接加载。

            - "overwrite": bool (可选)
                是否覆盖已有的向量数据库。

        description: str (默认 "")
            为 retrieve_content 工具设置的自定义描述文本。
            LLM 通过此描述理解工具的用途，决定何时调用。
            如果不提供，函数会自动生成默认描述（包含可用文档列表）。

    Returns:
        tuple: (retrieve_content: Callable, rag_assistant: RetrieveUserProxyAgent)

        - retrieve_content: 可注册为 Agent 工具的检索函数。
            签名: retrieve_content(message: str, n_results: int = 3) -> str
            调用时，内部触发 RetrieveUserProxyAgent 的检索流程，
            返回检索到的文档内容（字符串）。

        - rag_assistant: AutoGen 的 RetrieveUserProxyAgent 实例。
            返回它主要是为了 debug 和状态管理：
              - SingleAssistantRAG.reset() 需要调用 rag_assistant.reset()
              - 调试时可以查看 rag_assistant 的内部状态（如检索历史）
    """
    # ── 延迟导入：将 AutoGen 的 import 放在函数内部 ──
    # 这样做的好处：
    #   1. 只有在真正需要 RAG 功能时才加载 AutoGen 的 contrib 模块
    #   2. 如果用户不使用 RAG，不需要安装 AutoGen 的额外依赖（如 chromadb）
    #   3. 避免模块导入时的循环依赖问题
    from autogen.agentchat.contrib.retrieve_user_proxy_agent import (
        RetrieveUserProxyAgent,
    )

    # ── 定义终止条件判断函数 ──
    # RetrieveUserProxyAgent 内部的对话循环需要知道何时停止。
    # 此函数检查消息内容是否以 "TERMINATE" 结尾（忽略大小写和空白）。
    # AutoGen 会在每轮对话后调用 is_termination_msg 来检查是否应该结束。
    def termination_msg(x):
        return (
            isinstance(x, dict)
            and "TERMINATE" == str(x.get("content", ""))[-9:].upper()
        )
        # ↑ 检查逻辑：
        #   1. x 必须是 dict 类型（AutoGen 的消息格式）
        #   2. 取 content 字段的字符串值
        #   3. 取最后 9 个字符 → 转大写 → 与 "TERMINATE" 比较
        #   取 [-9:] 而非完整比较的原因：容错，消息末尾可能有多余空白或标点

    # ── 设置默认提示词 ──
    # 如果调用方没有提供自定义的 RAG 提示词，就使用本模块的 PROMPT_RAG_FUNC。
    # 这个提示词会被 RetrieveUserProxyAgent 用于构造检索后的 LLM 上下文。
    if "customized_prompt" not in retrieve_config:
        retrieve_config["customized_prompt"] = PROMPT_RAG_FUNC

    # ── 创建 RAG 代理 ──
    # RetrieveUserProxyAgent 是 AutoGen 内置的专门用于 RAG 检索的 UserProxyAgent 子类。
    # 它与普通 UserProxyAgent 的区别在于：
    #   1. 内置了文档加载和向量检索能力（基于 ChromaDB）
    #   2. 不需要 LLM 配置（它不调用 LLM，只做检索）
    #   3. 有专门的上下文更新逻辑（_check_update_context, _generate_retrieve_user_reply）
    #
    # 参数说明：
    #   name: 代理名称，用于在对话日志中标识
    #   is_termination_msg: 终止条件判断函数（上面定义的）
    #   human_input_mode="NEVER": 全自动模式，不等待人类输入
    #     — 因为 RAG 检索是确定性的，不需要人类介入
    #   max_consecutive_auto_reply=3: 最多连续自动回复 3 次
    #     — 防止检索陷入死循环（如反复重新查询却不返回结果）
    #   retrieve_config: 检索配置（文档路径、向量库设置等）
    #   code_execution_config=False: 禁止代码执行
    #     — RAG 代理只做文档检索，绝不执行代码（安全设计）
    #   description: 代理的自我描述（用于 GroupChat 中的自我介绍）
    rag_assitant = RetrieveUserProxyAgent(
        name="RAG_Assistant",
        is_termination_msg=termination_msg,
        human_input_mode="NEVER",
        default_auto_reply="Reply `TERMINATE` if the task is done.",
        max_consecutive_auto_reply=3,
        retrieve_config=retrieve_config,
        code_execution_config=False,  # we don't want to execute code in this case.
        description="Assistant who has extra content retrieval power for solving difficult problems.",
    )

    # ── 创建检索函数（闭包）──
    # 这是整个模块的核心产物。retrieve_content 是一个闭包函数，捕获了
    # 外层作用域的 rag_assitant 变量。当它被注册为 Agent 的 function-calling
    # 工具后，LLM 在对话中可以"调用"它来检索文档内容。
    #
    # 函数的执行者是 UserProxyAgent，但函数内部操作的是 rag_assitant
    # （一个独立的 RetrieveUserProxyAgent 实例），这是关键的设计：
    #   - 外层 Agent 对话由 UserProxyAgent 执行工具
    #   - 但实际的 RAG 检索在 rag_assitant 内部完成
    #   - 两层对话分离，RAG 检索是一个"子对话"
    #
    # 参数使用了 Annotated 类型提示，AutoGen 会从中提取参数描述
    # 作为 function calling 的 schema，LLM 据此理解参数含义。
    def retrieve_content(
        message: Annotated[
            str,
            # ↑ 消息类型：必须是字符串。
            #   这是 LLM 传给工具的检索查询，应该是一个精炼后的搜索短语，
            #   而不是原始的用户消息。例如用户说"帮我查一下 NVIDIA 最近的
            #   财务风险"，LLM 应该将其精炼为 "risk factors of NVIDIA in Q4"
            #   再传给此参数。
            "Refined query message which keeps the original meaning and can be used to retrieve content for code generation or question answering from the provided files."
            "For example, 'YoY comparisons of profit margin', 'risk factors of NVIDIA in Q4', 'retrieve historical stock price data using YFinance'",
        ],
        n_results: Annotated[int, "Number of results to retrieve, default to 3"] = 3,
        # ↑ 检索结果数量。默认 3，意味着返回与查询最相似的 3 个文档片段。
        #   LLM 可以根据需要调整：答案藏在细节中时可以增加，只需概览时可以减少。
        #   注意：n_results 越大，返回的文本越长，可能超出 LLM 的上下文窗口。
    ) -> str:
        # ── 步骤 1：设定本次检索的结果数量 ──
        # 在每次调用时动态设置，因为同一个 Agent 对话中可能需要不同数量的结果。
        rag_assitant.n_results = n_results  # Set the number of results to be retrieved.

        # ── 步骤 2：判断是否需要更新检索上下文 ──
        # _check_update_context(message) 是 RetrieveUserProxyAgent 的内部方法，
        # 它比较新消息与缓存的消息，判断是否需要重新检索。返回值是两个布尔值：
        #
        #   update_context_case1: 新查询与上次查询不同 → 需要重新检索
        #     — 例如上次查 "Apple revenue"，这次查 "Apple profit margin"，
        #       查询变了，需要检索新内容
        #
        #   update_context_case2: 上次检索的结果不足以回答新问题 → 需要更新
        #     — 例如上次只查了年报，这次需要查季报，虽然query可能相似但需要更新
        #
        # 这两个 case 共同保护了缓存的有效性：
        #   - 查询内容变了？→ 重新检索（case1）
        #   - 检索覆盖不够？→ 重新检索（case2）
        #   - 都没变？→ 直接用缓存（走 else 分支，避免重复计算）
        #
        # 这是性能优化的关键设计——如果同一个问题被多次询问，
        # 不需要每次都做昂贵的向量检索操作。
        update_context_case1, update_context_case2 = rag_assitant._check_update_context(
            message
        )
        if (
            update_context_case1 or update_context_case2
        ) and rag_assitant.update_context:
            # ── 分支 A：需要更新上下文 → 执行完整检索流程 ──
            # 设置 rag_assitant.problem：
            #   如果是第一次检索（没有 problem 属性），保存当前消息
            #   如果已有 problem（之前检索过），保留原始问题不变
            # 这个设计确保多次相关的检索始终围绕同一个原始问题展开
            rag_assitant.problem = (
                message
                if not hasattr(rag_assitant, "problem")
                else rag_assitant.problem
            )
            # _generate_retrieve_user_reply(message) 的工作流程：
            #   1. 用 message 作为查询 → ChromaDB 向量检索
            #   2. 将检索结果填充到 customized_prompt 模板
            #   3. 返回 (是否终止, 检索结果消息)
            # 返回的 ret_msg 是一个包含检索上下文和 LLM 回复的字典。
            _, ret_msg = rag_assitant._generate_retrieve_user_reply(message)
        else:
            # ── 分支 B：不需要更新上下文 → 使用 message_generator ──
            # message_generator 是 RetrieveUserProxyAgent 的快捷方法，
            # 它跳过上下文更新检查，直接用给定的上下文生成回复。
            # 参数：
            #   - self (rag_assitant): 代理自身
            #   - None: 不使用嵌套对话（因为是同行调用，不是嵌套）
            #   - _context: {"problem": message, "n_results": n_results}
            #
            # 为什么 context 中又包含了 message 和 n_results？
            #   因为这是"快捷路径"，不经过 _check_update_context 的完整流程，
            #   所以需要显式传入这些参数让 message_generator 知道该做什么。
            _context = {"problem": message, "n_results": n_results}
            ret_msg = rag_assitant.message_generator(rag_assitant, None, _context)

        # ── 步骤 3：返回检索结果 ──
        # 如果检索成功（ret_msg 存在且有内容），返回检索到的文本；
        # 如果检索失败（ret_msg 为 None 或空），回退到原始 message。
        # 这个回退机制确保即使 RAG 检索完全失败，Agent 也不会收到空字符串，
        # LLM 至少可以看到原始查询，然后判断是需要重新查询还是用已有知识回答。
        return ret_msg if ret_msg else message

    # ── 设置函数的 docstring ──
    # 在 AutoGen 中，function-calling 工具的 description 来自函数的 __doc__ 属性。
    # LLM 通过阅读这个描述来决定何时调用该工具。
    #
    # 如果调用方提供了自定义 description，使用它；
    # 否则自动生成描述，并附上可用文档列表，帮助 LLM 判断
    # "这个知识库里有我需要的信息吗？"
    if description:
        retrieve_content.__doc__ = description
    else:
        retrieve_content.__doc__ = "retrieve content from documents to assist question answering or code generation."
        docs = retrieve_config.get("docs_path", [])
        if docs:
            docs_str = "\n".join(docs if isinstance(docs, list) else [docs])
            retrieve_content.__doc__ += f"Availale Documents:\n{docs_str}"

    # ── 返回检索函数和 RAG 代理实例 ──
    # 调用方需要 rag_assitant 的原因：
    #   1. session 管理：在 reset() 中需要 rag_assitant.reset() 清理状态
    #   2. debug 使用：可以检查 rag_assitant 的检索历史和内部状态
    return retrieve_content, rag_assitant  # for debug use
