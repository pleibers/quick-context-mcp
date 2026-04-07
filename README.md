# quick-search

`quick-search` is a FastMCP server for bounded, keyword-driven repository context search. It is designed for broad codebase exploration when a client needs likely-relevant files and small, targeted snippets instead of reading whole files.

The server exposes a single MCP tool:

- `search_repo_context`

It is intended for agent workflows such as:

- finding the most relevant implementation files for a feature or bug
- surfacing symbol definitions before reading full files
- keeping context windows small during repository search
- falling back to normal search only when bounded retrieval is not enough

## What It Does

For each request, `quick-search`:

1. discovers candidate files under the target directory
2. skips obvious noise such as generated files, binaries, vendored code, and very large files
3. counts keyword hits per file
4. boosts likely source files and symbol-definition matches
5. ranks files with an explainable score
6. returns only bounded snippet windows around strong matches

The result is compact structured JSON with:

- summary counts
- ranked files
- eligible ranked files omitted by `max_files`
- snippet excerpts
- excluded matching files that were filtered out of ranking
- short usage guidance for the calling agent

## Retrieval Behavior

The implementation follows the broad-context retrieval plan in [broad-context-mcp-plan.md](/home/leibersp/code/quick-mcp/broad-context-mcp-plan.md).

Current behavior includes:

- `cwd` search by default, with optional explicit `directory`
- `rg --files` discovery when available, with Python fallback
- `ripgrep` keyword collection when available, with Python fallback
- source-file preference over docs/config when scores are similar
- definition-hit preference for common source languages using regex heuristics
- overlap-aware snippet window merging
- hard budgets on returned files, snippets, and total lines

The server intentionally does not do embeddings, AST parsing, or semantic reranking in v1.

## Defaults

Default retrieval limits:

- `max_files = 12`
- `max_snippets = 24`
- `lines_before = 8`
- `lines_after = 12`
- `max_total_lines = 400`

Internal implementation defaults also include:

- `max_snippets_per_file = 3`
- `max_file_size_bytes = 1_000_000`

## Tool Contract

### Input

`search_repo_context` accepts:

```json
{
  "keywords": ["snow", "albedo", "melt"],
  "directory": "/path/to/repo",
  "max_files": 12,
  "max_snippets": 24,
  "lines_before": 8,
  "lines_after": 12,
  "prefer_source_files": true,
  "max_total_lines": 400
}
```

Parameter notes:

- `keywords` must contain at least one non-empty string
- `directory` defaults to the current working directory seen by the MCP process
- `max_files`, `max_snippets`, and `max_total_lines` must be `>= 1`
- `lines_before` and `lines_after` must be `>= 0`
- per-call values override configured environment defaults

### Output

Typical output shape:

```json
{
  "searched_directory": "/path/to/repo",
  "summary": {
    "files_considered": 1832,
    "files_ranked": 47,
    "files_returned": 12,
    "omitted_ranked_files": 35,
    "snippets_returned": 24,
    "excluded_keyword_matches": 3,
    "budget_truncated": true
  },
  "ranked_files": [
    {
      "path": "src/model/snow_energy_balance.jl",
      "is_source_file": true,
      "keyword_hits": 18,
      "distinct_keywords_matched": 3,
      "score": 0.94,
      "recommended_read": true,
      "reason": "high hit count, multiple keywords, source file, definition hits"
    }
  ],
  "omitted_ranked_files": [
    {
      "path": "src/model/legacy_snow.jl",
      "is_source_file": true,
      "keyword_hits": 7,
      "distinct_keywords_matched": 2,
      "score": 0.62,
      "reason": "source file, moderate hit density"
    }
  ],
  "snippets": [
    {
      "path": "src/model/snow_energy_balance.jl",
      "line_start": 120,
      "line_end": 140,
      "matched_keywords": ["snow", "melt"],
      "snippet": "..."
    }
  ],
  "excluded_keyword_matches": [
    {
      "path": "generated/snow_model.generated.py",
      "reason": "generated directory: generated",
      "matched_keywords": ["snow", "melt"],
      "distinct_keywords_matched": 2
    }
  ],
  "usage_guidance": "Prefer source files over docs when scores are similar. Prefer files with multiple distinct keywords and clustered hits. Use the snippets to decide which files deserve deeper reading. If the returned context is insufficient, fall back to normal repository context search."
}
```

### Ranking Signals

Ranking is explainable rather than opaque. Signals include:

- total keyword hits
- number of distinct keywords matched
- source-file bonus
- keyword density
- likely definition hits
- penalties for tests, generated files, vendor code, and similar noise

This means a source file with a relevant `def`, `class`, `function`, `struct`, or similar declaration can outrank a doc file with many incidental mentions.

### Excluded Matching Files

The result also includes `excluded_keyword_matches`.

This list is for files that matched one or more search keywords but were not ranked because they were filtered out during discovery, for example due to:

- generated directories or generated filename patterns
- vendor directories
- binary extensions or binary content
- file size above the configured maximum

This makes exclusions auditable so a potentially important file is not silently omitted.

### Omitted Ranked Files

The result also includes `omitted_ranked_files`.

This list contains files that were eligible, matched keywords, and were fully scored, but were not returned in `ranked_files` because of `max_files`.

Use this list to audit coverage when you intentionally run with tight result budgets.

## Running The Server

### With `uv`

