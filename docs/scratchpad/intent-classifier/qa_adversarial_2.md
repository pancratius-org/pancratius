# Adversarial QA — Round 2

Auditor: adversarial QA agent (claude-sonnet-4-6), 2026-05-30.
Ground truth: OOXML via `ir_view`, live ir_view output, re-derived numbers.

---

## Scoreboard (ranked by severity)

| # | Severity | Area | Finding | Evidence |
|---|----------|------|---------|----------|
| 1 | CRITICAL | Benchmark | Consensus self-inclusion inflates DS score 15pp; conclusion that DS is the "consensus centroid" corpus labeler is unsupported | DS vs own consensus 0.988; DS leave-one-out (excluded from its own ref) 0.859; owl leave-one-out 0.871 — DS is NOT uniquely the centroid |
| 2 | CRITICAL | Data integrity | `lineation_task.json` and `calib3_faithful.json` were both built from a **stale** IR (before the heading+image index shift in book 02); cf07_book02 has 17/31 lines with wrong roles | `ir_view` idx=1013 = heading "Часть 2"; task has idx=1013 as body with 6 sub-lines of prose; `calib3` card text shows `p1013 Всё изменилось.` — a body paragraph — but current `ir_view` shows idx=1013 = heading |
| 3 | CRITICAL | Benchmark | md-mode test: unfair in at least **two independent ways** (structural context stripped, ANY-vs-MAJORITY collapse rule) but the notebook only half-retracted it | md-mode lineated rate 0.587 vs heavy ref 0.731 on same 85 keys; structured g2 lineated rate 0.462; md over-predicts lineated by 12.5pp while still scoring higher (0.837 vs 0.731) — gap explained by ANY rule and context deprivation |
| 4 | HIGH | ir_view | Russian ellipsis `…` (U+2026) is not in the `_looks_pseudo_header` terminal-punctuation filter; 65+ body lines across the corpus are misclassified as `ROLE_PSEUDO_HEADER` | `scripts/ir_view.py:117`: `not t.endswith((".", "!", "?"))` — does not include `…`; confirmed hits: book 13 idx=1853 `"Кто наблюдает?…"`, book 25 idx=16081 `"И ещё…"`, book 27 has 12 cases (`5. Панкратиус…`, `6. Панкратиус…`, etc.), book 31 has 8 cases, books 19/29/31/32/33/35/36/38/40/53/54/56/58/61/62/63/70/71/74 each have 1-4 cases |
| 5 | HIGH | Benchmark | `segments(soft_boundaries=False)` does NOT actually merge across pseudo_header/speaker_label boundaries; the parameter is broken | `scripts/ir_view.py:218`: inner `while` loop tests `paras[j].role == ROLE_BODY or paras[j].role == ROLE_EMPTY`; PSEUDO_HEADER/SPEAKER_LABEL are neither, so the loop always stops there regardless of the `bounds` set; `soft_boundaries=False` is a no-op for soft boundaries; verified with a synthetic test |
| 6 | HIGH | Scoring | `score_sweep.py` `par_label()`: multi-line paragraphs (`nlines > 1`) are hardcoded to `"lineated"` regardless of model output; 31/32 multi-line keys in `heavy_ref` say `lineated` (trivially correct), inflating all-key agreement scores by ~2-4pp | `deepseek/score_sweep.py:37`; verified: 32 multi-line keys in the 164-key reference, 31 correctly labeled lineated by rule alone; removing multi-line keys changes g2 from 0.805 to 0.808 (single-only); removing cf07 changes gpt4o_g2 from 0.769 to 0.809 |
| 7 | HIGH | Data integrity | `lineation_task.json` index shift for cf07_book02: task has idx=1013+1014 as multi-line body (heading text the IR now assigns to real headings); models correctly label that narrative text as "flowing" but heavy_ref says "lineated" → 2 guaranteed false negatives per model for cf07 | models return all-flowing for task idx=1013 (pred_base, pred_g2_goal, pred_gpt4o_g2 verified); heavy says lineated; nlines[(cf07,1013)] = 1 (heading in current ir_view) so no multi-line override fires; net: cf07 score depressed by 2/10 keys |
| 8 | HIGH | Benchmark | Cross-subset model comparison is invalid: DeepSeek g2 scores 0.805(n=164) vs gpt4o 0.769(n=104) on different key subsets (4 extra cards in DeepSeek); DeepSeek on the same 8-card subset scores 0.731, BELOW gpt4o 0.769 | verified by restricting DS pred_g2_goal.jsonl to the 8-card SAMPLE subset |
| 9 | MED | ir_view | `bold` detection is asymmetric with `italic`: bold requires ALL non-break top-level inlines to be `Emphasis{strong}`; italic requires ANY top-level inline to be `Emphasis{em}`; mixed partial-bold lines (118 in book 02 alone) are silently classified as non-bold | `ir_view.py:103-104`; `Book 02: lines with mixed bold+plain: 118`; these are classified bold=False even if >90% of the line is bold; `_looks_pseudo_header` and `bold_all` computation both silently miss these |
| 10 | MED | ir_view | `_looks_speaker_label` multi-line paragraphs: a para with `br_count > 0` (multiple lines) can be detected as speaker_label if only the FIRST line is bold and ends with `:` — the continuation lines' content is ignored | `ir_view.py:120-125`: `if p.role != ROLE_BODY or not p.lines or not p.lines[0].bold`; book 02 idx=1055 (`"Так появилась новая игра:"`, nlines=2) confirmed detected as speaker_label despite having a continuation |
| 11 | MED | Benchmark | The `heavy_ref` is derived purely from `svetozar_calib.jsonl`, which is the GPT-4o Светозар persona annotations; the gpt4o_svetozar predictions use the same persona; this is structurally circular even though the prompts differ | svetozar.md documents Variant A was used to create svetozar_calib; gpt4o_sample.py uses Russian Светозар prompt; the reference was labeled by GPT-4o-as-Светозар, tested against GPT-4o-as-Светозар |
| 12 | MED | Benchmark | `cf10_book27` idx=176 has `nlines=2` in current ir_view (`par_label` hardcodes it as `lineated`) but `heavy_ref` says `flowing`; every model is ALWAYS penalized for this key | verified: `ir_view` idx=176 = two-line para ("Панкратиус: Не обычно…" + long wrapping continuation); heavy says flowing; par_label always returns lineated (nlines>1 rule) |
| 13 | MED | ir_view | `_looks_pseudo_header` heuristic fires on quoted fragments (lines starting with `«` and ending in `…`), dialogue lines ending in `…`, and Biblical citation fragments | book 32: idx=18035 `"«Я люблю Тебя…"` (quote fragment), idx=18036 `"Я скучал…"` (plain line); book 33 idx=6670 dialogue line `"— Ты знаешь, мне прислали одно письмо…"` |
| 14 | LOW | ir_view | `_walk()` performs depth-first recursion into `ContainerInline` children; but `bold` and `italic` classification in `_line_of()` only inspect top-level inlines, not the children that `_walk` exposes; nested emphasis (e.g. `Emphasis{strong, [Emphasis{em, [Text]}]}`) shows bold=True, italic=False | `ir_view.py:43-48` vs `ir_view.py:101-107`; text extraction via `inline_plain` is correct (recursive); only the boolean flags are shallow |
| 15 | LOW | Scripts | `prompt_sweep.py`, `gpt4o_sample.py`, `qwen_sample.py` all mutate `label_lineation.L.SYSTEM_PROMPT` and `L.MODEL` as module globals; this is a latent race condition if any of these scripts were ever parallelized | `prompt_sweep.py:71`, `gpt4o_sample.py:58-59`, `qwen_sample.py:39-40`; currently harmless (sequential calls) |
| 16 | LOW | Benchmark | `md_mode_bench.py` line 108: `has_hard = any(ln.endswith("  ")…)` is immediately overwritten by line 110-111 — the first assignment is dead code | `md_mode_bench.py:108-111`: `binfo` loop body assigns `has_hard` twice; first assignment is dead |

