# walkthrough.md — what was built, and where it deviates from IMPLEMENTATION_PLAN.md

## What was built

`workflow-ai/` is a self-contained FastAPI service implementing the plan's
pipeline end to end:

```
NL prompt ──▶ tenant catalogs + retrieval ──▶ LLM → compact IR ──▶ compiler
          ──▶ validator ──▶ (repair loop, max 2) ──▶ direct Mongo write
```

- **Phase 0 (catalog harvest)** — `app/catalog/palette.json` was harvested from
  the real `sys/controllers/workflow/Leftblock.php`: all ~50 block types with
  their true `obj_id` and `icon_path`. The 38 `platform-metadata` JSON schemas
  are vendored into `app/catalog/metadata/` and used for prompt hints.
- **Phase 1 (compiler/validator)** — `app/compiler/` turns the small IR into a
  full `VizWorkflow` document: id allocation (`{obj_id}_{counter}`), graph
  wiring (`source`/`target`/`target_yes`/`target_no`), BFS auto-layout,
  `connection[]` derivation, `setvariable` `k=v;` strings, spreadsheet id
  resolution, `field-mapping`, PHP-compatible `full_objects`, and
  `short_code = alnum(name).lower() + uniqid()`. `decompile.py` inverts it for
  the edit flow. `app/validate/validate.py` checks entry/terminals/reachability,
  dangling targets, `{Label.field}` references, and spreadsheet columns.
- **Phase 2 (DB)** — `app/db/mongo.py` (VizWorkflow insert/update mimicking
  `aMongoObject::addNew/update`, VizSpreadsheet schema reads, LiveCloud function
  and reusable-workflow catalogs) and `app/db/mysql.py` (the exact
  `viz_livespace_files` index query the builder uses).
- **Phase 3 (API + Docker)** — endpoints listed in `README.md`; separate
  `docker-compose.yaml` that joins the platform's network and reuses the
  platform's `mongo-url` / `mysql-dsn` secret files.
- **Phase 4 (LLM)** — provider-agnostic OpenAI-compatible client, cached static
  system prompt with the DSL cheatsheet + a gold few-shot IR modeled on the real
  `HMS_GenerateDoctorOTP`, generation/edit/repair pipelines.
- **Tests** — 28 passing. The round-trip suite loads the real production
  documents from `../referance.json` (Mongo-shell syntax parsed with pyjson5),
  decompiles → recompiles, and asserts types, edges, filters, variables, ssids,
  and field mappings are preserved and that both original and recompiled docs
  validate.

## Corrections to the plan (found during implementation)

1. **Execution order ≠ `w_objects` order; the entry can point anywhere.**
   The plan's IR implicitly wired the trigger to `steps[0]`. Real doc
   `GD_AutoIncrementDataInsertionChild` has its entry (`datatransfer 2001_2`)
   targeting a condition that is *not* the next element in `w_objects`.
   Fix: the IR trigger gained an explicit `next` field (defaults to the first
   step); the decompiler records the true first block. Without this, round-trip
   produced two `source==0` blocks and an unreachable subgraph.

2. **`block_properties` keys are not cleanly per-type — no global "managed keys" set.**
   The plan assumed keys like `variables` belong to `setvariable` only. Real doc
   `GD_KioskWhatsAppFileReciever` has a `realtimepush` block with a `variables`
   key. A global managed-keys list silently dropped it on decompile. Fix: the
   decompiler manages keys *per block type* (`variables` only for setvariable,
   `message` only for condition, SS keys only for SS blocks, etc.); everything
   else round-trips untouched through `step.config`.

3. **IR uses the platform's native `{Label.field}` syntax, not the plan's `{{...}}`.**
   The plan proposed a friendly `{{Entry.phone}}` syntax rewritten by the
   compiler. Dropped: it adds a lossy rewrite step, and the few-shot examples
   would no longer match real documents 1:1. The LLM sees and emits the exact
   production DSL; the validator checks the references instead.

4. **Lexical retrieval instead of embeddings.** The plan called for an
   embeddings index. For ~50 blocks and typically tens of spreadsheets per
   tenant, a token-overlap scorer (`app/catalog/retrieval.py`) achieves the same
   context-bounding goal with zero per-request cost, no extra infrastructure,
   and no API dependency. The interface is a drop-in seam for embeddings later.

5. **JSON mode + local Pydantic validation instead of server-side
   `json_schema` constrained output.** Many OpenAI-compatible endpoints
   (OpenRouter models, Groq, Ollama) don't support strict schema enforcement.
   `response_format={"type":"json_object"}` + Pydantic parsing + the repair loop
   is portable and equally safe — a schema violation is just another repairable
   error.

6. **`full_objects` is computed at persistence time, not compile time.** This
   matches `Save.php`, which always derives it from `w_objects` on save — so an
   edited document can never carry a stale copy. `php_json_encode` replicates
   PHP's `\/` slash escaping and `\uXXXX` unicode escaping.

