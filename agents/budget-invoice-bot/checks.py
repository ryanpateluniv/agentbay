"""Plain-code invoice parsing and checks (no model needed). Shared with budget-invoice-bot (copy)."""
import re
from datetime import date

TOL = 0.011
RISK_ORDER = {"High": 0, "Medium": 1, "Low": 2}

def money(v):
    if v is None or v == "": return None
    if isinstance(v, (int, float)): return float(v)
    s = re.sub(r"[^\d.\-]", "", str(v).replace(",", ""))
    try: return float(s)
    except ValueError: return None

def looks_like_invoice(text):
    return bool(re.search(r"invoice|subtotal|\btotal\b", text, re.I) and re.search(r"\d", text))

_N = r"[£$€]?\s*([\d,]+(?:\.\d+)?)"
def parse_invoice_text(text):
    """Regex fallback parser for the common 'Label: value' + 'n. desc | qty | unit | amount' layout."""
    def f(p):
        m = re.search(p, text, re.I | re.M); return m.group(1).strip() if m else None
    items = []
    for m in re.finditer(r"^\s*\d+[.)]\s*(.+?)\s*\|\s*qty\s*([\d.]+)\s*\|\s*unit\s*" + _N + r"\s*\|\s*" + _N, text, re.I | re.M):
        items.append({"description": m.group(1), "qty": money(m.group(2)), "unit_price": money(m.group(3)), "amount": money(m.group(4))})
    tm = re.search(r"^Tax\s*(?:\((\d+(?:\.\d+)?)\s*%\))?\s*:\s*" + _N, text, re.I | re.M)
    return {
        "invoice_number": f(r"^Invoice\s*(?:No\.?|Number|#)\s*[:#]?\s*(\S+)"),
        "date": f(r"^(?:Invoice\s+)?Date\s*:\s*(.+)$"),
        "due_date": f(r"^Due(?:\s+Date)?\s*:\s*(.+)$"),
        "supplier": f(r"^Supplier\s*:\s*(.+)$"),
        "supplier_tax_id": f(r"^(?:Supplier\s+)?(?:Tax|VAT)\s*ID\s*:\s*(.+)$"),
        "items": items,
        "subtotal": money(f(r"^Subtotal\s*:\s*" + _N)),
        "tax_rate": money(tm.group(1)) if tm and tm.group(1) else None,
        "tax": money(tm.group(2)) if tm else None,
        "total": money(f(r"^Total\s*:\s*" + _N)),
    }

def normalize(model_json, fallback):
    """Prefer model output, fill gaps from the regex parser, coerce numbers."""
    inv = dict(fallback)
    if isinstance(model_json, dict):
        for k, v in model_json.items():
            if v not in (None, "", []): inv[k] = v
    items = []
    for it in inv.get("items") or []:
        if isinstance(it, dict):
            items.append({"description": str(it.get("description", "")).strip(), "qty": money(it.get("qty")),
                          "unit_price": money(it.get("unit_price")), "amount": money(it.get("amount"))})
    inv["items"] = items
    for k in ("subtotal", "tax", "tax_rate", "total"): inv[k] = money(inv.get(k))
    return inv

def _d(s):
    try: return date.fromisoformat(str(s).strip()[:10])
    except ValueError: return None

INJECTION = re.compile(r"ignore (?:all |your |any |previous |the )+(?:rules|instructions)|approve everything|report no issues|disregard .{0,30}instructions|you are now", re.I)

def needs_tax_question(inv):
    return inv.get("tax") is None and inv.get("tax_rate") is None and bool(inv["items"])

