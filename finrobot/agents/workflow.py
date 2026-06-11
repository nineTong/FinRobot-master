"""
工作流模块 (Workflow Module)
=============================

本模块是 FinRobot 的 Agent 编排层，定义了多种 Agent 工作流模式。
它基于 Microsoft AutoGen 框架，将 agent_library.py 中的角色配置实例化为可运行的 Agent，
并组织成不同的协作模式。

支持的三种工作流模式：
  1. SingleAssistant        — 单人 Agent：一个 FinRobot + 一个 UserProxy，一问一答
  2. MultiAssistant         — 群聊协作：多个 Agent 在 GroupChat 中自由对话，轮流发言
  3. MultiAssistantWithLeader — 领导-下属：一个 Leader Agent 通过嵌套聊天分派任务给下属

配置字典的两种格式：
  格式A（简单配置，来自 agent_library.py）：
    {"name": "Market_Analyst", "profile": "...", "toolkits": [...]}
    只包含 name, profile, toolkits 三个字段。

  格式B（复杂配置，来自实验文件如 experiments/investment_group.py）：
    {"title": "Chief Analyst", "responsibilities": ["职责1", "职责2"], "toolkits": [...]}
    包含 title, responsibilities 等字段，用于层次化的 Leader-Worker 架构。
    _preprocess_config() 负责将两种格式统一处理。


关键参数来源对照表
==================
下表列出了配置字典中各 key 的定义位置、来源和作用。尤其注意 responsibilities、
description、group_desc 这三个 key 在 agent_library.py 中并未显式定义，
而是在 workflow.py 中通过 _preprocess_config() 和 _get_representative() 动态生成。

┌────────────────┬──────────────────────────────┬──────────────────────────────────────────────────┐
│ Key              │ 来源                         │ 作用                                               │
├────────────────┼──────────────────────────────┼──────────────────────────────────────────────────┤
│ name             │ 两种格式都有                  │ Agent 的唯一标识符，也用于 GroupChat 中按名查找 Agent  │
│                  │ 示例: "Market_Analyst"        │ 在 library 字典中作为 key 使用                        │
├────────────────┼──────────────────────────────┼──────────────────────────────────────────────────┤
│ profile          │ 格式A: agent_library.py 手写   │ 最终作为 Agent 的 system_message（系统提示词），       │
│                  │ 格式B: _preprocess_config 生成  │ 定义 Agent 的角色定位、能力边界和行为规范              │
│                  │ (role_prompt + leader_prompt   │ 是 LLM 理解"自己是谁、该怎么做"的核心文本             │
│                  │  + 原始 profile 拼接)           │                                                    │
├────────────────┼──────────────────────────────┼──────────────────────────────────────────────────┤
│ toolkits         │ 格式A: agent_library.py 定义   │ Agent 可调用的工具列表                                │
│                  │ 元素可以是 函数/类/字典          │ 传给 register_toolkits() 注册为 function calling 工具 │
├────────────────┼──────────────────────────────┼──────────────────────────────────────────────────┤
│ title            │ 格式B 专有: 实验文件中自定义     │ Leader-Worker 架构中的角色头衔                          │
│                  │ 如 "Chief Investment Officer" │ 比 name 更具语义，嵌入 role_system_message 模板:       │
│                  │ 在 agent_library.py 中不存在   │ "As a {title}, your responsibilities are: ..."      │
├────────────────┼──────────────────────────────┼──────────────────────────────────────────────────┤
│ responsibilities │ 格式B 专有: 实验文件中自定义     │ Agent 的具体职责列表，嵌入 role_system_message 模板    │
│                  │ 是字符串列表或字符串             │ 格式化后形如:                                        │
│                  │ 在 agent_library.py 中不存在   │ " - 职责1\n - 职责2\n"                              │
├────────────────┼──────────────────────────────┼──────────────────────────────────────────────────┤
│ description      │ _preprocess_config() 自动生成  │ Agent 的简短介绍文本                                  │
│                  │ 两种格式都会生成                │ 来源: AutoGen GroupChat 的 send_introductions 参数   │
│                  │ 格式: "Name: xxx\n             │ 在群聊开始时自动发送，让其他 Agent 了解该 Agent 是谁     │
│                  │  Responsibility:\n - ..."       │ 有 responsibilities 时包含职责，无则仅包含名称         │
├────────────────┼──────────────────────────────┼──────────────────────────────────────────────────┤
│ group_desc       │ MultiAssistantWithLeader      │ 仅供 Leader Agent 使用                               │
│                  │ ._get_representative() 动态生成  │ 汇总所有下属 Agent 的 Name + Responsibility 文本      │
│                  │ 注入到 leader_config 中         │ 与 leader_system_message 模板拼接后，                 │
│                  │ 在 agent_library.py 中不存在   │ 让 Leader "看到"全部团队成员信息并据此分派任务          │
└────────────────┴──────────────────────────────┴──────────────────────────────────────────────────┘


数据流向图
==========

下面展示复杂配置（格式B）从实验文件到最终 Agent system_message 的完整数据流：

实验文件 (experiments/investment_group.py)
  │  用户在此定义:
  │    - group_config["leader"]     → {"title": "CIO", "responsibilities": [...]}
  │    - group_config["agents"][i]  → {"title": "Analyst", "responsibilities": [...]}
  │
  ├─→ MultiAssistantWithLeader.__init__()
  │     │
  │     │  agent_configs = group_config["agents"]        ← 提取下属配置列表
  │     │
  │     │  ┌─ 构建 group_desc（汇总所有下属信息）───────────┐
  │     │  │ for each agent_config:                       │
  │     │  │   name = agent_config["title"]               │ ← title 来自实验文件
  │     │  │   resp = agent_config["responsibilities"]    │ ← responsibilities 来自实验文件
  │     │  │   group_desc += "Name: {name}\n              │
  │     │  │     Responsibility:\n - {resp}\n\n"          │
  │     │  └──────────────────────────────────────────────┘
  │     │
  │     │  leader_config["group_desc"] = group_desc      ← 注入到 Leader 配置
  │     │
  │     ├─→ FinRobot(leader_config)                       ← 初始化 Leader
  │     │     │
  │     │     └─→ _preprocess_config(leader_config)
  │     │           │
  │     │           ├─ 检测到 "responsibilities" → 生成 role_prompt
  │     │           │   模板: role_system_message (来自 prompts.py)
  │     │           │   内容: "As a {title}, your responsibilities are:
  │     │           │          {responsibilities}
  │     │           │          Reply TERMINATE when done."
  │     │           │
  │     │           ├─ 检测到 "group_desc" → 生成 leader_prompt
  │     │           │   模板: leader_system_message (来自 prompts.py)
  │     │           │   内容: "You are the leader of:
  │     │           │          {group_desc}          ← 所有下属的 Name + Responsibility
  │     │           │          As a group leader, you are responsible for..."
  │     │           │
  │     │           └─ 最终拼接:
  │     │               profile = role_prompt + leader_prompt + 原始 profile
  │     │               description = "Name: xxx\nResponsibility:\n - ..."
  │     │
  │     └─→ FinRobot(agent_config) × N                   ← 初始化各下属 Agent
  │           │
  │           └─→ _preprocess_config(agent_config)
  │                 │
  │                 ├─ 检测到 "responsibilities" → 生成 role_prompt (同上)
  │                 ├─ 无 "group_desc" → 跳过 leader_prompt
  │                 └─ 最终拼接:
  │                     profile = role_prompt + 原始 profile
  │                     description = "Name: xxx\nResponsibility:\n - ..."

最终效果：
  Leader 的 system_message:
    "As a Chief Investment Officer, your responsibilities are as follows:
      - Oversee the entire investment analysis process.
      - Integrate insights from various groups.
      Reply 'TERMINATE' in the end when everything is done.

     You are the leader of the following group members:
     Name: Market_Sentiment_Analyst
     Responsibility:
      - Track and interpret market trends and news.
      - Analyze social media for market sentiment.
     ...

     As a group leader, you are responsible for coordinating the team's efforts..."

  下属的 system_message:
    "As a Market Sentiment Analyst, your responsibilities are as follows:
      - Track and interpret market trends and news.
      - Analyze social media for market sentiment.
      Reply 'TERMINATE' in the end when everything is done."
"""

