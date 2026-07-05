# Structure brief: Idea Development / Brainstorm

Use this structure when developing an idea, concept, feature proposal, or brainstorm output.

## Required sections (in order)

1. **Header** — doc-header div with the idea title and date
2. **The Idea** — a .callout div (no modifier) with a 2-3 sentence summary of the core idea
3. **Why This Matters** — h2 section; 3-5 bullet points explaining the problem being solved or opportunity
4. **How It Could Work** — h2 section; numbered list or h3 sub-sections for approach/mechanics
5. **Key Considerations** — h2 section; use badge-warn .badge inline labels like "Risk:", "Dependency:", "Open question:" before each bullet
6. **Next Possible Actions** — h2 section; checklist ul with concrete first steps
7. **Related / Resources** (optional) — h2 section; plain ul of links, references, or related ideas
8. **Footer** — doc-footer div

## Notes

- This is a THINKING document, not a decision document — language should be exploratory ("could", "might", "worth exploring")
- Use .callout.good for "strong signal" points and .callout.warn for risks that need resolving
- Sub-ideas or variants belong in h3 sections under "How It Could Work"
- Keep language concrete — abstract brainstorms are useless; anchor every idea to a specific example

## HTML skeleton

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>[IDEA TITLE]</title>
  <link rel="stylesheet" href="/mnt/data/projects/telegram-agent/assets/brand.css">
</head>
<body>
  <div class="doc-header">
    <div>
      <div class="branding">Idea</div>
      <h1>[IDEA TITLE]</h1>
    </div>
    <div class="doc-meta">[DATE]</div>
  </div>

  <div class="callout">[2-3 sentence core idea summary]</div>

  <h2>Why This Matters</h2>
  <ul>
    <li>...</li>
  </ul>

  <h2>How It Could Work</h2>
  <h3>[Approach 1]</h3>
  <p>...</p>

  <h2>Key Considerations</h2>
  <ul>
    <li><span class="badge badge-warn">Risk</span> ...</li>
    <li><span class="badge badge-blue">Open question</span> ...</li>
  </ul>

  <h2>Next Possible Actions</h2>
  <ul class="checklist">
    <li>...</li>
  </ul>

  <div class="doc-footer">
    <span>Generated [DATE]</span>
    <span>[PROJECT]</span>
  </div>
</body>
</html>
```