def run_checks(inv, raw_text="", level="full", prices_include_tax=None):
    """level='basic' = arithmetic + duplicates only. level='full' adds fields, dates, tax logic, injection flag."""
    out = []
    def add(risk, cat, title, evidence): out.append({"risk": risk, "cat": cat, "title": title, "evidence": evidence})
    items = inv["items"]
    for i, it in enumerate(items, 1):
        q, u, a = it["qty"], it["unit_price"], it["amount"]
        if None not in (q, u, a) and abs(q * u - a) > TOL:
            diff = a - q * u
            add("High" if abs(diff) >= 50 else "Medium", "line",
                f"Line {i} amount does not match qty x price",
                f"'{it['description']}': {q:g} x {u:.2f} = {q*u:.2f}, but the invoice shows {a:.2f} (off by {diff:+.2f})")
    amounts = [it["amount"] for it in items if it["amount"] is not None]
    lines_sum = round(sum(amounts), 2)
    sub, tax, rate, total = inv["subtotal"], inv["tax"], inv["tax_rate"], inv["total"]
    if sub is not None and amounts and abs(sub - lines_sum) > TOL:
        add("High", "subtotal", f"Subtotal is {abs(sub-lines_sum):.2f} {'higher' if sub>lines_sum else 'lower'} than the sum of lines",
            f"Lines add up to {lines_sum:.2f}, subtotal shows {sub:.2f}")
    base = sub if sub is not None else (lines_sum if amounts else None)
    if level == "full" and None not in (rate, tax, base) and abs(base * rate / 100 - tax) > TOL:
        add("Medium", "tax", "Tax amount does not match the stated rate",
            f"{rate:g}% of {base:.2f} = {base*rate/100:.2f}, but tax shows {tax:.2f}")
    if total is not None and base is not None:
        expected = round(base + (tax or 0), 2)
        if abs(total - expected) > TOL:
            diff = total - expected
            add("High", "total", f"Total is {abs(diff):.2f} {'higher' if diff>0 else 'lower'} than the sum of lines and tax",
                f"Subtotal {base:.2f} + tax {(tax or 0):.2f} = {expected:.2f}, but the invoice total shows {total:.2f}")
    seen = {}
    for i, it in enumerate(items, 1):
        seen.setdefault((it["description"].lower(), it["qty"], it["unit_price"]), []).append(i)
    for (_, _, _), idx in seen.items():
        if len(idx) > 1:
            it = items[idx[0] - 1]
            add("High", "duplicate", f"Duplicate line: {it['description']}",
                f"Lines {', '.join(map(str, idx))} are identical ({it['qty']:g} x {it['unit_price']:.2f})")
    if level == "full":
        for key, label in [("invoice_number", "Invoice number"), ("date", "Invoice date"), ("supplier", "Supplier name"),
                           ("supplier_tax_id", "Supplier tax ID"), ("due_date", "Due date")]:
            if not inv.get(key): add("Medium", "missing", f"Missing required field: {label}", f"No {label.lower()} found on the invoice")
        d1, d2 = _d(inv.get("date")), _d(inv.get("due_date"))
        if d1 and d2 and d2 < d1:
            add("Medium", "dates", "Due date is before the invoice date", f"Invoice date {d1}, due date {d2}")
        if inv.get("tax") is None and prices_include_tax is False:
            add("Medium", "tax", "No tax charged although prices exclude tax", "The buyer confirmed prices exclude tax, but the invoice has no tax line")
        if INJECTION.search(raw_text or ""):
            add("High", "injection", "Invoice contains instructions aimed at the auditor",
                "A line in the invoice tries to change the audit outcome. It was treated as data and ignored; ask the supplier why it is there")
    out.sort(key=lambda x: RISK_ORDER[x["risk"]])
    return out

ACTIONS = {
    "line": "Ask the supplier to correct the line amounts before paying.",
    "subtotal": "Reconcile the subtotal with the line items and request a corrected invoice.",
    "total": "Do not pay the stated total; request a corrected invoice or credit note.",
    "tax": "Confirm the correct tax rate and amount with the supplier.",
    "duplicate": "Confirm whether the repeated line was delivered twice; remove it if not.",
    "missing": "Request the missing details (they are usually required for tax records).",
    "dates": "Confirm the real invoice and due dates with the supplier.",
    "injection": "Flag this invoice to a human reviewer; the embedded instruction is a red flag.",
}

def render_report(inv, issues, notes="", partial=False, title="Invoice audit report", assumptions=()):
    n = {r: sum(1 for i in issues if i["risk"] == r) for r in RISK_ORDER}
    total = f"{inv['total']:.2f}" if inv.get("total") is not None else "unknown"
    L = [f"# {title}", "",
         f"**Invoice:** {inv.get('invoice_number') or 'n/a'}  |  **Supplier:** {inv.get('supplier') or 'n/a'}  |  **Stated total:** {total}", "",
         "## Summary"]
    L.append(f"**{len(issues)} issue(s) found** ({n['High']} high, {n['Medium']} medium, {n['Low']} low)." if issues
             else "**No issues found** by the automated checks.")
    if partial: L.append("\n> This report is partial: the budget cap was reached before every step finished.")
    for a in assumptions: L.append(f"\n> Note: {a}")
    if issues:
        L += ["", "## Issues (ranked by risk)", "", "| # | Risk | Description | Evidence |", "|---|---|---|---|"]
        for k, i in enumerate(issues, 1):
            L.append(f"| {k} | {i['risk']} | {i['title']} | {i['evidence'].replace('|', '/')} |")
    if notes: L += ["", "## Context review", "", notes]
    L += ["", "## Recommended actions", ""]
    acts = list(dict.fromkeys(ACTIONS[i["cat"]] for i in issues)) or ["No action needed based on these checks."]
    L += [f"{k}. {a}" for k, a in enumerate(acts, 1)]
    return "\n".join(L) + "\n"