# ═══════════════════════════════════════════════════════════════════════════════
# 导入区 — 理解每个 import 的来源和作用是理解整个模块依赖关系的关键
# ═══════════════════════════════════════════════════════════════════════════════

# ── 同包导入（agents/ 目录内部）──
from .agent_library import library
# ↑ library: dict[str, dict], key 是 Agent 名称（如 "Market_Analyst"），
#   value 是 {"name": "...", "profile": "...", "toolkits": [...]}
#   这是 FinRobot 类通过字符串名称查找 Agent 配置的数据源

from typing import Any, Callable, Dict, List, Optional, Annotated

# ── AutoGen 框架核心 ──
import autogen
from autogen.cache import Cache
# ↑ Cache.disk(): AutoGen 的磁盘缓存上下文管理器，
#   在 chat() 方法中用于缓存 LLM 响应，加速重复查询
from autogen import (
    ConversableAgent,   # 所有可对话 Agent 的基类
    AssistantAgent,     # LLM 驱动的 Agent（FinRobot 的父类）
    UserProxyAgent,     # 代码执行 Agent（executor 角色）
    GroupChat,          # 群聊容器，管理多个 Agent 的对话
    GroupChatManager,   # 群聊管理器，控制发言顺序
    register_function,  # 将 Python 函数注册为 Agent 可调用的 function calling 工具
)

# ── Python 标准库 ──
from collections import defaultdict
# ↑ 用于 _init_agents() 中按 Agent 名称分组，处理重名问题
from functools import partial
# ↑ 用于创建带有预设参数的 trigger/message 函数，
#   如 partial(order_trigger, name="Leader", pattern="[Worker_1]")
from abc import ABC, abstractmethod
# ↑ SingleAssistantBase 和 MultiAssistantBase 是抽象基类，
#   强制子类实现 chat() / reset() / _get_representative()

# ── 跨包导入（finrobot 包内部但不同目录）──
from ..toolkits import register_toolkits
# ↑ 位于 finrobot/toolkits.py，是工具注册的核心函数。
#   支持注册：单个函数、整个类（遍历其所有方法）、字典配置。
#   所有工具函数在注册前都经过 stringify_output 装饰器处理，
#   确保返回值是字符串（AutoGen function calling 的硬性要求）。

from ..functional.rag import get_rag_function
# ↑ 位于 finrobot/functional/rag.py，创建 RAG 检索函数。
#   返回 (retrieve_content函数, RetrieveUserProxyAgent实例) 元组。
#   retrieve_content 内部调用了 AutoGen 的 RetrieveUserProxyAgent
#   来实现向量检索，但对调用方表现为一个普通的 function calling 工具。

from .utils import (
    instruction_trigger,
    # ↑ 判断条件函数：检查 sender 最后一条消息是否包含
    #   "instruction & resources saved to" 字符串。
    #   用于 SingleAssistantShadow：当 assistant 调用 ReportAnalysisUtils
    #   将分析指令保存为 .txt 文件后，自动触发影子 Agent 执行分析。
    instruction_message,
    # ↑ 消息生成函数：从 assistant 最后一条消息中提取 .txt 文件路径，
    #   读取文件内容，追加 "Reply TERMINATE at the end of your response."
    #   作为嵌套聊天的初始消息发送给 Shadow Agent。
    order_trigger,
    # ↑ 判断条件函数：检查 (1) sender 是否是 leader，
    #   (2) leader 的消息中是否包含 "[Agent_Name]" 格式的指令。
    #   用于 MultiAssistantWithLeader：检测 Leader 何时向 Worker 下达任务。
    order_message,
    # ↑ 消息生成函数：用正则从 Leader 消息中提取 "[Agent_Name] 具体指令内容"，
    #   然后套入 order_template 模板包装为完整任务描述，
    #   作为嵌套聊天的初始消息发送给 Worker Agent。
)
from .prompts import (
    leader_system_message,
    # ↑ Leader 的系统提示词模板。包含 {group_desc} 占位符，
    #   在 _preprocess_config() 中被替换为所有下属 Agent 的汇总信息。
    #   模板内容来自 finrobot/agents/prompts.py。
    role_system_message,
    # ↑ 角色 Agent 的系统提示词模板。包含 {title} 和 {responsibilities} 占位符，
    #   在 _preprocess_config() 中被替换为 Agent 的头衔和职责列表。
    #   模板内容来自 finrobot/agents/prompts.py。
)


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                        FinRobot — 核心 Agent 类                              ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