---

## Detailed Findings

### F1 (CRITICAL) — Consensus self-inclusion inflates DS score 15pp

**Location:** `deepseek/score_sweep.py` + notebook.md line 684-687

**What the code does:** The 4-model g2 consensus is computed by majority vote across DeepSeek, Qwen, GPT-4o, and Owl. Then each model's agreement *with that consensus* is reported. DeepSeek is 1 of the 4 models contributing to the consensus it is scored against.

**Evidence:** Re-derived leave-one-out (each model excluded from its own reference):
```
ds (leave-one-out): 0.859 (n=85 single-line keys)
ow (leave-one-out): 0.871
g4 (leave-one-out): 0.835
qw (leave-one-out): 0.812
```
Notebook-reported (self-included): DS=0.988, owl=0.918, gpt4o=0.847, qwen=0.882.

**Downstream conclusion corrupted:** "DeepSeek-v4-flash is not just cheap-enough — it's the MOST consensus-central labeler" (notebook line 687). In leave-one-out, owl is marginally better (0.871 vs 0.859). The choice of DS as corpus labeler is not wrong, but the stated evidence is self-referential and inflated by 13 pp.

---

### F2 (CRITICAL) — lineation_task.json and calib3_faithful.json built from stale IR (book 02 index shift)

**Location:** `data/lineation_task.json`, `data/calib3_faithful.json`

