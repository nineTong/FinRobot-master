"""
================================================================================
multi_factor_agents.py — 基于多因子分析的量化投资策略 Agent 系统
================================================================================

【文件定位】
  本文件是 experiments/ 下的独立实验脚本，实现了一个"领导-下属 (Leader-Worker)"
  多 Agent 协作模式的量化投资策略原型。它直接使用原始 AutoGen API（而非
  finrobot.agents.workflow 的封装类）手动构建 Agent 编排逻辑。

【与 workflow.py 的关系】
  ┌──────────────────────────────────────────────────────────────────┐
  │  本文件与 workflow.py 的 MultiAssistantWithLeader 实现的是       │
  │  同一个架构模式，但抽象层次不同：                                  │
  │                                                                  │
  │  multi_factor_agents.py           workflow.py                    │
  │  (低抽象 / 原始 API)              (高抽象 / 框架封装)              │
  │  ┌─────────────────────┐         ┌──────────────────────────┐   │
  │  │ autogen.AssistantAgent│        │ FinRobot(AssistantAgent) │   │
  │  │ 手动 trigger/message  │        │ utils.order_trigger/message│ │
  │  │ JSON 文件读取配置     │        │ Python 字典 + library     │   │
  │  │ 无工具注册            │        │ register_toolkits() 工具  │   │
  │  └─────────────────────┘         └──────────────────────────┘   │
  └──────────────────────────────────────────────────────────────────┘

【Agent 角色体系】
  本文件定义了一个 12 角色的量化投研团队：

                    ┌───────────────┐
                    │ Group_Leader  │  ← 团队领导，负责协调、分派任务
                    └───────┬───────┘
                            │ [AgentName] <指令> 格式分派任务
          ┌───────┬─────────┼─────────┬─────────┬───────┬───────┐
          ▼       ▼         ▼         ▼         ▼       ▼       ▼
    ┌─────────┐┌─────────┐┌─────────┐┌─────────┐┌─────────┐┌─────────┐┌─────────┐
    │ Value   ││ Growth  ││Momentum ││ Quality ││Volatility││Liquidity││Sentiment│
    │ Factor  ││ Factor  ││ Factor  ││ Factor  ││ Factor   ││ Factor  ││ Factor  │
    │Researcher││Researcher││Researcher││Researcher││Researcher││Researcher││Researcher│
    └─────────┘└─────────┘└─────────┘└─────────┘└─────────┘└─────────┘└─────────┘

          ┌───────┐         ┌───────────────────┐    ┌───────────────────┐
          │ Macro │         │ Portfolio_Manager │    │ Quantitative_Analyst│
          │ Factor│         │ (策略整合)          │    │ (回测验证)           │
          │Researcher│      └───────────────────┘    └───────────────────┘
          └───────┘
                                   │
                          ┌───────────────────┐
                          │ Data_Specialist   │
                          │ (数据获取与处理)    │
                          └───────────────────┘

【执行流程】
  ┌──────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────┐
  │ 用户发起  │────▶│ Group_Leader │────▶│ executor     │────▶│ Worker   │
  │ 任务描述  │     │ 分析 & 分派   │     │ 检测 trigger │     │ 执行任务  │
  └──────────┘     └──────────────┘     └──────────────┘     └──────────┘
                                               │                    │
                                               │  nested_chat       │
                                               │  (最多10轮)         │
                                               ◀────────────────────┘
                                               │  summary_method=
                                               │  "reflection_with_llm"
                                               ▼
                                        ┌──────────────┐
                                        │ Group_Leader │
                                        │ 检查结果,继续 │
                                        │ 或 TERMINATE │
                                        └──────────────┘

【关键设计决策】
  1. Worker Agent 无工具 — 纯 LLM 推理，依赖 executor 执行代码
     （这与 portfolio_optimization.py 不同，后者通过 register_function 注册了
     FinnHub/YFinance 等数据工具和 RAG 检索函数）

  2. 使用 reflection_with_llm 做摘要 — Worker 的多轮工具调用 / 分析结果
     通过 LLM 反思提炼后返回给 Leader，而非仅取最后一条消息

  3. 终止条件: "TERMINATE" in content（使用 in 而非 endswith）
     因为消息可能包含 TERMINATE 后面的文本内容
"""

