"""
工具注册模块 (Tool Registration Module)
========================================

本模块是 Agent 工具系统的核心，负责将业务函数/类注册为 AutoGen Agent 可调用的工具。
它充当"翻译官"角色：把项目中各种数据获取、分析、报告生成函数，转化为 LLM 能理解和
调用的 function calling 工具。

核心流程：
  agent_library.py 定义每个 Agent 的 toolkits 列表
      → workflow.py 的 FinRobot.__init__ 读取 toolkits
          → register_proxy() 调用本模块的 register_toolkits()
              → 将每个函数/类方法注册到 AutoGen 的 ConversableAgent 上

支持的 toolkits 元素类型：
  1. 可调用对象 (函数/静态方法) → 直接注册为单个工具
  2. 类 (type)                 → 遍历类的所有公开方法，逐个注册
  3. 字典                       → {"function": ..., "name": ..., "description": ...}
"""

from autogen import register_function, ConversableAgent
from .data_source import *
from .functional.coding import CodingUtils

from typing import List, Callable
from functools import wraps
from pandas import DataFrame


def stringify_output(func):
    """
    装饰器：将函数的返回值统一转换为字符串。

    AutoGen 的 function calling 机制要求工具函数的返回值必须是字符串类型，
    但项目中很多数据源函数返回的是 pandas DataFrame。本装饰器在函数执行后
    自动完成类型转换：
      - DataFrame → df.to_string() （保留表格结构，便于 LLM 阅读）
      - 其他类型 → str(result)

    使用示例：
      @stringify_output
      def get_price(ticker): ...
      # 即使 get_price 返回 DataFrame，register_function 收到的也是字符串版本

    参数:
      func: 被装饰的原始函数

    返回:
      wrapper: 包装后的函数，返回值一定是 str
    """
    @wraps(func)  # 保留原函数的 __name__, __doc__ 等元信息（register_function 依赖这些）
    def wrapper(*args, **kwargs):
        result = func(*args, **kwargs)
        if isinstance(result, DataFrame):
            return result.to_string()
        else:
            return str(result)

    return wrapper


def register_toolkits(
    config: List[dict | Callable | type],
    caller: ConversableAgent,
    executor: ConversableAgent,
    **kwargs
):
    """
    根据配置列表，将工具批量注册到 AutoGen Agent 上。

    这是工具注册的入口函数，agent_library.py 中定义的 toolkits 列表最终通过此函数
    注册。它支持三种元素类型，采用统一的分派逻辑：

      ┌─ isinstance(tool, type) ──→ register_tookits_from_cls()  遍历类方法批量注册
      │
      └─ callable(tool) ──→ {"function": tool} ──→ register_function() 直接注册

    参数:
      config: 工具配置列表，来自 agent_library.py 中每个 Agent 的 "toolkits" 字段。
              元素可以是：
              - 函数/静态方法，如 FMPUtils.get_sec_report
              - 类，如 ReportAnalysisUtils（其所有公开方法会被逐一注册）
              - 字典，如 {"function": my_func, "name": "别名", "description": "说明"}
      caller: 发起调用的 Agent（通常是 AssistantAgent / FinRobot）。
              LLM 在它的上下文中"看到"这些工具描述，并决定是否调用。
      executor: 执行调用的 Agent（通常是 UserProxyAgent）。
                工具函数的实际代码在 executor 的环境中执行。
      **kwargs: 传递给 register_tookits_from_cls 的额外参数（如 include_private）
    """

    for tool in config:

        # ── 分支 1：tool 是一个类（如 ReportAnalysisUtils）──
        # 调用 register_tookits_from_cls 遍历该类的所有公开方法并注册
        if isinstance(tool, type):
            register_tookits_from_cls(caller, executor, tool, **kwargs)
            continue

        # ── 分支 2：tool 是函数/可调用对象 或 字典配置 ──
        # 如果是可调用对象（函数/静态方法），包装为统一字典格式
        tool_dict = {"function": tool} if callable(tool) else tool

        # 安全检查：确保字典中包含有效的可调用函数
        if "function" not in tool_dict or not callable(tool_dict["function"]):
            raise ValueError(
                "Function not found in tool configuration or not callable."
            )

        # 提取工具注册所需的三个要素
        tool_function = tool_dict["function"]          # 实际要执行的函数
        name = tool_dict.get("name", tool_function.__name__)       # 工具名称（LLM 通过 name 区分不同工具）
        description = tool_dict.get("description", tool_function.__doc__)  # 工具描述（LLM 根据描述决定何时调用该工具）

        # 调用 AutoGen 的 register_function，将函数注册为 Agent 可调用的工具
        # stringify_output 确保返回值始终为字符串
        register_function(
            stringify_output(tool_function),
            caller=caller,
            executor=executor,
            name=name,
            description=description,
        )


