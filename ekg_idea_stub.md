# Engineering Knowledge Graph — Idea Stub

**Working name:** EKG (Engineering Knowledge Graph) — a domain extension to LanceLLMot.
**Status:** Conceptual. To be developed iteratively against a seed example project.
**Premise:** Industrial safety engineering work products are *already* graph-shaped. Today that graph lives in senior engineers' heads. Make it explicit, queryable, and auditable.

---

## The thesis in one paragraph

Drawings, PLC code, SATs, FMEAs, hazard analyses, and SRSs are conventionally treated as independent documents. In practice they describe a single connected system: every device referenced in a drawing is a tag in code, a row in an FMEA, a step in a SAT, a clause in an SRS, and a contributor to one or more SIFs answering one or more hazards. The connections between these artifacts are real and traversed daily — by engineers, mentally, at significant cost. EKG materializes those connections as a typed graph layered over LanceLLMot's existing GraphRAG substrate. Documents become projections of and ingestion sources for the graph; the graph itself is the durable artifact.

---

## The four-graph model

EKG composes four domain graphs over a shared canonical entity layer:

### Shared entity layer
The interface every other graph attaches to. Tags (canonicalized via a normalizer), devices, terminals, signals, system states, setpoints. These are the universal connectors. When LT-2105 appears in a drawing, in PLC code as `LT_2105_PV`, in an SRS row, and in a SAT step, all four references resolve to the same node.

### 1. System graph (physical)
What is wired to what. Native nodes: device, terminal, wire, cable, panel, drawing sheet, loop. Native edges: `wired_to`, `terminates_at`, `part_of_loop`, `part_of_panel`, `appears_on_sheet`. Source artifacts: wiring diagrams, P&IDs, loop sheets, I/O lists, panel layouts.

### 2. Logical graph (code)
What the program does. Native nodes: routine, function block, rung/statement, condition, action, scan task. Native edges: `reads_tag`, `writes_tag`, `latches_tag`, `gates`, `triggers`, `calls`, `runs_in_task`. Source artifacts: PLC source code (.st, .scl, .lad, .l5x, .scl exports).

### 3. Procedural graph (tests + operations)
What is to be done, in what order, under what conditions, to verify what. Native nodes: procedure, step, system_state, evidence, step_template. Native edges: `precedes`, `groups_with`, `requires_state`, `establishes_state`, `restores_state`, `verifies_result_of`, `shares_evidence_with`, `supersedes`, `tests_device`, `tests_signal_path`, `references_drawing`. Source artifacts: SATs, FATs, calibration procedures, LOTO procedures, commissioning checklists.

### 4. Justification graph (safety case)
What must be true and why. Native nodes: hazard, requirement, failure_mode, sif, safeguard, sil_target, standard_clause. Native edges: `requires_sif`, `mitigates`, `arises_from`, `derives_from`, `allocates_to`, `verified_by`, `caused_by`, `detected_by`, `cites`. Source artifacts: hazard analyses, SRS, FMEA, SIL calculations, LOPA studies.

The four graphs share entities at the device/tag/signal layer. Cross-graph edges (especially in the justification graph) are what make traceability queries tractable.

---

## Two reframings of the GraphRAG concept

EKG inverts conventional GraphRAG twice:

**Inversion 1: Graph as primary, documents as projections.** Standard GraphRAG builds a graph from documents to aid retrieval; documents are the source of truth. EKG treats the graph as the source of truth and documents as either ingestion inputs or generated outputs (renderings). A SAT in the system isn't a file — it's a procedure subgraph that can be rendered as a PDF, a checklist, an executable test plan, or a coverage matrix.

**Inversion 2: Procedures as their own structured artifact.** SAT/FAT steps aren't leaves attached to device nodes. They have rich relational structure *among themselves* — sequencing, prerequisite states, verification chains, shared evidence, supersession, mutual exclusion. The procedure graph is a first-class graph, attached to the system graph via interface edges. The pattern of those interface edges *is* the test plan.

---

## What this unlocks

Each of these is currently expensive senior-engineer time. Each becomes a graph traversal:

- **End-to-end SIF tracing** — sensor through wiring through I/O through code through wiring through final element, with all touching FMEA rows, SAT steps, and SRS requirements
- **Coverage gap analysis** — every requirement, is it `verified_by` an existing procedure step? Every SIF, does it have a complete verification chain? Every device, is it referenced by at least one SAT step?
- **MOC blast radius** — propose a logic change; find every wire, device, procedure step, and requirement transitively affected
- **Traceability matrix** — a query, not a deliverable. From standards through hazards through requirements through allocations through verifications, end to end
- **SAT regeneration** — rerun the SAT generator after the system graph changes; diff against the previous SAT to produce a re-test scope automatically
- **Bypass and state tracking** — every `establishes_state` edge resolves to a specific code tag and value; field bypass becomes a verifiable claim, not a hope
- **Interlock provenance** — given a permissive tag, walk back through every condition that contributes to its value, recursively, terminating at sensor inputs

The LLM's role in all of this is at the *edges* — parsing legacy documents into structure during ingestion, generating prose during rendering, helping users phrase queries. The graph itself is deterministic, queryable, and auditable.

---

## Architectural fit with LanceLLMot

EKG layers on top of existing infrastructure rather than replacing it:

| Existing capability | How EKG uses it |
|---|---|
| `db.py` nodes/edges tables | Hosts all four graph layers; arbitrary `node_type`/`edge_type` already supported |
| `graph.py` traversal helpers | Extended with EKG-specific helpers (signal-path traversal, SIF subgraph extraction, blast-radius walk) |
| `chunker.py` structure-aware chunking | Used for SRS/SAT prose extraction during ingestion |
| `extractor.py` concept extraction | Repurposed for entity extraction from FMEA/HA prose fields; concept vocabulary extended with controls-engineering and FS terms |
| Workspace scoping (client/project) | EKG graphs are project-scoped; cross-project queries explicitly opt in |
| Connection plumbing (M-Files/SP/WebDAV) | Source for ingesting drawing packages, code repos, document libraries |
| ChromaDB | Continues to serve text RAG over document prose; structured queries hit the graph instead |
| Document classification (public/client) | All EKG-derived data inherits the source document's classification |

New modules:

- `tag_normalizer.py` — canonical form for any tag string; the foundation of cross-graph entity resolution
- `drawing_parser.py` — DXF/L5X/EPLAN/PDF → system graph fragment
- `code_parser.py` — IEC 61131-3 source → logical graph fragment (start with ST)
- `procedure_parser.py` — SAT/FAT/LOTO docs → procedural graph fragment
- `safety_case_parser.py` — FMEA/HA/SRS Excel + Word → justification graph fragment
- `traversal.py` — EKG-specific graph queries (SIF spine, traceability, coverage, blast radius)
- `renderers/` — graph subgraph → SAT/FAT/checklist/matrix output
- New routers: `/ekg/system`, `/ekg/sat`, `/ekg/trace`, `/ekg/coverage`

---

## Hard problems, named explicitly

**Tag normalization.** ISA-5.1 tags are written inconsistently across artifacts (LT-2105, LT2105, LT 2105, 21-LT-105, LT_2105_PV). The canonicalizer is unglamorous infrastructure but it's the keystone — without it, joins don't join. Build it deliberately and treat it as version-controlled domain knowledge.

**Cross-sheet topology in legacy drawings.** Off-page connectors and "see sheet 47" references are where automated extraction breaks. Budget for human-in-the-loop QA on legacy drawings and for native CAD ingestion (DXF/L5X/EPLAN XML) for new ones.

**Graph-document drift.** When source documents change via MOC, the graph must be re-derived without losing manual annotations. Re-extraction has to be a diff, not an overwrite.

**Provenance.** Every node and edge must carry a source citation (which document, which page, which row). Without provenance, the graph is unauditable.

**Coverage honesty.** "We tested this" must be distinguishable from "we have no record of this existing." Missing edges are not the same as absent claims.

---

## MVP scope

The minimum useful demonstration:

**One project. One SIF. Vertical slice through all four graphs.** Pick a high-high level trip on a vessel — sensor LT-XXXX, logic, valve XV-XXXX. Ingest:

- The wiring diagram(s) for that loop
- The PLC code routine that contains the trip logic
- The hazard analysis row that drives the SIF
- The SRS clause(s) allocated to the SIF
- The FMEA rows for the sensor and final element
- The SAT step(s) that verify the trip

Produce three artifacts:

1. **The SIF spine view** — sensor through final element, with code logic inset and verification status overlaid
2. **The traceability matrix** for that SIF — hazard through requirement through allocation through verification, end to end
3. **A regenerated SAT step** — given the system + code + requirement graph, produce the SAT step prose; diff against the human-written one

If those three demonstrations work for one SIF, the structural argument is proven. Scale becomes an engineering problem, not a conceptual one.

---

## Seed corpus — what to put in the example project

