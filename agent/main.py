"""Agent 本地验证入口。

使用方式：
    1. 在 agent 目录下创建 .env（参考 .env.example），填入大模型配置。
    2. 运行：python main.py
    3. 输入自然语言问题，例如：分析各工序的良率
"""

from agents.text2sql_agent import Text2SQLAgent
from schemas.agent_io import AgentQuestion


def main() -> None:
    agent = Text2SQLAgent()
    print("企业数据底座智能问析 Agent 已启动。")
    print("输入 exit / quit 退出。")

    while True:
        try:
            question = input("\n请输入分析问题: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n退出")
            break

        if not question:
            continue
        if question.lower() in {"exit", "quit", "q"}:
            print("退出")
            break

        result = agent.ask(AgentQuestion(question=question))
        print("\n===== 分析结果 =====")
        if result.success:
            print(result.analysis_text)
        else:
            print(f"错误: {result.error}")


if __name__ == "__main__":
    main()