import re
import json
import autogen
from autogen.cache import Cache

# from finrobot.utils import create_inner_assistant

from functools import partial


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                     第 1 步：LLM 配置                                        ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

config_list_gpt4 = autogen.config_list_from_json(
    # ↑ AutoGen 的工具函数：从 JSON 文件中读取 API 配置列表
    #   "OAI_CONFIG_LIST" 文件路径（不含 .json 后缀），AutoGen 自动追加 .json
    #   文件格式: [{"model": "gpt-4-0125-preview", "api_key": "sk-...", ...}]
    #   返回值: list[dict] — 包含 API key、base_url 等字段的配置字典列表
    "OAI_CONFIG_LIST",
    filter_dict={
        # filter_dict: 从配置列表中筛选特定模型。OAI_CONFIG_LIST 可能包含多个模型配置，
        #   此过滤器只保留 model 字段值为 "gpt-4-0125-preview" 的条目。
        #   注意: gpt-4-0125-preview 是 GPT-4 Turbo 的一个特定版本（2024年1月发布），
        #   该版本修复了 GPT-4 Turbo 的"懒惰"问题（生成不完整代码的倾向）。
        "model": ["gpt-4-0125-preview"],
    },
)

llm_config = {
    "config_list": config_list_gpt4,
    "cache_seed": 42,
    # ↑ cache_seed: AutoGen 的 LLM 响应缓存种子。相同 seed + 相同 prompt → 命中缓存。
    #   开发调试阶段使用固定种子可加速重复运行，但生产环境应设为 None 以获得新鲜回复。
    #   这里设为 42 是约定俗成的"魔法数字"。
    "temperature": 0,
    # ↑ temperature=0: 使 LLM 输出具有最大确定性。
    #   量化金融分析要求精确、可复现的结果，因此采用 0 温度（而非创造性任务常用的 0.7-1.0）。
}


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                第 2 步：读取 Agent 配置定义                                   ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

quant_group_config = json.load(open("quantitative_investment_group_config.json"))
# ↑ 从 JSON 文件读取 11 个 Agent 的定义。每个元素包含：
#     - "name": str        — Agent 唯一标识符（如 "Value_Factor_Researcher"）
#     - "profile": str     — Agent 的 system_message / 角色描述
#   ⚠ 注意：这个 JSON 仅包含 name 和 profile，没有 toolkits 字段。
#     这意味着所有 Worker Agent 都没有注册任何工具功能，只能做纯 LLM 推理。
#     实际的代码执行由 executor (UserProxyAgent) 完成。
#
#   对比 workflow.py 的方式：
#     workflow.py 通过 agent_library.py 的 library 字典获取配置（含 toolkits），
#     或通过 investment_group.py 定义 title/responsibilities/toolkits。
#     本文件直接用 JSON 文件，是最简单的配置方式。

# user_proxy = autogen.UserProxyAgent(
#     name="User",
#     # human_input_mode="ALWAYS",
#     human_input_mode="NEVER",
#     code_execution_config=False
# )
# ↑ 注释掉的代码：最初可能有一个单独的 user_proxy，后来改为 executor 统一扮演
#   UserProxy 角色。在最终版本中，executor 既执行代码又作为嵌套聊天的发起者。


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║             第 3 步：构建团队描述文本 → 注入 Leader 的 system_message          ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

group_descs = "\n\n".join(
    # ↑ 将每个 Agent 的 Name + Responsibility 拼成一个长字符串，用两个换行分隔。
    #   这个文本会被拼接到 Leader 的 system_message 末尾，
    #   让 Leader 知道自己团队有哪些成员、各自擅⻓什么。
    #   没有这个信息，Leader 无法正确给 Worker 分派任务。
    #   输出格式示例：
    #     Name: Value_Factor_Researcher
    #     Responsibility: As a value factor researcher, the individual must ...
    #
    #     Name: Growth_Factor_Researcher
    #     Responsibility: As a growth factor researcher, the individual must ...
    [
        "Name: {} \nResponsibility: {}".format(c["name"], c["profile"])
        # profile 字段在这里充当了"职责描述"的角色
        for c in quant_group_config
    ]
)


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║              第 4 步：创建 Group Leader Agent                                ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

