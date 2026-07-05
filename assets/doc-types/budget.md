# Structure brief: Budget

Use this structure for any financial breakdown: project budget, monthly budget, expense report, cost estimate.

## Required sections (in order)

1. **Header** — doc-header div with title (e.g. "Project Budget — June 2026"), period in doc-meta
2. **Budget Overview** — card with kv-grid: Period, Total Budget, Total Spent, Remaining, Status (badge)
3. **Income / Revenue** (if applicable) — h2 section with a table (columns: Source, Amount, Notes)
4. **Expenses** — h2 section with a table (columns: Category, Item, Amount, Notes)
   - Group rows by category using thead-style h4 labels or a Category column
   - Last row of the table body: a totals-row div below the table showing Total
5. **Variance / Notes** — h2 section; callout divs (.callout.good for under-budget, .callout.warn for over)
6. **Footer** — doc-footer div

## Table format for expenses

```html
<table>
  <thead>
    <tr><th>Category</th><th>Item</th><th class="num">Amount</th><th>Notes</th></tr>
  </thead>
  <tbody>
    <tr><td>Hosting</td><td>VPS (monthly)</td><td class="num">£25.00</td><td>DigitalOcean</td></tr>
    ...
    <tr class="total"><td colspan="2"><strong>Total</strong></td><td class="num"><strong>£XXX.XX</strong></td><td></td></tr>
  </tbody>
</table>
<div class="totals-row">
  <span><span class="label">Budget</span> £XXX</span>
  <span><span class="label">Spent</span> £XXX</span>
  <span><span class="label">Remaining</span> <span class="amount">£XXX</span></span>
</div>
```

## Currency

Use the currency symbol the user provides; default to £ if unspecified.
Format numbers with 2 decimal places and commas for thousands: 1,234.56
