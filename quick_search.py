from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from mcp.server.fastmcp import Context, FastMCP

from repo_context_search import focused_context_result, search_repo_context_result

SERVER_INSTRUCTIONS = (
    "quick-search provides bounded repository retrieval for keyword-driven "
    "codebase "
    "exploration. Use search_repo_context when you want likely relevant files and "
    "small, targeted snippets instead of reading whole files. The tool searches the "
    "current working directory by default, or an explicitly provided directory, then "
    "discovers candidate files, optionally restricts search to a subtree or globbed "
    "path set, skips obvious noise such as generated files, vendor directories, "
    "binaries, and very large files, ranks matches using explainable signals such as "
    "keyword hits, distinct keyword coverage, source-file preference, keyword "
    "density, and likely symbol-definition matches, and returns only bounded snippet "
    "windows around the strongest hits. Prefer the ranked_files list to decide what "
    "deserves deeper inspection, and use snippets as triage context rather than as a "
    "complete file view. The result is intentionally not exhaustive: it does not "
    "contain all files that might be eligible for context, especially when ranking "
    "and snippet budgets are tight. For agent use, pass an absolute directory path. "
    "Relative directory values such as '.' are only a best-effort fallback when the "
    "MCP client exposes roots, and must not be relied on across clients. This tool "
    "expands provided keywords into a bounded, more general query set and returns "
    "the resolved query terms for transparency. Prefer specific keywords over very "
    "general ones, because expansion terms are only weak recall helpers and the "
    "ranking favors matches on the original input keywords. Agent workflow: start with output_mode='compact' "
    "and include_diagnostics=false to keep context small; if the result looks "
    "promising but you need more ranking or query detail, reuse the returned "
    "query_id with output_mode='full' to expand the cached result without "
    "recomputing; if you need to debug empty or surprising results, reuse the same "
    "query_id with include_diagnostics=true. It is best for broad repo search, "
    "feature discovery, symbol hunting, and narrowing a large codebase before "
    "normal reads. When broad search indicates the relevant code is spread across "
    "multiple files, use the focused context tool to retrieve full enclosing "
    "definitions from just those files. Only do further searches across the "
    "repository if this context is insufficient."
)

DEFAULT_MAX_FILES = 12
DEFAULT_MAX_SNIPPETS = 8
DEFAULT_MAX_TOTAL_LINES = 1200

mcp = FastMCP(
    name="quick-search",
    instructions=SERVER_INSTRUCTIONS,
)


