# Adversarial QA #3 — intent-classifier re-bench + mdio + reader-LLM

Date: 2026-05-31. All claims below are derived, not speculated.

---

## SCOREBOARD (ranked)

| # | Severity | Finding | Corrupts |
|---|----------|---------|----------|
| F1 | CRITICAL | oo_dense and lo_sparse JSON-costume cells use wrong idx throughout | 26-key rebench comparison |
| F2 | CRITICAL | fidelity mean masks per-card catastrophic failures; two models echoed the prompt | fidelity claims 0.948–0.977 |
| F3 | CRITICAL | F8 fix (multi-line → lineated UNLESS a line wraps) was NEVER applied to score_rebench.py | par-collapse correctness |
| F4 | HIGH | 135-key full comparison is not reproducible from committed score_rebench.py | reproducibility of main conclusion |
| F5 | HIGH | lineated-rate swing is understated (0.41→0.64 from flash only; true range 0.41→0.73) | framing-effect magnitude |
| F6 | HIGH | *** literal body line (cf11 idx=1135) always misses read_back; labeled 'flowing' silently | 1/135 key permanently contaminated |
| F7 | MED | rebench mdmode and mdio free use DIFFERENT inputs for 4 of 13 cards (bold speaker_labels) | cross-framing comparison not fully controlled |
| F8 | MED | [IMAGE] with trailing two-spaces in md outputs could spuriously label a source line lineated | read_back false positives |
| F9 | MED | reader_llm.py temperature=0.3, single reader, zero cross-reader reproducibility check | reader-LLM as quality signal |
| F10 | LOW | oo_dense rebench labels are PARTIALLY valid (18/305 keys happen to be correct by coincidence) | framing "garbage" diagnosis slightly overstated |
| S1 | SOUND | kappa values in full comparison verified exact (κ=0.348/0.489/0.496/0.803/0.832) | main framing-comparison conclusion stands |
| S2 | SOUND | extraction path asymmetry = ZERO: both read_back implementations produce identical results | cross-framing κ diffs are real framing effects |
| S3 | SOUND | 135 single-line keys correct: excludes all 39 multi-line pars, correct par-level collapse | sample construction is valid |

---

## Detailed Findings

### F1 — CRITICAL: oo_dense and lo_sparse rebench cells use wrong (sequential) idx throughout

**Evidence (re-derived):**

`pred_oo_dense_flash.jsonl` has 305 labels but only 18 overlap with the expected 308 keys from `lineation_task.json`. The model was asked for keys like `(cf00_book13, 1258, 0)` but returned `(cf00_book13, 0, 0)`, `(cf00_book13, 1, 0)` … i.e., sequential 0-based indices.

```
cf00_book13 oo_dense_flash actual idx: [0, 1, 2, 3, ..., 17]
cf00_book13 expected idx:              [1258, 1259, 1261, ..., 1277]
```