group_leader = autogen.AssistantAgent(
    name="Group_Leader",
    # system_message: Leader 的核心行为指令，定义了 Leader 的：
    #   1) 角色定位：团队协调者
    #   2) 行为规范：每次回复必须总结进展 + 分派任务
    #   3) 指令格式：[<name of staff>] <order> — 这是触发嵌套聊天的关键
    #   4) 质量控制：检查任务完成度，不满足则要求重新执行
    #   5) 终止条件：整个项目完成时回复 TERMINATE
    system_message="""
    As a group leader, you are responsible for coordinating the team's efforts to achieve the project's objectives.
    You must ensure that the team is working together effectively and efficiently.
    Summarize the status of the whole project progess every time you respond, and assign task to one of the group members to progress the project.
    Orders should follow the format: \"[<name of staff>] <order>\" and appear at the end of your response.
    After receiving feedback from the team members, check the progress of the task, and make sure the task is well completed before proceding to th next order.
    If the task is not well completed, your order should be to provide assistance and guidance for the team members to complete it again.
    Reply TERMINATE only when the whole project is done. Your team members are as follows:\n\n
    """
    + group_descs,
    # ↑ 拼接团队描述：Leader 的 system_message = 行为指令 + 团队成员列表。
    #   这与 workflow.py 的 leader_system_message 模板功能相同，但这里手动拼接
    #   而非使用 prompts.py 的模板系统。
    llm_config=llm_config,
)


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║              第 5 步：创建 Executor Agent (UserProxyAgent)                    ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

executor = autogen.UserProxyAgent(
    name="Executor",
    human_input_mode="NEVER",
    # ↑ NEVER 模式：全自治运行，不需要人类介入。
    #   如果设为 "ALWAYS"，每次 Agent 对话需要人类确认后才能继续，
    #   适合调试阶段，但不适合自动化运行。
    # human_input_mode="ALWAYS",

    is_termination_msg=lambda x: x.get("content", "")
    and "TERMINATE" in x.get("content", ""),
    # ↑ 终止条件判断函数：
    #   当消息内容包含 "TERMINATE" 子串（不一定是结尾）时，对话自动结束。
    #   与 workflow.py 的 endswith("TERMINATE") 不同，这里使用 in 操作符更宽容，
    #   可以匹配 "xxx TERMINATE xxx" 这种格式的消息。
    #   参数 x: 消息字典，格式为 {"content": "...", "role": "...", "name": "..."}
    #   返回 True → AutoGen 标记对话为终止状态，停止继续生成回复。

    # max_consecutive_auto_reply=3,
    # ↑ 注释掉了连续自动回复限制。如果不设置此值，AutoGen 使用默认的无限次自动回复，
    #   完全依靠 is_termination_msg 来终止对话。取消注释可设上限防止死循环。

    code_execution_config={
        "last_n_messages": 3,
        # ↑ 代码执行时传递给 Python 解释器的上下文：最近 N 条消息。
        #   设为 3 意味着执行代码时可以看到最近 3 轮对话的上下文，
        #   帮助 LLM 生成的代码理解当前讨论的变量和数据。
        "work_dir": "quant",
        # ↑ 代码执行的工作目录。Agent 生成的 Python 脚本在此目录下创建和运行。
        #   所有中间数据文件（CSV、图表等）也保存在此目录下。
        "use_docker": False,
        # ↑ 不使用 Docker 隔离执行代码。
        #   False = 在本地 Python 环境中直接执行（方便但不够安全）。
        #   True  = 在 Docker 容器中执行（安全隔离但需要 Docker 环境）。
    },
)


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║              第 6 步：创建 Worker Agent 团队 (quant_group)                    ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