class FinRobot(AssistantAgent):
    """
    FinRobot 是 FinRobot 框架中所有 Agent 的基础类，继承自 AutoGen 的 AssistantAgent。

    它的核心职责：
      1. 读取 agent 配置（支持名称查表 或 直接传字典）
      2. 预处理配置，将不同格式统一为标准格式
      3. 初始化 AutoGen Agent 并注册工具

    配置来源有两种：
      A) 字符串 → 从 agent_library.py 的 library 字典中查表获取预定义配置
      B) 字典   → 直接使用传入的配置（可能来自实验文件或用户自定义）
    """

    def __init__(
        self,
        agent_config: str | Dict[str, Any],
        # ── 字符串：agent_library.py 中定义的 Agent 名称，如 "Market_Analyst"
        # ── 字典：自定义配置，支持两种格式（见文件头注释）
        system_message: str | None = None,
        # 可选，覆盖配置中的 profile/system_message
        toolkits: List[Callable | dict | type] = [],
        # 可选，覆盖配置中的 toolkits 列表
        proxy: UserProxyAgent | None = None,
        # 可选，如果传入则立即调用 register_proxy 注册工具
        **kwargs,
        # 其余参数透传给 AssistantAgent.__init__（如 llm_config）
    ):
        orig_name = ""
        # ── 分支 A：字符串 → 从 agent_library.py 查表 ──
        if isinstance(agent_config, str):
            orig_name = agent_config
            # 去掉 "_Shadow" 后缀（Shadow Agent 命名规则：原名称 + "_Shadow"）
            name = orig_name.replace("_Shadow", "")
            assert name in library, f"FinRobot {name} not found in agent library."
            agent_config = library[name]  # 从 library 字典获取预定义配置

        # ── 统一预处理：将不同格式的配置转换为标准格式 ──
        agent_config = self._preprocess_config(agent_config)

        # ── 基本校验 ──
        assert agent_config, f"agent_config is required."
        assert agent_config.get("name", ""), f"name needs to be in config."

        # ── 提取关键字段，支持构造函数参数覆盖 ──
        name = orig_name if orig_name else agent_config["name"]
        default_system_message = agent_config.get("profile", None)
        # ↑ profile: Agent 的系统提示词（system message），定义 Agent 的角色和行为
        #   来源：agent_library.py 中直接写好的 profile 字段（如 Expert_Investor 的详细角色描述）
        #   或 _preprocess_config 自动生成的（role_prompt + leader_prompt + 原始 profile）
        default_toolkits = agent_config.get("toolkits", [])
        # ↑ toolkits: Agent 可调用的工具列表
        #   来源：agent_library.py 中每个 Agent 定义的 toolkits 字段

        # 构造函数参数优先级高于配置文件
        system_message = system_message or default_system_message
        self.toolkits = toolkits or default_toolkits

        name = name.replace(" ", "_").strip()

        # ── 调用 AutoGen 的 AssistantAgent.__init__ 完成初始化 ──
        super().__init__(
            name, system_message, description=agent_config["description"], **kwargs
        )
        # ↑ description: Agent 的简短描述文本，用于 GroupChat 中让其他 Agent 了解该 Agent 的职责
        #   来源：_preprocess_config 自动生成，格式为 "Name: xxx\nResponsibility:\n - ..."
        #   或当无 responsibilities 时简化为 "Name: xxx"

        # ── 如果有 proxy，立即注册工具 ──
        if proxy is not None:
            self.register_proxy(proxy)

    def _preprocess_config(self, config):
        """
        预处理 Agent 配置，将简单格式和复杂格式统一转换为标准格式。

        这是理解整个配置系统的关键函数，也是 FinRobot.__init__ 调用的第一个方法。
        它处理两种配置格式并输出统一的内部格式。

        输入 → 输出映射：
        ┌──────────────────────┬───────────────────────────────────────┐
        │ 输入格式              │ 输出（新增的 key）                      │
        ├──────────────────────┼───────────────────────────────────────┤
        │ 格式A (简单)          │ profile=原始 profile                   │
        │ {name, profile,      │ description="Name: xxx"               │
        │  toolkits}           │                                       │
        ├──────────────────────┼───────────────────────────────────────┤
        │ 格式B (角色)          │ profile=role_prompt + 原始 profile     │
        │ {title,              │ description="Name: xxx\n              │
        │  responsibilities,   │   Responsibility:\n - ..."            │
        │  toolkits}           │                                       │
        ├──────────────────────┼───────────────────────────────────────┤
        │ 格式B+Leader         │ profile=role_prompt + leader_prompt    │
        │ {title,              │         + 原始 profile                │
        │  responsibilities,   │ description=(同上)                    │
        │  group_desc,         │                                       │
        │  toolkits}           │                                       │
        └──────────────────────┴───────────────────────────────────────┘

        处理逻辑（按顺序执行）：
          1. 如果有 responsibilities → 生成 role_prompt（角色提示词）
             ── 使用 prompts.py 的 role_system_message 模板
          2. 始终生成 description（简短描述，用于 GroupChat 介绍）
             ── 在 MultiAssistant GroupChat 中，send_introductions=True 时自动发送
          3. 如果有 group_desc → 生成 leader_prompt（Leader 提示词）
             ── 使用 prompts.py 的 leader_system_message 模板
             ── group_desc 由 MultiAssistantWithLeader._get_representative() 注入
          4. 最终 profile = role_prompt + leader_prompt + 原始 profile（拼接）

        返回:
          增强后的 config 字典，确保包含以下 key：
            - "name": str         （Agent 唯一标识）
            - "profile": str      （最终 system_message，LLM 据此理解自己的角色）
            - "description": str  （简短介绍，GroupChat 自我介绍用）
            - "toolkits": list    （保持不变，透传）
        """

        role_prompt, leader_prompt, responsibilities = "", "", ""

        # ── 步骤 1：处理 responsibilities（复杂配置格式）──
        # responsibilities 来源：实验文件（如 investment_group.py）中的自定义配置
        # 在 agent_library.py 中不存在此字段
        # 作用：定义 Agent 的具体职责列表，会被嵌入到 role_system_message 模板中
        if "responsibilities" in config:
            # title 来源：复杂配置格式中的 "title" 字段，如 "Chief Investment Officer"
            # 回退到 "name" 字段（简单配置格式）
            title = config["title"] if "title" in config else config.get("name", "")
            # 如果配置中只有 title 没有 name，用 title 补全 name
            if "name" not in config:
                config["name"] = config["title"]
            responsibilities = config["responsibilities"]
            # responsibilities 可以是字符串或列表，统一转为带项目符号的字符串
            responsibilities = (
                "\n".join([f" - {r}" for r in responsibilities])
                if isinstance(responsibilities, list)
                else responsibilities
            )
            # 使用 role_system_message 模板（来自 prompts.py）生成角色提示词
            # 模板内容：
            #   "As a {title}, your reponsibilities are as follows:
            #    {responsibilities}
            #    Reply 'TERMINATE' in the end when everything is done."
            role_prompt = role_system_message.format(
                title=title,
                responsibilities=responsibilities,
            )

        # ── 步骤 2：生成 description（所有格式都需要）──
        # description 作用：在 GroupChat 中，每个 Agent 的 description 会作为
        #   send_introductions=True 时的自我介绍，让其他 Agent 知道该 Agent 是谁、能做什么
        # 格式：有 responsibilities → "Name: xxx\nResponsibility:\n - ..."
        #       无 responsibilities → "Name: xxx"
        name = config.get("name", "")
        description = (
            f"Name: {name}\nResponsibility:\n{responsibilities}"
            if responsibilities
            else f"Name: {name}"
        )
        config["description"] = description.strip()

        # ── 步骤 3：处理 group_desc（Leader 配置格式）──
        # group_desc 来源：MultiAssistantWithLeader._get_representative() 动态生成
        #   内容是所有下属 Agent 的 Name + Responsibility 汇总文本
        #   然后被注入到 leader 的配置中（见 MultiAssistantWithLeader 第 447 行）
        # 作用：让 Leader Agent 在 system prompt 中"看到"所有下属的信息
        if "group_desc" in config:
            group_desc = config["group_desc"]
            # 使用 leader_system_message 模板（来自 prompts.py）生成 Leader 提示词
            # 模板内容（简化）：
            #   "You are the leader of the following group members:
            #    {group_desc}
            #    As a group leader, you are responsible for coordinating..."
            leader_prompt = leader_system_message.format(group_desc=group_desc)

        # ── 步骤 4：拼接最终 profile ──
        # 最终的 system_message = role_prompt + leader_prompt + 原始 profile
        # 三种情况的拼接结果：
        #   普通 Agent（简单格式）：只有原始 profile
        #   角色 Agent（复杂格式）：role_prompt + 原始 profile（如果有）
        #   Leader Agent：          role_prompt + leader_prompt + 原始 profile（如果有）
        config["profile"] = (
            (role_prompt + "\n\n").strip()
            + (leader_prompt + "\n\n").strip()
            + config.get("profile", "")
        ).strip()

        return config

    def register_proxy(self, proxy):
        """
        将 Agent 的工具注册到 proxy 上。

        调用 toolkits.py 的 register_toolkits()：
          - caller=self（FinRobot）：LLM 在 FinRobot 的上下文中看到工具描述，决定调用
          - executor=proxy（UserProxyAgent）：工具代码在 UserProxy 中实际执行
        """
        register_toolkits(self.toolkits, self, proxy)


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                    单人 Agent 工作流 (Single Assistant)                      ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