To exercise the MVP end to end, the example project needs documents covering all four graph layers, anchored on a single chosen SIF. Recommendation: build a fictional but realistic example rather than using a real client project, so the corpus can live in source control and be shared for development.

### Minimum viable corpus

**Pick a process and a hazard up front.**
A simple one is best for the seed corpus. Suggested: a feed tank to a downstream process, with high-high level trip protecting against overfill / overflow to atmosphere. One vessel, one transmitter, one trip valve, one PLC. This gives you:
- A concrete scope to bound every other document
- A real (if simple) safety case worth modeling
- Enough complexity to exercise all four graphs without drowning in detail

**1. Drawing package (system graph)**
- One **P&ID** showing the vessel, the level transmitter, the trip valve, and the inlet/outlet piping. Minimum quality: clean PDF with readable tags. DXF preferable if you want to exercise the native CAD path.
- One **wiring diagram** showing LT-XXXX terminations to the I/O card, and the I/O card termination to XV-XXXX solenoid. Two sheets is fine; one sheet is fine if it fits.
- One **loop sheet** for the SIF (sensor → I/O → logic solver → I/O → final element). This is the rosetta stone for the system graph.
- One **I/O list** (Excel) showing the AI channel for LT-XXXX and the DO channel for XV-XXXX, with addresses, ranges, and tag names.
- *Optional but recommended:* a **panel layout** showing the SIS PLC and I/O cards in the safety panel.

**2. PLC code (logical graph)**
- One **PLC routine** containing the trip logic. Format: structured text (.st) is easiest for the parser; ladder export (Rockwell L5X, Siemens SCL) works if your target environment uses it. Should contain at least:
  - The trip condition (LT_PV ≥ trip_setpoint)
  - The output assignment (XV_CMD := 0 on trip)
  - A bypass mechanism (maintenance bit) — exercises the state-tracking story
  - A reset mechanism — exercises the supersession/restore-state story
- *Optional:* a **task configuration** export showing scan-cycle period and priority.
- *Optional:* one **HMI screen export** referencing the same tags — useful for showing the tag normalizer working across yet another source.

**3. Procedural artifacts (procedural graph)**
- One **SAT** in Word or PDF format with at least:
  - A scope section referencing the SIF
  - A prerequisites section ("Place LT-XXXX in bypass," "Verify XV-XXXX in test position")
  - A trip test section ("Apply 90% level signal, verify XV closes within 2 s")
  - A restore section ("Remove bypass, return to operations")
  - A signoff block
- *Optional:* a **FAT procedure** for the same SIF — tests the "same SIF, two procedures, shared verification edges" case.
- *Optional:* a **LOTO procedure** for the trip valve — tests the procedural-graph generality (LOTO is just another procedure subgraph).
- *Optional:* one **calibration record** — exercises the evidence-node story.

**4. Safety case artifacts (justification graph)**
- One **hazard analysis row** (Excel or Word) for the overfill scenario, with: hazard description, initiating event, severity, frequency before mitigation, required risk reduction, allocated SIF, residual risk.
- One **SRS** (Word) with at least three rows:
  - A safety requirement specifying the trip function ("On high-high level, XV-XXXX shall close within 2 s")
  - A SIL requirement ("The high-high level trip shall achieve SIL 2")
  - A reliability requirement (PFDavg target, proof test interval)
  - Each row should reference an IEC 61511 clause for the standard linkage
- One **FMEA** (Excel) covering the SIF components, minimum:
  - Row for LT-XXXX: failure mode "stuck low," cause, effect, detection mechanism, RPN
  - Row for XV-XXXX: failure mode "fails to close," cause, effect, detection mechanism, RPN
  - Row for the logic solver / I/O: at least one systematic failure mode
- One **SIL calculation** (Excel) showing PFDavg derivation for the SIF — tests the justification graph's link to the system graph's architectural properties.

