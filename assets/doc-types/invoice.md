# Structure brief: Invoice

Use this structure for client invoices or billing documents.

## Required sections (in order)

1. **Header** — doc-header div; left side: company/sender name + "Invoice"; right side: Invoice # and Date
2. **Bill To / From** — two-column card layout using kv-grid (From on left, Bill To on right, or stacked)
3. **Line Items table** — the main table with columns: Description, Qty, Unit Price, Amount
4. **Totals block** — below the table: Subtotal, Tax (if any), **Total Due** in a totals-row div
5. **Payment Details** — card with kv-grid: Due Date, Payment Method, Bank/PayPal/etc details
6. **Notes** (optional) — small callout for any terms, late fees, or thank-you message
7. **Footer** — doc-footer div with invoice number and "Thank you for your business"

## Critical formatting rules

- Invoice number format: INV-YYYYMM-NNN (e.g. INV-202606-001) — infer from context or use 001
- Amounts: always 2 decimal places, right-aligned (.num class on td/th)
- Due date: default 30 days from invoice date unless specified
- Total Due row must be visually prominent — use totals-row div

## HTML skeleton

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Invoice [NUMBER]</title>
  <link rel="stylesheet" href="/mnt/data/projects/telegram-agent/assets/brand.css">
</head>
<body>
  <div class="doc-header">
    <div>
      <div class="branding">Invoice</div>
      <h1>[SENDER NAME]</h1>
    </div>
    <div class="doc-meta">
      Invoice #[NUMBER]<br>
      Date: [DATE]<br>
      Due: [DUE DATE]
    </div>
  </div>

  <div class="card">
    <dl class="kv-grid">
      <dt>From</dt><dd>[SENDER]<br>[ADDRESS]<br>[EMAIL]</dd>
      <dt>Bill To</dt><dd>[CLIENT NAME]<br>[CLIENT ADDRESS]</dd>
    </dl>
  </div>

  <h2>Services</h2>
  <table>
    <thead>
      <tr><th>Description</th><th class="num">Qty</th><th class="num">Unit</th><th class="num">Amount</th></tr>
    </thead>
    <tbody>
      <tr><td>[Item]</td><td class="num">1</td><td class="num">£0.00</td><td class="num">£0.00</td></tr>
    </tbody>
  </table>
  <div class="totals-row">
    <span><span class="label">Subtotal</span> £0.00</span>
    <span><span class="label">Tax (20%)</span> £0.00</span>
    <span><span class="label">Total Due</span> <span class="amount">£0.00</span></span>
  </div>

  <h2>Payment Details</h2>
  <div class="card">
    <dl class="kv-grid">
      <dt>Due Date</dt><dd>[DATE]</dd>
      <dt>Method</dt><dd>[BANK TRANSFER / PAYPAL / etc]</dd>
      <dt>Details</dt><dd>[ACCOUNT / EMAIL]</dd>
    </dl>
  </div>

  <div class="doc-footer">
    <span>Invoice #[NUMBER] · [DATE]</span>
    <span>Thank you for your business</span>
  </div>
</body>
</html>
```
