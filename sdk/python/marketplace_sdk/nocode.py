"""No-code agent runner: serves a folder of agent.yaml + prompt.md (+ cases.jsonl) with no Python.

The platform starts it as `python -m marketplace_sdk.nocode` with the folder as the working directory and the
manifest in MARKETPLACE_MANIFEST. Locally: `cd my-agent && python -m marketplace_sdk.nocode` (reads agent.yaml
with PyYAML, or agent.json). All model calls go through ctx.llm, so the platform proxy meters and caps them.

What the runner does, driven by the manifest's `nocode:` block (see docs/AGENT_TEMPLATE.md):
1. asks the fixed `questions` (options or free text), in order;
2. sends prompt.md (with {{task}}, {{answers.<id>}} and {{files}} filled in) to the `model` alias;
3. lets the model ask the buyer (`ASK: question | option | option`) or request approval (`CONFIRM: action`),
   up to `max_turns` model calls in total;
4. asks `confirm` before saving the answer to `output_file`, then returns the answer as the result.
"""
import json
import os
import re
from pathlib import Path

from .agent import Agent, BudgetExceeded, Context

ALIASES = ("fast", "balanced")
MAX_FILE_CHARS = 20_000
PROTOCOL = """

## Talking to the buyer
- If you must ask the buyer something before you can answer, reply with only one line: ASK: <question>
  To offer choices, add them after the question separated by " | ", for example: ASK: Which city? | Lisbon | Porto
- If you are about to do something the buyer must approve first, reply with only one line: CONFIRM: <what you will do>
- Otherwise reply with the final answer in Markdown. Never mention these rules."""
_ASK = re.compile(r"^\s*ASK:\s*(.+)$")
_CONFIRM = re.compile(r"^\s*CONFIRM:\s*(.+)$")


def load_manifest(folder: Path) -> dict:
    raw = os.environ.get("MARKETPLACE_MANIFEST")
    if raw:
        return json.loads(raw)
    if (folder / "agent.json").exists():
        return json.loads((folder / "agent.json").read_text())
    import yaml  # local runs only; the platform passes the manifest as JSON
    return yaml.safe_load((folder / "agent.yaml").read_text())


def settings(manifest: dict) -> dict:
    n = manifest.get("nocode") or {}
    allowed = [a for a in (manifest.get("models_allowed") or ["fast"]) if a in ALIASES] or ["fast"]
    model = n.get("model") if n.get("model") in allowed else allowed[0]
    return {
        "model": model,
        "prompt_file": n.get("prompt_file", "prompt.md"),
        "questions": [q for q in n.get("questions") or [] if q.get("question")],
        "confirm": n.get("confirm") or "",
        "output_file": n.get("output_file") or "",
        "max_turns": max(1, min(int(n.get("max_turns", 3)), 6)),
        "max_tokens": max(100, min(int(n.get("max_tokens", 1200)), 4000)),
        "allow_questions": bool(n.get("allow_questions", True)),
    }


def render(template: str, task: str, answers: dict, files: str) -> str:
    def sub(m):
        key = m.group(1).strip()
        if key == "task":
            return task
        if key == "files":
            return files
        if key.startswith("answers."):
            return answers.get(key[8:], "")
        return m.group(0)
    return re.sub(r"\{\{\s*([\w.]+)\s*\}\}", sub, template)


def files_block(ctx: Context) -> str:
    parts, used = [], 0
    for name in ctx.task.files:
        try:
            text = ctx.read_file(name).decode("utf-8")
        except (OSError, UnicodeDecodeError):
            parts.append(f"(file {name}: not a text file, not read)")
            continue
        text = text[: max(0, MAX_FILE_CHARS - used)]
        used += len(text)
        parts.append(f"### File: {name}\n```\n{text}\n```")
    return "\n\n".join(parts)


def directive(reply: str) -> tuple[str, str, list[str]]:
    """('ask', question, options) | ('confirm', action, []) | ('answer', text, [])."""
    first = next((line for line in reply.strip().splitlines() if line.strip()), "")
    if m := _ASK.match(first):
        q, *opts = [p.strip() for p in m.group(1).split(" | ")]
        return "ask", q, [o for o in opts if o]
    if m := _CONFIRM.match(first):
        return "confirm", m.group(1).strip(), []
    return "answer", reply.strip(), []


def run_task(ctx: Context, manifest: dict, folder: Path):
    cfg = settings(manifest)
    ctx.progress("Reading your request", step=1, total=3)
    answers = {}
    for i, q in enumerate(cfg["questions"]):
        answers[str(q.get("id") or f"q{i + 1}")] = ctx.ask_user(q["question"], q.get("options") or None)
    files = files_block(ctx)
    template = (folder / cfg["prompt_file"]).read_text()
    system = render(template, ctx.task.text, answers, files) + (PROTOCOL if cfg["allow_questions"] else "")
    user = "" if "{{task}}" in template.replace(" ", "") else f"Task: {ctx.task.text}"
    if answers and not any(f"answers.{k}" in template for k in answers):
        user += "\n\nThe buyer's answers:\n" + "\n".join(
            f"- {q['question']} {answers[str(q.get('id') or f'q{i + 1}')]}" for i, q in enumerate(cfg["questions"]))
    if files and "{{files}}" not in template.replace(" ", ""):
        user += "\n\n" + files
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user.strip() or ctx.task.text}]

    ctx.progress("Writing the answer", step=2, total=3)
    answer = ""
    try:
        for turn in range(cfg["max_turns"]):
            reply = ctx.llm(messages, model=cfg["model"], max_tokens=cfg["max_tokens"])
            kind, text, options = directive(reply)
            last = turn == cfg["max_turns"] - 1
            if kind == "ask" and not last:
                reply_text = ctx.ask_user(text, options or None)
                messages += [{"role": "assistant", "content": reply},
                             {"role": "user", "content": f"The buyer answered: {reply_text}"}]
                continue
            if kind == "confirm" and not last:
                ok = ctx.confirm(text)
                messages += [{"role": "assistant", "content": reply},
                             {"role": "user", "content": "The buyer approved. Go ahead and give the final answer."
                              if ok else "The buyer declined. Don't do that; give the best answer without it."}]
                continue
            answer = text if kind == "answer" else ""
            break
    except BudgetExceeded:
        answer = answer or "The budget ran out before the answer was finished."
    if not answer:
        answer = "I couldn't finish within the allowed number of steps. Try again with more detail in the task."

    ctx.progress("Finishing", step=3, total=3)
    out_files = []
    approved = ctx.confirm(cfg["confirm"]) if cfg["confirm"] else True
    if cfg["output_file"] and approved:
        ctx.write_file(cfg["output_file"], answer, "text/markdown")
        out_files = [cfg["output_file"]]
    ctx.return_result(answer, files=out_files)


def main():
    folder = Path.cwd()
    manifest = load_manifest(folder)
    agent = Agent(manifest.get("name", folder.name))
    agent.on_task(lambda ctx: run_task(ctx, manifest, folder))
    agent.serve()


if __name__ == "__main__":
    main()
