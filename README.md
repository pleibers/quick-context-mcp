# quick-search

`quick-search` is a FastMCP server for bounded, keyword-driven repository context search. It is designed for broad codebase exploration when a client needs likely-relevant files and small, targeted snippets instead of reading whole files.

The server exposes a single MCP tool:

- `search_repo_context`

It is intended for agent workflows such as:

- finding the most relevant implementation files for a feature or bug
- surfacing symbol definitions before reading full files
- keeping context windows small during repository search
- falling back to normal search only when bounded retrieval is not enough

Recommended agent workflow:

1. Call `search_repo_context` with `output_mode="compact"` and `include_diagnostics=false`.
2. Keep the returned `query_id`.
3. If the result identifies promising files but you need more ranking or query metadata, call the tool again with `query_id=<prior id>` and `output_mode="full"`.
4. If the result is empty or surprising, call the tool again with `query_id=<prior id>` and `include_diagnostics=true`.
5. Once the search is narrowed, switch to Serena or normal file reads.

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
- snippet excerpts
- short usage guidance for the calling agent

## Retrieval Behavior

The implementation follows the broad-context retrieval plan in [broad-context-mcp-plan.md](/home/leibersp/code/quick-mcp/broad-context-mcp-plan.md).

Current behavior includes:

- `cwd` search by default, with optional explicit `directory`
- optional `subpath` restriction to a subtree or single file within `directory`
- optional include/exclude glob filters on candidate file paths
- `match_mode` with `substring`, `word`, and `identifier` behavior
- optional `prompt` input with bounded heuristic keyword expansion
- `output_mode` with compact-by-default responses
- optional `include_diagnostics` for backend and exclusion summaries
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
  "prompt": "find the snow melt balance implementation",
  "query_id": null,
  "directory": "/path/to/repo",
  "subpath": "src/model",
  "paths_include_glob": "src/**/*.py",
  "paths_exclude_glob": "**/tests/*",
  "match_mode": "substring",
  "output_mode": "compact",
  "include_diagnostics": false,
  "max_files": 12,
  "max_snippets": 24,
  "lines_before": 8,
  "lines_after": 12,
  "prefer_source_files": true,
  "max_total_lines": 400
}
```

Parameter notes:

- provide at least one non-empty `keyword` or a non-empty `prompt`
- `query_id` may be used instead of rerunning the same search when you only want a different `output_mode` or diagnostics view
- explicit `keywords` are preserved first; prompt-derived terms are added up to a bounded limit
- `directory` should be an absolute path for reliable agent behavior
- `subpath` is optional and must be relative to `directory`
- `subpath` may point to either a directory or a single file
- `paths_include_glob` and `paths_exclude_glob` filter candidate file paths relative to `directory`
- if both glob filters are provided, the exclude glob wins
- `match_mode` defaults to `substring`
- `word` uses word-boundary matching
- `identifier` matches identifier tokens such as `snow_model` and `snowModel`
- `output_mode` defaults to `compact`
- use `output_mode="full"` when you need richer ranking, snippet, and query metadata
- `include_diagnostics` defaults to `false`
- relative paths such as `.` are not portable across MCP clients and only work when the client exposes roots
- if you are calling this tool from an agent, do not rely on `.` meaning the agent's current directory; pass an absolute `directory`
- if `directory` is omitted, the server falls back to its own process working directory unless exactly one client root is exposed
- `max_files`, `max_snippets`, and `max_total_lines` must be `>= 1`
- `lines_before` and `lines_after` must be `>= 0`
- per-call values override configured environment defaults

### Output

Typical output shape:

```json
{
  "query_id": "9e0a4f3f8e6b2c1d",
  "searched_directory": "/path/to/repo",
  "summary": {
    "files_considered": 1832,
    "files_ranked": 47,
    "files_returned": 12,
    "snippets_returned": 24,
    "budget_truncated": true
  },
  "query": {
    "prompt_used": true,
    "resolved_keywords": ["snow", "albedo", "melt", "snow melt", "balance", "implementation"]
  },
  "ranked_files": [
    {
      "path": "src/model/snow_energy_balance.jl",
      "category": "source",
      "keyword_hits": 18,
      "distinct_keywords_matched": 3,
      "definition_hits": 2,
      "score": 0.94,
      "recommended_read": true,
      "reason": "high hit count, multiple keywords, source file, definition hits"
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
  "usage_guidance": "Prefer source files over docs when scores are similar. Prefer files with multiple distinct keywords and clustered hits. Use the snippets to decide which files deserve deeper reading. Only do further searches across the repository if this context is insufficient."
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

The ranked file output also exposes several of these internal signals directly so an
agent can decide the next Serena call without recomputing them.

In `compact` mode this stays lean:

- `category`
- `keyword_hits`
- `distinct_keywords_matched`
- `definition_hits`

In `full` mode additional fields are included:

- `line_count`
- `is_source_file`
- `is_test_file`
- `definition_hits`
- `keyword_density`

## Match Modes

`quick-search` supports three matching modes:

- `substring`
  - current behavior
  - matches query text anywhere in the line
- `word`
  - requires word boundaries
  - avoids matching `snow` inside `snowpack` or `snow_model`
- `identifier`
  - matches identifier-like tokens in code
  - matches `snow` inside `snow_model` and `snowModel`
  - does not match `snow` inside `snowfall`

## Prompt Queries

You can pass prompt text directly instead of hand-curating keyword lists.

`quick-search` keeps this heuristic and bounded:

- extracts repeated technical terms
- keeps useful identifier-like tokens
- preserves some short technical phrases
- caps the final resolved keyword set

The derived and resolved keywords are returned in the `query` object so the behavior
is transparent to the caller.

In `compact` mode, `query` contains:

- `prompt_used`
- `resolved_keywords`

In `full` mode, `query` also includes:

- `explicit_keywords`
- `derived_keywords`

## Output Modes

`quick-search` now defaults to `output_mode="compact"` to reduce MCP response size.

Use `compact` when:

- you only need the top candidate files and snippets
- the tool is feeding another retrieval step
- context budget matters

Use `full` when:

- you are tuning retrieval quality
- you want ranking metadata for debugging or evaluation
- you want to inspect explicit versus prompt-derived keywords
- you plan to immediately inspect only a small number of returned files

## Diagnostics

Set `include_diagnostics=true` to include a compact diagnostics block with:

- discovery backend
- matching backend
- excluded file counts by reason

This is off by default so normal agent calls stay small.

## Compact-First Pattern

The intended call pattern for agents is:

1. Run a compact query first.
2. Keep the returned `query_id`.
3. If needed, call the tool again with that `query_id` and:
   - `output_mode="full"` for richer metadata
   - `include_diagnostics=true` for debugging

This gives you a cheap first pass without committing to the larger payload every time or recomputing the search.

## Cached Expansion

Every successful search response includes a `query_id`.

That id refers to an in-process cached full result:

- the first call can use `output_mode="compact"`
- a later call can reuse `query_id` with `output_mode="full"`
- a later call can reuse `query_id` with `include_diagnostics=true`

This avoids recomputing the same search just to retrieve richer metadata.

This means a source file with a relevant `def`, `class`, `function`, `struct`, or similar declaration can outrank a doc file with many incidental mentions.

### Bounded Results

The result is intentionally not exhaustive.

`quick-search` returns the top ranked files and bounded snippets only. It does not return every file that was eligible for ranking, and it does not return filtered files that were skipped during discovery.

If you need broader coverage, raise the budgets and only do further searches across the repository if this context is insufficient.

## Scoped Search

You can narrow the ranking pass before matching and snippet extraction:

- use `subpath` to search only inside a subtree
- use `subpath` to search a single known file
- use `paths_include_glob` to keep only specific candidate file patterns
- use `paths_exclude_glob` to remove low-value areas such as tests or fixtures

Example:

```json
{
  "keywords": ["snow", "melt"],
  "directory": "/repo",
  "subpath": "src/physics",
  "paths_include_glob": "src/physics/*.py",
  "paths_exclude_glob": "src/physics/test_*"
}
```

## Directory Resolution

For agents, the safe contract is simple: always pass an absolute repository path.

Relative paths are not reliable because MCP servers do not automatically know the caller's current working directory. Some clients expose workspace roots and let `quick-search` resolve `.` against those roots, but others do not. In those clients, a relative path will fail rather than silently searching the wrong directory.

Use relative paths only if you control the client and know it exposes roots. Otherwise, pass an absolute `directory`.

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
- snippets returned
- whether budgets truncated the result
- top-ranked files
- per-file snippet coverage as `shown_lines=<returned>/<total> (<percent>%)`

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
- subtree restriction
- include/exclude glob filtering
- `word` and `identifier` matching modes
- prompt-derived keyword expansion
- compact and full output modes
- optional diagnostics output
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

If the returned context is insufficient, the intended client behavior is to only do further searches across the repository when this context is insufficient.