**What happened:** Both files were built before a paragraph index shift in book 02's IR output. When built, idx=1013 was a multi-line body paragraph; currently idx=1013 is the heading "Часть 2". cf07_book02 has 17/31 line entries with wrong roles. cf08_book02 has 11/26 entries with wrong roles (body/empty swap + one image-para).

**Verified cross-check:**
```
task cf07_book02 idx=1013 sub=0: role=body, text="Всё изменилось."
current ir_view idx=1013: role=heading, text="Часть 2"

task cf07_book02 idx=1022 sub=0: role=body, text="Сергей улыбнулся."
current ir_view idx=1022: role=IMAGE (the recently fixed image boundary)
```

**Impact:** Models labeled heading content as body candidates; heavy_ref was labeled on the same stale context; cf07 scoring is internally consistent but the underlying paragraphs are wrong. Models return all-flowing for old idx=1013 (confirmed across pred_base, pred_g2_goal, pred_gpt4o_g2), heavy_ref says lineated → 2 guaranteed false negatives per model depressing cf07 accuracy. The notebook acknowledges needing to rebuild lineation_task (line 822) but this has not been done.

---

### F3 (CRITICAL) — md-mode comparison unfair in two independent ways

**Location:** `deepseek/md_mode_bench.py:99-132`

**Fault 1 — ANY vs MAJORITY collapse:** Structured mode collapses per-subline predictions to paragraph level by majority vote. `extract_labels()` in md-mode collapses by ANY: `out[ln["idx"]] = "lineated" if (out.get(ln["idx"]) == "lineated" or lab == "lineated") else "flowing"` (line 131). Any single subline labeled lineated makes the whole paragraph lineated. This biases md-mode toward lineated.

**Fault 2 — Structural context stripped:** `card_to_source()` (line 65-77) skips every non-body line. The structured-mode prompt receives explicit `[ctx:heading]`, `[ctx:empty]`, `[ctx:pseudo_header]` context lines that define structural fences. md-mode gets flat body text with no fences.

**Numbers:**
```
md-mode lineated rate:     0.587
structured g2 lineated rate (same 85 keys): 0.462
heavy_ref lineated rate:   0.731
md_mode vs heavy_ref:      0.837
g2 vs heavy_ref:           0.731
```
md-mode is scoring higher despite systematically under-predicting lineated relative to reference (0.587 vs 0.731) — the higher score is largely driven by the subsets where md makes different errors. The notebook has partially retracted this but describes it only as "layout liberties" without enumerating the mechanical sources above.

---

### F4 (HIGH) — Russian ellipsis `…` not in pseudo_header terminal filter: 65+ misfires

**Location:** `scripts/ir_view.py:117`

```python
return bool(t) and len(t) <= 60 and not t.endswith((".", "!", "?"))
```

`"…"` (U+2026) is not in the tuple. Any all-bold, ≤60-char body line ending with `…` is misclassified as `ROLE_PSEUDO_HEADER`, inserting a spurious SOFT boundary and splitting the body run.

**Confirmed instances (sample):**
- Book 13 idx=1853: `"Кто наблюдает? — Система, которая предпочитает круги линиям…"` — mid-sentence Q&A, not a section header
- Book 25 idx=16081: `"И ещё…"` — transition phrase before verse
- Book 27: 12 instances of `"5. Панкратиус…"`, `"6. Панкратиус…"`, etc. — dialog speaker labels used as body text
- Books 31, 33, 36, 40, 54, 63: Biblical quotes starting with `«` and ending with `…`
- Books 19, 29, 32, 35, 38, 53, 56, 58, 61, 62, 70, 71, 74: 1-4 cases each

Total confirmed: 65 cases across 20 books.

---

### F5 (HIGH) — `segments(soft_boundaries=False)` is broken: parameter is ignored

**Location:** `scripts/ir_view.py:215-228`

The inner `while` loop:
```python
while j < n and (paras[j].role == ROLE_BODY or paras[j].role == ROLE_EMPTY):
```
stops at `ROLE_PSEUDO_HEADER` and `ROLE_SPEAKER_LABEL` regardless of the `soft_boundaries` parameter, because these roles are neither `ROLE_BODY` nor `ROLE_EMPTY`. The `bounds` set is built correctly but the loop condition never references it for soft boundary types.

**Verified:**
```python
segs(soft_boundaries=True)  → [(0, 0), (2, 2)]
segs(soft_boundaries=False) → [(0, 0), (2, 2)]   # SAME — parameter has no effect
```
Currently no code calls `segments(soft_boundaries=False)` so no active corruption, but the API contract is silently broken.

---

### F6 (HIGH) — Multi-line override in `par_label` inflates per-model agreement scores

**Location:** `deepseek/score_sweep.py:37`

```python
out[k] = "lineated" if (nlines.get(k, 1) > 1 or Counter(v).most_common(1)[0][0] == "lineated") else "flowing"
```

