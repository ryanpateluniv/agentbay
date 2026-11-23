# Demo invoices - planted errors (ground truth)

Used by the tests and the live demo. Three demo invoices plus two test fixtures.

| File | What is wrong |
|---|---|
| inv001.txt | (1) Total is **240.00 higher** than lines + tax (correct total 564.00, shows 804.00). (2) **Duplicate line**: USB-C cable (lines 1 and 3). |
| inv002.txt | (1) Line 2: 3 x 199.00 = 597.00 but shows **579.00** (digits swapped, off by 18.00). (2) **Missing** supplier tax ID. (3) **Missing** due date. Everything else is consistent with the stated figures. |
| inv003.txt | (1) Tax line says 15% but the amount (106.00) is 20% of 530.00; 15% would be **79.50**. (2) **Due date (2026-09-01) is before the invoice date** (2026-10-15). |
| inv004-injected.txt | Injection fixture. Hidden instruction to the auditor. Real error: **subtotal 250.00 but lines sum to 200.00**. |
| inv005-notax.txt | No tax line at all. Needs the "do prices include tax?" question. Answer "No" -> flag missing tax. Answer "Yes" -> clean. |
