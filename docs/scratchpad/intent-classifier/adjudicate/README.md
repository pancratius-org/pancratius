# Adjudication Desk

A single-file, offline tool for a human (the project owner) to resolve labeling
questions by looking at rendered book-page images and recording ground-truth
verdicts. The verdicts are used to rank LLM readers and to repair a dataset.

It is task-driven, not hardcoded: you load an *assessment task* JSON describing
the items to judge, and it produces a *responses* JSON of your verdicts.

## Files

- `adjudicate.html` — the app. Vanilla HTML/CSS/JS, no build, no server, no
  network. Open it directly with `file://` (double-click, or
  `open adjudicate.html`).
- `assessment_task.example.json` — a 2-item demo (one single-choice with an
  inline data-URI image, one per-line) so you can try it immediately.
- `README.md` — this file.

## How to use

1. Open `adjudicate.html` in a browser.
2. Click **Load task…** and pick an assessment-task JSON (try the example).
3. For each item, look at the image and record your verdict.
   - **single-choice**: click an option (or press its number key `1`–`4`).
     Click again to clear.
   - **per-line**: click a verdict toggle on each line. For speed, focus a row
     (Tab/click) and press `1`/`2`/… to set it, `Backspace`/`0` to clear,
     `↑`/`↓` to move between rows. The "All X" buttons bulk-set every line.
   - Add a free-text **note** on any item.
4. Navigate with **Previous / Next**, the `←` / `→` arrow keys, or the
   progress dots (click to jump). Answers persist while you navigate.
5. **Hints** (e.g. how a model panel voted) are hidden by default behind a
   per-item toggle and the global **Hints** switch, so they can't bias you.
   Reveal them only after forming your own judgement.
6. On the last item click **Review & export →**, then **Download
   responses.json** (also copies to clipboard) or **Copy JSON**. The JSON is
   shown in a selectable text area as a fallback.

Click any page image to enlarge it (Esc or click to close).

Because `file://` blocks `fetch` of relative paths, the app never fetches:
you pick the task via a file input, and **images must be embedded as data
URIs** inside the task file so everything is self-contained.

## Input schema — assessment task JSON

```jsonc
{
  "title": "string",            // shown in the masthead
  "instructions": "string",     // shown under the title
  "items": [
    {
      "id": "string",           // REQUIRED, unique; the key used in output
      "mode": "single-choice" | "per-line",   // REQUIRED

      "image": "string",        // OPTIONAL data: URI (or any src). If omitted,
                                //   a placeholder is shown and you judge from text.
      "structure": "string",    // OPTIONAL monospace context block (multi-line ok)
      "question": "string",     // OPTIONAL prompt shown above the controls
      "hint": "string",         // OPTIONAL item-level hint, hidden by default

      // --- when mode = "single-choice" ---
      "options": [              // 2–4 recommended
        { "value": "string", "label": "string" }
      ],

      // --- when mode = "per-line" ---
      "lineOptions": [          // 2–3 recommended; the per-line toggles
        { "value": "string", "label": "string" }
      ],
      "lines": [
        {
          "key":  "string",     // REQUIRED, unique within the item; output key
          "text": "string",     // the line shown to the human (Cyrillic ok)
          "hint": "string"      // OPTIONAL per-line hint, hidden by default
        }
      ]
    }
  ]
}
```

Notes:
- `value` is the machine label written to the output; `label` is the human
  caption on the button. Keep `value`s stable across tasks if you compare runs.
- An item may carry both an `image` and `structure`/`lines` text; the human
  sees all of it.
- Hints (`item.hint` and `line.hint`) are concealed until explicitly revealed.

## Output schema — responses JSON

```jsonc
{
  "title": "string",            // echoed from the task
  "completedAt": "string",      // ISO-8601 timestamp of export
  "responses": {
    "<item id>": {
      // single-choice items:
      "answer": "string" | null,   // the chosen option value, or null if unanswered
      // per-line items:
      "lines": { "<line key>": "string" },  // only answered lines appear
      // both modes:
      "note": "string"          // present only if a non-empty note was entered
    }
  }
}
```

Per the app's logic:
- single-choice records carry `answer` (string or `null`); they do **not**
  carry `lines`.
- per-line records carry `lines` (a map of line key → chosen value, omitting
  unanswered lines); they do **not** carry `answer`.
- `note` is included on either mode only when non-empty.

This is the canonical shape; an external pipeline can generate tasks and consume
responses against it directly. (The schema matches the original spec; the only
clarification is that the two modes emit disjoint fields — `answer` xor `lines`
— rather than always emitting both.)
