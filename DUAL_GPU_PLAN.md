# Dual P40 GPU Analysis Architecture

## Hardware

Two NVIDIA Tesla P40 GPUs, 24 GB VRAM each, 48 GB total.
Connected via PCIe 3.0 x16. Inter-GPU communication goes through the PCIe bus,
not NVLink, so cross-GPU bandwidth is limited — a single model spanning both
GPUs will be slower per token than a model that fits on one.

---

## Model Selection

### Daytime — Qwen 2.5 30B (per-GPU, parallel)

| Property | Value |
|---|---|
| VRAM at Q4 | ~18 GB |
| Fits on single P40 | Yes |
| Instance count | 2 (one per GPU) |
| Dispatch | Round-robin across both Ollama instances |

Qwen 2.5 30B is a substantial step up from Llama 3 8B for structured extraction:
better JSON discipline, more reliable instruction following, better handling of
complex prompts with many rules. At 18 GB per instance it fits comfortably on a
single P40 with room for KV cache, so each GPU runs fully independently with no
PCIe cross-talk during inference.

### Overnight — Qwen 2.5 72B (spanning both GPUs)

| Property | Value |
|---|---|
| VRAM at Q4 | ~43 GB |
| Fits on single P40 | No |
| Fits across both P40s | Yes (~5 GB headroom for KV cache) |
| Instance count | 1 |
| Context window | 4–12K tokens comfortably within headroom |

Qwen 2.5 72B is one of the strongest open models available at any size for
structured reasoning. Using the same model family as the 30B daytime model means
consistent prompt format, tokenizer, and output behaviour. The overnight pass
trades speed for quality on the items that matter most.

---

## VRAM Allocation — The Key Constraint

VRAM is not a shared pool. A loaded model occupies a contiguous block across
whatever GPUs it spans. You cannot run two 30B instances and a 72B instance
simultaneously — that would require 36 + 43 = 79 GB.

The two modes are mutually exclusive:

```
Daytime mode:   [GPU 0: 30B instance A] [GPU 1: 30B instance B]   36 GB used
Overnight mode: [GPU 0 + GPU 1: 72B instance spanning both]        43 GB used
```

Switching modes requires unloading the current model(s) before loading the next.
Ollama supports explicit model unloading via its API (`DELETE /api/delete` or by
setting `keep_alive: 0` on a generate request).

---

## Daytime Pipeline — Parallel Analysis

### Ollama instance configuration

```bash
# Instance A — GPU 0 only
CUDA_VISIBLE_DEVICES=0 ollama serve --port 11434

# Instance B — GPU 1 only
CUDA_VISIBLE_DEVICES=1 ollama serve --port 11435
```

### Squire changes required

1. **Raise the semaphore** from `Semaphore(1)` to `Semaphore(2)` in `app.py`
2. **Add a second Ollama URL** to config (e.g. `OLLAMA_URL_B`)
3. **Round-robin dispatcher** — replace the single `OLLAMA_URL` with a small
   pool; each semaphore acquisition picks the next available URL

The LLM calls are stateless — each item goes in, JSON comes out, no shared
state between calls. The existing `db_lock` in `_save_analysis()` serialises
all TinyDB writes, so concurrent saves from both workers are already safe.

---

## Overnight Pipeline — Parallel Batch with Quality Pass

### Phase 1: Parallel analysis (split workload)

Rather than running a single re-analyze pass, split the pending items into two
halves and write to separate output files:

```
Worker A  →  output_a.json   (items 0..N/2)
Worker B  →  output_b.json   (items N/2..N)
```

Each worker owns its output file entirely — no inter-process locking needed
during analysis. Item partitioning is by `item_id` with no overlap, so the
outputs are fully independent.

### Phase 2: Merge

Call the existing `_save_analysis()` upsert logic sequentially against both
output files. The merge is safe because:

- `item_id` is the primary key — no collisions
- `_save_analysis()` already preserves user-edited fields (project tags, etc.)
- If one worker crashes, the other's output is still valid and mergeable

### Phase 3: Situation formation

Run situation formation **after** the merge, against the complete DB. This is
critical — situation formation looks across all items to find clusters. Running
it before the merge would produce incomplete clusters from half the data.

Squire's existing `situations_pending` state machine already separates analysis
from situation formation, so enforcing this order is straightforward.

### Sequencing summary

```
1. Unload two 30B instances
2. Load Qwen 72B across both GPUs
3. Split pending items → Worker A + Worker B
4. Workers run in parallel → output_a.json, output_b.json
5. Merge both outputs into page.db via _save_analysis()
6. Run situation formation (single pass, full DB)
7. Unload 72B, reload two 30B instances for daytime
```

---

## Scheduling

The overnight job trigger can be a cron entry or a new `/reanalyze/deep`
endpoint in the Squire API that:

- Accepts a scheduled time parameter
- Handles the model swap (calls Ollama to unload/load)
- Kicks off the two-worker batch
- Runs the merge and situation formation on completion
- Swaps back to daytime configuration

Speed is not a concern for the overnight pass. At ~3–6 tokens/second on the
72B model, a full re-analysis of several hundred items will complete in a few
hours — well within an overnight window.

---

## Why This Split Makes Sense

| Concern | Daytime 30B x2 | Overnight 72B |
|---|---|---|
| Speed | Fast (parallel, single-GPU each) | Slow (acceptable overnight) |
| Quality | Good — major improvement over 8B | Best available in 48 GB |
| PCIe bottleneck | None (each model on one GPU) | Present but irrelevant overnight |
| Situation synthesis | Adequate | Significantly better cross-item reasoning |
| Project tagging | Already mostly keyword/sender driven | Better on hard ambiguous cases |
| JSON discipline | Strong in Qwen 30B | Strong in Qwen 72B |

The 8B model is retired entirely. Even the daytime 30B model is a large quality
jump, and the overnight 72B pass handles the cases the 30B gets wrong.