@mcp.tool(
    name="search_repo_context",
    description=(
        "Search a repository with explicit keywords, rank likely relevant "
        "files without reading full files, and return bounded snippets around the "
        "strongest clustered matches. The tool prefers source files over docs when "
        "scores are similar, boosts likely function, class, struct, module, or method "
        "definition hits, merges overlapping match windows, and enforces hard budgets "
        "on returned files, snippets, and total lines. Optional subtree and glob "
        "filters can narrow the search space before ranking. Matching defaults to "
        "substring mode, with optional word and identifier-aware modes for stricter "
        "code search. Keyword expansion is bounded and returned transparently in the "
        "output. Prefer specific keywords over very general ones; ranking gives "
        "substantially more weight to matches on the original input keywords than "
        "to expansion-only matches. Preferred agent workflow: call the "
        "tool in compact mode first, then expand the cached result by query_id in "
        "full mode only if you need richer metadata; enable diagnostics only for "
        "debugging or tuning. Use it to narrow a large repo before deeper "
        "inspection. It returns structured output with summary counts, ranked files, "
        "snippets, and usage guidance. Pass an absolute directory path for reliable "
        "agent behavior. Relative directory values such as '.' only work when the "
        "MCP client exposes roots, so they are not portable across clients. The "
        "result is bounded and does not necessarily include every file that could be "
        "relevant for context. Only do further searches across the repository if "
        "this context is insufficient."
    ),
    structured_output=True,
)
async def search_repo_context(
    keywords: list[str] | None = None,
    query_id: str | None = None,
    directory: str | None = None,
    subpath: str | None = None,
    paths_include_glob: str | None = None,
    paths_exclude_glob: str | None = None,
    match_mode: str = "substring",
    include_diagnostics: bool = False,
    output_mode: str = "compact",
    max_files: int | None = None,
    max_snippets: int | None = None,
    lines_before: int = 24,
    lines_after: int = 40,
    prefer_source_files: bool = True,
    max_total_lines: int | None = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Return ranked file candidates and bounded snippets for keyword-driven repo search.

    Args:
        keywords: Optional explicit keywords to search for. Prefer specific
            phrases or identifiers over very general terms because ranking
            prioritizes original input-keyword matches.
        query_id: Optional cached result id returned by a prior call. When
            provided, the cached search result is rendered again with the
            requested output_mode and diagnostics settings without recomputing
            the search. This is the preferred way to expand a compact result.
        directory: Root directory to search. Pass an absolute path for reliable
            agent behavior. Relative paths only work when the MCP client exposes
            roots.
        subpath: Optional subtree or single file within directory to search.
            Must be relative to directory.
        paths_include_glob: Optional glob that candidate file paths must match,
            relative to directory.
        paths_exclude_glob: Optional glob that candidate file paths must not
            match, relative to directory.
        match_mode: Matching mode for keywords. Use `substring` for current
            behavior, `word` for word-boundary matching, or `identifier` for
            snake_case and camelCase token matching.
        include_diagnostics: Whether to include compact backend and exclusion
            diagnostics in the response.
        output_mode: Response verbosity. Use `compact` for lean agent context
            or `full` for richer ranking and snippet metadata.
        max_files: Maximum number of ranked files to return.
        max_snippets: Maximum number of snippet windows to return across all files.
        lines_before: Context lines to include before each selected match window.
        lines_after: Context lines to include after each selected match window.
        prefer_source_files: Prefer source files over docs/config when scores are close.
        max_total_lines: Global line budget across all returned snippets.
        ctx: Optional FastMCP request context, used to resolve client roots.

    Returns:
        Structured JSON-compatible data with summary counts, ranked files, snippets,
        and short guidance for next-step reading.
    """
    resolved_directory = await _resolve_directory(directory, ctx)
    return search_repo_context_result(
        keywords=keywords,
        query_id=query_id,
        directory=resolved_directory,
        subpath=subpath,
        paths_include_glob=paths_include_glob,
        paths_exclude_glob=paths_exclude_glob,
        match_mode=match_mode,
        include_diagnostics=include_diagnostics,
        output_mode=output_mode,
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


@mcp.tool(
    name="search_focused_context",
    description=(
        "Use this only after `search_repo_context` when the relevant code is spread "
        "across multiple files and you need full enclosing functions, classes, or "
        "similar definitions instead of truncated snippets. It reuses a prior "
        "`query_id`, narrows to a few files, prefers source files, and returns "
        "complete matching blocks without dumping whole files."
    ),
    structured_output=True,
)
async def search_focused_context(
    query_id: str | None = None,
    keywords: list[str] | None = None,
    directory: str | None = None,
    subpath: str | None = None,
    paths_include_glob: str | None = None,
    paths_exclude_glob: str | None = None,
    match_mode: str = "substring",
    file_paths: list[str] | None = None,
    max_files: int = 3,
    max_blocks: int = 6,
    max_blocks_per_file: int = 2,
    max_total_lines: int = 400,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Return multi-file focused code context from a prior broad repo search.

    Args:
        query_id: Optional cached search id returned by `search_repo_context`.
        keywords: Optional explicit keywords for standalone focused search.
        directory: Root directory for standalone focused search.
        subpath: Optional subtree or file restriction for standalone focused search.
        paths_include_glob: Optional include glob for standalone focused search.
        paths_exclude_glob: Optional exclude glob for standalone focused search.
        match_mode: Matching mode for standalone focused search.
        file_paths: Optional relative paths to focus on. When omitted, the tool
            selects the strongest source files from the broad-search result.
        max_files: Maximum number of files to consider.
        max_blocks: Maximum number of full blocks to return across files.
        max_blocks_per_file: Maximum number of blocks to return from any single file.
        max_total_lines: Global line budget across returned blocks.

    Returns:
        Structured JSON-compatible data with the selected files and full
        enclosing code blocks around the strongest matches.
    """
    resolved_directory = await _resolve_directory(directory, ctx)
    return focused_context_result(
        query_id=query_id,
        keywords=keywords,
        directory=resolved_directory,
        subpath=subpath,
        paths_include_glob=paths_include_glob,
        paths_exclude_glob=paths_exclude_glob,
        match_mode=match_mode,
        file_paths=file_paths,
        max_files=max_files,
        max_blocks=max_blocks,
        max_blocks_per_file=max_blocks_per_file,
        max_total_lines=max_total_lines,
    )


async def _resolve_directory(directory: str | None, ctx: Context | None) -> str | None:
    if directory is None:
        return await _default_directory_from_client_roots(ctx)

    candidate = Path(directory).expanduser()
    if candidate.is_absolute():
        return str(candidate.resolve())

    client_root = await _resolve_relative_base(candidate, ctx)
    if client_root is None:
        raise ValueError(
            "relative directory paths such as '.' are not reliable across MCP "
            "clients and require client roots support; pass an absolute path"
        )
    return str((client_root / candidate).resolve())


async def _default_directory_from_client_roots(ctx: Context | None) -> str | None:
    roots = await _list_client_root_paths(ctx)
    if not roots:
        return None
    if len(roots) == 1:
        return str(roots[0])
    raise ValueError(
        "directory is ambiguous because the MCP client exposed multiple roots; pass "
        "an absolute directory"
    )


async def _resolve_relative_base(
    relative_directory: Path,
    ctx: Context | None,
) -> Path | None:
    roots = await _list_client_root_paths(ctx)
    if not roots:
        return None
    if len(roots) == 1:
        return roots[0]

    matching_roots = [root for root in roots if (root / relative_directory).exists()]
    if len(matching_roots) == 1:
        return matching_roots[0]
    if len(matching_roots) > 1:
        raise ValueError(
            f"relative directory {relative_directory!s} is ambiguous across multiple "
            "MCP client roots; pass an absolute directory"
        )
    raise ValueError(
        f"relative directory {relative_directory!s} could not be resolved against "
        "the available MCP client roots; pass an absolute directory"
    )


async def _list_client_root_paths(ctx: Context | None) -> list[Path]:
    if ctx is None:
        return []
    try:
        roots_result = await ctx.request_context.session.list_roots()
    except Exception:
        return []
    root_paths: list[Path] = []
    for root in roots_result.roots:
        parsed = urlparse(str(root.uri))
        if parsed.scheme != "file":
            continue
        root_path = Path(unquote(parsed.path)).expanduser().resolve()
        root_paths.append(root_path)
    return root_paths


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
