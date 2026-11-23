"""Marketplace SDK: wrap any Python agent in the platform runtime contract.

    from marketplace_sdk import Agent, Context
    agent = Agent()

    @agent.on_task
    def run(ctx: Context):
        answer = ctx.ask_user("What is your budget?")
        text = ctx.llm([{"role": "user", "content": ctx.task.text + answer}], model="fast")
        ctx.write_file("out.md", text)
        ctx.return_result("Done", files=["out.md"])

    agent.serve()
"""
from .agent import Agent, BudgetExceeded, Cancelled, Context, Task, chat

__all__ = ["Agent", "Context", "Task", "BudgetExceeded", "Cancelled", "chat"]
__version__ = "0.1.0"
