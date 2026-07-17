# workflow-ai

Natural-language workflow generation/editing service for the Vizru platform.
A user prompt goes in; a validated, runnable `VizWorkflow` MongoDB document
comes out. See `walkthrough.md` for design details and deviations from
`../IMPLEMENTATION_PLAN.md`.

## Architecture

```
prompt -> [catalog + lexical retrieval] -> [LLM -> compact IR] -> [compiler]
       -> [validator] -> (repair loop) -> [Mongo write to VizWorkflow]
```

The LLM only produces a small intermediate representation (labels, block types,
semantic config, edges-by-label). Deterministic Python generates everything
mechanical: block ids (`{obj_id}_{counter}`), palette `obj_id`/`icon_path`,
positions, `connection[]`, `full_objects` (PHP-style `json_encode`),
`short_code`, and boilerplate `block_properties`.

## Run

```bash
# 1. copy .env.example to .env and set your LLM_API_KEY
# 2. make sure the main platform stack is up (it creates the network)
docker compose -f workflow-ai/docker-compose.yaml up -d --build
# service: http://localhost:5003  (inside the network: http://workflow-ai:5003)
```

If your platform compose project isn't named `vizru-docker`, set the network:

```bash
docker network ls   # find the *_vizru-network name
VIZRU_NETWORK=<name> docker compose -f workflow-ai/docker-compose.yaml up -d
```

Environment (all optional): `LLM_MODEL` (default `gpt-4o-mini`), `LLM_BASE_URL`
(any OpenAI-compatible endpoint), `MONGO_URL`, `MYSQL_DSN`, `CATALOG_TTL_SECONDS`.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/workflows/generate` | `{prompt, tid, uid, save}` → IR + document (+ Mongo id if `save`) |
| POST | `/workflows/{id}/edit` | `{instruction, tid, uid, save}` → edited document |
| POST | `/workflows/compile` | `{ir, tid?}` → compile a hand-written IR (no LLM) |
| POST | `/workflows/validate` | `{document, tid?}` → structural validation |
| POST | `/workflows/save` | `{document, tid, uid}` → validate + insert |
| GET | `/workflows?tid=` | list workflows |
| GET | `/workflows/{id}/ir?tid=` | decompile a stored workflow to IR |
| GET | `/catalog/blocks` | block palette |
| GET | `/catalog/spreadsheets?tid=` | tenant spreadsheet catalog (names + columns) |
| GET | `/catalog/functions?tid=` | LiveCloud functions + child workflows |
| GET | `/healthz` | health check |

## Tests

```bash
cd workflow-ai
pip install -r requirements.txt
python -m pytest tests -q
```

The round-trip suite decompiles the real production documents in
`../referance.json` to IR, recompiles them, and asserts the graph (types,
edges, filters, variables, field mappings) is preserved and validates.

## Layout

```
app/
  main.py            FastAPI endpoints
  config.py          env + Docker-secret configuration
  db/                Mongo (VizWorkflow, VizSpreadsheet) + MySQL (viz_livespace_files)
  catalog/           palette.json (harvested from Leftblock.php), block metadata,
                     tenant spreadsheet/function catalogs, lexical retrieval
  ir/                Pydantic IR schema the LLM emits
  compiler/          IR -> VizWorkflow doc (+ decompiler for edits)
  validate/          graph / reference / spreadsheet-column validation
  llm/               provider-agnostic client, prompts, generate/edit pipelines
tests/               round-trip + compiler + validator tests
```
