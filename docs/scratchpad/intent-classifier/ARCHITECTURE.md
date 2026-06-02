# Pancratius: a document compiler with a learned pass

Status: **target architecture** — the end-state shape, not the migration plan. No
production code changed by this document. Empirical claims measured across the 75-book
corpus (pandoc 3.9). Acceptance criteria (§13) describe what an implementation MUST prove,
not facts already true. **§14 is an adversarial pressure-test that corrects several
earlier claims (P1-P7) with measurements — read §14 against §2-§6; where they conflict,
§14 wins.** When approved, the durable parts move into `docs/architecture.md` +
`docs/tooling.md`; this file stays as the rationale.

Two things share one substrate:
1. the **document compiler** (DOCX → IR → Markdown/EPUB/PDF/site), and
2. the **ground-truth dataset + learned classifier** that decides the compiler's hardest
   pass (per-line prose-vs-verse **lineation**).

Thesis: this is a compiler whose hardest enrichment pass is *learned*. The dataset the
classifier trains on is the compiler's own IR observed **at a named pass boundary**, with
full source provenance — not a research script. Design it that way and the bug class (a
labelling tool that re-guesses structure the compiler already knows) is impossible by
construction.

---

## 1. The shape: an hourglass

Many source formats narrow to one IR; an ordered chain of enrichment passes annotates it;
then it widens to many representations. Top-to-bottom is the data flow.

```
   FRONT-ENDS  (format-specific, WIDE)
        DOCX ─┐  DocxFrontend: Pandoc AST (content) ⨝ DocxSource (trivia)
       (…)  ─┤    via RECONCILIATION → Document + SourceMap + diagnostics   [§2]
             ▼
   ┌─────────────────────────────────────────┐
   │  IR  — the WAIST: one CANONICAL compiler  │   nodes.py + NodeId + SourceMap
   │  IR carrying normalized source-layout     │   Not "purely format-neutral": it owns
   │  trivia. Nothing raw-DOCX-shaped survives.│   normalized layout evidence (§3.3).
   └─────────────────────────────────────────┘
             │
   ENRICHMENTS  (ordered passes over the IR — the EXTENSION POINT)
             │   structural enrichment : headings, signature, epigraph, dialogue,
             │                           thematic, bibliography-lift, endmatter-strip
             │   ── source-view observed HERE: after structural, before semantic ──
             │   semantic enrichment   : lineation (LEARNED) → verse-register → …
             │                           Open-ended. A semantic pass adds typed meaning;
             │                           "register" (verse) is ONE kind — an additive
             │                           annotation on lineated IR. Others (QA-block
             │                           structure, numbered-section semantics, callouts)
             │                           may be semantic STRUCTURE, not register, and need
             │                           not depend on lineation. The arch holds because
             │                           enrichments are an append-only ordered list.
             ▼
   ┌─────────────────────────────────────────┐
   │  enriched IR                            │
   └─────────────────────────────────────────┘
             │
   BACK-ENDS  (representations, WIDE)
        ├─► Markdown   (lower.py — by hand, no pandoc; the canonical author surface)
        ├─► EPUB / PDF (downloads)   └─► site (Astro renders the Markdown)
```

Verified facts the shape rests on:
- **Pandoc is a front-end only.** `lower.py:954` emits Markdown ourselves; Pandoc never
  sees the output side.
- **The middle/back-ends are format-neutral about *source format*** (only the front-end
  imports pandoc/zipfile) **but the IR is NOT content-only** — it carries normalized
  source-layout trivia that semantic passes read (§3.3). Stated honestly, not both ways.
- **`lineated_blocks` MERGES** N source paragraphs into one block, dropping per-paragraph
  ownership (normalize.py:1031/1060) — which is exactly why the source-view is observed
  *before* the semantic passes (§5), never recovered from the final IR.

### 1.2 Why Pandoc is the content authority (rejected: "read OOXML ourselves")
Emphasis is pervasively carried by *named character styles* (`w:rStyle`, 7774 uses) —
book 40 applies an italic char-style to **9763** runs, book 03 a bold one to **3245** —
plus `w:b val="0"` toggle-off (1033), hyperlinks via `_rels` (708), field codes (438).
Reproducing inline emphasis from raw runs = reimplementing OOXML style-chain resolution +
toggle algebra + field/hyperlink resolution = reimplementing Pandoc. Proof it's a trap:
the orphan `ooxml.py` already computes bold/italic from direct props only and is **wrong**
for every style-inherited run. We ask Pandoc for content, OOXML for the trivia it drops —
never the reverse.

---

## 2. Front-end: `DocxFrontend` = source reader + reconciliation (the hard part, named)

