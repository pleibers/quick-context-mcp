from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from mcp.server.fastmcp import Context, FastMCP

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
    "snippet windows around the strongest hits. Prefer the ranked_files list to "
    "decide what deserves deeper inspection, and use snippets as triage context "
    "rather than as a complete file view. The result is intentionally not exhaustive: "
    "it does not contain all files that might be eligible for context, especially "
    "when ranking and snippet budgets are tight. For agent use, pass an absolute "
    "directory path. Relative directory values such as '.' are only a best-effort "
    "fallback when the MCP client exposes roots, and must not be relied on across "
    "clients. This tool is best for broad repo search, feature discovery, symbol "
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
        "ranked files, snippets, and usage guidance. Pass an absolute directory path "
        "for reliable agent behavior. Relative directory values such as '.' only "
        "work when the MCP client exposes roots, so they are not portable across "
        "clients. The result is bounded and does not necessarily include every file "
        "that could be relevant for context. If the bounded result is not enough, "
        "fall back to normal repository context search."
    ),
    structured_output=True,
)
async def search_repo_context(
    keywords: list[str],
    directory: str | None = None,
    max_files: int | None = None,
    max_snippets: int | None = None,
    lines_before: int = 8,
    lines_after: int = 12,
    prefer_source_files: bool = True,
    max_total_lines: int | None = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Return ranked file candidates and bounded snippets for keyword-driven repo search.

    Args:
        keywords: One or more non-empty exact keywords to search for.
        directory: Root directory to search. Pass an absolute path for reliable
            agent behavior. Relative paths only work when the MCP client exposes
            roots.
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
        directory=resolved_directory,
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