From the repository root:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run quick-search
```

If your local `uv` setup is stable, this is the simplest command.

### With the Local Virtual Environment

If you want a more explicit launch command:

```bash
/home/leibersp/code/quick-mcp/.venv/bin/python /home/leibersp/code/quick-mcp/main.py
```

This is often the safer registration target for MCP clients because it avoids extra resolver and cache behavior at process startup.

### Transport

The server defaults to stdio transport:

```bash
MCP_TRANSPORT=stdio
```

That is the correct default for Codex and Claude Code local MCP registration.

## Registering The MCP

### Codex

Direct Python launch:

```bash
codex mcp add quick-search -- \
  /home/leibersp/code/quick-mcp/.venv/bin/python \
  /home/leibersp/code/quick-mcp/main.py
```

With `uv`:

```bash
codex mcp add quick-search -- \
  uv run --directory /home/leibersp/code/quick-mcp python main.py
```

Useful checks:

```bash
codex mcp list
codex mcp get quick-search
```

### Claude Code

Local scope:

```bash
claude mcp add quick-search --scope local -- \
  /home/leibersp/code/quick-mcp/.venv/bin/python \
  /home/leibersp/code/quick-mcp/main.py
```

Project scope:

```bash
claude mcp add quick-search --scope project -- \
  /home/leibersp/code/quick-mcp/.venv/bin/python \
  /home/leibersp/code/quick-mcp/main.py
```

Equivalent `.mcp.json` entry:

```json
{
  "mcpServers": {
    "quick-search": {
      "command": "/home/leibersp/code/quick-mcp/.venv/bin/python",
      "args": ["/home/leibersp/code/quick-mcp/main.py"],
      "env": {}
    }
  }
}
```

## Configuration

You can configure default retrieval budgets at MCP registration time with environment variables:

```bash
QUICK_SEARCH_MAX_FILES=20
QUICK_SEARCH_MAX_SNIPPETS=30
QUICK_SEARCH_MAX_TOTAL_LINES=500
QUICK_SEARCH_LOG_LEVEL=INFO
```

Behavior:

- `QUICK_SEARCH_MAX_FILES` sets the default `max_files`
- `QUICK_SEARCH_MAX_SNIPPETS` sets the default `max_snippets`
- `QUICK_SEARCH_MAX_TOTAL_LINES` sets the default `max_total_lines`
- `QUICK_SEARCH_LOG_LEVEL` controls Python logging verbosity

Per-call tool arguments still override the retrieval defaults.

Example Codex registration with configured defaults:

```bash
codex mcp add quick-search \
  --env QUICK_SEARCH_MAX_FILES=20 \
  --env QUICK_SEARCH_MAX_SNIPPETS=30 \
  --env QUICK_SEARCH_MAX_TOTAL_LINES=500 \
  --env QUICK_SEARCH_LOG_LEVEL=INFO \
  -- \
  /home/leibersp/code/quick-mcp/.venv/bin/python \
  /home/leibersp/code/quick-mcp/main.py
```

## Logging

`quick-search` emits meaningful runtime logs to stderr. This is important because stdout is reserved for MCP protocol traffic.

Each search logs:

- target root directory
- query keywords
- files considered, ranked, and returned
- ranked matches omitted by `max_files`
- snippets returned
- whether budgets truncated the result
- top-ranked files
- per-file snippet coverage as `shown_lines=<returned>/<total> (<percent>%)`
- excluded matching files with their exclusion reasons

Example log lines:

```text
2026-04-07 13:45:45,123 INFO repo_context_search: repo search root=/repo keywords=snow, melt files_considered=3 files_ranked=2 files_returned=2 snippets_returned=1 budget_truncated=False
2026-04-07 13:45:45,124 INFO repo_context_search: rank=1 path=src/model.py score=1.000 hits=5 distinct_keywords=2 definition_hits=1 snippets=1 shown_lines=12/240 (5.0%)
```

If you want quieter output, set:

```bash
QUICK_SEARCH_LOG_LEVEL=WARNING
```

## Development

### Project Layout

- [main.py](/home/leibersp/code/quick-mcp/main.py): process entrypoint and logging setup
- [quick_search.py](/home/leibersp/code/quick-mcp/quick_search.py): FastMCP server and tool registration
- [repo_context_search.py](/home/leibersp/code/quick-mcp/repo_context_search.py): retrieval, ranking, and snippet extraction engine
- [tests/test_repo_context_search.py](/home/leibersp/code/quick-mcp/tests/test_repo_context_search.py): unit tests
- [broad-context-mcp-plan.md](/home/leibersp/code/quick-mcp/broad-context-mcp-plan.md): implementation plan

### Install / Sync

If needed:

```bash
uv sync
```

### Run Tests

```bash
.venv/bin/python -m unittest -v tests.test_repo_context_search
```

or:

```bash
.venv/bin/python -m unittest discover -s tests
```

### Compile Check

```bash
.venv/bin/python -m py_compile quick_search.py repo_context_search.py main.py
```

## Verified Behaviors

The test suite currently covers:

- default search in `cwd`
- source-file preference
- definition-hit preference
- overlapping snippet merge
- total line budget enforcement
- binary, generated, and large-file exclusion
- no-match behavior
- docs-only matches
- environment-driven default budgets
- logging of ranked files and snippet coverage

## Limitations

Current limitations are intentional:

- exact keyword matching only
- no semantic search or embeddings
- no AST parsing
- definition detection is heuristic, not language-complete
- retrieval is optimized for bounded context, not exhaustive code intelligence

If the returned context is insufficient, the intended client behavior is to fall back to normal repository context search.
