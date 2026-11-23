"""Reference agent: multi-step, asks one question, calls the LLM proxy twice, writes a file."""
from marketplace_sdk import Agent, BudgetExceeded, Context

agent = Agent("echo-agent")


@agent.on_task
def run(ctx: Context):
    ctx.progress("Reading your request", step=1, total=3)
    detail = ""
    if any(w in ctx.task.text.lower() for w in ("plan", "trip", "budget")):
        detail = ctx.ask_user("Any constraints I should know (budget, dates)?")
        ctx.message(f"Thanks, noted: {detail}")
    ctx.progress("Drafting", step=2, total=3)
    try:
        draft = ctx.llm([{"role": "system", "content": "You are a concise assistant."},
                         {"role": "user", "content": f"{ctx.task.text}\nConstraints: {detail}"}], model="fast")
        ctx.log(f"draft length {len(draft)}")
        ctx.progress("Polishing", step=3, total=3)
        final = ctx.llm([{"role": "user", "content": f"Tighten this into key points:\n{draft}"}], model="fast")
    except BudgetExceeded:
        ctx.return_result("Budget reached; here is a partial answer.")
        return
    ctx.write_file("result.md", f"# Result\n\n{final}\n")
    ctx.return_result(final[:500], files=["result.md"])


if __name__ == "__main__":
    agent.serve()
