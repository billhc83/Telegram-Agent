# Structure brief: List / Task List

Use this structure for any list-type document: to-do lists, shopping lists, feature lists, reading lists, etc.

## Required sections (in order)

1. **Header** — doc-header div with title and date in doc-meta
2. **Summary callout** (optional) — a .callout div with one sentence describing the list and its purpose
3. **The List** — one or more h2 sections, each containing a checklist ul or plain ul
   - Use class="checklist" for task/to-do lists where items can be marked done
   - Use plain ul for simple enumerations
   - Group items under h3 sub-headings if there are more than ~10 items or natural categories
4. **Footer** — doc-footer div

## Notes

- Keep each list item tight: verb + object, no trailing punctuation
- For priority or status, use .badge spans (badge-blue, badge-green, badge-warn, badge-red) inline after the item text
- If items have due dates or owners, add them as a small .muted span after the item

## HTML skeleton

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>[TITLE]</title>
  <link rel="stylesheet" href="/mnt/data/projects/telegram-agent/assets/brand.css">
</head>
<body>
  <div class="doc-header">
    <div>
      <div class="branding">List</div>
      <h1>[TITLE]</h1>
    </div>
    <div class="doc-meta">[DATE]</div>
  </div>

  <div class="callout">[One-line purpose statement]</div>

  <h2>[Category or "Items"]</h2>
  <ul class="checklist">
    <li>[Item] <span class="badge badge-blue">High</span></li>
    <li class="done">[Completed item]</li>
  </ul>

  <div class="doc-footer">
    <span>Generated [DATE]</span>
    <span>[PROJECT]</span>
  </div>
</body>
</html>
```
