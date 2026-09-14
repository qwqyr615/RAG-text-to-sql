"""Agent 本地验证入口。

支持：
- 普通自然语言 SQL 查询/统计分析：Text2SQLAgent
- 包含“报告”的问题：自动生成 Markdown 报告
- 包含“异常/离群”的问题：Isolation Forest 异常检测
- 包含“预测/回归”的问题：LinearRegression 简单回归建模

使用方式：
    1. 在 agent 目录下创建 .env（参考 .env.example），填入大模型配置。
    2. 运行：python main.py
    3. 输入自然语言问题
"""

from agents.enterprise_agent import EnterpriseAgent


def print_result(result) -> None:
    print("\n===== 分析结果 =====")

    if not result.success:
        print(f"错误: {result.error}")
        return

    print(f"文字结论：\n{result.analysis_text}\n")

    if result.prompt_usage:
        usage = result.prompt_usage
        print("===== Prompt 段用量 =====")
        print(
            f"合计 {usage.get('used_chars', 0)}/{usage.get('total_budget', 0)} 字符"
        )
        for section in usage.get("sections", []):
            line = f"  {section['name']}: {section['used_chars']}/{section['max_chars']}"
            if section.get("dropped_items"):
                line += f" 丢弃{section['dropped_items']}项"
            if section.get("truncated"):
                line += " 已截断"
            if section.get("error"):
                line += f" 失败({section['error']})"
            print(line)
        print()

    if result.rag_context:
        print("===== RAG 检索示例 =====")
        print(result.rag_context)
        print()

    if result.report:
        print("===== 分析报告 =====")
        print(result.report)
        print()

    if result.sql:
        print("===== 生成的 SQL =====")
        print(result.sql)
        print()

    if result.sql_error:
        print("===== 取数失败（分析结论已保留）=====")
        print(result.sql_error)
        print()

    if result.columns:
        print("===== 查询结果 =====")
        print("列名:", result.columns)
        for row in result.rows[:20]:
            print(row)
        if len(result.rows) > 20:
            print(f"... 共 {len(result.rows)} 行，仅展示前 20 行")


def print_model_result(res: dict) -> None:
    print("\n===== 分析结果 =====")
    for key, value in res.items():
        if key == "records":
            print(f"{key}:")
            for record in value[:10]:
                print(record)
        elif key == "sample_predictions":
            print(f"{key}:")
            for record in value[:10]:
                print(record)
        elif key == "coefficients":
            print("coefficients:")
            for feat, coef in value.items():
                print(f"  {feat}: {coef}")
        else:
            print(f"{key}: {value}")


def main() -> None:
    agent = EnterpriseAgent()
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

        response = agent.handle(question)

        if response["type"] == "agent":
            print_result(response["result"])
        elif response["type"] == "model":
            print_model_result(response["result"])
        else:
            print(f"错误: {response['result']}")


if __name__ == "__main__":
    main()