`lo_sparse_flash` has 318 labels (10 duplicates), `lo_sparse_owl` has 291 (17 missing, also sequential). The 18 accidentally-correct keys are cards where absolute idx happens to be small (cf02_book25 idx=1–11 because book25 starts at idx 1; cf04_book30 idx=126–132 matching the model's 0-6 relative count).

**Root cause:** oo_dense/lo_sparse prompts ask for JSON labels after presenting markdown. The model reasons over a markdown presentation and returns sequential position numbers rather than the absolute paragraph indices from the task.

**Corrupts:** The "26-key common intersection" used in the rebench scoring section of the notebook. Only 3 of 13 cards contribute to the 26 keys (cf01_book16, cf02_book25, cf04_book30). The rebench scoring section's kappa values (e.g. "oo_dense/lo_sparse JSON-costume: κ 0.23–0.32 — GARBAGE") rest on this thin and partly-accidental sample. The notebook acknowledges these framings are invalid but does not note the index-key contamination specifically.

**File:line:** `docs/scratchpad/intent-classifier/deepseek/rebench.py:170-172` (oo_dense/lo_sparse call `parse_struct()` which trusts model-returned idx); `data/rebench/pred_oo_dense_flash.jsonl`, `pred_lo_sparse_flash.jsonl`, `pred_lo_sparse_owl.jsonl`.

---

### F2 — CRITICAL: fidelity mean masks per-card catastrophic failures; prompt echoed in output

**Evidence (re-derived):**

Per-card fidelity for `free/flash`:
```
cf00: 0.987  cf01: 0.996  cf02: 1.000  cf03: 1.000  cf04: 1.000  cf05: 1.000
cf06: 0.939  cf07: 0.978  cf08: 0.964  cf09: 0.994  cf10: 0.996
cf11: 0.866  cf12: 0.765   <- FAILURES
Mean: 0.960
```

`md_free_flash_cf12_book02.txt` starts with the full English prompt instructions verbatim before the Russian markdown. `fidelity()` computes `norm(re.sub(r'[#>*-]', '', md))` on the entire string, inflating `got` from 157 source words to 243 words (86 extra English instruction words: "lineated", "verse", "trailing", "narrative", "paragraphs", …). The mean fidelity of 0.960 still looks fine.

`md_oo_dense_flash_cf06_book68.txt` also echoes the full prompt (fidelity 0.716).
`md_lo_sparse_flash_cf01_book16.txt` is truncated mid-sentence: src=789 words, got=461 words (328 words dropped), fidelity=0.734. Model hit a context/length limit.

Across all 78 mdio runs (6 cells × 13 cards), 15 cards fall below 0.95 fidelity. The notebook reports only the mean per-framing (oo_dense 0.960, lo_sparse 0.948–0.977, free 0.960) with no per-card breakdown and no threshold for rejection.

**Corrupts:** The claim "md-out is faithful regardless of framing" and "all high." The `read_back()` label assignments for the failed cards are unreliable (the subsequence matcher works on a text that includes instruction words, and for cf01/lo_sparse, 328 words are simply missing from the output, making label alignment silently degrade).

`mdio.py:177` prints only the mean, never a per-card threshold. `fidelity()` at `mdio.py:159-163` does not strip `[IMAGE]` markers or detect prompt echoing; `re.sub(r'[#>*-]', '', md)` leaves English instruction text intact.

---

### F3 — CRITICAL: F8 fix (multi-line → lineated UNLESS a line wraps) not applied to score_rebench.py

**Evidence (re-derived):**

`score_rebench.py:34`:
```python
return {k: ("lineated" if nlines.get(k, 1) > 1 or Counter(v).most_common(1)[0][0] == "lineated" else "flowing")
        for k, v in by.items()}
```

The F8 fix agreed in QA #2 was: "multi-line ⇒ lineated UNLESS a line wraps." This is not implemented. For `perline_flash`, 6 multi-line pars are force-overridden to `lineated` despite all model sub-votes being `flowing`:

```
('cf07_book02', 1019): 7 sub-votes ALL 'flowing' -> forced lineated
('cf07_book02', 1020): 3 sub-votes ALL 'flowing' -> forced lineated
('cf12_book02', 1015): 5/6 sub-votes 'flowing' -> forced lineated
('cf12_book02', 1019): 7 sub-votes ALL 'flowing' -> forced lineated
('cf12_book02', 1020): 3 sub-votes ALL 'flowing' -> forced lineated
('cf10_book27', 176):  2 sub-votes ALL 'flowing' -> forced lineated
```

Par 1019 in book02 is confirmed in ir_view: 7 lines of narrative dialogue ("Анфиса бежала ему навстречу.", "— У нас новенький! —", "— Что, опять?", …) — none wrap (wraps=False for all 7). The par_collapse rule incorrectly calls this `lineated` because nlines=7>1, even though 7 unanimous `flowing` votes AND no wrapping lines agree it is prose dialogue.

Additionally, 2 multi-line pars with wrapping lines (`cf10_book27` idx=166 and idx=176) are also force-overridden, where the F8 fix would correctly exempt them.

**Corrupts:** Any per-paragraph agreement computation that includes multi-line pars; the "same collapse rule for all framings" fairness claim; specifically the LOO consensus computation for framings where multiple sub-votes disagree with the override.

---

### F4 — HIGH: 135-key full comparison is not reproducible from committed score_rebench.py

**Evidence:**

`score_rebench.py:35`: `REB = HERE.parent / "data" / "rebench"` and line 44: `for fp in sorted(REB.glob("pred_*.jsonl"))`. The script reads ONLY `data/rebench/`. The mdio pred files are in `data/mdio/`.

Running `score_rebench.py` as committed produces the 26-key comparison (3 cards only), NOT the 135-key comparison. The notebook's "FULL framing comparison (135 single-line keys, all valid cells)" with κ=0.832/0.803/0.489/0.496/0.348 was computed ad-hoc (combining mdio/ + rebench/ directories), and that computation is not reflected in the committed script.

A reader trying to reproduce the notebook's main result would run `score_rebench.py` and get entirely different numbers on an unrepresentative 3-card sample.

**Not a data fabrication:** The κ numbers are correct (independently verified; see S1). The issue is reproducibility and the gap between the claimed procedure and the committed code.

---

### F5 — HIGH: lineated-rate swing understated (0.41→0.64 is flash only; true range is 0.41→0.73)

**Evidence (re-derived):**

Lineated rates on 135 single-line keys per cell:
```
MDIO_free_flash:    0.407   <- used as "free" low end
MDIO_free_owl:      0.681   <- not mentioned
MDIO_lo_sparse_flash: 0.570
MDIO_lo_sparse_owl:   0.578
MDIO_oo_dense_flash:  0.600
MDIO_oo_dense_owl:    0.607
RB_mdmode_flash:    0.644   <- used as "mdmode" high end
RB_mdmode_owl:      0.733   <- not mentioned
RB_mdmode_pro:      0.578
RB_perline_flash:   0.563
RB_perline_owl:     0.504
RB_perline_pro:     0.504
```

The notebook states: "lineated_RATE swings 0.41 (free) → 0.64 (mdmode): framing shifts ~23pts." This cites `free_flash` (0.407) as the low and `mdmode_flash` (0.644) as the high, ignoring that `free_owl`=0.681 (above `mdmode_flash`) and `mdmode_owl`=0.733. True range: 0.407→0.733, span=33pp not 23pp. More striking: within the `free` framing alone, flash vs owl diverges by 27pp (0.407 vs 0.681), which is larger than the ~23pp cross-framing claim.

**Corrupts:** The "framing shifts ~23pts" figure. The within-framing variance for `free` (27pp) being larger than the claimed cross-framing effect (23pp) further undermines "free-md least consistent" as being about framing vs noise in the lowest-kappa cell.

---

### F6 — HIGH: `***` literal body line (cf11 idx=1135) permanently misassigned in mdio read_back

**Evidence:**

`lineation_task.json`, card `cf11_book31`, line `idx=1135`: `{"role":"body","text":"***","emph":"","wraps":false}`. In ir_view, par 1135 of book31 has `role=body, text='***'` — this is a literal `***` typed in a paragraph, not a thematic-break element.

`norm('***')` = `[]` (empty). `_sub(bw, [])` returns `False` (`mdio.py:134`: `if not needle: return False`). So this line never matches any output block and falls through to `lab = None → "flowing"` (the miss default). This happens for ALL 6 mdio cells and the rebench mdmode cells. The line is always labeled `flowing` regardless of model intent, consuming 1 of the 135 single-line keys.

Additionally, this line cannot meaningfully receive a `flowing` or `lineated` label — it is a thematic separator. Its presence in the body-line key set contaminates the label distribution.

---

### F7 — MED: rebench mdmode and mdio free use different inputs for 4 of 13 cards

**Evidence:**

`rebench.py card_md()`: `_MARK['speaker_label'] = ''` (empty prefix, no emphasis applied). `mdio.py render_input()`: for `role in ('speaker_label', 'epigraph', 'signature')` → `emph(ln)` (applies bold/italic). For `cf11_book31`, the `Ответ:` speaker label is rendered as `Ответ:` (plain) in rebench mdmode vs `**Ответ:**` (bold) in mdio free. Affected cards: `cf06_book68`, `cf09_book05`, `cf10_book27`, `cf11_book31` (4 of 13).

**Not a smoking gun:** The within-framing kappa comparison excluding the 4 affected cards gives κ=0.289 (lower, not higher), so the input difference is NOT the main driver of low cross-framing kappa. The conclusion "framing changes the answer" survives. But the rebench mdmode and mdio free are not fully controlled ablations of framing only.

---

### F8 — MED: [IMAGE] marker with trailing two-spaces triggers false lineated block

**Evidence:**

`md_free_flash_cf12_book02.txt` line 28: `'[IMAGE]  '` (two trailing spaces). `read_back()` does not strip `[IMAGE]` markers. `re.search(r'\S {2,}$', '[IMAGE]  ')` matches (`]` is `\S`, then two spaces). Block 10 has `words=['image'], has_2space=True`. `norm('[IMAGE]') = ['image']`.

Any source body line whose words are a subsequence of `['image']` would be labeled `lineated` via this block. In this corpus, Russian body lines don't contain English "image", so no actual mislabeling is observed for cf12. But the block correctly reads: a flowing paragraph block that happens to have `[IMAGE]  ` would contaminate the block's lineation verdict for all lines in that block.

For `md_lo_sparse_owl_cf12_book02.txt` line 32: `'[IMAGE]  '` in a block by itself — same false positive potential. `md_lo_sparse_flash_cf12_book02.txt` line 22: `'[IMAGE] — А это не опасно?'` — image merged with following dialogue, creates a block where image words and dialogue words are combined, potentially creating false subsequence matches.

---

### F9 — MED: reader_llm.py: temperature=0.3, single reader, zero reproducibility check

**Evidence:**

`reader_llm.py:59`: `"temperature": 0.3`. Only one reader file exists: `data/reader/reader_mdmode_flash.json`. The notebook presents friction points as "catches REAL grouping errors" but does not acknowledge that with temperature>0, the same run would produce different friction points. No second run, no cross-reader agreement, no stability check.

The notebook itself says "CAVEAT: temperature 0.3, single reader; would need calibration" — but this caveat does not prevent the claims from being cited as validation evidence. The friction signal is at best directionally suggestive; it cannot be used as a quality gate without reproducibility.

---

### F10 — LOW: oo_dense "GARBAGE" characterization slightly overstated

**Evidence:**

18 of the 26 keys in the common set happen to be VALID (cf02_book25 and part of cf04_book30 where absolute idx = small sequential numbers matching model output). For cards whose absolute paragraph indices start near 0, the oo_dense/lo_sparse outputs are interpretable. The κ=0.23–0.32 for these framings on 26 keys may partially reflect genuine model behavior on those 3 cards. This doesn't rescue oo/lo as framings (the other 10 cards produce garbage), but the "confirms they're broken" characterization from 26-key scoring is 85% accident.

---

## Sound findings

### S1 — SOUND: kappa values in the full comparison are exactly correct

The five framing κ values (0.348/0.489/0.496/0.803/0.832) were independently re-derived from the raw pred files:

```python
MDIO_free:      0.348  ✓
MDIO_oo_dense:  0.489  ✓
RB_mdmode:      0.496  ✓
MDIO_lo_sparse: 0.803  ✓
RB_perline:     0.832  ✓
```

Cross-framing κ values for flash (RB_perline vs MDIO_lo_sparse = 0.713; vs RB_mdmode = 0.462; vs MDIO_free = 0.377) also verified. Numbers are correct.

### S2 — SOUND: extraction path asymmetry is zero

Both `read_back()` implementations (rebench.py and mdio.py) produce **identical** labels when applied to the same markdown output (288/308 keys tested, 0 disagreements, κ=1.000 on par-level single-line keys). The different heading-strip regex (`re.sub` in mdio vs none in rebench) has no functional effect because heading content is not `\w+` tokens that would contaminate `_sub()` matching. The conclusion that "cross-framing κ differences are real framing effects" survives.

### S3 — SOUND: 135 single-line key construction is valid

The 39 multi-line pars (nlines>1) are correctly identified from ir_view and excluded from the 135-key set. The 135 keys span all 13 cards. Par-level collapse for single-line keys = identity (majority of 1 vote). No collapse rule inflation on the 135-key comparison.

---

## What the extraction-path comparison proves

The notebook's deepest question: "is the κ difference between JSON-framings and md-out-framings measuring model behavior or measurement asymmetry?" Answer: it is measuring model behavior. Same md output → same labels from both read_back implementations (S2). The κ=0.381 between `mdmode_flash` (rebench) and `free_flash` (mdio) is genuine framing divergence, not extraction noise. The main conclusion "framing changes the answer" is correct.

The low `MDIO_free` κ=0.348 has a partial confound: `free_owl` lineated rate (0.681) diverges from `free_flash` (0.407) by 27pp — larger than the ~23pp "framing effect" claim. So some of the `MDIO_free` instability is inter-model variance under this framing, not pure within-model framing sensitivity.