**5. Standards reference (already in LanceLLMot's library scope)**
The seed corpus assumes IEC 61508 and IEC 61511 are already ingested into the global library scope. The graph layer needs `standard_clause` nodes that the SRS and HA reference; the prose of those standards doesn't need to be in the seed corpus, just the clause identifiers (e.g., "IEC 61511-1 §11.2.4").

### What NOT to include in the seed corpus

- Real client identifiers, real plant tags, real personnel names. Fictional throughout.
- More than one SIF. Resist the temptation. One worked example end to end is more valuable than three half-modeled ones.
- Ancillary documents that don't anchor to the chosen SIF (P&IDs of other systems, hazard rows for other scenarios). They add noise without adding signal.
- Photographs, vendor cut sheets, and other unstructured supplements. The MVP is about structured artifacts; vendor docs come later if at all.

### Authoring shortcuts

If creating the seed corpus from scratch, a few shortcuts are reasonable:

- **Use real standard structures.** ISA-5.1 tag conventions, real-looking loop sheet layouts, real Rockwell or Siemens code dialects. The structures should be authentic even if the specific values are fictional.
- **Reuse public examples where possible.** ISA's TR84 family contains worked SIL examples; HSE's Layer of Protection Analysis guidance has worked LOPA cases. Adapt one of these as the seed scenario rather than inventing from scratch — it gives the example credibility.
- **Cross-validate the corpus before ingesting.** If the SAT references LT-2105 but the wiring diagram shows LT-2105A, that's a tag normalization bug to surface deliberately; if it's an unintentional inconsistency, fix it. The seed corpus should have exactly the inconsistencies you want to exercise the system on.

---

## Development sequence

Rough order, each step building on the prior:

1. **Tag normalizer + minimal schema.** Add the EKG-specific node and edge types to `db.py`. Build `tag_normalizer.py`. Get one tag flowing through it correctly across three different source formats.
2. **One drawing parser.** Pick the format your seed corpus uses. Get one wiring diagram producing a system graph fragment.
3. **One code parser.** Structured text first. Get one routine producing a logical graph fragment that shares tags with the system graph.
4. **Manual SAT step ingestion.** Don't parse SATs yet; hand-author one SAT step's worth of procedure graph nodes and edges, attached to the system + code graphs. Validates the schema before investing in parsing.
5. **First traversal: SIF spine.** Given a SIF identifier, walk sensor → wiring → I/O → code → I/O → wiring → final element, returning a structured result.
6. **First visualization.** Render the SIF spine as a Cytoscape.js artifact in LanceLLMot's chat. This is the first time the concept feels real.
7. **Justification graph manual ingestion.** Hand-author one hazard, one SRS row, one FMEA row, attached. Validates the cross-graph edges.
8. **Traceability matrix traversal + render.** From hazard to verification, end to end. This is the artifact every FS auditor wants.
9. **Now invest in parsers** — SAT prose, SRS Word tables, FMEA Excel — once the schema and traversals are pinned.
10. **SAT regeneration.** Given the populated graph + step templates, produce SAT prose from the procedure subgraph. Compare to the human-written SAT.

Each step is independently demoable. Each step's output earns the next step's investment.

---

## Why this fits LanceLLMot specifically

LanceLLMot already has:

- A scoped, encrypted, project-organized document store
- A graph layer with arbitrary typed nodes and edges
- LLM integration at the edges (extraction, summarization, prose generation)
- SSE-streamed chat as a query surface
- Connection infrastructure for M-Files / SharePoint / WebDAV ingestion
- Document classification and privacy gating
- A controls/FS-aware concept vocabulary in the extractor

What's missing is exactly the domain-specific graph schemas and parsers above. Most "AI for engineering" products start by building infrastructure; EKG starts with the infrastructure already built and adds the domain.

The result, when populated, is something the controls and FS engineering disciplines have arguably needed for decades: a living, queryable, auditable representation of a project's safety case, with documents as one of several output formats rather than the primary medium.

---

## Open questions to resolve in development

- **Procedure step template authoring.** YAML? In-database? Mini-DSL? The format determines who can write templates and how brittle they are.
- **Conflict resolution on re-ingest.** If a drawing changes and the system graph needs to update, how are manual annotations preserved?
- **Provenance granularity.** Per-edge citation is ideal; is per-node sufficient for the MVP?
- **Multi-revision handling.** SAT-rev-A and SAT-rev-B both exist; the supersession edge handles the relationship, but the query layer needs to know which revision is "current" and which is historical.
- **Visualization framework.** Cytoscape.js for prototyping is settled; what's the longer-term path? D3-custom for the traceability matrix is probably right; the SIF spine probably wants schematic-style rendering eventually.
- **LLM use boundaries.** Where should LLM assistance be allowed, and where must the system be deterministic? The justification chain should probably be deterministic end to end; prose extraction during ingestion is a reasonable LLM task; SAT prose generation during rendering is too. Drawing the line explicitly matters.

---

## Closing

This document is a stub. It captures enough of the concept to start building against, names the hard problems honestly, and bounds the MVP small enough to be achievable. The development should be iterative: build one small thing, demonstrate it, decide whether to push deeper or pivot.

The visual story (`why_a_graph.html`) is the companion artifact for explaining the concept to others. This document is for the implementer.