7. **The metadata JSON schemas are too weak for validation.** They were
   regex-extracted (everything typed "string", graph keys missing, some real
   runtime keys absent — the plan itself noted this for RoveraiBlock). Using
   them as hard validators would reject valid real documents. They are used
   only as prompt hints; structural validation is graph-, reference- and
   spreadsheet-column-based, which is what actually breaks workflows.

8. **Palette corrections vs the plan's table.** `Leftblock.php` shows
   `roverai`/`roveragent` are palette ids **4209/4210** with `XAE.svg`/`XAT.svg`
   icons (the plan guessed the generic cloud icon), `sendmail` is **1001**,
   `return` is **2002**, `getparameters/datatransfer` entry is **2001**, and
   there are palette-only types (`zipfiles` 2054, `tospreadsheet` 3001) with no
   dedicated PHP class. The harvested `palette.json` is the source of truth.

9. **Compose network name.** The plan's snippet added the service to
   `vizru-network` as if shared by name. Because the platform compose file
   declares the network non-external, its real name is project-prefixed
   (`vizru-docker_vizru-network`). The separate compose file joins it as an
   external network with a `VIZRU_NETWORK` override.

10. **Duplicate/empty labels exist in production.** Real docs contain blocks
    with empty labels and duplicated labels (`Output` twice in
    `HMS_CheckDoctorOTP`). The decompiler synthesises unique labels
    (`settvariable_6`-style) so the IR stays addressable; the validator emits a
    warning (not an error) for duplicates, since the platform itself tolerates
    them.

## Round 2: full block awareness (post-review improvements)

Problem observed: "onboard a user to an app" produced only a spreadsheet
check+insert. The model saw the full block list but had no reason (or knowledge)
to use platform-level blocks. Three root causes, three grounded fixes:

1. **Thin block knowledge.** The metadata JSON schemas don't carry real config
   contracts, so the model avoided blocks it couldn't configure.
   Fix: `app/catalog/block_docs.json` — per-block usage, required/optional
   `block_properties` keys, and output fields harvested from the PHP block
   classes (`userManagementBlocks.php` etc., e.g. `adduser` requires
   email+name+systemrole/systemRoleName, supports `sendmail='on'`;
   `addusertolivespace` accepts `lid` or `livespace_shortcode` and
   `livespacerole` or `livespaceroleName`). Rendered into the always-included,
   category-grouped catalog (`prompt_catalog`). Whole prompt stays ~4.5k tokens.
2. **Sample-driven selection.** The single few-shot biased everything toward
   spreadsheet flows. Fix: capability-layer reasoning in the system prompt
   (PLATFORM / APP / DATA / INTEGRATION / COMMUNICATION / CONTROL) with explicit
   rules ("a spreadsheet row is NOT a user account"), a mandatory `plan` field
   in the IR (requirement decomposition -> block choice, cheap chain-of-thought
   in the same call), and the few-shot re-framed as format-only. A plan-coverage
   check in `flows._try_build` turns "planned block never used" into a
   repair-loop error, so incomplete workflows self-correct.
3. **No app identity context.** With `lid`, the service now sends real
   livespace context: name/short_code from `viz_livespace` (MySQL), role names
   from `t-livespaces-roles` (Mongo, for `livespaceroleName`), and ALL of the
   app's spreadsheets (up to 15) instead of lexical top-5 guessing. Exposed for
   inspection at `GET /catalog/livespace?tid=&lid=`.

Environment note: `pyjson5`'s native DLL became blocked by a Windows
Application Control policy, so the test fixture parser switched to the
pure-Python `json5` package (requirements.txt updated).

## Known limitations / next steps

- **Live-DB paths are untested here** (no running Mongo/MySQL in this
  workspace). The offline paths — compile, decompile, validate, round-trip —
  are fully tested; `db/` queries replicate the exact SQL/aggregations found in
  the PHP source. First integration step: `GET /catalog/spreadsheets?tid=204`
  against the running stack, then a `save` of a compiled doc to a dev tenant
  and opening it in the builder.
- **`livecloudfunction` / `executeworkflow` need pre-existing entities**; the
  resolvers match by name/id from the tenant catalog and fail compilation with
  a clear error if missing (by design — the AI cannot invent integrations).
- **Position layout is simple layered BFS** — functional, but branch-heavy
  workflows will look plainer in the builder than hand-drawn ones. Cosmetic
  only; the engine ignores positions.
- **Semantic correctness is not provable structurally.** The recommended flow
  is `save: false` (default) → show the user the IR + explanation → confirm →
  `POST /workflows/save`.
- The `source` field on each block records one inbound edge (the builder writes
  the last-drawn edge; the compiler writes the first). The engine only reads
  `source == 0` to find the entry, so this difference is inert.