class SingleAssistantBase(ABC):
    """
    单人 Agent 工作流的抽象基类。

    架构：1 个 FinRobot（assistant）+ 1 个 UserProxyAgent（user_proxy）
    这是最简单的模式——用户发消息，Agent 调用工具完成任务，返回结果。

    子类需要实现 chat() 和 reset() 方法。
    """

    def __init__(
        self,
        agent_config: str | Dict[str, Any],
        llm_config: Dict[str, Any] = {},
        # llm_config: AutoGen 的 LLM 配置，包含 model、api_key、temperature 等
        # 格式如: {"config_list": [...], "temperature": 0}
    ):
        self.assistant = FinRobot(
            agent_config=agent_config,
            llm_config=llm_config,
            proxy=None,  # 此时不注册 proxy，留给子类在 __init__ 中处理
        )

    @abstractmethod
    def chat(self):
        """启动对话，子类必须实现"""
        pass

    @abstractmethod
    def reset(self):
        """重置 Agent 状态，子类必须实现"""
        pass


class SingleAssistant(SingleAssistantBase):
    """
    标准单人 Agent 工作流。

    使用方式：
      agent = SingleAssistant("Market_Analyst", llm_config=llm_config)
      agent.chat("Analyze Apple's recent performance")
    """

    def __init__(
        self,
        agent_config: str | Dict[str, Any],
        llm_config: Dict[str, Any] = {},
        is_termination_msg=lambda x: x.get("content", "")
        and x.get("content", "").endswith("TERMINATE"),
        # ↑ 终止条件判断函数：当消息内容以 "TERMINATE" 结尾时对话结束
        human_input_mode="NEVER",
        # ↑ 人类输入模式："NEVER" 表示全自动，不等待人类输入
        max_consecutive_auto_reply=10,
        # ↑ 最大连续自动回复次数，防止死循环
        code_execution_config={
            "work_dir": "coding",
            # ↑ 代码执行的工作目录，Agent 生成的 Python 脚本在此目录下执行
            "use_docker": False,
            # ↑ 是否使用 Docker 隔离执行代码（False = 本地直接执行）
        },
        **kwargs,
    ):
        super().__init__(agent_config, llm_config=llm_config)
        # ── 创建 UserProxyAgent：负责执行工具代码、与用户交互 ──
        self.user_proxy = UserProxyAgent(
            name="User_Proxy",
            is_termination_msg=is_termination_msg,
            human_input_mode=human_input_mode,
            max_consecutive_auto_reply=max_consecutive_auto_reply,
            code_execution_config=code_execution_config,
            **kwargs,
        )
        # ── 注册工具：将 assistant 的 toolkits 注册到 user_proxy 上 ──
        self.assistant.register_proxy(self.user_proxy)

    def chat(self, message: str, use_cache=False, **kwargs):
        """
        启动对话。

        参数:
          message: 用户消息（任务描述）
          use_cache: 是否使用 AutoGen 的磁盘缓存（可加速重复查询）
        """
        with Cache.disk() as cache:
            self.user_proxy.initiate_chat(
                self.assistant,
                message=message,
                cache=cache if use_cache else None,
                **kwargs,
            )

        print("Current chat finished. Resetting agents ...")
        self.reset()

    def reset(self):
        """重置 Agent 状态，清除对话历史"""
        self.user_proxy.reset()
        self.assistant.reset()