def register_code_writing(caller: ConversableAgent, executor: ConversableAgent):
    """
    注册代码编写相关工具（文件系统操作）。

    这是 register_toolkits 的便捷封装，将 CodingUtils 中的四个文件操作方法
    以自定义名称注册。与直接使用 register_toolkits 的区别在于：
      - 原始方法名被替换为更直观的别名（如 list_dir → list_files）
      - 提供更明确的 description，帮助 LLM 正确选择工具

    注册的四个工具：
      list_files           → CodingUtils.list_dir      列出目录内容
      see_file             → CodingUtils.see_file       查看文件内容
      modify_code          → CodingUtils.modify_code    修改已有代码
      create_file_with_code → CodingUtils.create_file_with_code  创建新文件

    参数:
      caller: 调用方 Agent
      executor: 执行方 Agent
    """

    register_toolkits(
        [
            {
                "function": CodingUtils.list_dir,
                "name": "list_files",
                "description": "List files in a directory.",
            },
            {
                "function": CodingUtils.see_file,
                "name": "see_file",
                "description": "Check the contents of a chosen file.",
            },
            {
                "function": CodingUtils.modify_code,
                "name": "modify_code",
                "description": "Replace old piece of code with new one.",
            },
            {
                "function": CodingUtils.create_file_with_code,
                "name": "create_file_with_code",
                "description": "Create a new file with provided code.",
            },
        ],
        caller,
        executor,
    )


def register_tookits_from_cls(
    caller: ConversableAgent,
    executor: ConversableAgent,
    cls: type,
    include_private: bool = False,
):
    """
    将类的所有公开方法批量注册为 Agent 工具。

    这是支持 agent_library.py 中 toolkits 可以直接写类名（如 ReportAnalysisUtils）
    的关键函数。它通过反射（dir + getattr）自动发现类中所有可调用的方法，
    然后将它们逐一注册。

    典型应用场景：
      - ReportAnalysisUtils 包含 analyze_income_stmt, analyze_balance_sheet 等实例方法
        → 仅需将 ReportAnalysisUtils 放入 toolkits 列表
        → 本函数自动提取所有分析方法并注册
      - 新增方法后无需修改 agent_library.py，自动生效

    过滤规则（include_private=False，默认）：
      排除：
        - 双下划线方法（__init__, __str__ 等 Python 内置方法）
        - 单下划线开头的方法（_helper 等私有/保护方法）
      保留：
        - 所有公开方法（如 analyze_income_stmt, plot_chart 等）

    参数:
      caller: 调用方 Agent
      executor: 执行方 Agent
      cls: 要注册的类，其公开方法将被提取为工具
      include_private: 是否包含单下划线开头的"私有"方法（默认 False 不包含）
    """
    if include_private:
        # 包含私有方法：排除双下划线（__xxx__），但保留单下划线（_xxx）
        funcs = [
            func
            for func in dir(cls)
            if callable(getattr(cls, func)) and not func.startswith("__")
        ]
    else:
        # 默认：只保留公开方法，排除 __xxx__ 和 _xxx
        funcs = [
            func
            for func in dir(cls)
            if callable(getattr(cls, func))
            and not func.startswith("__")
            and not func.startswith("_")
        ]
    # 将方法名列表转为实际函数对象列表，然后递归调用 register_toolkits 完成注册
    register_toolkits([getattr(cls, func) for func in funcs], caller, executor)
