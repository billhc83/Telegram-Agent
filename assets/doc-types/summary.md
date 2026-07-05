# Structure brief: Conversation / Meeting Summary

Use this structure when summarising a conversation, meeting notes, email thread, or session.

## Required sections (in order)

1. **Header** — doc-header div with title (e.g. "Meeting Summary"), date, and optional participant names in doc-meta
2. **At a Glance** — a card with a kv-grid: Date, Participants, Duration (if known), Topic
3. **Key Points** — h2 section; use a plain ul with the 4-8 most important takeaways as concise bullet points
4. **Action Items** — h2 section; use a checklist ul (class="checklist"); each li gets class="done" only if already completed
5. **Discussion Notes** — h2 section; prose paragraphs or nested h3 sub-sections for each topic discussed
6. **Next Steps / Follow-up** — h2 section; ordered list of what happens next and by whom
7. **Footer** — doc-footer div with "Generated [date]" on the left and project name on the right

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
      <div class="branding">Summary</div>
      <h1>[TITLE]</h1>
    </div>
    <div class="doc-meta">[DATE]<br>[PARTICIPANTS]</div>
  </div>

  <h2>At a Glance</h2>
  <div class="card">
    <dl class="kv-grid">
      <dt>Date</dt><dd>[DATE]</dd>
      <dt>Topic</dt><dd>[TOPIC]</dd>
    </dl>
  </div>

  <h2>Key Points</h2>
  <ul>
    <li>...</li>
  </ul>

  <h2>Action Items</h2>
  <ul class="checklist">
    <li>[Action item]</li>
    <li class="done">[Completed item]</li>
  </ul>

  <h2>Discussion Notes</h2>
  <p>...</p>

  <h2>Next Steps</h2>
  <ol>
    <li>...</li>
  </ol>

  <div class="doc-footer">
    <span>Generated [DATE]</span>
    <span>[PROJECT]</span>
  </div>
</body>
</html>
```