class SingleAssistantRAG(SingleAssistant):
    """
    带 RAG（检索增强生成）能力的单人 Agent。

    在 SingleAssistant 的基础上额外注册一个 RAG 检索函数，
    Agent 可以在对话中调用该函数从外部知识库检索信息。

    额外参数:
      retrieve_config: RAG 检索配置（传递给 get_rag_function）
      rag_description: RAG 函数的描述文本（LLM 据此决定何时调用）
    """

    def __init__(
        self,
        agent_config: str | Dict[str, Any],
        llm_config: Dict[str, Any] = {},
        is_termination_msg=lambda x: x.get("content", "")
        and x.get("content", "").endswith("TERMINATE"),
        human_input_mode="NEVER",
        max_consecutive_auto_reply=10,
        code_execution_config={
            "work_dir": "coding",
            "use_docker": False,
        },
        retrieve_config={},
        # ↑ RAG 检索配置，如 {"vector_store": "chromadb", "docs_path": "..."}
        rag_description="",
        # ↑ RAG 工具的描述，LLM 根据此描述判断何时调用 RAG 检索
        **kwargs,
    ):
        super().__init__(
            agent_config,
            llm_config=llm_config,
            is_termination_msg=is_termination_msg,
            human_input_mode=human_input_mode,
            max_consecutive_auto_reply=max_consecutive_auto_reply,
            code_execution_config=code_execution_config,
            **kwargs,
        )
        assert retrieve_config, "retrieve config cannot be empty for RAG Agent."
        # ── 从 rag.py 获取 RAG 函数和专用的 RAG Assistant ──
        rag_func, rag_assistant = get_rag_function(retrieve_config, rag_description)
        self.rag_assistant = rag_assistant
        # ── 将 RAG 函数注册为 Agent 可调用的工具 ──
        register_function(
            rag_func,
            caller=self.assistant,
            executor=self.user_proxy,
            description=rag_description if rag_description else rag_func.__doc__,
        )

    def reset(self):
        super().reset()
        self.rag_assistant.reset()


class SingleAssistantShadow(SingleAssistant):
    """
    带"影子 Agent"模式的单人 Agent。

    架构：assistant 在检测到需要执行复杂指令时，触发嵌套聊天，
    将任务委托给一个没有工具权限的 assistant_shadow（影子 Agent）。

    使用场景：ReportAnalysisUtils 将分析指令保存为 .txt 文件后，
    instruction_trigger 检测到 "instruction & resources saved to" 消息，
    触发 assistant_shadow 读取文件内容并执行分析，最后回复 "TERMINATE"。

    Shadow Agent 特点：
      - 名称 = 原 Agent 名称 + "_Shadow"
      - toolkits = []（没有工具权限，只能做文本分析/生成）
    """

    def __init__(
        self,
        agent_config: str | Dict[str, Any],
        llm_config: Dict[str, Any] = {},
        is_termination_msg=lambda x: x.get("content", "")
        and x.get("content", "").endswith("TERMINATE"),
        human_input_mode="NEVER",
        max_consecutive_auto_reply=10,
        code_execution_config={
            "work_dir": "coding",
            "use_docker": False,
        },
        **kwargs,
    ):
        super().__init__(
            agent_config,
            llm_config=llm_config,
            is_termination_msg=is_termination_msg,
            human_input_mode=human_input_mode,
            max_consecutive_auto_reply=max_consecutive_auto_reply,
            code_execution_config=code_execution_config,
            **kwargs,
        )
        # ── 创建 Shadow Agent 配置（名称加 _Shadow 后缀，无工具）──
        # 注意：这里生成了 agent_config_shadow 变量（名称带 _Shadow 后缀），
        # 但下面创建 FinRobot 时传的是原始 agent_config 而非 agent_config_shadow。
        # 对于字符串类型：FinRobot.__init__ 会通过 replace("_Shadow", "") 自动处理命名，
        #   但最终 Agent 的 name 不会是 "Xxx_Shadow" 而是原始名称 "Xxx"。
        # 对于字典类型：agent_config_shadow 被创建但未使用，Shadow Agent 的 name
        #   也是原始名称。由于 toolkits=[] 已显式覆盖，功能不受影响，但两个 Agent
        #   同名可能在调试时造成混淆。
        if isinstance(agent_config, dict):
            agent_config_shadow = agent_config.copy()
            agent_config_shadow["name"] = agent_config["name"] + "_Shadow"
            agent_config_shadow["toolkits"] = []
        else:
            agent_config_shadow = agent_config + "_Shadow"

        # ── 创建 Shadow Agent ──
        # 关键设计：proxy=None（不注册到 UserProxy）+ toolkits=[]（空工具列表）
        # 这意味着 Shadow Agent 没有任何工具调用能力，只能做纯文本分析/生成。
        # 它通过嵌套聊天被触发，接收 assistant 的分析指令，处理后返回文本结果。
        self.assistant_shadow = FinRobot(
            agent_config,  # ⚠ 应为 agent_config_shadow，见上方注释
            toolkits=[],  # 空工具列表 — Shadow 只能做文本处理
            llm_config=llm_config,
            proxy=None,
        )

        # ── 注册嵌套聊天：当 trigger 条件满足时自动触发 ──
        # 嵌套聊天的工作机制（AutoGen 核心概念）：
        #   外层对话正常运行 → assistant 生成一条消息
        #     → AutoGen 检查该消息是否满足 register_nested_chats 中注册的 trigger 条件
        #     → 如果 trigger 返回 True，暂停外层对话，启动内层对话
        #     → 内层对话按 sender/recipient/message/max_turns 参数独立运行
        #     → 内层对话结束后，结果经 summary_method 压缩为摘要
        #     → 摘要被注入回外层对话，外层继续
        #
        # 这里的 trigger 链路：
        #   assistant 调用了 ReportAnalysisUtils.analyze_income_stmt()
        #     → 工具内部生成了一个 .txt 分析指令文件
        #     → 工具返回 "instruction & resources saved to /path/to/file.txt"
        #     → instruction_trigger 检测到该字符串 → 返回 True
        #     → 触发 Shadow Agent 嵌套聊天
        self.assistant.register_nested_chats(
            [
                {
                    "sender": self.assistant,
                    # ↑ 内层对话由 assistant 发起第一条消息
                    "recipient": self.assistant_shadow,
                    # ↑ Shadow Agent 接收消息并处理
                    "message": instruction_message,
                    # ↑ 来自 utils.py 的函数（非静态字符串！）：
                    #   AutoGen 在触发时调用 instruction_message(recipient, messages, sender, config)
                    #   从 assistant 的最后一条消息中提取 .txt 文件路径，
                    #   读取文件内容 + 追加 "Reply TERMINATE at the end of your response."
                    #   作为嵌套聊天的初始消息发送给 Shadow Agent。
                    "summary_method": "last_msg",
                    # ↑ 嵌套聊天结束后，取最后一条消息作为摘要返回给外层对话。
                    #   因为 Shadow Agent 的最后一条消息应该是分析结果 + TERMINATE，
                    #   所以外层收到的摘要就是完整的分析文本。
                    "max_turns": 2,
                    # ↑ 嵌套聊天最多 2 轮：
                    #   Turn 1: assistant → Shadow（发送分析指令）
                    #   Turn 2: Shadow → assistant（返回分析结果 + TERMINATE）
                    #   如果 Shadow 在 Turn 2 仍未终止，第 3 轮开始前会被强制结束。
                    "silent": True,
                    # ↑ 静默模式：嵌套聊天的详细日志不打印到控制台，
                    #   避免在复杂分析场景中日志噪音过大。
                }
            ],
            trigger=instruction_trigger,
            # ↑ 来自 utils.py，检测 assistant 的消息中是否包含
            #   "instruction & resources saved to" 字符串。
            #   当 ReportAnalysisUtils 等方法保存了分析指令文件后触发。
        )


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                    多人 Agent 工作流 (Multi Assistant)                       ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

