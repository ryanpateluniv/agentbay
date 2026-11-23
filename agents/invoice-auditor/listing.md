# Invoice Auditor

**Summary:** Finds math errors, duplicate lines and missing fields in an invoice, then ranks the problems by risk.

## Description

Upload an invoice (pasted text, .txt or text-based PDF). The agent extracts line items, re-checks every total and tax figure in code, flags duplicate lines and missing required fields, and returns an audit report with the problems ranked by risk. It asks at most one question (whether prices include tax) and only when the invoice has no tax line.

## Example tasks

1. Check this invoice for math errors and duplicate charges
2. Audit this invoice and tell me if any required fields are missing
3. Does the tax on this invoice add up?
