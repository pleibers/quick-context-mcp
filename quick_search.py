from __future__ import annotations

import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from repo_context_search import search_repo_context_result

SERVER_INSTRUCTIONS = (
    "quick-search provides bounded repository retrieval for keyword-driven codebase "
    "exploration. Use search_repo_context when you want likely relevant files and "
    "small, targeted snippets instead of reading whole files. The tool searches the "
    "current working directory by default, or an explicitly provided directory, then "
    "discovers candidate files, skips obvious noise such as generated files, vendor "
    "directories, binaries, and very large files, ranks matches using explainable "
    "signals such as keyword hits, distinct keyword coverage, source-file preference, "
    "keyword density, and likely symbol-definition matches, and returns only bounded "
    "snippet windows around the strongest hits. The result also includes "
    "omitted_ranked_files for eligible matches that were ranked but not returned due "
    "to max_files limits, and "
    "excluded_keyword_matches for files that matched one or more keywords but were "
    "left out of ranking because they were filtered as generated, vendored, binary, "
    "or too large. Prefer the ranked_files list to decide what deserves deeper "
    "inspection, use snippets as triage context rather than as a complete file view, "
    "and inspect excluded_keyword_matches when you need to audit potentially important "
    "omissions. This tool is best for broad repo search, feature discovery, symbol "
    "hunting, and narrowing a large codebase before normal reads. If the returned "
    "context is insufficient, default back to normal repository context search."
)

DEFAULT_MAX_FILES = 12
DEFAULT_MAX_SNIPPETS = 24
DEFAULT_MAX_TOTAL_LINES = 400

mcp = FastMCP(
    name="quick-search",
    instructions=SERVER_INSTRUCTIONS,
)


@mcp.tool(
    name="search_repo_context",
    description=(
        "Search a repository with one or more exact keywords, rank likely relevant "
        "files without reading full files, and return bounded snippets around the "
        "strongest clustered matches. The tool prefers source files over docs when "
        "scores are similar, boosts likely function, class, struct, module, or method "
        "definition hits, merges overlapping match windows, and enforces hard budgets "
        "on returned files, snippets, and total lines. Use it to narrow a large repo "
        "before deeper inspection. It returns structured output with summary counts, "
        "ranked files, omitted_ranked_files, snippets, excluded_keyword_matches for "
        "filtered-but-matching files, and usage guidance. If the bounded result is "
        "not enough, fall back to normal repository context search."
    ),
    structured_output=True,
)
def search_repo_context(
    keywords: list[str],
    directory: str | None = None,
    max_files: int | None = None,
    max_snippets: int | None = None,
    lines_before: int = 8,
    lines_after: int = 12,
    prefer_source_files: bool = True,
    max_total_lines: int | None = None,
) -> dict[str, Any]:
    """Return ranked file candidates and bounded snippets for keyword-driven repo search.

    Args:
        keywords: One or more non-empty exact keywords to search for.
        directory: Root directory to search. Defaults to the MCP process cwd.
        max_files: Maximum number of ranked files to return.
        max_snippets: Maximum number of snippet windows to return across all files.
        lines_before: Context lines to include before each selected match window.
        lines_after: Context lines to include after each selected match window.
        prefer_source_files: Prefer source files over docs/config when scores are close.
        max_total_lines: Global line budget across all returned snippets.

    Returns:
        Structured JSON-compatible data with summary counts, ranked files,
        omitted_ranked_files, snippets, excluded_keyword_matches, and short guidance
        for next-step reading.
    """
    return search_repo_context_result(
        keywords=keywords,
        directory=directory,
        max_files=_get_int_env("QUICK_SEARCH_MAX_FILES", max_files, DEFAULT_MAX_FILES),
        max_snippets=_get_int_env(
            "QUICK_SEARCH_MAX_SNIPPETS",
            max_snippets,
            DEFAULT_MAX_SNIPPETS,
        ),
        lines_before=lines_before,
        lines_after=lines_after,
        prefer_source_files=prefer_source_files,
        max_total_lines=_get_int_env(
            "QUICK_SEARCH_MAX_TOTAL_LINES",
            max_total_lines,
            DEFAULT_MAX_TOTAL_LINES,
        ),
    )


def _get_int_env(name: str, explicit_value: int | None, default: int) -> int:
    if explicit_value is not None:
        return explicit_value
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw_value!r}") from exc
    if value < 1:
        raise ValueError(f"{name} must be >= 1, got {value}")
    return value