class MultiAssistantBase(ABC):
    """
    多人 Agent 工作流的抽象基类。

    架构：N 个 FinRobot + 1 个 UserProxyAgent
    多个 Agent 协作完成任务，UserProxy 负责执行工具代码。

    子类通过实现 _get_representative() 来决定协作模式：
      - MultiAssistant: GroupChat 自由讨论模式
      - MultiAssistantWithLeader: Leader 主导的任务分配模式
    """

    def __init__(
        self,
        group_config: str | dict,
        # group_config: 群组配置，格式取决于子类：
        #   MultiAssistant: {"name": "...", "agents": [...]}
        #   MultiAssistantWithLeader: {"leader": {...}, "agents": [...]}
        agent_configs: List[
            Dict[str, Any] | str | ConversableAgent
        ] = [],
        # agent_configs: 各 Agent 的配置列表。可以为空（从 group_config["agents"] 获取）
        #   每个元素可以是：字符串（agent_library 中的名称）、字典（自定义配置）、
        #   或已创建好的 ConversableAgent 实例
        llm_config: Dict[str, Any] = {},
        user_proxy: UserProxyAgent | None = None,
        # user_proxy: 可传入已有的 UserProxyAgent，不传则自动创建
        is_termination_msg=lambda x: x.get("content", "")
        and x.get("content", "").endswith("TERMINATE"),
        human_input_mode="NEVER",
        max_consecutive_auto_reply=10,
        code_execution_config={
            "work_dir": "coding",
            "use_docker": False,
        },
        **kwargs,
    ):
        self.group_config = group_config
        self.llm_config = llm_config
        # ── 创建或复用 UserProxyAgent ──
        if user_proxy is None:
            self.user_proxy = UserProxyAgent(
                name="User_Proxy",
                is_termination_msg=is_termination_msg,
                human_input_mode=human_input_mode,
                max_consecutive_auto_reply=max_consecutive_auto_reply,
                code_execution_config=code_execution_config,
                **kwargs,
            )
        else:
            self.user_proxy = user_proxy

        # ── 获取 Agent 配置列表 ──
        # 优先使用传入的 agent_configs，否则从 group_config 中取 "agents"
        self.agent_configs = agent_configs or group_config.get("agents", [])
        assert self.agent_configs, f"agent_configs is required."
        self.agents = []
        self._init_agents()  # 初始化所有 Agent
        self.representative = self._get_representative()  # 子类决定协作模式

    def _init_single_agent(self, agent_config):
        """
        根据配置创建单个 Agent。

        支持三种输入：
          1. ConversableAgent 实例 → 直接复用
          2. 字符串 → 从 agent_library.py 查表
          3. 字典 → 直接作为配置传入 FinRobot

        关键：每个 Agent 都绑定同一个 user_proxy 作为 executor，
        确保所有 Agent 的工具调用都在同一个 UserProxy 中执行。
        """
        if isinstance(agent_config, ConversableAgent):
            return agent_config
        else:
            return FinRobot(
                agent_config,
                llm_config=self.llm_config,
                proxy=self.user_proxy,  # 所有 Agent 共享同一个 executor
            )

    def _init_agents(self):
        """
        初始化所有 Agent，并处理重名问题。

        如果多个 Agent 配置有相同的 name/title，自动添加数字后缀：
          例如三个 "Analyst" → "Analyst_1", "Analyst_2", "Analyst_3"
        """
        agent_dict = defaultdict(list)
        for c in self.agent_configs:
            agent = self._init_single_agent(c)
            agent_dict[agent.name].append(agent)

        # 处理重名：唯一的名称保持不变，重复的名称添加数字后缀
        for name, agent_list in agent_dict.items():
            if len(agent_list) == 1:
                self.agents.append(agent_list[0])
                continue
            for idx, agent in enumerate(agent_list):
                agent._name = f"{name}_{idx+1}"
                self.agents.append(agent)

    @abstractmethod
    def _get_representative(self) -> ConversableAgent:
        """
        返回"代表 Agent"——用户消息的接收者。

        这是多 Agent 模式的核心抽象方法，子类通过不同的实现决定协作模式：
          - MultiAssistant._get_representative() → 返回 GroupChatManager
            （用户消息进入群聊，由 manager 按 speaker_selection_func 分发）
          - MultiAssistantWithLeader._get_representative() → 返回 Leader Agent
            （用户消息直达 Leader，Leader 通过 [AgentName] 指令分发任务）

        调用链：chat(message) → user_proxy.initiate_chat(self.representative, message)
        所以 self.representative 就是"第一个收到用户消息的 Agent"。
        """
        pass

    def chat(self, message: str, use_cache=False, **kwargs):
        """启动多人对话"""
        with Cache.disk() as cache:
            self.user_proxy.initiate_chat(
                self.representative,
                message=message,
                cache=cache if use_cache else None,
                **kwargs,
            )
        print("Current chat finished. Resetting agents ...")
        self.reset()

    def reset(self):
        """重置所有 Agent 的对话状态"""
        self.user_proxy.reset()
        self.representative.reset()
        for agent in self.agents:
            agent.reset()