quant_group = {
    # ↑ quant_group: dict[str, AssistantAgent]
    #   key = Agent 名称（如 "Value_Factor_Researcher"）
    #   value = AssistantAgent 实例
    #
    #   这些 Worker Agent 的特点：
    #     - 直接使用 autogen.agentchat.AssistantAgent（而非 FinRobot）
    #       因为不需要 FinRobot 的配置预处理和工具注册功能
    #     - system_message = profile（JSON 中定义的角色描述）
    #     - 没有注册任何工具 — Worker 只能做 LLM 推理，
    #       实际的代码编写和执行在嵌套聊天中通过 executor 完成
    #     - 所有 Worker 使用同一个 llm_config（共享 LLM 配置）
    c["name"]: autogen.agentchat.AssistantAgent(
        name=c["name"],
        system_message=c["profile"],
        # ↑ profile 直接作为 system_message。
        #   例如 Value_Factor_Researcher 的 profile 是：
        #     "As a value factor researcher, the individual must possess expertise
        #      in financial statement analysis, a strong understanding of valuation
        #      metrics, adeptness in Python for quantitative modeling, ..."
        #   这个 profile 定义了 Agent 的专家角色，LLM 据此扮演相应的专家行为。
        llm_config=llm_config,
    )
    for c in quant_group_config
}


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║          第 7 步：定义嵌套聊天的 Trigger 和 Message 函数                       ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

def order_trigger(pattern, sender):
    """
    嵌套聊天的触发条件函数 — 检测 Leader 是否在向某个 Worker 下达指令。

    参数:
      pattern: str
        要匹配的模式，格式为 "[AgentName]"，如 "[Value_Factor_Researcher]"。
        通过 functools.partial 预设此参数。

      sender: autogen.Agent
        发送最后一条消息的 Agent 实例（在外层对话中通常是 Leader）。
        AutoGen 在每轮对话后自动将 sender 传给 trigger 函数。

    返回:
      bool — True 表示触发嵌套聊天，False 表示继续外层对话。

    与 workflow.py / utils.py 版本的关键区别：
      ┌────────────────────────────────────────────────────────────────┐
      │  utils.py 的 order_trigger:                                    │
      │    sender.name == name and pattern in sender.last_message()... │
      │    即：同时检验 (1) sender 是否是 leader (2) 消息是否含 pattern  │
      │                                                                │
      │  本文件的 order_trigger:                                        │
      │    pattern in sender.last_message()["content"]                 │
      │    即：仅检验消息是否含 pattern，不检验 sender 身份              │
      │                                                                │
      │  差异影响：本文件版本更简单但不够安全。如果某个 Worker 的消息中   │
      │  意外包含了其他 Worker 的名字（如讨论协作时），可能误触发嵌套聊天。│
      │  但在本场景中 Worker 之间不直接通信（所有消息通过 Leader 中转）， │
      │  所以实际上不会发生误触发。                                      │
      └────────────────────────────────────────────────────────────────┘
    """
    # print(pattern)
    # print(sender.last_message()['content'])
    return pattern in sender.last_message()["content"]
    # ↑ sender.last_message(): AutoGen Agent 的内置方法，返回对话历史中最后一条消息。
    #   消息格式: {"content": "...", "role": "user"/"assistant", "name": "AgentName"}
    #   pattern 如 "[Value_Factor_Researcher]" 被检查是否出现在消息文本中。


