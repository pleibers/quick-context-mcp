# Broad Context MCP Plan

## Goal

Build an MCP that improves broad repository context gathering by:

- finding potentially relevant files from keywords
- ranking them without reading full files
- reading only bounded snippets around keyword hits
- keeping returned context small
- falling back to normal search when retrieval is insufficient

## Core Design

The MCP should expose one main retrieval workflow, not many tiny tools. Keep the API simple.

### Main Tool

1. `search_repo_context`

### Optional Later Split

- `list_relevant_files`
- `read_relevant_snippets`

For the first implementation, one tool is better. It can internally do discovery, ranking, and snippet extraction while enforcing a strict token or line budget.

### Suggested Input

```json
{
  "keywords": ["snow", "albedo", "melt"],
  "directory": "/path/to/search",
  "max_files": 20,
  "max_snippets": 40,
  "lines_before": 8,
  "lines_after": 12,
  "prefer_source_files": true
}
```

### Suggested Output

```json
{
  "searched_directory": "/path/to/search",
  "summary": {
    "files_considered": 1832,
    "files_ranked": 47,
    "files_returned": 12,
    "snippets_returned": 24,
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
      "reason": "high hit count, multiple keywords, source file"
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
  "usage_guidance": "Read source files with high keyword density first. If this result is insufficient, fall back to normal repository context search."
}
```

## Retrieval Strategy

Use a two-stage pipeline.

### 1. File Discovery

- Search in `cwd` by default.
- Allow overriding with `directory`.
- Enumerate files with `rg --files` if available.
- Respect `.gitignore` by default.
- Skip obvious noise:
  - `.git`
  - `node_modules`
  - build directories
  - generated artifacts
  - binary files
  - very large files above a size threshold

### 2. File Scoring

- Count keyword matches per file with ripgrep.
- Prefer keyword matches that occur on probable function, class, struct, module, or method definition lines over plain mentions.
- Prefer files matching multiple distinct keywords.
- Prefer source files over docs or config unless docs have much higher hit density, or docs are explicitly asked for.
- Penalize test files slightly, not absolutely.
- Penalize vendored or generated files heavily.
- Use density, not only raw count:
  - `keyword_hits / file_lines`
- Keep ranking explainable.

### Practical Score

- `score = 0.30 * normalized_hit_count`
- `+ 0.20 * normalized_distinct_keyword_count`
- `+ 0.20 * source_file_bonus`
- `+ 0.10 * keyword_density`
- `+ 0.20 * definition_hit_bonus`
- subtract penalties for test, generated, vendor, and huge files

### Definition Hit Bonus

- Definition hits should be valued much higher than ordinary keyword appearances.
- A definition hit means a keyword appears on a likely symbol-definition line such as a function, class, struct, module, method, or similar declaration.
- This should be implemented with lightweight regex heuristics by language or extension, not AST parsing in v1.
- Example patterns:
  - Python: `class`, `def`
  - Julia: `function`, `struct`, `mutable struct`, `abstract type`, `module`, compact function assignment forms where feasible
  - TypeScript or JavaScript: `class`, `function`, method signatures, exported declarations
  - C or C++: class or struct declarations and likely function signatures
- If language-specific detection is unavailable, fall back to a small set of generic definition-like patterns.
- Track both:
  - number of definition hits
  - whether the file contains any high-confidence definition hit for a keyword
- A single high-confidence definition hit should often outrank many incidental mentions in comments or docs.

### Source File Detection

- whitelist common code extensions first
- optionally infer from known repo languages
- classify `README`, docs, YAML, JSON, TOML, and config separately
- Definition-hit detection should only run on likely source files to avoid unnecessary overhead.

## Snippet Extraction

Do not read full files by default.

For each selected file:

- collect match line numbers
- merge nearby hits into windows
- read only small windows around hits
- deduplicate overlapping windows
- cap per-file snippet count
- cap per-snippet line count
- cap total lines returned across the whole call

### Recommended Defaults

- `lines_before = 8`
- `lines_after = 12`
- `max_files = 12`
- `max_snippets = 24`
- `max_total_lines = 400`
- `max_snippets_per_file = 3`

### Important Behavior

- if a file has many hits, do not return many windows blindly
- choose the best windows by:
  - highest local keyword density
  - multiple keyword co-occurrence
  - direct definition hits first
  - closeness to definitions if detectable
- trim long windows aggressively

## Model-Facing Instructions

The MCP should return short instructions with the result, not a long essay.

### Recommended Guidance

- Prefer source files over docs when scores are similar.
- Prefer files with multiple distinct keywords over files with one repeated keyword.
- Prefer files where keywords appear in clustered windows, not scattered incidental mentions.
- Use the returned snippets to decide which files deserve deeper reading.
- Do not read all returned files by default.
- If the returned context is insufficient, fall back to normal repository context search.

That last sentence should be explicit.

## Implementation Plan

1. Build a single MCP tool `search_repo_context`.
2. Implement filesystem discovery with `rg --files` and ignore handling.
3. Implement keyword match counting with ripgrep.
4. Implement file classification:
   - source
   - doc
   - config
   - test
   - generated, vendor, binary
5. Implement lightweight definition-hit detection using regex heuristics on likely source files.
6. Implement ranking with explainable scores, including a strong definition-hit bonus.
7. Implement bounded snippet extraction around match windows, prioritizing definition windows first.
8. Add hard budgets on:
   - files
   - snippets
   - total lines
   - max file size
9. Return compact JSON only.
10. Add model instructions in a short `usage_guidance` field.
11. Add fallback instruction if insufficient.

## Testing

Cover at least:

- default search in `cwd`
- explicit directory override
- keyword ranking correctness
- definition-hit ranking correctness
- source-file preference
- overlapping snippet merge
- total line budget enforcement
- binary, large, and generated file exclusion
- behavior when no matches are found
- behavior when only docs match
- fallback guidance always present

## Important Risks

- Raw hit count can over-rank large docs or logs.
- Weak definition heuristics can create false positives, so the bonus should apply only to high-confidence patterns.
- Without density scoring, noisy files will dominate.
- Without hard budgets, the MCP will bloat context and defeat its purpose.
- Without overlap merging, snippet extraction becomes redundant quickly.
- If source-file preference is too strong, relevant docs or config can be missed.
- If too weak, README-like files can crowd out actual implementation files.

## Recommended First Cut

Keep v1 narrow:

- exact keyword search with ripgrep
- lightweight scoring
- regex-based definition-hit bonus
- source-file bonus
- bounded snippets
- compact JSON output

Avoid in v1:

- embeddings
- semantic reranking
- AST parsing
- LLM-in-the-loop ranking

Those can come later once the bounded retrieval behavior is stable.

## Final Instruction For the MCP

If the returned context is insufficient, default back to normal repository context search.