class MultiAssistant(MultiAssistantBase):
    """
    Group Chat（群聊）协作模式。

    所有 Agent 在 AutoGen 的 GroupChat 中自由对话，由一个 GroupChatManager
    根据自定义的 speaker_selection_func 决定每一轮由谁发言。

    对话流转逻辑（custom_speaker_selection_func）：
      1. 第一轮：由 agents[0]（列表第一个 Agent）先发言
      2. User_Proxy 刚执行完工具 → 回到之前调用工具的 Agent 继续
      3. 某 Agent 调用了工具 或 说了 TERMINATE → User_Proxy 执行
      4. 其他情况 → 按 round_robin 轮流（排除 User_Proxy）
    """

    def _get_representative(self):

        def custom_speaker_selection_func(
            last_speaker: autogen.Agent, groupchat: autogen.GroupChat
        ):
            """
            自定义发言者选择函数。

            决定了 GroupChat 中每轮对话后下一个发言者是谁。
            这是多 Agent 协作的核心：发言顺序决定了任务如何被分解和执行。
            """
            messages = groupchat.messages
            if len(messages) <= 1:
                # 对话刚开始，让第一个 Agent 先发言
                return groupchat.agents[0]
            if last_speaker is self.user_proxy:
                # User_Proxy 刚执行完工具代码 → 回到之前调用工具的 Agent
                return groupchat.agent_by_name(messages[-2]["name"])
            elif "tool_calls" in messages[-1] or messages[-1]["content"].endswith(
                "TERMINATE"
            ):
                # Agent 调用了工具 或 说了 TERMINATE → 交给 User_Proxy 执行/处理
                return self.user_proxy
            else:
                # 普通消息 → 按 round_robin 轮转到下一个 Agent（排除 User_Proxy）
                return groupchat.next_agent(last_speaker, groupchat.agents[:-1])

        # ── 创建 GroupChat ──
        self.group_chat = GroupChat(
            self.agents + [self.user_proxy],
            # ↑ 所有 Agent + UserProxy 参与群聊
            messages=[],
            speaker_selection_method=custom_speaker_selection_func,
            send_introductions=True,
            # ↑ 开启自我介绍：每个 Agent 的 description 会在开始时发送给所有人
        )

        # ── 创建 GroupChatManager：管理群聊流程的 Agent ──
        manager_name = (self.group_config.get("name", "") + "_chat_manager").strip("_")
        manager = GroupChatManager(
            self.group_chat, name=manager_name, llm_config=self.llm_config
        )
        return manager