def order_message(pattern, recipient, messages, sender, config):
    """
    嵌套聊天的初始消息生成函数 — 从 Leader 的消息中提取指令并包装为任务描述。

    参数:
      pattern: str
        要匹配的 Agent 名称模式，格式为 "[AgentName]"。
        通过 functools.partial 预设。

      recipient: autogen.Agent
        嵌套聊天的接收方（即被分派任务的 Worker Agent）。
        AutoGen 在触发嵌套聊天时自动传入。

      messages: list[dict]
        外层对话的完整消息历史。AutoGen 自动传入。
        注意：本函数实际未使用此参数，而是通过 recipient.chat_messages_for_summary
        获取消息，这是两种不同的获取对话历史的方式。

      sender: autogen.Agent
        嵌套聊天的发送方（即 executor，UserProxyAgent）。
        AutoGen 自动传入。

      config: dict
        AutoGen 的配置字典。AutoGen 自动传入。
        本函数未使用此参数。

    返回:
      str — 包装后的任务描述文本，作为嵌套聊天的第一条消息发给 Worker。

    执行逻辑拆分：
      1. 获取 Leader 最后一条消息（包含 "[AgentName] <指令>" 格式）
      2. 用正则从消息中提取指令内容（排除 "[AgentName]" 标记本身）
      3. 如果正则匹配失败（Leader 未严格遵循格式），回退到完整消息文本
      4. 将指令包装在行为规范模板中（强调编码、保存结果、TERMINATE 规则）
    """
    # ── 子步骤 1：获取 Leader 最后一条消息 ──
    full_order = recipient.chat_messages_for_summary(sender)[-1]["content"]
    # ↑ recipient.chat_messages_for_summary(sender):
    #   获取 recipient 视角下与 sender 的对话历史（用于摘要）。
    #   这里取 [-1]（最后一条消息），即 Leader 刚刚发送的消息。
    #   消息格式: {"content": "...", "role": "...", "name": "Group_Leader"}

    # ── 子步骤 2：正则提取指令内容 ──
    pattern = rf"\[{pattern}\](?::)?\s*(.+?)(?=\n\[|$)"
    # ↑ 正则表达式解析：
    #   \[{pattern}\]   — 匹配 "[AgentName]" 文字（如 "[Value_Factor_Researcher]"）
    #   (?::)?          — 可选冒号（允许 "[AgentName]: instruction" 格式）
    #   \s*             — 跳过空白字符
    #   (.+?)           — 懒惰捕获：提取实际指令内容（捕获组 1）
    #   (?=\n\[|$)      — 前瞻断言：指令内容在遇到下一个 "[AgentName]" 或字符串末尾时停止
    #   re.DOTALL       — 使 . 匹配换行符（指令可能跨多行）
    match = re.search(pattern, full_order, re.DOTALL)
    if match:
        order = match.group(1).strip()
        # ↑ 成功提取：例如 Leader 说
        #   "[Value_Factor_Researcher] Analyze P/E ratios for Dow 30 stocks"
        #   → order = "Analyze P/E ratios for Dow 30 stocks"
    else:
        order = full_order
        # ↑ 回退策略：如果 Leader 未按 [AgentName] 格式分派（可能是由于 LLM 生成不规范），
        #   将整条消息作为指令内容。这确保了即使格式不完美，Worker 仍能收到任务。

    # ── 子步骤 3：包装为格式化的任务描述 ──
    return f"""
    Follow leader's order and complete the following task: {order}.
    For coding tasks, provide python scripts and executor will run it for you.
    Save your results or any intermediate data locally and let group leader know how to read them.
    DO NOT include TERMINATE in your response until you have received the results from the execution of the Python scripts.
    If the task cannot be done currently or need assistance from other members, report the reasons or requirements to group leader ended with TERMINATE.
    """
    # ↑ 返回的文本包含几个关键行为约束：
    #   1) "provide python scripts and executor will run it for you"
    #      — 告知 Worker：你只需写代码，不必自己执行（executor 会执行）
    #      这是 UserProxyAgent 的核心工作机制：Agent 生成代码 → UserProxy 执行 → 结果返回
    #   2) "Save your results or any intermediate data locally"
    #      — 确保中间数据可被其他 Agent 或 Leader 读取
    #   3) "DO NOT include TERMINATE in your response until..."
    #      — 防止 Worker 在代码执行完毕前过早终止嵌套聊天
    #   4) "If the task cannot be done... report... ended with TERMINATE"
    #      — 给 Worker 提供"上报无法完成"的退出路径

    # For coding tasks, only use the functions you have been provided with.


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║       第 8 步：为每个 Worker Agent 注册嵌套聊天 (Nested Chats)                ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