The previous draft called the source-view a "pure JOIN keyed by ordinal" and hid the
single hardest piece of the front-end: **Pandoc does not emit stable DOCX paragraph
ordinals.** The join is only possible because a fingerprint-matching **reconciliation**
already aligned the Pandoc AST to the OOXML paragraph stream (today: `_assign_source_spans`
+ `reconcile_alignment`, docx_adapter.py:551-605 — exact match, multi-record fusion,
ambiguous/collapsed → unmapped). Make it a first-class component (finding #2):

```python
# pancratius/corpus/docx/frontend.py
@dataclass(frozen=True)
class FrontendResult:
    document: ir.Document       # typed content (from Pandoc AST)
    source: DocxSource          # the OOXML trivia stream (§2.1)
    sourcemap: SourceMap        # AST↔source alignment, incl. unmapped/ambiguous (§3.2)
    diagnostics: tuple[ir.Diagnostic, ...]

def read_docx(docx: Path) -> FrontendResult: ...   # owns reconciliation; import-pure
```

Reconciliation is where ambiguity is *born*, so it is also where ambiguity is *recorded*
(into `SourceMap`), not silently dropped. Nothing downstream re-derives source alignment.

### 2.1 `DocxSource` — the one source reader (collapses FOUR walks)
Four inconsistent OOXML walks read `word/document.xml` today, several coupled by comments
("same walk as read_rows"): `ooxml.py`, `docx_adapter.read_w_jc`, `docx_inspect.read_rows`,
`docx_render._ordered_paragraphs`. The fifth — `ir_view` — was the shadow classifier. One
reader replaces all source-access.

The stream is **not** a flat paragraph list (findings #3): a `w:tbl` sits *between*
paragraphs and its cells are paragraphs *inside* it; a body may open with a table or have
adjacent tables. Model a typed item stream with **`SourceAddress`** identity, not a bare
ordinal:

```python
# pancratius/corpus/docx/source.py
@dataclass(frozen=True)
class SourceAddress:
    """Stable identity of a source paragraph. `stream_index` orders ALL items (paras,
    tables, boundaries) in document order. `cell` is set ONLY for a table-cell paragraph
    (table, row, col, inner-paragraph), which has no top-level body ordinal."""
    stream_index: int
    body_ordinal: int | None = None        # top-level <w:p> ordinal; None for cell paras
    cell: tuple[int, int, int, int] | None = None   # (table_index,row,col,inner_p)

@dataclass(frozen=True)
class Indent:  left:int|None=None; first_line:int|None=None; hanging:int|None=None  # twips
@dataclass(frozen=True)
class Spacing: before:int|None=None; after:int|None=None; before_auto=False; after_auto=False

@dataclass(frozen=True)
class ParagraphItem:
    address: SourceAddress
    match_text: str         # OOXML reading text, <w:br>→space. For AST MATCHING + empty/
                            # thematic detection ONLY. NOT display truth (that is IR inlines).
    empty: bool; br_count: int
    align: str; indent: Indent; spacing: Spacing
    numbered: bool; bordered: bool; contextual: bool
    style: str; direct_style: str; is_heading_style: bool; is_thematic_glyph: bool
    lineation_group: int|None
    xml_ref: XmlRef         # handle to the live w:p element for the slice service (§2.3)

@dataclass(frozen=True)
class TableItem:    address: SourceAddress; cells: tuple[tuple[tuple[ParagraphItem,...],...],...]
@dataclass(frozen=True)
class BoundaryItem: address: SourceAddress; kind: str   # sdt edge / section break / …
type SourceItem = ParagraphItem | TableItem | BoundaryItem

@dataclass(frozen=True)
class DocxSource:
    items: tuple[SourceItem, ...]            # the ordered source stream (the real shape)
    package: DocxPackageHash                 # §2.2
    def at(self, addr: SourceAddress) -> SourceItem: ...

def read_docx_source(docx: Path) -> DocxSource: ...   # ONE walk, import-pure
```

Derivations (every prior walk collapses onto this):

| was | becomes |
|---|---|
| `docx_inspect.read_rows`/`ParaRow` | iterate `DocxSource.items` |
| `docx_adapter.read_w_jc` (+ sentinels) | derived reconciliation projection over `items`; bespoke walk deleted |
| `docx_render._ordered_paragraphs` | the **slice service** (§2.3) — it needs XML, not ordinals (finding #6) |
| `ooxml.read_docx_paragraph_meta` (orphan, wrong emphasis) | deleted; `docx_conversion` re-points (§2.4) |

`lineation_group` (pure-OOXML contextual-spacing grouping) is assigned here. It is
incidental spacing, **not** lineation truth — exposed as one signal, never a decision.

### 2.2 Package hash, not document hash (finding #6 + smaller issue)
```python
@dataclass(frozen=True)
class DocxPackageHash:
    parts: tuple[tuple[str, str], ...]   # sorted (part_name, sha256) over document/styles/
                                         # numbering/rels/footnotes/endnotes + each media file
    def digest(self) -> str: ...         # stable combined hash for the manifest (§8)
```
A precise, sorted per-part/per-media hash — not a single fuzzy `media: str|None`.

### 2.3 The slice service (finding #6 — `_ordered_paragraphs` needs XML, not ordinals)
`docx_render.slice_docx` re-parents real `w:p` elements and strips drawings to render a
layout-faithful PNG slice (docx_render.py:99-111). An ordinal list cannot do that. So
`ParagraphItem` carries an `XmlRef` (a handle into the parsed tree the source reader
already holds), and slicing is a named service over `DocxSource`:

```python
def slice_source(source: DocxSource, addresses: Sequence[SourceAddress], dest: Path) -> Path: ...
```
The body-`<w:p>` walk lives in ONE place (the source reader); the renderer consumes
`XmlRef`s. The "same walk, same indices" comment-invariant is gone.

### 2.4 Poem-title strip re-point (smaller issue — concrete path)
`docx_conversion.py:239` uses the orphan `ooxml.read_docx_paragraph_meta` (wrong-emphasis)
only to detect a duplicated title line. Re-point it to read `DocxSource` paragraph trivia
(align/style/`match_text`) for the title match; emphasis is irrelevant to that check, so
the corrected reader is strictly better. Its golden is re-verified; any diff is the
latent emphasis bug-fix, documented in the PR.

---

## 3. The waist: IR + node identity + `SourceMap`

### 3.1 Node identity vs lineage (finding #4 — these were conflated)
Two distinct concepts, named separately:
```python
type NodeId = int     # IDENTITY of a block instance. Assigned by Document at creation.
                      # A rewrite that produces a new block produces a NEW NodeId.
# LINEAGE travels in the SourceMap, not in the id:
@dataclass(frozen=True)
class Origin:
    node: NodeId
    origin_nodes: tuple[NodeId, ...]   # the block(s) this one was rewritten from ([] if leaf)
    rewrite: Literal["create","merge","split","annotate","drop","lift"]
```
"Ids don't survive rewrites; lineage does." A merge creates a new `NodeId` whose `Origin`
lists its inputs; a split creates child ids sharing one origin. No object identity, no
"same id across a rewrite" contradiction.

### 3.2 `SourceMap` — provenance and fate are SEPARATE (findings #1, #5)
`provenance(block)` cannot return `dropped` — a dropped paragraph has no block. Split the
two concepts:
```python
# pancratius/corpus/ir/sourcemap.py
type BlockRelation = Literal["one","merged","split","collapsed","synthetic"]  # block↔source
type SourceFate    = Literal["present","dropped_toc","stripped_endmatter",
                             "lifted_bibliography","dropped_empty_heading","scrubbed"]

class SourceMap:
    def provenance(self, node: NodeId) -> tuple[SourceAddress, ...]: ...   # what fed this block
    def relation(self, node: NodeId) -> BlockRelation: ...
    def blocks_at(self, addr: SourceAddress) -> tuple[NodeId, ...]: ...     # 0 ⇒ see fate; >1 ⇒ split
    def fate(self, addr: SourceAddress) -> SourceFate: ...                  # every source para has one
    def lineage(self, node: NodeId) -> Origin: ...
```
`merge_source_spans` (nodes.py) is subsumed. Every source paragraph has exactly one fate;
every block has provenance + relation + lineage. Ambiguity is recorded at reconciliation
(§2), never invented.

### 3.3 Trivia ownership — ONE model, stated (findings #1, #4 from both reviewers)
The IR is a **canonical compiler IR carrying normalized source-layout trivia** — not a
content-only neutral tree. Production passes already depend on it (`Paragraph.indented`
read at normalize.py:1378/1405). Own it: replace the lossy `Paragraph.indented` bool with
`Paragraph.trivia: SourceTrivia` (the indent/spacing subset passes use), attached by the
front-end. The source-view reads the *same* `ParagraphItem` off `DocxSource`. One source
of trivia, two readers. The side-table alternative is rejected: it hides a dependency the
IR contract already has.

---

## 4. The enrichment chain is an ordered, observable pass list (replaces the `Stage` enum)
Ordinary compiler debug capability: enrichments are a **named ordered list of passes**, and
you can run the pipeline up to a named pass and observe the IR there.
```python
# pancratius/corpus/ir/pipeline.py
class PassId(StrEnum):                     # typed, not a raw string (smaller issue)
    DROP_TOC = "drop_toc"; …; DIALOGUE_LABELS = "dialogue_labels"
    LINEATION = "lineation"; VERSE_REGISTER = "verse_register"
STRUCTURAL_PASSES: list[Pass]   # … through DIALOGUE_LABELS
SEMANTIC_PASSES:   list[Pass]   # LINEATION (learned, §9) → VERSE_REGISTER → … (append-only)

def run(doc, *, until: PassId | None = None) -> ir.Document:
    """Apply passes in order; stop after `until` (None = all). normalize(doc)==run(doc);
    identical output to today's chain → equivalence goldens hold (§13)."""
```
- whole compiler = `run(doc)`.
- IR after structure, before any semantic decision = `run(doc, until=PassId.DIALOGUE_LABELS)`
  — the snapshot the source-view is built on (§5). No enum filter trick; just "stop after
  pass N." Appending a future semantic pass does not move the source-view boundary.

---

## 5. The source-view: structural IR, projected — items → fragments → display lines

### 5.1 One paragraph is not one role (findings #2, #3, #7)
Production already splits a `<w:p>` into `[DialogueLabel, Paragraph]` (verified:
`_emit_dialogue_segment`, normalize.py:831-880, both stamped the same span) and multi-turn
paragraphs into many `[label, body, label, body…]`. And `<w:br>` cuts display lines within
a paragraph. So the view is items → **fragments** → **display lines**, and **structure
(role) is separate from votability** (finding #7):

```python
# pancratius/ml/lineation/sourceview.py
class FragmentRole(StrEnum):       # the STRUCTURAL-IR block kind. STRUCTURE only.
    PROSE; EMPTY; HEADING; THEMATIC; LIST; TABLE; SIGNATURE; EPIGRAPH
    DIALOGUE_LABEL; BLOCKQUOTE; IMAGE; AMBIGUOUS; UNKNOWN   # NO lineated/verse (§4)

@dataclass(frozen=True)
class DisplayLine:                 # one <w:br> segment — the label/render grain (§6)
    line: int                      # 0-based display-line index within the paragraph
    text: str; md: str; html: str; bold: bool; italic: bool   # text+emphasis FROM IR INLINES
    is_lineation_candidate: bool   # SEPARATE from role: True iff a votable display line
                                   # (a PROSE/DIALOGUE-body line, not a heading/table/etc).

@dataclass(frozen=True)
class SourceFragment:
    role: FragmentRole
    node: NodeId | None            # the structural-IR block (None for empty/dropped/lifted)
    lines: tuple[DisplayLine, ...]

@dataclass(frozen=True)
class SourceParagraphView:
    address: SourceAddress
    fragments: tuple[SourceFragment, ...]   # usually 1; >1 for label+body, multi-turn, …
    trivia: ParagraphItem                    # lossless physical record, verbatim
    fate: SourceFate                         # §3.2 — accounts for 100% of source paragraphs

@dataclass(frozen=True)
class SourceView:
    paragraphs: tuple[SourceParagraphView, ...]
    tables: tuple[TableView, ...]            # cell text as context; never candidates
    package: DocxPackageHash

def build_source_view(docx: Path) -> SourceView:
    """Pure JOIN keyed on SourceAddress of three providers from read_docx +
    run(until=DIALOGUE_LABELS): trivia ← DocxSource; fragments/roles ← structural-IR via
    SourceMap; display lines+emphasis ← IR inline tree (inline_lines, soft_break=False).
    NO heuristics. Absent ⇒ absent, never guessed. Review gate: no endswith / len(...)<= /
    '***' / neighbour-scan in this module."""
```

**Votability rule, decided (finding #7):** `FragmentRole` is structure; whether a line is
labelled is `DisplayLine.is_lineation_candidate`. A `label + body` paragraph has a
`DIALOGUE_LABEL` fragment (its label line is *not* a candidate) and a `PROSE` fragment
(its body lines *are*). So "label and body are independently addressable" and "only votable
lines are voted" are both true without contradiction. The classifier emits a typed
`LineationLabel ∈ {prose, lineated}` for candidate lines only.

### 5.2 Every source paragraph has a fate (finding #5)
The view is taken after `drop_toc`/bibliography-lift/endmatter-strip, so some paragraphs
became no block. They stay in the view with a `SourceFate` and zero candidate lines —
shown as context. Nothing vanishes silently.

---

## 6. Display lines (the grain), defined (prior "subline" was a coinage)
- A **run** is an emphasis span (`<w:r>` / `Strong`/`Emph`) — *finer* than a line; kept as
  IR inlines. NOT the grain.
- A **display line** is a paragraph segment between hard `<w:br>` breaks. `br_count = 2`
  ⇒ 3 display lines. THIS is the grain, keyed `(SourceAddress, line)`.
- Why: prose-vs-lineated is per-display-line; the committed dataset already keys it; and
  g05 (`**1. Вода**`<br>`Мир — как река…` — one paragraph, a list-label line then a body
  line) needs two labels in one paragraph, expressible only at display-line grain.

## 7. `LineationPlan` — the candidate renderer is designed, not asserted
g05 is fixed here, by a pure render at display-line grain:
```python
# pancratius/ml/lineation/render.py
@dataclass(frozen=True)
class LineationPlan:
    """Pure fn of DisplayLine[] (candidates) + per-(address,line) LineationLabel → HTML.
    Grain-exact: groups by LABEL at display-line resolution; a label line and a body line
    in ONE paragraph render differently and never fuse."""
    units: tuple[tuple[DisplayLine, LineationLabel], ...]
def render_candidate(view: SourceView, labels: dict[LineKey, LineationLabel]) -> str: ...
```
Contiguous same-label lines may share a block for readability, but a label boundary always
splits; lineated lines are `<br>`-joined within a stanza. The three-panel
prose/lineated/DOCX composite is built from this one function — no per-paragraph fuse
anywhere.

## 8. Manifest + cache invalidation (finding #6)
Every package writes a manifest; nothing reused on a bare filename:
```json
{ "schema":"sourceview/4", "docx_package_digest":"…", "code_git_sha":"…",
  "pandoc":"3.9","libreoffice":"24.8","render":{"width":680,"dpi":144},
  "panel":{"models":[…],"prompt_sha256":"…"}, "linekey_format":"address.line" }
```
Renders/HTML content-addressed by `(docx_package_digest, address-range, render-params)`;
`--force` refreshes. The current `comp_path.exists()` check (gold_build.py:386) — stale
silent reuse — is eliminated.

---

## 9. The learned pass as a compiler pass (finding #9 — the missing contract)
"Compiler with a learned pass" is only real if the distilled classifier has a *pass
contract*. The `LINEATION` semantic pass (§4) is:

```python
# pancratius/corpus/ir/passes/lineation.py  (the PRODUCTION pass; loads a frozen model)
@dataclass(frozen=True)
class LineationModel:
    artifact_sha256: str            # the frozen model bytes (committed/LFS), in the manifest
    feature_schema: str             # versioned input contract (must match build-time)
    trained_git_sha: str
def lineation_pass(doc, model: LineationModel) -> ir.Document: ...
```

Contract, explicit:
- **Input/feature schema** is versioned; the pass asserts the model's `feature_schema`
  matches the features it extracts (a mismatch is fatal, not silent).
- **Confidence / abstain:** the model emits `(LineationLabel, confidence)`. Below a
  threshold it **abstains**, and the pass falls back to the deterministic heuristic
  currently in `lineated_blocks` — so the compiler never blocks on low confidence.
- **Human override:** a per-book/per-address override table (committed) wins over the model
  unconditionally; the model never overrides a human decision.
- **Determinism:** the production pass is deterministic given (model artifact, IR) — no
  network, no sampling. (The *panel readers* in §10 are the non-deterministic part, and
  they live in the dataset pipeline, never in the compiler.)
- **Split by BOOK, not by line:** train/val/test partition at the book level so no book's
  lines straddle splits (leakage control). Recorded in the dataset manifest.
- **Reproducibility:** model artifact hash + feature schema + trained git sha travel in the
  manifest (§8); a build is reproducible or it is rejected.

This is the seam where ML becomes compilation: a frozen, versioned, abstaining,
human-overridable, deterministic pass with a fallback.

---

## 10. The ML half: home, naming, write boundary, and the studio

The dataset exists only to train the **lineation** classifier; it lives under `ml/`,
namespaced by the decision learned (`lineation/`; a future verse-register classifier →
`ml/register/`). It is NOT `corpus` (the compiler) — `corpus` produces the substrate, `ml`
consumes it to learn a pass.

```
pancratius/ml/lineation/      (eval + training stack for the lineation pass)
  sourceview.py  render.py  dataset.py        ← deterministic, pure (read corpus IR)
  readers/       LLM panel adapters            ← EXTERNAL I/O, costs, non-deterministic
  adjudicate.py  features.py model.py distill.py
pancratius/ml/studio/         (the annotation & evaluation STUDIO — see §10.1)
```

CLI verbs split by **nature of work** (`gold` is gone):
- `uv run pancratius ml lineation dataset build|package` — deterministic assembly + manifests.
- `uv run pancratius ml lineation readers run --panel …` — paid, non-deterministic passes.
- `uv run pancratius ml lineation dataset ingest-responses` — validate+manifest+write canonical artifacts (§10.2).
- `uv run pancratius ml lineation {adjudicate,distill}` — consensus + training.

### 10.1 `ml/studio/` — the annotation & evaluation studio (not "ui")
A task-loading **annotation & evaluation studio**: it loads a *task* (a packaged dataset +
candidate renders + panel responses) and supports human labelling, panel/model
disagreement review, and adjudication — across classifiers, not one desk per model. It is
internal eval tooling (model/dataset-specific), so it lives in `ml/`, NOT the public site
— a reviewer opening `ml/` finds the whole eval stack (substrate → readers → studio →
distill) in one place (grep coherence + interview legibility). Placed at `ml/studio/`
(sibling to the classifiers) precisely because it is *general* across tasks, not a
per-classifier desk. It is its own npm workspace.

### 10.2 Write boundary + monorepo integration (finding #7 + smaller issue)
- **The studio never writes committed dataset files.** It **exports** human/panel responses
  (plain JSON); `pancratius ml lineation dataset ingest-responses` validates against the
  schema, manifests, and writes the canonical artifacts. Data mutation stays in Python
  library tooling.
- **Monorepo contract:** `ml/studio/` is a separate npm workspace, excluded from the public
  site build; root `npm run verify`/CI treat it as an independent workspace (its own
  lint/test target, not part of the site's). The repo's two-entry-point split holds by
  *role* — public site (`npm run`) vs internal eval studio (its own workspace) vs Python
  library (`uv run pancratius`).

---

## 11. Target package layout (the tree narrates the system)
```
pancratius/
  corpus/                  # the document COMPILER over the book corpus
    docx/  frontend.py source.py slice.py adapter.py render.py optimize.py merge.py
           outline.py inspect.py
    ir/    nodes.py sourcemap.py pipeline.py normalize.py lower.py passes/lineation.py
    catalog.py cross_refs.py …
  ml/                      # LEARNED passes: ground truth → model, per decision
    lineation/  sourceview.py render.py dataset.py readers/ adjudicate.py
                features.py model.py distill.py
    studio/                # annotation & evaluation studio (npm workspace)
  site/        downloads, video, conceptosphere generators
  cli.py paths.py __init__.py
```
`corpus/ir/{sourcemap,pipeline,passes/lineation}` reads as a real compiler with debug-info,
an observable pipeline, and a learned pass; `ml/{lineation,studio}` reads as a real ML eval
problem with judgment about determinism, cost, and human-in-the-loop. CLI groups
(`corpus`/`ml`/`site`) match the packages. (One-wave-vs-several is a *planning* concern,
out of scope here.)

## 12. Production-IR gaps surfaced (fixed at the compiler, not the view)
- **G1** literal `***` kept as text, not `ThematicBreak` (normalize.py:634) — teach
  `thematic_breaks` the all-glyph form. `is_thematic_glyph` is advisory only.
- **G2** no typed "numbered-label + body" item — display-line grain + fragments carry it;
  a typed kind only if systematic.
- **G3** `AMBIGUOUS` fragments / split relations surfaced via `SourceMap`, never guessed.

## 13. Acceptance criteria — phase-separated (MUST prove, not yet true)
1. **Refactor equivalence** — `normalize`/`lower`/`docx_adapter`/`docx_render`/`docx_inspect`
   goldens byte-identical after the four-walk collapse, `DocxFrontend`+reconciliation,
   `pipeline.run`, NodeId+`SourceMap`, and `indented`→`trivia`. (`docx_conversion` diff =
   the documented emphasis bug-fix.)
2. **Source-view correctness** — every source `SourceAddress` has exactly one `SourceFate`;
   PRESENT paragraphs' fragments cover their display lines; role↔kind total `match`
   (`assert_never`); `SourceMap` relation/lineage correct on merge/split/collapse/drop/lift
   fixtures.
3. **Candidate-render regression** — `LineationPlan` on g05 (label+body), `***`-glued
   heading, em-dash couplet; grain == keys == render.
4. **Learned-pass contract** — feature-schema mismatch is fatal; abstain falls back to the
   deterministic path; human override wins; the production pass is deterministic; splits are
   by book.
5. **Production behavior-change** — G1 `***`→ThematicBreak and hard-break-leakage fixes get
   their own RED→GREEN tests (these *intend* golden diffs).

---

## 14. Adversarial pressure-test — findings that CHANGE the design (measured)

A self-review hunted the claims I had asserted without verifying. These are measured
against books 03/13/40, and several invalidate earlier wording. They are load-bearing.

### P1 — The source map is PARTIAL and best-effort, not total (corrects §3.2, §5.2)
Measured (book 13): after `adapt`, **2674 / 2690 blocks carry a `source_span`; 16 do
not**; after full normalize, 2569 / 2572. Reconciliation (`reconcile_alignment`,
docx_adapter.py:608) is **fuzzy fingerprint matching** that *explicitly* skips records
whose text never surfaces and leaves collapsed/ambiguous blocks unmapped
(`_assign_source_spans`, "ambiguous or collapsed shapes stay None rather than inventing a
source location"). So:
- "every block has provenance" is **false**. The `SourceMap` must model **`UNMAPPED`** as
  a first-class block state, and `provenance(node)` returns `None` legitimately.
- "every source paragraph has exactly one fate" holds only if `fate` also has an
  **`UNRECONCILED`** value for source paragraphs whose text never bound to a block. ~16/
  book is small but non-zero and clusters on exactly the hard shapes the gold set cares
  about. The dataset MUST report its mapped-coverage per book and exclude unmapped lines
  from voting (they cannot be rendered faithfully against a known block).

### P2 — `SourceAddress` for short repeated lines is POSITION-INFERRED, not certain (corrects §2.1, §5)
Measured (book 13): **38 distinct reading-texts collide**, one appearing **15×** (dialogue
dashes, refrains). Reconciliation disambiguates collisions only by an advancing `cursor`
(position), not by identity. Consequence: for a short repeated line, the `(SourceAddress,
line)` key is an *inference*, and a mis-bind silently mislabels. The dataset MUST:
- carry a `match_confidence ∈ {exact, fused, positional}` on each address-binding, and
- treat `positional` bindings of colliding texts as a review/abstain class, not silent
  ground truth. This is precisely the "don't smuggle ambiguity back in" requirement.

### P3 — Display-line count ≠ `br_count + 1` (corrects §6 — the grain definition was wrong)
Measured: **107 paragraphs in book 03** (6 in book 40) where non-empty `inline_lines`
count ≠ `br_count + 1` (empty lines filtered, breaks nested in emphasis, consecutive
breaks collapsed). So the `line` index is **NOT** a function of raw `<w:br>` position. The
grain MUST be defined as the **index into the canonical `inline_lines(inlines,
soft_break=False)` output after the same empty-line filter the renderer uses** — one
function, shared by the listing, the keys, and `LineationPlan`. If the dataset keys by raw
break position and the renderer by filtered lines, keys and renders desync — the exact bug
class this whole effort exists to kill. Add an acceptance test: `key.line` round-trips
through the renderer's own line enumeration for every candidate line.

### P4 — Tables are UNMAPPED today; the cell-address model is aspirational (corrects §2.1, §5)
Measured: **0 of 3 tables across books 03/13/40 carry a `source_span`.** Pandoc table
reconciliation does not exist. So `TableItem.cells` with per-cell `SourceAddress` describes
a capability the pipeline lacks. Decision: tables are **never lineation candidates** (a
table cell is not prose-vs-verse), so the gold scope **excludes table interiors** — the
view surfaces a `TABLE` fragment as opaque context with no votable lines, and cell-level
`SourceAddress` is explicitly **out of scope** (not "via SourceMap"). If table-cell
lineation ever matters, it is a separate front-end capability with its own reconciliation,
flagged here, not assumed.

### P5 — `site/` is not part of the compiler; it consumes the compiler's OUTPUT (corrects §11)
Measured: `conceptosphere`, `video_*`, `render_downloads` import **neither `ir` nor
`docx_adapter`** — they read `CONTENT_ROOT`/`DATA_ROOT` (the *built* Markdown/committed
data). So `site/` is a **downstream consumer of representations**, not a sibling of the
compiler. The tree should say that: it is "tooling over the published corpus," at the same
conceptual level as a reader of the back-end output — arguably its own top-level
(`publish/` or `corpus_tooling/`), not a vague `site/`. The compiler (`corpus/`) and the
learned-pass stack (`ml/`) are the two things that share the IR; `site/` shares only the
*output*. Lumping it beside them blurred the very boundary the doc is about.

### P6 — `XmlRef` lifecycle is unspecified and `DocxSource` is then not a pure value (open)
`slice_docx` needs live `w:p` elements (verified, §2.3), but `read_docx_source` parses the
tree inside a `with zipfile…` that closes. An `XmlRef` into a discarded tree dangles.
Resolution to specify: the slice service **re-parses from `docx` + `SourceAddress`** (the
address already encodes the body/cell path) rather than holding live elements — so
`DocxSource` stays an immutable value and slicing is a pure `(docx, addresses) → docx`
function. Drop `XmlRef` from `ParagraphItem`; the address IS the handle. (This reverses the
§2.1 `xml_ref` field — corrected here.)

### P7 — `lineation_group` already changes the verse decision; the source-view must not leak it as truth (watch)
`reconcile_alignment` assigns `lineation_group`, and `lineated_blocks` branches on it
(normalize.py:1089). The source-view exposes it as a *signal* (§2.1), which is correct —
but because production *acts on it*, a reviewer of the gold labels could anchor on it and
reproduce the converter's bias. Mitigation: the studio (§10.1) MUST present candidate lines
**without** the converter's lineation_group-derived grouping visible as a hint; it is a
feature column for the model, not a visual prior for the human labeler. (Behavioral
requirement on the studio, not the IR.)

### What held up
The hourglass, the single `DocxSource` reader collapsing four walks, `NodeId`≠lineage, the
observable pass pipeline / structural-boundary snapshot, fragments-not-roles, and the
learned-pass contract all survived the pass. The corrections are about **honesty over
coverage** (P1/P2/P4: the map is partial, addresses are sometimes inferred, tables aren't
mapped) and **grain precision** (P3: define `line` by the renderer's own enumeration), plus
two boundary fixes (P5 `site/`, P6 `XmlRef`).

---

## 15. The `site/` bucket — there is no good sense in it (measured)

`conceptosphere`, `conceptosphere_embed`, `video_*`, `render_downloads` import **neither
`ir` nor `docx_adapter`**; they read `CONTENT_ROOT`/`DATA_ROOT` (built Markdown + committed
data). And the package already calls itself a *corpus* in its own docstrings
("corpus-management tooling," "Sergey Orekhov's corpus," "local corpus tooling"). So:

- **`corpus/` is not an invented name** — it is the repo's existing self-description: the
  tooling that manages the book corpus (import compiler + content model + the generators
  that derive committed data *from* the corpus).
- **`site/` was a bad bucket.** These modules are not "the site" (the site is Astro/`npm`).
  They are corpus-derived **data generators** (concept graph, embeddings, video catalogue,
  download artifacts) that produce committed data the site later reads. They belong **in
  `corpus/`** beside the other corpus tooling — they operate on the same corpus, just at a
  different stage (post-Markdown) than the import compiler. No third top-level bucket.

### 15.1 Drop `corpus/` — the package root IS the corpus (chosen layout)

`pancratius` the package *is* the corpus tooling (its own docstrings say so). A `corpus/`
subdir would stutter: `pancratius.corpus.ir` says "corpus" twice. Without it, the compiler
groups sit at the package root, which reads as a flatter, more honest compiler. The cut is
then **by role**, as sibling sub-packages — not one mega-bucket:

**Folder depth follows actual size/surface** (measured): a *subsystem* earns a folder; a
*single-purpose script* stays a single file. No artificial umbrella (`generate/`) lumping
unlike things. The package top level then mirrors the CLI verbs 1:1.

```
pancratius/
│
│  ── the COMPILER (DOCX → IR → Markdown) ────────────────────────────────────
├── ir/                     # the INTERMEDIATE REPRESENTATION (keep the name — 15.2)
│   ├── nodes.py            #   typed block/inline tree + NodeId  (the data structure)
│   ├── sourcemap.py        #   block↔source provenance/fate/lineage (partial; §14-P1)
│   └── lower.py            #   IR → Markdown back-end (by hand; the canonical emitter)
│
├── passes/                 # ordered IR→IR PASSES (LLVM/GCC sense) — the extension point
│   ├── pipeline.py         #   PassId list + run(doc, until=…) — the observable chain (§4)
│   ├── structural.py       #   headings/signature/epigraph/dialogue/thematic/bib-lift/…
│   └── lineation.py        #   the LEARNED semantic pass (model contract, §9)
│   (today's monolithic normalize.py splits into structural.py + lineation.py)
│
├── docx/                   # the DOCX FRONT-END (the only format-specific code)
│   ├── source.py           #   DocxSource: the ONE OOXML reader (collapses 4 walks)
│   ├── frontend.py         #   Pandoc AST ⨝ source via reconciliation → Document+SourceMap
│   ├── adapter.py          #   Pandoc-node → ir.Block lowering
│   ├── slice.py            #   layout-faithful w:p slice service (re-parse by address; §14-P6)
│   ├── inspect.py          #   read-only fidelity diagnostic (thin; prints over the above)
│   └── optimize.py merge.py outline.py    # source-DOCX maintenance (image cap, multipart)
│
├── importer/               # the IMPORT operation = the one FILESYSTEM-MUTATION boundary
│   ├── writeplan.py        #   WritePlan — import's safety boundary (plan before write)
│   ├── writer.py           #   the one mutator (every other module is import-pure)
│   ├── import_docx.py      #   orchestrates docx → passes → lower → write
│   └── svg_sanitize.py footnotes.py poem_title.py    # asset/footnote/title helpers
│
├── content/                # the CONTENT MODEL (format-neutral facts about the corpus)
│   └── catalog.py cross_refs.py kinds.py locales.py
│
│  ── CORPUS-DERIVED data products (read BUILT data; feed the site) ──────────
├── conceptosphere/         # ★ portfolio subsystem: NLP concept graph + embeddings
│   ├── graph.py            #   igraph/leidenalg/networkx/pymorphy3 concept-graph extraction
│   └── embed.py            #   semantic embeddings (was conceptosphere_embed.py)
├── video/                  # YouTube channel scanner + catalogue
│   └── scan.py channels.py
├── downloads.py            # one script → one file: render release PDF/EPUB
│
│  ── the LEARNED-PASS discipline (its own thing) ───────────────────────────
├── ml/
│   └── lineation/          #   sourceview.py render.py dataset.py readers/ adjudicate.py
│       │                   #   features.py model.py distill.py
│       └── studio/         #   annotation & evaluation studio (npm workspace)
│
├── cli.py                  # the one library door; groups map 1:1 to the dirs above
├── paths.py __init__.py
```

Why this is *better* than `corpus/` (and than `generate/`):
- **The top level reads like a compiler + its products.** `ir/`, `passes/`, `docx/`,
  `importer/` are the compiler; `conceptosphere/`, `video/`, `downloads.py` are the products
  derived from its output; `ml/` is the learned pass. A skimmer gets the whole system from
  the top level. `corpus/` would bury all of this one redundant level down.
- **`docx/` at root names the front-end as replaceable** — a future `epub/`/`html/`
  front-end is an obvious sibling.
- **`conceptosphere/` is top-level, not hidden.** It is 2,053 lines of real NLP (graph
  community detection + embeddings) — the second most portfolio-impressive thing after the
  ML. Burying it in `generate/` undersold it; `generate/` is gone.
- **Depth = surface.** `conceptosphere` (2 modules, 2 CLI verbs) and `video` (2 modules) are
  folders; `downloads` (1 file, 1 verb) is a file. No folder created just to hold one script.
- **`importer/` vs `mutators/`:** `import` is a Python keyword (`from pancratius.import …`
  is illegal), so that name is out regardless. Between `importer/` and `mutators/`: the dir
  holds `import_docx` (orchestrator) + `writeplan` + `writer` + asset helpers. Its *job* is
  the import operation; its *invariant* is "the only filesystem mutator." Name it after the
  job, not the invariant — **`importer/`**. Reasons: (a) it maps 1:1 to the CLI noun
  (`pancratius work import`), so the package mirrors the door; (b) `mutators/` is plural and
  vague — it invites "what else mutates? put it here," eroding the single-mutator boundary
  it was meant to protect; (c) the mutation invariant is already enforced by the
  `# import-pure` markers everywhere *else*, so the boundary doesn't need to be the folder
  name to hold. `importer/` = the operation; "only mutator" = its internal discipline,
  asserted by the pure-markers on every other module.

Prefer this flat-by-role layout unless `pancratius` later grows a second non-corpus domain
(then a top bucket earns its keep).

### 15.2 Keep the name `ir` (not "transpiler"/"compiler"/"converter")
`ir` is the standard term for the typed **intermediate representation** — a *noun*, a data
structure (cf. LLVM IR, Rust MIR). "Transpiler"/"compiler"/"converter" name the *process*
(the whole DOCX→Markdown path is the transpiler; `passes/` + `lower.py` are the compiler
back half). Renaming the data structure after the process is a category error and would
*reduce* legibility for the exact audience (engineers who read `ir/nodes.py` and instantly
know what it is). If spelled out at all, `intermediate/` — but `ir` is the precise,
recognized name; keep it. The reason `ir/` *felt* mis-sized was that it conflated the
representation (`nodes`) with passes over it (`normalize`/`lower`); 15.1 fixes that by
moving the passes to `passes/` and leaving `ir/` as representation + its provenance + the
back-end emitter.

---

## 16. The SMALLEST slice that unblocks ML now (separate from the grand refactor)

The architecture above is the right *end state*. It is **not** what unblocks the ML work,
and conflating the two would stall the ML for a multi-week production refactor that changes
zero labels. Measured reality:

- The current substrate **`ir_view.py` runs today** and already emits the g05 case at the
  correct `(idx, sub)` display-line grain (verified: book 37 idx 388 → `388.0` label line,
  `388.1` body line). Nothing is on fire.
- The ML scratchpad's *entire* production dependency is tiny: `docx_inspect` (5×),
  `docx_render` (1×), `docx_adapter` (1×), `ir.normalize.{inline_lines,inline_plain}` (1×).
- The only thing that **corrupts the dataset** is `ir_view`'s **inference**: across 20
  books / ~34k body paragraphs, the heuristics fire **~1,256×** (195 pseudo-header + 1,061
  speaker-label). Each pulls a paragraph out of the votable pool by a *guess* — biasing
  exactly the ambiguous cases the gold set exists to resolve.

So the minimal unblock is **not** DocxFrontend / SourceMap / NodeId / package reshape. It
is: **replace `ir_view`'s structural GUESSING with a conservative MASK derived from the IR
the compiler already computes.** The key reframing (from the Codex pressure-test): the
production classification is **not truth** — it is a *conservative mask* that only removes a
paragraph from voting when production is confident it is non-body structure, and defaults to
votable otherwise. That is strictly safer than guessing, and it is the real unblock.

**Slice 0 (the unblock) — replace guessing with a conservative mask. Five corrected steps:**

1. Add one production helper `classify_source_roles(docx) -> {source_ordinal: frozenset[kind]}`
   factored from `docx_inspect.classify_source_spans` (docx_inspect.py:339). It runs
   `adapt()` + **full** `normalize()` (incl. `verse_blocks`, normalize.py:1547), so it
   returns the *kinds per source ordinal* — including `LineatedBlock`/`VerseBlock`.

2. **Redact the decision-under-test.** Map kinds to a votability mask, never a label/hint:
   - `Paragraph | LineatedBlock | VerseBlock` present → **BODY** (votable). Lineated/verse
     are redacted to BODY — they are the converter's lineation verdict and must never reach
     the yardstick as truth or as a visual hint.
   - **only** structural kinds (Heading/Table/List/Signature/Epigraph/DialogueLabel/…) →
     context (non-votable).
   - **mixed / unknown / unmapped / span covering >1 ordinal** → **BODY + `needs_review`**
     (e.g. a `<w:p>` that became `{DialogueLabel, Paragraph}` stays votable, flagged — never
     silently masked as a speaker label).

3. **Join by `src_start/src_end`, NOT `p.index`.** Measured: in book 13, **1,996 / 2,690
   paras have `src_start != index`** and 16 have no `src_start`. `ir_view.Para.index` is the
   raw-`adapt` block counter (ir_view.py:243), a *different space* than the source ordinal
   `classify_source_roles` keys on. Look the mask up via the paragraph's `SourceSpan`; a
   paragraph with no span → `needs_review` (the ~16/book unmapped tail, §14-P1). Keep the
   on-disk `(idx, sub)` keys exactly as they are — only the *role lookup* changes.

4. **Do not blind-delete `_THEMATIC_TEXTS`.** The literal-`***` shim (ir_view.py:292) fires
   before normalize; full normalize catches some via `thematic_breaks` (normalize.py:634)
   but maybe not all (gap G1). Delete the shim **only after** the diff (step 5) proves every
   row it used to mark `THEMATIC` still becomes `THEMATIC` via the mask; otherwise keep it
   and file G1.

5. **Ship a role/key diff BEFORE rebuilding any gold.** Emit `old_role → new_role` per para,
   added/removed votable keys, unmapped spans, mixed-kind hits, and per-region changes. No
   silent dataset drift; a human reviews the delta first.

Delete the *guessing* (`_looks_pseudo_header`, `_looks_speaker_label`, the bold-run
reclassification pass) and replace the role with the masked kind. Keep `Line`, wrap stats,
`segments`, the `(idx, sub)` keys, and the listing byte-identical so `gold_build`/
`astro_preview` and committed `gold_block`/`gold_block2` keep working.

That is the whole unblock: one production helper + a span-join + a redaction rule + a diff
gate, in one scratchpad file. No SourceMap, no DocxFrontend, no stages, no package move.

**What Slice 0 deliberately does NOT fix (and why that's fine for now):**
- P1/P2 (partial map, positional collisions): pre-existing; the `needs_review` rule (steps
  2-3) routes them to review instead of silently trusting them. Not worse than today; better.
- P3 (display-line ≠ br+1): `ir_view` already enumerates via `inline_lines`, so keys come
  from the renderer's own enumeration — self-consistent within the substrate. The hazard
  only appears if a *second* renderer is introduced; don't introduce one in Slice 0.
- The "roles are structural truth" framing: explicitly downgraded to "conservative mask."
  True structural truth needs the `normalize_until(STRUCTURAL)` seam — that is Slice 1+.
- The grand refactor (one reader, SourceMap, stages, reshape): pays down debt and enables the
  *production* learned pass later. It is **Slice 1+**, after the model proves the dataset is
  worth productionizing. Sequencing the expensive refactor *after* the cheap unblock is the
  whole point.

**Decision rule:** do Slice 0 to get trustworthy labels and keep the ML loop moving. Pursue
the §1-§14 architecture only when you decide the distilled classifier becomes a real
production compiler pass (§9) — i.e. when the refactor buys correctness in *production*, not
just in the dataset. Until then, the grand design is a validated target, not a prerequisite.

---

## Reviewer summary
The pipeline is an **hourglass**: format-specific front-ends narrow to one canonical **IR
that carries normalized source-layout trivia** (not a content-only neutral tree — stated
once, honestly), an ordered chain of **enrichment** passes annotates it (the hardest,
**lineation**, is a *learned* pass; verse-register is one *semantic* pass among an
append-only list), then it widens to representations (Markdown is one). The front-end is a
named **`DocxFrontend`** that does the load-bearing **reconciliation** of the Pandoc AST to
the OOXML stream and emits `Document + SourceMap + diagnostics` — the join is not free. One
**`DocxSource`** whose stream is `ParagraphItem | TableItem | BoundaryItem` with a real
**`SourceAddress`** (stream index + optional table-cell path, not a bare ordinal) and an
`XmlRef` feeding a shared **slice service** so `docx_render` stops re-walking. The waist has
**`NodeId` identity separated from `Origin` lineage**, and a **`SourceMap`** that keeps
**block provenance/relation distinct from source fate** (a dropped paragraph has a fate, not
a relation). The source-view is the structural IR observed via `run(until=DIALOGUE_LABELS)`,
projected to **paragraphs → fragments → display lines**, with **structure (`FragmentRole`)
separated from votability (`is_lineation_candidate`)** so a `label + body` paragraph is two
addressable fragments without contradiction. A designed **`LineationPlan`** fixes the
one-paragraph-two-labels case. The **learned pass has a real contract** (versioned feature
schema, abstain→deterministic fallback, human override, determinism, book-level splits,
hashed artifacts). The ML half lives in `ml/` with an **annotation & evaluation studio**
(`ml/studio/`, task-loading, not "ui") that **exports responses Python ingests** — never
writing committed data — integrated as an isolated npm workspace. Package-level hashing and
manifests make artifacts reproducible. Every production refactor is an **acceptance
criterion to prove byte-identical**, not a claimed fact.