Of the 164 reference keys, 32 are multi-line (`nlines > 1`). The rule forces all 32 to `lineated` regardless of model output. Of these 32, heavy_ref says lineated for 31 — so every model gets 31 free correct answers (and 1 forced wrong answer for cf10_book27 idx=176). The 31 trivially correct keys inflate all-key agreement by ~1-2pp depending on model. Single-line agreement is the correct basis for comparison; multi-line keys should be separately reported (or excluded) since any model will score 97% on them by the override alone.

---

### F7 (HIGH) — Cross-subset comparison: DS 0.805(n=164) vs gpt4o 0.769(n=104) on different keys

**Location:** notebook.md line 653-686

DeepSeek ran on all 12 calibration cards (164 keys); GPT-4o/Qwen/Owl ran on only 8 cards (104 keys). The notebook reports and compares these scores directly. On the same 8-card subset, DeepSeek g2 scores 0.731, **below** gpt4o at 0.769.

```
ds_g2 (same 8 cards): 0.731 (n=104)
gpt4o_g2:             0.769 (n=104)
owl_g2:               0.837 (n=104)
qwen_g2:              0.683 (n=104)
ds_g2 (all 12 cards): 0.805 (n=164)  ← reported headline number
```

The 4 extra cards (cf01, cf05, cf09, cf11) are easier; they inflate DS's all-card score. Cross-subset comparison is invalid.

---

### F8 (MED) — `cf10_book27` idx=176 is a two-line flowing paragraph; rule forces it lineated

**Location:** `deepseek/score_sweep.py:37` + `data/lineation_heavy_ref.json`

Para idx=176 in book 27: `"Панкратиус: Не обычно. Я привык искать Тебя в себе,"` (fill=0.951, nowrap) followed by a very long wrapping continuation (fill=7.782, wraps=True). Heavy_ref says `flowing`. The rule forces `lineated` (nlines=2 > 1). Every model scores a false positive on this key.

---

### F9 (MED) — Partial-bold lines silently lose pseudo_header detection

**Location:** `scripts/ir_view.py:103`, `ir_view.py:114`

`bold = bool(nz) and all(isinstance(n, ir.Emphasis) and n.kind == "strong" for n in nz)` requires ALL non-break top-level inlines to be strong. A line like `"— Царь — это не кто главный. Царь — это кто **видит**…"` has a plain Text node followed by a bold word, so bold=False. Book 02 alone has 118 such mixed-emphasis lines. Any section-head that uses partial bold (one key word bolded, rest plain) is never detected as pseudo_header and gets no soft boundary.

---

### F10 (MED) — Speaker_label detection fires on first line of multi-line paragraphs

**Location:** `scripts/ir_view.py:120-125`

`_looks_speaker_label` checks only `p.lines[0].bold` and `p.lines[0].text.endswith(":")`. A multi-line paragraph whose first line is bold and ends with `:` is classified as speaker_label even if lines 2+ are normal body text. Book 02 idx=1055 (`"Так появилась новая игра:"`, nlines=2) is classified as speaker_label.

---

### F11 (MED) — Reference circularity: heavy_ref annotated by GPT-4o-Светозар, scored against GPT-4o-Светозар

**Location:** `data/svetozar_calib.jsonl` → `data/lineation_heavy_ref.json`; `deepseek/gpt4o_sample.py`

The `heavy_ref` collapses `svetozar_calib.jsonl`, which was created by GPT-4o playing the Светозар persona (documented in `prompts/svetozar.md`). `pred_gpt4o_svetozar` uses the same persona on the same model. The reference and prediction share the same model identity, though different prompts and contexts. The anti-circular observation (gpt4o_sv scores *lower* at 0.606 vs neutral g2's 0.769) is noted in the notebook but the structural circularity is not flagged.

---

### F12 (LOW) — Dead code in `md_mode_bench.py` `has_hard` detection

**Location:** `deepseek/md_mode_bench.py:108-111`

```python
has_hard = any(ln.endswith("  ") or ln.rstrip("\n").endswith("  ") for ln in b.split("\n"))  # line 108
# ... 
has_hard = any(re.search(r"\S {2,}$", ln) for ln in phys) or ...  # line 110-111 OVERWRITES
```

Line 108 computes `has_hard` and immediately overwrites it on line 110. The first computation is dead code. The second computation (regex) is what actually runs. No functional impact since the regex is logically equivalent in the test cases, but the dead code is confusing.

---

## What the notebook has already documented (not new findings)

The notebook (entries 2026-05-30) already captures: the image boundary double-bug, that md-mode was "doubly invalid" for the #02 example, that lineation_task.json needs rebuilding, and that the md-vs-structured conclusion was retracted. Those are NOT scored here as new defects. The new findings above (F1–F12) are distinct from what the notebook records.