for name, agent in quant_group.items():
    # ↑ 遍历所有 11 个 Worker Agent，为每个 Worker 注册一个嵌套聊天配置。
    #   每个嵌套聊天配置都是"当 Leader 叫到我的名字时，executor 就派活给我"的机制。
    #
    #   嵌套聊天是 AutoGen 的核心编排机制，它与普通对话的关系如下：
    #
    #   ┌─────────────────────────────────────────────────────────────────┐
    #   │  外层对话 (Outer Chat)                                          │
    #   │                                                                 │
    #   │  executor ←─────────→ Group_Leader                              │
    #   │             1. Leader 说: "Let me summarize...                  │
    #   │                [Value_Factor_Researcher] Analyze P/E ratios..."  │
    #   │             2. order_trigger 检测到 [Value_Factor_Researcher]    │
    #   │                → 返回 True → 暂停外层对话                        │
    #   │                                                                 │
    #   │  ┌─ 嵌套聊天 (Nested Chat) ──────────────────────────────────┐  │
    #   │  │                                                           │  │
    #   │  │  executor ←─────────→ Value_Factor_Researcher              │  │
    #   │  │             1. executor 发送 order_message 生成的指令       │  │
    #   │  │             2. Worker 分析任务，生成 Python 代码            │  │
    #   │  │             3. executor 执行代码，返回结果给 Worker         │  │
    #   │  │             4. Worker 基于结果继续分析...                   │  │
    #   │  │             5. Worker 返回最终结论 + TERMINATE              │  │
    #   │  │                                                           │  │
    #   │  │  summary_method="reflection_with_llm"                       │  │
    #   │  │  → LLM 反思整个嵌套对话，提炼核心结论                       │  │
    #   │  │  → 摘要注入回外层对话                                       │  │
    #   │  └───────────────────────────────────────────────────────────┘  │
    #   │                                                                 │
    #   │             3. 外层对话恢复，Leader 收到 Worker 的结果摘要       │
    #   │             4. Leader 检查结果 → 分派下一个任务或 TERMINATE     │
    #   └─────────────────────────────────────────────────────────────────┘
    executor.register_nested_chats(
        # ↑ register_nested_chats 是 UserProxyAgent（以及所有 ConversableAgent）
        #   的方法。它接受两个参数：
        #     - chat_queue: list[dict] — 嵌套聊天的配置列表
        #     - trigger: Callable — 触发条件判断函数
        #   当外层对话中某条消息满足 trigger 条件时，启动 chat_queue 中定义的嵌套对话。
        [
            {
                "sender": executor,
                # ↑ 嵌套聊天的发送方：executor (UserProxyAgent)。
                #   注意这里 sender 是 executor 而非 Leader — 这意味着在嵌套聊天中，
                #   是 executor 向 Worker 发送第一条消息（通过 order_message 生成的指令）。
                #   Worker 回复后，executor 如果发现 Worker 的消息中包含代码块，
                #   会自动提取并执行代码，然后将执行结果返回给 Worker 继续分析。

                "recipient": agent,
                # ↑ 嵌套聊天的接收方：当前循环对应的 Worker Agent。
                #   每次循环关联不同的 Worker（Value_Factor_Researcher, Growth_Factor_Researcher, ...）

                "message": partial(order_message, name),
                # ↑ 嵌套聊天的初始消息：使用 functools.partial 预设 pattern=name 参数。
                #   当 trigger 触发时，AutoGen 调用：
                #     order_message(pattern=name, recipient=agent, messages=..., sender=executor, config=...)
                #   从 Leader 的消息中提取针对该 Worker 的指令文本。

                "summary_method": "reflection_with_llm",
                # ↑ 摘要方法：嵌套聊天结束后如何压缩内容返回给外层对话。
                #   可选值：
                #     "last_msg"             — 仅取嵌套聊天的最后一条消息
                #     "reflection_with_llm"  — 用 LLM 反思整个对话，提炼核心结论
                #
                #   为什么用 reflection_with_llm 而非 last_msg？
                #     Worker 的分析通常包含多轮交互：
                #       Worker: "我需要先获取数据，这里是代码..."
                #       executor: [执行代码] "结果是: ..."
                #       Worker: "基于数据，我计算了 P/E 比率...发现..."
                #       Worker: [继续深入分析] "..."
                #       Worker: "结论是... TERMINATE"
                #     如果只用 last_msg，可能丢失 Worker 中途的代码执行结果。
                #     reflection_with_llm 会用一个额外的 LLM 调用对整个嵌套对话
                #     做总结，确保 Leader 获得最核心的结论。
                #   代价：增加一次 LLM 调用的 token 消耗。

                "max_turns": 10,
                # ↑ 嵌套聊天最多 10 轮往返（Worker 发言 + executor 执行代码 = 1 轮）。
                #   对于需要多步数据分析的复杂任务（获取数据→清洗→建模→回测→输出），
                #   10 轮是合理上限。超过 10 轮后嵌套聊天强制终止。

                "max_consecutive_auto_reply": 3,
                # ↑ Worker 连续 3 次自动回复后强制终止。
                #   防止 Worker 陷入"思考→输出→思考→输出"的死循环。
                #   比外层对话的限制更严格（此处 3 vs 外层默认 10），
                #   因为每个 Worker 的子任务应该有限且明确。
            }
        ],
        trigger=partial(order_trigger, f"[{name}]"),
        # ↑ trigger 函数：当外层对话中某条消息满足条件时触发嵌套聊天。
        #   partial(order_trigger, f"[{name}]") 等价于：
        #     lambda sender: f"[{name}]" in sender.last_message()["content"]
        #   即只要 Leader 的消息中包含 "[Value_Factor_Researcher]"，
        #   就自动触发与 Value_Factor_Researcher 的嵌套聊天。
    )


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║              第 9 步：定义量化投资任务                                        ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

