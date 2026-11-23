# Invoice audit report

**Invoice:** INV-2041  |  **Supplier:** Northwind Office Supply Ltd  |  **Stated total:** 804.00

## Summary
**2 issue(s) found** (2 high, 0 medium, 0 low).

## Issues (ranked by risk)

| # | Risk | Description | Evidence |
|---|---|---|---|
| 1 | High | Total is 240.00 higher than the sum of lines and tax | Subtotal 470.00 + tax 94.00 = 564.00, but the invoice total shows 804.00 |
| 2 | High | Duplicate line: USB-C cable | Lines 1, 3 are identical (10 x 12.50) |

## Recommended actions

1. Do not pay the stated total; request a corrected invoice or credit note.
2. Confirm whether the repeated line was delivered twice; remove it if not.
