"""LOCAL DEV STUB of marketplace_sdk. NOT the real SDK (backend owns sdk/python).
Implements the Section 4 names so agents run locally: Agent, Context, BudgetExceeded.
LLM_MODE=fake swaps the `openai` module for a deterministic fake (no API key)."""
import os, sys, types

class BudgetExceeded(Exception): pass
class Cancelled(Exception): pass

class _Task:
    def __init__(self, text="", files=None): self.text, self.files = text, files or []

class Context:
    def __init__(self, task, workspace, answers=None, budget=1000.0, interactive=False):
        self.task, self.workspace, self.answers = task, workspace, list(answers or [])
        self.budget, self.interactive = budget, interactive
        self.events, self.result, self.result_files, self.asks = [], None, [], 0
        os.makedirs(workspace, exist_ok=True)
    def _ev(self, kind, **kw): self.events.append({"type": kind, **kw})
    def message(self, text): self._ev("message", text=text); print(f"[message] {text}")
    def progress(self, label, step, total):
        self._ev("progress", label=label, step=step, total=total); print(f"[progress {step}/{total}] {label}")
    def ask_user(self, question, options=None):
        self.asks += 1; self._ev("ask_user", question=question, options=options)
        if self.answers: ans = self.answers.pop(0)
        elif self.interactive: ans = input(f"[ask] {question} {options or ''} > ")
        else: ans = (options or [""])[0]
        print(f"[ask_user] {question} -> {ans}"); return ans
    def confirm(self, action):
        self._ev("confirm", action=action)
        if self.answers: a = str(self.answers.pop(0)).lower() in ("yes", "true", "y")
        elif self.interactive: a = input(f"[confirm] {action} (y/n) > ").lower().startswith("y")
        else: a = True
        print(f"[confirm] {action} -> {a}"); return a
    def read_file(self, name): return open(os.path.join(self.workspace, name), "rb").read()
    def write_file(self, name, data):
        mode = "wb" if isinstance(data, bytes) else "w"
        with open(os.path.join(self.workspace, name), mode) as f: f.write(data)
    def budget_remaining(self): return self.budget
    def return_result(self, text, files=None): self.result, self.result_files = text, files or []; print(f"[result] {text}")
    def log(self, text): print(f"[log] {text}", file=sys.stderr)

LAST_AGENT = None
class Agent:
    def __init__(self):
        global LAST_AGENT; self.handler = None; LAST_AGENT = self
    def on_task(self, fn): self.handler = fn; return fn
    def serve(self):
        if os.environ.get("MP_STUB_RUN"): return          # test runner drives the handler
        text = sys.argv[1] if len(sys.argv) > 1 else input("task> ")
        ws = os.path.join(os.getcwd(), ".workspace"); os.makedirs(ws, exist_ok=True)
        for f in sys.argv[2:]:
            open(os.path.join(ws, os.path.basename(f)), "wb").write(open(f, "rb").read())
        ctx = Context(_Task(text, [os.path.basename(f) for f in sys.argv[2:]]), ws, interactive=True)
        self.handler(ctx); print("files:", ctx.result_files, "in", ws)

# ---------------- fake openai (LLM_MODE=fake) ----------------
def _fake_reply(p):
    if "Context review" in p: return "NONE"
    if "three itinerary outlines" in p:
        return ("OPTION A: Classic Highlights - the famous sights at a steady pace\n"
                "OPTION B: Local Flavour - food, markets and neighbourhoods\n"
                "OPTION C: Off the Beaten Path - quieter spots and day trips")
    if "Parse the trip request" in p: return "not json"
    if "day-by-day" in p: return "## Day 1\n- Morning: arrival and a walk\n- Evening: dinner\n\n## Day 2\n- Full-day activity\n\n## Day 3\n- Relaxed morning, departure"
    if "Extract" in p: return "cannot parse"
    return "[fake model reply]"

def _install_fake_openai():
    class _Msg:
        def __init__(s, c): s.content = c
    class _Choice:
        def __init__(s, c): s.message = _Msg(c)
    class _Resp:
        def __init__(s, c): s.choices = [_Choice(c)]
    class _Comp:
        def create(self, model=None, messages=None, **kw): return _Resp(_fake_reply(messages[-1]["content"]))
    class _Chat: completions = _Comp()
    class OpenAI:
        def __init__(self, *a, **k): self.chat = _Chat()
    m = types.ModuleType("openai"); m.OpenAI = OpenAI; sys.modules["openai"] = m

if os.environ.get("LLM_MODE") == "fake": _install_fake_openai()