quant_task = "Develop and test the feasibility of a quantitative investment strategy focusing on the Dow Jones 30 stocks, utilizing your multi-factor analysis expertise to identify potential investment opportunities and optimize the portfolio's performance. Ensure the strategy is robust, data-driven, and aligns with our risk management principles."
# ↑ 这是发送给 Group_Leader 的任务描述（prompt）。
#   它定义了整个 Agent 团队要完成的目标：
#     1) 开发一个量化投资策略
#     2) 测试策略的可行性
#     3) 聚焦 Dow Jones 30 股票（道琼斯工业平均指数成分股，30 只蓝筹股）
#     4) 利用多因子分析（价值、成长、动量、质量、波动率、流动性、情绪、宏观等因子）
#     5) 优化投资组合表现
#     6) 确保策略稳健、数据驱动、符合风险管理原则


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║          第 10 步：启动对话 — 整个系统的入口点                                 ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

with Cache.disk() as cache:
    # ↑ Cache.disk(): AutoGen 的磁盘缓存上下文管理器。
    #   在 with 块内，所有 LLM 调用的响应都会被缓存到磁盘（默认路径: .cache/）。
    #   作用：
    #     1) 加速重复运行：相同 prompt → 从缓存读取，不消耗 API 调用
    #     2) 节省成本：开发调试阶段不需要为相同的 LLM 响应反复付费
    #     3) 可复现性：相同输入保证相同输出（因为 temperature=0 + 缓存）
    #
    #   注意：如果修改了 task 文本或 system_message，需要清除缓存才能看到新结果。

    executor.initiate_chat(group_leader, message=quant_task, cache=cache)
    # ↑ initiate_chat 是 AutoGen 对话的启动方法。
    #   参数：
    #     group_leader   — 对话的"对方"（recipient），即 userId 的消息发给谁
    #     message=quant_task — 用户（executor）发出的第一条消息（任务描述）
    #     cache=cache    — 传入缓存管理器，用于缓存此次对话中的所有 LLM 响应
    #
    #   对话启动后的执行序列：
    #     第1步: executor → Leader（发送任务描述）
    #     第2步: Leader 分析任务，决定先让哪个 Factor Researcher 开始工作
    #            → 生成消息，末尾包含 "[Value_Factor_Researcher] <指令>"
    #     第3步: AutoGen 检查 trigger → order_trigger 返回 True
    #            → 启动嵌套聊天: executor ↔ Value_Factor_Researcher
    #     第4步: Worker 执行分析任务，生成 Python 代码
    #            → executor 执行代码 → 结果返回 Worker → Worker 给出结论
    #     第5步: 嵌套聊天结束 → reflection_with_llm 摘要 → 注入回外层对话
    #     第6步: Leader 收到结果，检查质量
    #            → 满足 → 分派下一个任务给另一个 Worker
    #            → 不满足 → 重新分派或调整要求
    #     第7步: 循环步骤 2-6，直到 Leader 认为所有因子分析完成
    #            → Leader 回复 "TERMINATE" → 外层对话结束
    #
    #   最终产出：
    #     - quant/ 目录下的分析代码和数据文件
    #     - Leader 的综合分析报告（包含多因子评估和投资建议）