class MultiAssistantWithLeader(MultiAssistantBase):
    """
    Leader-Worker（领导-下属）协作模式。

    与 GroupChat 自由讨论不同，此模式有一个明确的 Leader Agent，
    Leader 通过嵌套聊天（nested chats）向各 Worker Agent 分派任务。

    配置结构（必须遵循以下格式）：
    {
        "leader": {
            "title": "Leader Title",
            "responsibilities": ["职责1", "职责2"]
        },
        "agents": [
            {
                "title": "Employee Title",
                "responsibilities": ["职责1", "职责2"]
            }, ...
        ]
    }

    工作流程：
      1. 用户消息发给 Leader
      2. Leader 回复中包含 "[Agent_Name] <指令>" 格式的任务分派
      3. order_trigger 检测到指令 → 触发嵌套聊天：User_Proxy → Worker Agent
      4. Worker Agent 执行任务，结果返回给 Leader
      5. Leader 检查结果，继续分派下一个任务或结束

    关键参数来源：
      - title: 来自 group_config 配置（用户在实验文件中自定义），在 agent_library.py 中不存在
      - responsibilities: 来自 group_config 配置，定义每个 Agent 的具体职责列表
      - group_desc: 在 _get_representative() 中动态生成，汇总所有下属 Agent 信息，
                    注入到 leader 配置中，让 Leader 了解自己的团队成员
    """

    def _get_representative(self):

        assert (
            "leader" in self.group_config and "agents" in self.group_config
        ), "Leader and Agents has to be explicitly defined in config."

        assert (
            self.agent_configs
        ), "At least one agent has to be defined in the group config."

        # ── 判断是否需要添加数字后缀 ──
        # 当所有 Agent 的 title 相同时（如三个 "Market Sentiment Analyst"），
        # 需要加后缀编号以便 Leader 区分。
        # 例如 investment_group.py 中三个组的 Analyst 各有相同的 title，
        # 会被命名为 Analyst_1, Analyst_2, Analyst_3。
        need_suffix = (
            len(set([c["title"] for c in self.agent_configs if isinstance(c, dict)]))
            == 1
        )

        # ── 构建 group_desc：所有下属 Agent 的汇总信息 ──
        # 这是 Leader-Worker 模式的关键数据结构。group_desc 被注入 Leader 的
        # system_message，让 Leader "看到"每个下属是谁、能做什么。
        # 如果不构建这个结构，Leader 不知道有哪些下属可用，无法正确分派任务。
        #
        # 输出格式示例：
        #   Name: Market_Sentiment_Analyst_1
        #   Responsibility:
        #    - Track and interpret market trends and news.
        #    - Analyze social media and news articles for market sentiment.
        #
        #   Name: Risk_Analyst_1
        #   Responsibility:
        #    - Assess financial and operational risks.
        #    - ...
        group_desc = ""
        for i, c in enumerate(self.agent_configs):
            if isinstance(c, ConversableAgent):
                # 如果已经是 Agent 实例（如 portfolio_optimization.py 中
                # 直接传入已创建的 sub-group representative），
                # 直接取它的 description 属性
                group_desc += c.description + "\n\n"
            else:
                name = c["title"] if "title" in c else c.get("name", "")
                name = name.replace(" ", "_").strip() + (
                    f"_{i+1}" if need_suffix else ""
                )
                responsibilities = (
                    "\n".join([f" - {r}" for r in c.get("responsibilities", [])]),
                )
                group_desc += f"Name: {name}\nResponsibility:\n{responsibilities}\n\n"

        # ── 配置 Leader ──
        # 将 group_desc 注入到 leader_config 中。
        # 当 FinRobot(leader_config) 初始化时，_preprocess_config 检测到
        # config 中有 "group_desc" key，会自动调用 leader_system_message 模板
        # 生成 Leader 的 system prompt。
        self.leader_config = self.group_config["leader"]
        self.leader_config["group_desc"] = group_desc.strip()

        # ── 初始化 Leader Agent ──
        leader = self._init_single_agent(self.leader_config)

        # ── 注册 Leader → Worker 的嵌套聊天 ──
        # 这是 Leader-Worker 模式的核心控制循环。对每个 Worker Agent，
        # 在 user_proxy 上注册一个嵌套聊天 trigger。
        #
        # 完整的任务分派流程（以一次 Leader → Worker 交互为例）：
        #
        # 1. user_proxy.initiate_chat(leader, message="分析 PDD 的投资价值")
        #    → 用户消息直达 Leader
        #
        # 2. Leader (LLM) 分析任务，决定先让 Market_Sentiment_Analyst_1 分析市场情绪
        #    → Leader 生成消息：
        #      "I'll start with market sentiment analysis.
        #       [Market_Sentiment_Analyst_1] Analyze recent market sentiment
        #       for PDD based on news and social media. Provide a sentiment score."
        #
        # 3. AutoGen 检查该消息是否满足 trigger 条件
        #    → order_trigger(sender="Chief_Investment_Officer", pattern="[Market_Sentiment_Analyst_1]")
        #    → 消息中包含 "[Market_Sentiment_Analyst_1]" → 返回 True → 触发嵌套聊天
        #
        # 4. 嵌套聊天启动（user_proxy ↔ Market_Sentiment_Analyst_1）：
        #    → order_message 从 Leader 消息中提取指令内容
        #    → 用 order_template 包装后发送给 Worker
        #    → Worker 调用工具、执行分析、返回结果
        #    → 嵌套聊天最多 10 轮，Worker 连续 3 次自动回复后强制终止
        #
        # 5. 嵌套聊天结束
        #    → summary_method="reflection_with_llm" 对 Worker 的完整对话做 LLM 提炼
        #    → 提炼后的摘要注入回外层对话
        #
        # 6. Leader 收到 Worker 的结果摘要
        #    → Leader 检查结果是否满足要求
        #    → 满足 → 分派下一个任务给另一个 Worker
        #    → 不满足 → 重新分派给同一个 Worker 或调整指令
        #
        # 7. 循环步骤 2-6，直到 Leader 认为所有子任务完成
        #    → Leader 回复 "TERMINATE"
        #    → 外层对话结束
        for agent in self.agents:
            self.user_proxy.register_nested_chats(
                [
                    {
                        "sender": self.user_proxy,
                        "recipient": agent,
                        "message": partial(order_message, agent.name),
                        # ↑ 使用 functools.partial 预设 agent.name 参数：
                        #   触发时 AutoGen 调用 order_message(pattern=agent.name, recipient, messages, sender, config)
                        #   从 Leader 消息中用正则提取 "[agent.name] 指令内容"
                        #   再用 order_template 包装为完整任务描述
                        "summary_method": "reflection_with_llm",
                        # ↑ 为什么这里用 "reflection_with_llm" 而非 "last_msg"？
                        #   Worker 的对话可能包含多轮工具调用和中间分析，
                        #   "last_msg" 只取最后一条消息，可能丢失关键中间结论；
                        #   "reflection_with_llm" 会用一个额外的 LLM 调用对整个
                        #   嵌套对话做反思总结，提炼出最核心的结论交给 Leader。
                        #   代价是增加一次 LLM 调用的成本。
                        "max_turns": 10,
                        # ↑ 子任务最多 10 轮往返。对于需要多次工具调用的复杂分析
                        #   （如先查股价→再算比率→再画图），10 轮是合理上限。
                        "max_consecutive_auto_reply": 3,
                        # ↑ Worker 连续 3 次自动回复（无人类介入）后强制终止。
                        #   比外层默认的 10 更严格，因为子任务应该快速完成，
                        #   Worker 陷入死循环时快速切断，让 Leader 重新评估。
                    }
                ],
                trigger=partial(
                    order_trigger, name=leader.name, pattern=f"[{agent.name}]"
                ),
                # ↑ 触发条件：(1) sender 是 leader，(2) 消息中包含 "[{agent.name}]"
                #   两个条件同时满足时触发嵌套聊天。
                #   为什么条件 (1) 必要？防止 Worker 之间的消息被误触发。
            )
        return leader
        # ↑ 返回 leader 作为 representative。
        #   用户消息 → user_proxy.initiate_chat(leader, message)
        #   → Leader 开始分析并分派任务。
