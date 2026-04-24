from __future__ import annotations

from collections import Counter, OrderedDict
from copy import deepcopy
import hashlib
import json
import logging
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any, Literal

DEFAULT_MAX_FILE_SIZE_BYTES = 1_000_000
DEFAULT_MAX_SNIPPETS_PER_FILE = 3
DEFAULT_MAX_QUERY_TERMS = 8
DEFAULT_CACHE_SIZE = 64
LOGGER = logging.getLogger(__name__)
GUIDANCE = (
    "Prefer source files over docs when scores are similar. Prefer files with "
    "multiple distinct keywords and clustered hits. Use the snippets to decide "
    "which files deserve deeper reading. Only do further searches across the "
    "repository if this context is insufficient."
)

SKIP_DIRECTORIES = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "target",
    ".mypy_cache",
    ".pytest_cache",
}
VENDOR_DIRECTORIES = {"vendor", "vendors", "third_party", "third-party", "external"}
GENERATED_DIRECTORIES = {"generated", "autogen", "gen"}
SOURCE_EXTENSIONS = {
    ".c",
    ".cc",
    ".cpp",
    ".cxx",
    ".go",
    ".h",
    ".hpp",
    ".java",
    ".jl",
    ".js",
    ".jsx",
    ".m",
    ".mm",
    ".py",
    ".r",
    ".rs",
    ".scala",
    ".swift",
    ".ts",
    ".tsx",
}
DOC_EXTENSIONS = {".adoc", ".md", ".qmd", ".rst", ".txt"}
CONFIG_EXTENSIONS = {".cfg", ".conf", ".ini", ".json", ".toml", ".yaml", ".yml"}
BINARY_EXTENSIONS = {
    ".a",
    ".bin",
    ".class",
    ".dll",
    ".dylib",
    ".gif",
    ".gz",
    ".ico",
    ".jpeg",
    ".jpg",
    ".mp3",
    ".mp4",
    ".o",
    ".obj",
    ".pdf",
    ".png",
    ".pyc",
    ".so",
    ".svgz",
    ".tar",
    ".tif",
    ".tiff",
    ".wav",
    ".webp",
    ".zip",
}
COMMENT_PREFIXES = ("#", "//", "/*", "*", "--")
GENERATED_FILE_PATTERNS = (
    re.compile(r".*\.generated\.[^.]+$", re.IGNORECASE),
    re.compile(r".*_pb2\.py$", re.IGNORECASE),
    re.compile(r".*\.min\.(js|css)$", re.IGNORECASE),
    re.compile(r"^package-lock\.json$", re.IGNORECASE),
)
DEFINITION_PATTERNS = {
    ".py": (re.compile(r"^\s*(async\s+def|def|class)\s+\w+", re.IGNORECASE),),
    ".jl": (
        re.compile(
            r"^\s*(function|struct|mutable\s+struct|abstract\s+type|module)\b",
            re.IGNORECASE,
        ),
        re.compile(r"^\s*[\w!]+\s*\([^)]*\)\s*=", re.IGNORECASE),
    ),
    ".js": (
        re.compile(r"^\s*(export\s+)?(async\s+)?function\b", re.IGNORECASE),
        re.compile(r"^\s*(export\s+)?class\b", re.IGNORECASE),
        re.compile(
            r"^\s*(export\s+)?(const|let|var)\s+\w+\s*=\s*(async\s*)?.*=>",
            re.IGNORECASE,
        ),
    ),
    ".ts": (
        re.compile(r"^\s*(export\s+)?(async\s+)?function\b", re.IGNORECASE),
        re.compile(r"^\s*(export\s+)?class\b", re.IGNORECASE),
        re.compile(
            r"^\s*(export\s+)?(const|let|var)\s+\w+\s*=\s*(async\s*)?.*=>",
            re.IGNORECASE,
        ),
        re.compile(r"^\s*\w+\s*\([^)]*\)\s*:\s*[\w<>\[\], |]+\s*\{"),
    ),
    ".jsx": (
        re.compile(r"^\s*(export\s+)?(async\s+)?function\b", re.IGNORECASE),
        re.compile(r"^\s*(export\s+)?class\b", re.IGNORECASE),
    ),
    ".tsx": (
        re.compile(r"^\s*(export\s+)?(async\s+)?function\b", re.IGNORECASE),
        re.compile(r"^\s*(export\s+)?class\b", re.IGNORECASE),
    ),
    ".c": (
        re.compile(r"^\s*(class|struct)\s+\w+", re.IGNORECASE),
        re.compile(r"^\s*[\w:\<\>\~\*&,\s]+\s+\w+\s*\([^;]*\)\s*\{?$"),
    ),
    ".cc": (
        re.compile(r"^\s*(class|struct)\s+\w+", re.IGNORECASE),
        re.compile(r"^\s*[\w:\<\>\~\*&,\s]+\s+\w+\s*\([^;]*\)\s*\{?$"),
    ),
    ".cpp": (
        re.compile(r"^\s*(class|struct)\s+\w+", re.IGNORECASE),
        re.compile(r"^\s*[\w:\<\>\~\*&,\s]+\s+\w+\s*\([^;]*\)\s*\{?$"),
    ),
    ".cxx": (
        re.compile(r"^\s*(class|struct)\s+\w+", re.IGNORECASE),
        re.compile(r"^\s*[\w:\<\>\~\*&,\s]+\s+\w+\s*\([^;]*\)\s*\{?$"),
    ),
    ".h": (
        re.compile(r"^\s*(class|struct)\s+\w+", re.IGNORECASE),
        re.compile(r"^\s*[\w:\<\>\~\*&,\s]+\s+\w+\s*\([^;]*\)\s*;?$"),
    ),
    ".hpp": (
        re.compile(r"^\s*(class|struct)\s+\w+", re.IGNORECASE),
        re.compile(r"^\s*[\w:\<\>\~\*&,\s]+\s+\w+\s*\([^;]*\)\s*;?$"),
    ),
}
GENERIC_DEFINITION_PATTERNS = (
    re.compile(r"^\s*(class|struct|module|function|def)\b", re.IGNORECASE),
)
TEST_PATH_RE = re.compile(r"(^|/)(test|tests|testing|spec|specs)(/|$)", re.IGNORECASE)
IDENTIFIER_PART_RE = re.compile(
    r"[A-Z]+(?=[A-Z][a-z]|\d|$)|[A-Z]?[a-z]+|\d+"
)
STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "how",
    "i",
    "in",
    "into",
    "is",
    "it",
    "me",
    "need",
    "of",
    "on",
    "or",
    "please",
    "show",
    "that",
    "the",
    "this",
    "to",
    "use",
    "want",
    "where",
    "which",
    "with",
}
_RESULT_CACHE: OrderedDict[str, dict[str, Any]] = OrderedDict()


@dataclass(slots=True)
class FileClassification:
    category: str
    is_source_file: bool
    is_test_file: bool


@dataclass(slots=True)
class MatchRecord:
    line_number: int
    matched_keywords: set[str]
    match_count: int
    input_keyword_hits: int
    definition_hit: bool


@dataclass(slots=True)
class FileRecord:
    path: Path
    relative_path: str
    classification: FileClassification
    line_count: int = 0
    keyword_hits: int = 0
    weighted_keyword_hits: float = 0.0
    input_keyword_hits: int = 0
    distinct_keywords: set[str] = field(default_factory=set)
    distinct_input_keywords: set[str] = field(default_factory=set)
    definition_hits: int = 0
    matches: list[MatchRecord] = field(default_factory=list)
    score: float = 0.0
    reason: str = ""

@dataclass(slots=True)
class SnippetWindow:
    start: int
    end: int
    match_count: int
    matched_keywords: set[str]
    has_definition_hit: bool
    density: float


@dataclass(slots=True)
class ContextBlock:
    source_path: Path
    path: str
    start: int
    end: int
    block_type: str
    signature: str
    matched_keywords: set[str] = field(default_factory=set)
    distinct_input_keywords: set[str] = field(default_factory=set)
    input_keyword_hits: int = 0
    keyword_hits: int = 0
    definition_hits: int = 0
    match_lines: set[int] = field(default_factory=set)
    score: float = 0.0


def search_repo_context_result(
    keywords: list[str] | None = None,
    query_id: str | None = None,
    directory: str | None = None,
    subpath: str | None = None,
    paths_include_glob: str | None = None,
    paths_exclude_glob: str | None = None,
    match_mode: Literal["substring", "word", "identifier"] = "substring",
    include_diagnostics: bool = False,
    output_mode: Literal["compact", "full"] = "compact",
    max_files: int = 12,
    max_snippets: int = 8,
    lines_before: int = 24,
    lines_after: int = 40,
    prefer_source_files: bool = True,
    max_total_lines: int = 1200,
    max_snippets_per_file: int = DEFAULT_MAX_SNIPPETS_PER_FILE,
    max_file_size_bytes: int = DEFAULT_MAX_FILE_SIZE_BYTES,
) -> dict[str, Any]:
    if query_id is not None:
        cached_result = _get_cached_result(query_id)
        if cached_result is None:
            raise ValueError(f"unknown query_id: {query_id}")
        return _render_cached_result(
            cached_result,
            output_mode=output_mode,
            include_diagnostics=include_diagnostics,
        )

    query_details = _resolve_query_details(keywords)
    input_keywords = query_details["keywords"]
    normalized_keywords = query_details["resolved_keywords"]
    if not input_keywords:
        raise ValueError("provide at least one non-empty keyword")
    if match_mode not in {"substring", "word", "identifier"}:
        raise ValueError(
            "match_mode must be one of: substring, word, identifier"
        )
    if output_mode not in {"compact", "full"}:
        raise ValueError("output_mode must be one of: compact, full")
    if max_files < 1 or max_snippets < 1 or max_total_lines < 1:
        raise ValueError("max_files, max_snippets, and max_total_lines must be >= 1")
    if lines_before < 0 or lines_after < 0:
        raise ValueError("lines_before and lines_after must be >= 0")

    root = Path(directory).expanduser().resolve() if directory else Path.cwd().resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError(f"directory does not exist or is not a directory: {root}")
    scope = _resolve_subpath(root, subpath)

    file_paths, discovery_diagnostics = _discover_candidate_files(
        root,
        scope=scope,
        paths_include_glob=paths_include_glob,
        paths_exclude_glob=paths_exclude_glob,
        max_file_size_bytes=max_file_size_bytes,
    )
    records, match_backend = _collect_matches(
        root, file_paths, normalized_keywords, input_keywords, match_mode
    )
    _rank_records(
        records,
        input_keyword_count=len(input_keywords),
        keyword_count=len(normalized_keywords),
        prefer_source_files=prefer_source_files,
    )

    ranked_records = records[:max_files]
    budget_truncated = len(records) > len(ranked_records)
    snippets, snippet_budget_truncated = _extract_snippets(
        ranked_records=ranked_records,
        lines_before=lines_before,
        lines_after=lines_after,
        max_snippets=max_snippets,
        max_total_lines=max_total_lines,
        max_snippets_per_file=max_snippets_per_file,
    )
    budget_truncated = budget_truncated or snippet_budget_truncated
    _log_search_summary(
        root=root,
        keywords=normalized_keywords,
        files_considered=len(file_paths),
        records=records,
        ranked_records=ranked_records,
        snippets=snippets,
        budget_truncated=budget_truncated,
    )

    full_result = {
        "searched_directory": str(root),
        "summary": {
            "files_considered": len(file_paths),
            "files_ranked": len(records),
            "files_returned": len(ranked_records),
            "snippets_returned": len(snippets),
            "budget_truncated": budget_truncated,
        },
        "query": query_details,
        "ranked_files": _format_ranked_files(ranked_records, max_files, "full"),
        "snippets": _format_snippets(snippets, "full"),
        "usage_guidance": GUIDANCE,
        "diagnostics": {
            "discovery_backend": discovery_diagnostics["backend"],
            "matching_backend": match_backend,
            "excluded_file_counts": discovery_diagnostics["excluded_counts"],
        },
        "search_config": {
            "match_mode": match_mode,
        },
    }
    canonical_query_id = _make_query_id(
        {
            "keywords": keywords or [],
            "resolved_keywords": normalized_keywords,
            "directory": str(root),
            "subpath": subpath or "",
            "paths_include_glob": paths_include_glob or "",
            "paths_exclude_glob": paths_exclude_glob or "",
            "match_mode": match_mode,
            "max_files": max_files,
            "max_snippets": max_snippets,
            "lines_before": lines_before,
            "lines_after": lines_after,
            "prefer_source_files": prefer_source_files,
            "max_total_lines": max_total_lines,
            "max_snippets_per_file": max_snippets_per_file,
            "max_file_size_bytes": max_file_size_bytes,
        }
    )
    full_result["query_id"] = canonical_query_id
    _store_cached_result(canonical_query_id, full_result)
    return _render_cached_result(
        full_result,
        output_mode=output_mode,
        include_diagnostics=include_diagnostics,
    )


def focused_context_result(
    query_id: str | None = None,
    keywords: list[str] | None = None,
    directory: str | None = None,
    subpath: str | None = None,
    paths_include_glob: str | None = None,
    paths_exclude_glob: str | None = None,
    match_mode: Literal["substring", "word", "identifier"] = "substring",
    file_paths: list[str] | None = None,
    max_files: int = 3,
    max_blocks: int = 6,
    max_blocks_per_file: int = 2,
    max_total_lines: int = 400,
) -> dict[str, Any]:
    if max_files < 1 or max_blocks < 1 or max_blocks_per_file < 1 or max_total_lines < 1:
        raise ValueError(
            "max_files, max_blocks, max_blocks_per_file, and max_total_lines must be >= 1"
        )
    if query_id is None and not _normalize_keywords(keywords or []):
        raise ValueError("provide either query_id or at least one non-empty keyword")

    if query_id is None and file_paths:
        return _focused_context_from_direct_files(
            keywords=keywords or [],
            directory=directory,
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

    if query_id is not None:
        cached_result = _get_cached_result(query_id)
        if cached_result is None:
            raise ValueError(f"unknown query_id: {query_id}")
    else:
        cached_result = search_repo_context_result(
            keywords=keywords,
            directory=directory,
            subpath=subpath,
            paths_include_glob=paths_include_glob,
            paths_exclude_glob=paths_exclude_glob,
            match_mode=match_mode,
            output_mode="full",
            max_files=max(12, max_files),
            max_snippets=1,
            lines_before=0,
            lines_after=0,
            max_total_lines=1,
        )
        query_id = cached_result["query_id"]

    root = Path(cached_result["searched_directory"])
    query_details = cached_result["query"]
    input_keywords = query_details["keywords"]
    resolved_keywords = query_details["resolved_keywords"]
    match_mode = cached_result.get("search_config", {}).get("match_mode", "substring")

    selected_relative_paths = _select_focused_file_paths(
        cached_result["ranked_files"],
        file_paths=file_paths,
        max_files=max_files,
    )
    selected_paths = [root / relative_path for relative_path in selected_relative_paths]
    records, _backend = _collect_matches(
        root,
        selected_paths,
        resolved_keywords,
        input_keywords,
        match_mode,
    )
    _rank_records(
        records,
        input_keyword_count=len(input_keywords),
        keyword_count=len(resolved_keywords),
        prefer_source_files=True,
    )
    blocks, budget_truncated = _extract_context_blocks(
        records,
        max_blocks=max_blocks,
        max_blocks_per_file=max_blocks_per_file,
        max_total_lines=max_total_lines,
        input_keyword_count=len(input_keywords),
    )
    return {
        "query_id": query_id,
        "searched_directory": str(root),
        "query": query_details,
        "summary": {
            "candidate_files_considered": len(selected_relative_paths),
            "files_with_context": len({block["path"] for block in blocks}),
            "blocks_returned": len(blocks),
            "budget_truncated": budget_truncated,
        },
        "candidate_files": selected_relative_paths,
        "blocks": blocks,
        "usage_guidance": (
            "Use this focused tool only after broad search when relevant context spans "
            "multiple files. It returns full enclosing definitions when possible "
            "instead of truncated snippets."
        ),
    }


def _select_focused_file_paths(
    ranked_files: list[dict[str, Any]],
    file_paths: list[str] | None,
    max_files: int,
) -> list[str]:
    if file_paths:
        return _normalize_file_paths(file_paths)[:max_files]

    selected: list[str] = []
    for ranked_file in ranked_files:
        category = ranked_file["category"]
        if category not in {"source", "test"}:
            continue
        selected.append(ranked_file["path"])
        if len(selected) >= max_files:
            break
    if selected:
        return selected
    return [ranked_file["path"] for ranked_file in ranked_files[:max_files]]


def _normalize_file_paths(file_paths: list[str]) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for file_path in file_paths:
        candidate = file_path.strip().replace("\\", "/")
        if candidate and candidate not in seen:
            seen.add(candidate)
            normalized.append(candidate)
    return normalized


def _focused_context_from_direct_files(
    keywords: list[str],
    directory: str | None,
    subpath: str | None,
    paths_include_glob: str | None,
    paths_exclude_glob: str | None,
    match_mode: Literal["substring", "word", "identifier"],
    file_paths: list[str],
    max_files: int,
    max_blocks: int,
    max_blocks_per_file: int,
    max_total_lines: int,
) -> dict[str, Any]:
    root = Path(directory).expanduser().resolve() if directory else Path.cwd().resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError(f"directory does not exist or is not a directory: {root}")

    query_details = _resolve_query_details(keywords)
    input_keywords = query_details["keywords"]
    resolved_keywords = query_details["resolved_keywords"]
    if not input_keywords:
        raise ValueError("provide at least one non-empty keyword")

    requested_paths = _normalize_file_paths(file_paths)[:max_files]
    scope = _resolve_subpath(root, subpath)
    scope_relative = scope.relative_to(root) if scope is not None else None
    scope_is_file = scope.is_file() if scope is not None else False

    selected_paths: list[Path] = []
    selected_relative_paths: list[str] = []
    for relative_path_text in requested_paths:
        relative_path = Path(relative_path_text)
        candidate = (root / relative_path).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError(
                f"file path escapes the search directory: {relative_path_text!r}"
            ) from exc
        if not candidate.exists() or not candidate.is_file():
            raise ValueError(
                f"file path does not exist within directory: {relative_path_text!r}"
            )
        normalized_relative = candidate.relative_to(root).as_posix()
        if not _path_in_scope(Path(normalized_relative), scope_relative, scope_is_file):
            raise ValueError(
                f"file path is outside the requested subpath: {relative_path_text!r}"
            )
        glob_reason = _glob_exclusion_reason(
            normalized_relative,
            paths_include_glob=paths_include_glob,
            paths_exclude_glob=paths_exclude_glob,
        )
        if glob_reason is not None:
            raise ValueError(
                f"file path is excluded by the provided glob filters: {relative_path_text!r}"
            )
        selected_paths.append(candidate)
        selected_relative_paths.append(normalized_relative)

    records, _backend = _collect_matches(
        root,
        selected_paths,
        resolved_keywords,
        input_keywords,
        match_mode,
    )
    _rank_records(
        records,
        input_keyword_count=len(input_keywords),
        keyword_count=len(resolved_keywords),
        prefer_source_files=True,
    )
    blocks, budget_truncated = _extract_context_blocks(
        records,
        max_blocks=max_blocks,
        max_blocks_per_file=max_blocks_per_file,
        max_total_lines=max_total_lines,
        input_keyword_count=len(input_keywords),
    )
    direct_query_id = _make_query_id(
        {
            "mode": "focused_direct",
            "keywords": input_keywords,
            "resolved_keywords": resolved_keywords,
            "directory": str(root),
            "subpath": subpath or "",
            "paths_include_glob": paths_include_glob or "",
            "paths_exclude_glob": paths_exclude_glob or "",
            "match_mode": match_mode,
            "file_paths": selected_relative_paths,
            "max_files": max_files,
            "max_blocks": max_blocks,
            "max_blocks_per_file": max_blocks_per_file,
            "max_total_lines": max_total_lines,
        }
    )
    return {
        "query_id": direct_query_id,
        "searched_directory": str(root),
        "query": query_details,
        "summary": {
            "candidate_files_considered": len(selected_relative_paths),
            "files_with_context": len({block["path"] for block in blocks}),
            "blocks_returned": len(blocks),
            "budget_truncated": budget_truncated,
        },
        "candidate_files": selected_relative_paths,
        "blocks": blocks,
        "usage_guidance": (
            "Direct-file focused mode skips broad ranking and searches only the "
            "provided files for the requested keywords before returning enclosing blocks."
        ),
    }


def _normalize_keywords(keywords: list[str]) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for keyword in keywords:
        cleaned = keyword.strip().lower()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            normalized.append(cleaned)
    return normalized


def _resolve_query_details(
    keywords: list[str] | None,
) -> dict[str, Any]:
    explicit_keywords = _normalize_keywords(keywords or [])
    resolved_keywords = _expand_keywords(explicit_keywords)
    return {
        "keywords": explicit_keywords,
        "resolved_keywords": resolved_keywords,
    }


def _expand_keywords(keywords: list[str]) -> list[str]:
    resolved_keywords: list[str] = []
    seen: set[str] = set()

    def add_keyword(term: str) -> bool:
        cleaned = term.strip().lower()
        if not cleaned or cleaned in seen:
            return False
        seen.add(cleaned)
        resolved_keywords.append(cleaned)
        return len(resolved_keywords) >= DEFAULT_MAX_QUERY_TERMS

    for keyword in keywords:
        if add_keyword(keyword):
            break
        for variant in _keyword_variants(keyword):
            if add_keyword(variant):
                return resolved_keywords

    return resolved_keywords


def _keyword_variants(keyword: str) -> list[str]:
    tokens: list[str] = []
    for candidate in re.findall(r"[A-Za-z0-9_./-]+", keyword):
        lowered_candidate = candidate.lower()
        if _is_significant_query_term(lowered_candidate):
            tokens.append(lowered_candidate)
        for token in _identifier_tokens(candidate):
            if _is_significant_query_term(token):
                tokens.append(token)

    variants = _normalize_keywords(tokens)
    for first, second in zip(variants, variants[1:], strict=False):
        if first != second:
            variants.append(f"{first} {second}")
    return _normalize_keywords(variants)


def _is_significant_query_term(term: str) -> bool:
    return term not in STOP_WORDS and (
        len(term) >= 3 or "_" in term or "/" in term or "-" in term
    )


def _format_query_details(
    query_details: dict[str, Any],
    output_mode: Literal["compact", "full"],
) -> dict[str, Any]:
    if output_mode == "compact":
        return {
            "keywords": query_details["keywords"],
            "resolved_keywords": query_details["resolved_keywords"],
        }
    return query_details


def _render_cached_result(
    full_result: dict[str, Any],
    output_mode: Literal["compact", "full"],
    include_diagnostics: bool,
) -> dict[str, Any]:
    rendered = {
        "query_id": full_result["query_id"],
        "searched_directory": full_result["searched_directory"],
        "summary": deepcopy(full_result["summary"]),
        "query": _format_query_details(full_result["query"], output_mode),
        "ranked_files": _render_ranked_files_from_full(
            full_result["ranked_files"],
            output_mode,
        ),
        "snippets": _format_snippets(deepcopy(full_result["snippets"]), output_mode),
        "usage_guidance": full_result["usage_guidance"],
    }
    if include_diagnostics:
        rendered["diagnostics"] = deepcopy(full_result["diagnostics"])
    return rendered


def _render_ranked_files_from_full(
    ranked_files: list[dict[str, Any]],
    output_mode: Literal["compact", "full"],
) -> list[dict[str, Any]]:
    if output_mode == "full":
        return deepcopy(ranked_files)
    compact_ranked_files: list[dict[str, Any]] = []
    for ranked_file in ranked_files:
        compact_ranked_files.append(
            {
                "path": ranked_file["path"],
                "category": ranked_file["category"],
                "keyword_hits": ranked_file["keyword_hits"],
                "distinct_keywords_matched": ranked_file["distinct_keywords_matched"],
                "definition_hits": ranked_file["definition_hits"],
                "score": ranked_file["score"],
                "recommended_read": ranked_file["recommended_read"],
                "reason": ranked_file["reason"],
            }
        )
    return compact_ranked_files


def _make_query_id(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def _get_cached_result(query_id: str) -> dict[str, Any] | None:
    cached = _RESULT_CACHE.get(query_id)
    if cached is None:
        return None
    _RESULT_CACHE.move_to_end(query_id)
    return deepcopy(cached)


def _store_cached_result(query_id: str, result: dict[str, Any]) -> None:
    _RESULT_CACHE[query_id] = deepcopy(result)
    _RESULT_CACHE.move_to_end(query_id)
    while len(_RESULT_CACHE) > DEFAULT_CACHE_SIZE:
        _RESULT_CACHE.popitem(last=False)


def _format_ranked_files(
    ranked_records: list[FileRecord],
    max_files: int,
    output_mode: Literal["compact", "full"],
) -> list[dict[str, Any]]:
    ranked_files: list[dict[str, Any]] = []
    for index, record in enumerate(ranked_records):
        entry = {
            "path": record.relative_path,
            "category": record.classification.category,
            "keyword_hits": record.keyword_hits,
            "distinct_keywords_matched": len(record.distinct_keywords),
            "definition_hits": record.definition_hits,
            "score": round(record.score, 3),
            "recommended_read": index < min(5, max_files)
            or record.definition_hits > 0,
            "reason": record.reason,
        }
        if output_mode == "full":
            entry.update(
                {
                    "is_source_file": record.classification.is_source_file,
                    "is_test_file": record.classification.is_test_file,
                    "line_count": record.line_count,
                    "keyword_density": round(
                        record.keyword_hits / max(record.line_count, 1),
                        6,
                    ),
                }
            )
        ranked_files.append(entry)
    return ranked_files


def _format_snippets(
    snippets: list[dict[str, Any]],
    output_mode: Literal["compact", "full"],
) -> list[dict[str, Any]]:
    if output_mode == "full":
        return snippets
    compact_snippets: list[dict[str, Any]] = []
    for snippet in snippets:
        compact_snippets.append(
            {
                "path": snippet["path"],
                "line_start": snippet["line_start"],
                "line_end": snippet["line_end"],
                "matched_keywords": snippet["matched_keywords"],
                "snippet": snippet["snippet"],
            }
        )
    return compact_snippets


def _discover_candidate_files(
    root: Path,
    scope: Path | None,
    paths_include_glob: str | None,
    paths_exclude_glob: str | None,
    max_file_size_bytes: int,
) -> tuple[list[Path], dict[str, Any]]:
    rg_path = shutil.which("rg")
    backend = "ripgrep"
    raw_paths = _discover_with_ripgrep(root, rg_path) if rg_path else None
    if raw_paths is None:
        backend = "walk"
        raw_paths = _discover_with_walk(root)

    candidates: list[Path] = []
    excluded_counts: Counter[str] = Counter()
    scope_relative = scope.relative_to(root) if scope is not None else None
    scope_is_file = scope.is_file() if scope is not None else False
    for relative_path in raw_paths:
        path = root / relative_path
        if not path.is_file():
            continue
        if not _path_in_scope(relative_path, scope_relative, scope_is_file):
            excluded_counts["out_of_scope"] += 1
            continue
        relative_path_text = relative_path.as_posix()
        glob_reason = _glob_exclusion_reason(
            relative_path_text,
            paths_include_glob=paths_include_glob,
            paths_exclude_glob=paths_exclude_glob,
        )
        if glob_reason is not None:
            excluded_counts[glob_reason] += 1
            continue
        excluded_reason = _excluded_reason_for_path(
            relative_path,
            path,
            max_file_size_bytes=max_file_size_bytes,
        )
        if excluded_reason is not None:
            excluded_counts[excluded_reason] += 1
            continue
        candidates.append(path)
    candidates.sort()
    return candidates, {
        "backend": backend,
        "excluded_counts": dict(sorted(excluded_counts.items())),
    }


def _discover_with_ripgrep(root: Path, rg_path: str | None) -> list[Path] | None:
    if rg_path is None:
        return None
    completed = subprocess.run(
        [rg_path, "--files", "-0"],
        cwd=root,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        return None
    entries = [item for item in completed.stdout.split(b"\0") if item]
    return [Path(item.decode("utf-8", errors="replace")) for item in entries]


def _discover_with_walk(root: Path) -> list[Path]:
    candidates: list[Path] = []
    for path in root.rglob("*"):
        try:
            relative_path = path.relative_to(root)
        except ValueError:
            continue
        if path.is_file():
            candidates.append(relative_path)
    return candidates


def _resolve_subpath(root: Path, subpath: str | None) -> Path | None:
    if subpath is None:
        return None
    candidate = Path(subpath).expanduser()
    if candidate.is_absolute():
        raise ValueError("subpath must be relative to directory")

    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            f"subpath escapes the search directory: {subpath!r}"
        ) from exc
    if not resolved.exists():
        raise ValueError(f"subpath does not exist within directory: {subpath!r}")
    return resolved


def _path_in_scope(
    relative_path: Path,
    scope_relative: Path | None,
    scope_is_file: bool,
) -> bool:
    if scope_relative is None:
        return True
    if scope_is_file:
        return relative_path == scope_relative
    scope_parts = scope_relative.parts
    return relative_path.parts[: len(scope_parts)] == scope_parts


def _glob_exclusion_reason(
    relative_path: str,
    paths_include_glob: str | None,
    paths_exclude_glob: str | None,
) -> str | None:
    include_pattern = (paths_include_glob or "").strip()
    exclude_pattern = (paths_exclude_glob or "").strip()
    if include_pattern and not fnmatchcase(relative_path, include_pattern):
        return "include_glob"
    if exclude_pattern and fnmatchcase(relative_path, exclude_pattern):
        return "exclude_glob"
    return None


def _should_skip_path(relative_path: Path) -> bool:
    return _skip_reason_for_path(relative_path) is not None


def _skip_reason_for_path(relative_path: Path) -> str | None:
    parts = relative_path.parts
    for part in parts[:-1]:
        lowered = part.lower()
        if lowered in SKIP_DIRECTORIES:
            return f"excluded directory: {part}"
        if lowered in VENDOR_DIRECTORIES:
            return f"vendor directory: {part}"
        if lowered in GENERATED_DIRECTORIES:
            return f"generated directory: {part}"
    filename = parts[-1].lower()
    if any(pattern.match(filename) for pattern in GENERATED_FILE_PATTERNS):
        return "generated filename pattern"
    return None


def _excluded_reason_for_path(
    relative_path: Path,
    path: Path,
    max_file_size_bytes: int,
) -> str | None:
    skip_reason = _skip_reason_for_path(relative_path)
    if skip_reason is not None:
        return skip_reason
    if path.suffix.lower() in BINARY_EXTENSIONS:
        return f"binary extension: {path.suffix.lower()}"
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size > max_file_size_bytes:
        return f"file exceeds size limit ({size} bytes > {max_file_size_bytes} bytes)"
    if _is_binary_file(path):
        return "binary content"
    return None


def _is_binary_file(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            sample = handle.read(4096)
    except OSError:
        return True
    return b"\0" in sample


def _collect_matches(
    root: Path,
    file_paths: list[Path],
    keywords: list[str],
    input_keywords: list[str],
    match_mode: Literal["substring", "word", "identifier"],
) -> tuple[list[FileRecord], str]:
    eligible = {path.relative_to(root).as_posix(): path for path in file_paths}
    rg_path = shutil.which("rg")
    if rg_path is not None and match_mode == "substring":
        records = _collect_matches_with_ripgrep(
            root, keywords, input_keywords, eligible, rg_path
        )
        if records is not None:
            return records, "ripgrep"
    return (
        _collect_matches_with_python(
            keywords, input_keywords, eligible, match_mode
        ),
        "python",
    )


def _collect_matches_with_ripgrep(
    root: Path,
    keywords: list[str],
    input_keywords: list[str],
    eligible: dict[str, Path],
    rg_path: str,
) -> list[FileRecord] | None:
    compiled = _compile_keyword_patterns(keywords, "substring")
    keyword_set = set(keywords)
    input_keyword_set = set(input_keywords)
    keyword_weights = _keyword_weights(keywords, input_keyword_set)
    records_by_path: dict[str, FileRecord] = {}
    eligible_paths = sorted(eligible)
    for index in range(0, len(eligible_paths), 200):
        batch = eligible_paths[index : index + 200]
        command = [rg_path, "--json", "-n", "-i", "--color", "never"]
        for keyword in keywords:
            command.extend(["-e", _keyword_pattern_text(keyword)])
        command.extend(batch)
        completed = subprocess.run(
            command,
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode not in (0, 1):
            return None

        for raw_line in completed.stdout.splitlines():
            if not raw_line:
                continue
            event = json.loads(raw_line)
            if event.get("type") != "match":
                continue
            data = event["data"]
            relative_path = Path(data["path"]["text"]).as_posix()
            line_text = data["lines"]["text"].rstrip("\n")
            matched_keywords, match_count, input_keyword_hits, weighted_match_hits = _line_match_details(
                line_text,
                compiled,
                "substring",
                keyword_set,
                input_keyword_set,
                keyword_weights,
            )
            if not matched_keywords:
                continue
            definition_hit = _has_definition_hit(
                relative_path, line_text, matched_keywords
            )
            record = records_by_path.setdefault(
                relative_path,
                FileRecord(
                    path=eligible[relative_path],
                    relative_path=relative_path,
                    classification=_classify_path(relative_path),
                ),
            )
            record.keyword_hits += match_count
            record.weighted_keyword_hits += weighted_match_hits
            record.input_keyword_hits += input_keyword_hits
            record.distinct_keywords.update(matched_keywords)
            record.distinct_input_keywords.update(
                keyword for keyword in matched_keywords if keyword in input_keyword_set
            )
            record.matches.append(
                MatchRecord(
                    line_number=int(data["line_number"]),
                    matched_keywords=matched_keywords,
                    match_count=match_count,
                    input_keyword_hits=input_keyword_hits,
                    definition_hit=definition_hit,
                )
            )
            if definition_hit:
                record.definition_hits += 1

    records = list(records_by_path.values())
    for record in records:
        record.line_count = _count_file_lines(record.path)
    return records


def _collect_matches_with_python(
    keywords: list[str],
    input_keywords: list[str],
    eligible: dict[str, Path],
    match_mode: Literal["substring", "word", "identifier"],
) -> list[FileRecord]:
    compiled = _compile_keyword_patterns(keywords, match_mode)
    keyword_set = set(keywords)
    input_keyword_set = set(input_keywords)
    keyword_weights = _keyword_weights(keywords, input_keyword_set)
    records: list[FileRecord] = []
    for relative_path, path in eligible.items():
        matches: list[MatchRecord] = []
        keyword_hits = 0
        weighted_keyword_hits = 0.0
        input_keyword_hits = 0
        distinct_keywords: set[str] = set()
        distinct_input_keywords: set[str] = set()
        definition_hits = 0
        line_count = 0
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for line_count, raw_line in enumerate(handle, start=1):
                    line_text = raw_line.rstrip("\n")
                    (
                        matched_keywords,
                        match_count,
                        line_input_keyword_hits,
                        weighted_match_hits,
                    ) = _line_match_details(
                        line_text,
                        compiled,
                        match_mode,
                        keyword_set,
                        input_keyword_set,
                        keyword_weights,
                    )
                    if not matched_keywords:
                        continue
                    definition_hit = _has_definition_hit(
                        relative_path, line_text, matched_keywords
                    )
                    matches.append(
                        MatchRecord(
                            line_number=line_count,
                            matched_keywords=matched_keywords,
                            match_count=match_count,
                            input_keyword_hits=line_input_keyword_hits,
                            definition_hit=definition_hit,
                        )
                    )
                    keyword_hits += match_count
                    weighted_keyword_hits += weighted_match_hits
                    input_keyword_hits += line_input_keyword_hits
                    distinct_keywords.update(matched_keywords)
                    distinct_input_keywords.update(
                        keyword
                        for keyword in matched_keywords
                        if keyword in input_keyword_set
                    )
                    if definition_hit:
                        definition_hits += 1
        except OSError:
            continue
        if not matches:
            continue
        records.append(
            FileRecord(
                path=path,
                relative_path=relative_path,
                classification=_classify_path(relative_path),
                line_count=line_count,
                keyword_hits=keyword_hits,
                weighted_keyword_hits=weighted_keyword_hits,
                input_keyword_hits=input_keyword_hits,
                distinct_keywords=distinct_keywords,
                distinct_input_keywords=distinct_input_keywords,
                definition_hits=definition_hits,
                matches=matches,
            )
        )
    return records


def _keyword_weights(
    keywords: list[str], input_keywords: set[str]
) -> dict[str, float]:
    return {
        keyword: (4.0 if keyword in input_keywords else 0.1) for keyword in keywords
    }




def _compile_keyword_patterns(
    keywords: list[str],
    match_mode: Literal["substring", "word", "identifier"],
) -> dict[str, re.Pattern[str]] | None:
    if match_mode == "identifier":
        return None
    if match_mode == "word":
        return {
            keyword: re.compile(
                rf"(?<!\w){_keyword_pattern_text(keyword)}(?!\w)",
                re.IGNORECASE,
            )
            for keyword in keywords
        }
    return {
        keyword: re.compile(_keyword_pattern_text(keyword), re.IGNORECASE)
        for keyword in keywords
    }


def _keyword_pattern_text(keyword: str) -> str:
    tokens = [token for token in _identifier_tokens(keyword) if token]
    if len(tokens) >= 2:
        return r"[\W_/-]*".join(re.escape(token) for token in tokens)
    return re.escape(keyword)


def _line_match_details(
    line_text: str,
    compiled_keywords: dict[str, re.Pattern[str]] | None,
    match_mode: Literal["substring", "word", "identifier"] = "substring",
    keywords: set[str] | None = None,
    input_keywords: set[str] | None = None,
    keyword_weights: dict[str, float] | None = None,
) -> tuple[set[str], int, int, float]:
    if match_mode == "identifier":
        return _identifier_match_details(
            line_text,
            keywords or set(),
            input_keywords or set(),
            keyword_weights or {},
        )
    assert compiled_keywords is not None
    matched_keywords: set[str] = set()
    match_count = 0
    input_keyword_hits = 0
    weighted_match_hits = 0.0
    for keyword, pattern in compiled_keywords.items():
        occurrences = len(pattern.findall(line_text))
        if occurrences:
            matched_keywords.add(keyword)
            match_count += occurrences
            if input_keywords and keyword in input_keywords:
                input_keyword_hits += occurrences
            weighted_match_hits += (keyword_weights or {}).get(keyword, 1.0) * occurrences
    return matched_keywords, match_count, input_keyword_hits, weighted_match_hits


def _identifier_match_details(
    line_text: str,
    keywords: set[str],
    input_keywords: set[str],
    keyword_weights: dict[str, float],
) -> tuple[set[str], int, int, float]:
    tokens = _identifier_tokens(line_text)
    matched_keywords: set[str] = set()
    match_count = 0
    input_keyword_hits = 0
    weighted_match_hits = 0.0
    for token in tokens:
        if token in keywords:
            matched_keywords.add(token)
            match_count += 1
            if token in input_keywords:
                input_keyword_hits += 1
            weighted_match_hits += keyword_weights.get(token, 1.0)
    return matched_keywords, match_count, input_keyword_hits, weighted_match_hits


def _identifier_tokens(line_text: str) -> list[str]:
    tokens: list[str] = []
    for raw_identifier in re.findall(r"[A-Za-z0-9_]+", line_text):
        for underscore_part in raw_identifier.split("_"):
            if not underscore_part:
                continue
            for part in IDENTIFIER_PART_RE.findall(underscore_part):
                lowered = part.lower()
                if lowered:
                    tokens.append(lowered)
    return tokens


def _classify_path(relative_path: str) -> FileClassification:
    path = Path(relative_path)
    suffix = path.suffix.lower()
    path_text = path.as_posix().lower()
    is_test = (
        bool(TEST_PATH_RE.search(path_text))
        or path.stem.lower().startswith("test_")
        or path.stem.lower().endswith("_test")
    )
    if suffix in SOURCE_EXTENSIONS:
        category = "test" if is_test else "source"
        return FileClassification(
            category=category, is_source_file=True, is_test_file=is_test
        )
    if suffix in DOC_EXTENSIONS or path.name.lower().startswith("readme"):
        return FileClassification(
            category="doc", is_source_file=False, is_test_file=False
        )
    if suffix in CONFIG_EXTENSIONS:
        return FileClassification(
            category="config", is_source_file=False, is_test_file=False
        )
    return FileClassification(
        category="other", is_source_file=False, is_test_file=is_test
    )


def _has_definition_hit(
    relative_path: str, line_text: str, matched_keywords: set[str]
) -> bool:
    stripped = line_text.strip()
    if not stripped:
        return False
    if stripped.startswith(COMMENT_PREFIXES):
        return False
    lower_line = stripped.lower()
    if not any(keyword in lower_line for keyword in matched_keywords):
        return False
    suffix = Path(relative_path).suffix.lower()
    patterns = DEFINITION_PATTERNS.get(suffix, GENERIC_DEFINITION_PATTERNS)
    return any(pattern.search(stripped) for pattern in patterns)


def _count_file_lines(path: Path) -> int:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return sum(1 for _ in handle) or 1
    except OSError:
        return 1


def _rank_records(
    records: list[FileRecord],
    input_keyword_count: int,
    keyword_count: int,
    prefer_source_files: bool,
) -> None:
    if not records:
        return
    max_input_hits = max(record.input_keyword_hits for record in records)
    max_weighted_hits = max(record.weighted_keyword_hits for record in records)
    max_hits = max(record.keyword_hits for record in records)
    max_density = max(
        record.weighted_keyword_hits / max(record.line_count, 1) for record in records
    )
    max_definition_hits = max(record.definition_hits for record in records)
    for record in records:
        input_hit_score = (
            record.input_keyword_hits / max_input_hits if max_input_hits else 0.0
        )
        input_distinct_score = (
            len(record.distinct_input_keywords) / input_keyword_count
            if input_keyword_count
            else 0.0
        )
        weighted_hit_score = (
            record.weighted_keyword_hits / max_weighted_hits
            if max_weighted_hits
            else 0.0
        )
        hit_score = record.keyword_hits / max_hits if max_hits else 0.0
        distinct_score = (
            len(record.distinct_keywords) / keyword_count if keyword_count else 0.0
        )
        density = record.weighted_keyword_hits / max(record.line_count, 1)
        density_score = density / max_density if max_density else 0.0
        definition_score = 0.0
        if record.definition_hits:
            scaled = (
                record.definition_hits / max_definition_hits
                if max_definition_hits
                else 1.0
            )
            definition_score = min(1.0, 0.5 + 0.5 * scaled)

        source_bonus = _source_bonus(record.classification, prefer_source_files)
        penalty = _penalty(record.classification)
        score = (
            0.35 * input_hit_score
            + 0.25 * input_distinct_score
            + 0.10 * weighted_hit_score
            + 0.05 * hit_score
            + 0.05 * distinct_score
            + 0.20 * source_bonus
            + 0.05 * density_score
            + 0.20 * definition_score
            - penalty
        )
        record.score = max(0.0, min(1.0, score))
        record.reason = _reason_for_record(record)

    records.sort(
        key=lambda record: (
            -record.score,
            -record.input_keyword_hits,
            -len(record.distinct_input_keywords),
            -record.definition_hits,
            -len(record.distinct_keywords),
            -record.weighted_keyword_hits,
            -record.keyword_hits,
            record.relative_path,
        )
    )


def _source_bonus(
    classification: FileClassification, prefer_source_files: bool
) -> float:
    if classification.is_source_file and not classification.is_test_file:
        return 1.0 if prefer_source_files else 0.8
    if classification.is_source_file and classification.is_test_file:
        return 0.65 if prefer_source_files else 0.55
    if classification.category == "doc":
        return 0.25 if prefer_source_files else 0.4
    if classification.category == "config":
        return 0.15
    return 0.1


def _penalty(classification: FileClassification) -> float:
    if classification.is_test_file:
        return 0.08
    if classification.category == "other":
        return 0.05
    return 0.0


def _reason_for_record(record: FileRecord) -> str:
    reasons: list[str] = []
    if record.input_keyword_hits:
        reasons.append("input keyword matches")
    if record.definition_hits:
        reasons.append("definition hits")
    if record.distinct_input_keywords and len(record.distinct_keywords) > len(
        record.distinct_input_keywords
    ):
        reasons.append("expansion-assisted recall")
    if len(record.distinct_keywords) > 1:
        reasons.append("multiple keywords")
    if record.classification.is_source_file and not record.classification.is_test_file:
        reasons.append("source file")
    elif record.classification.category == "doc":
        reasons.append("doc match")
    elif record.classification.category == "config":
        reasons.append("config match")
    if record.keyword_hits >= 5:
        reasons.append("high hit count")
    if record.classification.is_test_file:
        reasons.append("test-file penalty")
    return ", ".join(reasons) if reasons else "keyword match"


def _extract_snippets(
    ranked_records: list[FileRecord],
    lines_before: int,
    lines_after: int,
    max_snippets: int,
    max_total_lines: int,
    max_snippets_per_file: int,
) -> tuple[list[dict[str, Any]], bool]:
    snippets: list[dict[str, Any]] = []
    remaining_lines = max_total_lines
    budget_truncated = False

    for record in ranked_records:
        if len(snippets) >= max_snippets or remaining_lines <= 0:
            budget_truncated = True
            break
        candidate_windows = _build_snippet_windows(
            record.matches,
            line_count=record.line_count,
            lines_before=lines_before,
            lines_after=lines_after,
        )
        if len(candidate_windows) > max_snippets_per_file:
            budget_truncated = True
        selected_windows = candidate_windows[:max_snippets_per_file]
        file_lines = _read_file_lines(record.path)
        for window in selected_windows:
            if len(snippets) >= max_snippets or remaining_lines <= 0:
                budget_truncated = True
                break
            start = window.start
            end = window.end
            window_length = end - start + 1
            if window_length > remaining_lines:
                end = start + remaining_lines - 1
                budget_truncated = True
                window_length = end - start + 1
            snippet_lines = file_lines[start - 1 : end]
            snippets.append(
                {
                    "path": record.relative_path,
                    "line_start": start,
                    "line_end": end,
                    "matched_keywords": sorted(window.matched_keywords),
                    "match_count": window.match_count,
                    "has_definition_hit": window.has_definition_hit,
                    "snippet": "".join(snippet_lines),
                }
            )
            remaining_lines -= window_length

    return snippets, budget_truncated


def _extract_context_blocks(
    records: list[FileRecord],
    max_blocks: int,
    max_blocks_per_file: int,
    max_total_lines: int,
    input_keyword_count: int,
) -> tuple[list[dict[str, Any]], bool]:
    line_budget_remaining = max_total_lines
    budget_truncated = False
    aggregated: list[ContextBlock] = []

    for record in records:
        file_lines = _read_file_lines(record.path)
        blocks_by_key: dict[tuple[int, int], ContextBlock] = {}
        for match in record.matches:
            block_bounds = _find_enclosing_block(
                record.relative_path,
                file_lines,
                match.line_number,
            )
            if block_bounds is None:
                continue
            start, end, block_type, signature = block_bounds
            key = (start, end)
            block = blocks_by_key.setdefault(
                key,
                ContextBlock(
                    source_path=record.path,
                    path=record.relative_path,
                    start=start,
                    end=end,
                    block_type=block_type,
                    signature=signature,
                ),
            )
            block.matched_keywords.update(match.matched_keywords)
            block.distinct_input_keywords.update(
                keyword
                for keyword in match.matched_keywords
                if keyword in record.distinct_input_keywords
            )
            block.input_keyword_hits += match.input_keyword_hits
            block.keyword_hits += match.match_count
            if match.definition_hit:
                block.definition_hits += 1
            block.match_lines.add(match.line_number)

        ranked_blocks = sorted(
            blocks_by_key.values(),
            key=lambda block: (
                -_score_context_block(block, input_keyword_count),
                -(block.end - block.start + 1),
                block.path,
                block.start,
            ),
        )
        if len(ranked_blocks) > max_blocks_per_file:
            budget_truncated = True
        for block in ranked_blocks[:max_blocks_per_file]:
            block.score = _score_context_block(block, input_keyword_count)
            aggregated.append(block)

    aggregated.sort(
        key=lambda block: (
            -block.score,
            block.path,
            block.start,
        )
    )

    rendered: list[dict[str, Any]] = []
    for block in aggregated:
        if len(rendered) >= max_blocks or line_budget_remaining <= 0:
            budget_truncated = True
            break
        window_length = block.end - block.start + 1
        if window_length > line_budget_remaining:
            budget_truncated = True
            continue
        rendered_block = _render_context_block(block)
        if rendered_block is None:
            continue
        rendered.append(rendered_block)
        line_budget_remaining -= window_length

    return rendered, budget_truncated


def _score_context_block(block: ContextBlock, input_keyword_count: int) -> float:
    distinct_input = min(len(block.distinct_input_keywords), input_keyword_count)
    return (
        0.5 * block.input_keyword_hits
        + 0.3 * distinct_input
        + 0.15 * block.definition_hits
        + 0.05 * block.keyword_hits
    )


def _render_context_block(block: ContextBlock) -> dict[str, Any] | None:
    file_lines = _read_file_lines(block.source_path)
    snippet_lines = file_lines[block.start - 1 : block.end]
    if not snippet_lines:
        return None
    return {
        "path": block.path,
        "block_type": block.block_type,
        "signature": block.signature,
        "line_start": block.start,
        "line_end": block.end,
        "matched_keywords": sorted(block.matched_keywords),
        "match_lines": sorted(block.match_lines),
        "score": round(block.score, 3),
        "content": "".join(snippet_lines),
    }


def _find_enclosing_block(
    relative_path: str,
    file_lines: list[str],
    line_number: int,
) -> tuple[int, int, str, str] | None:
    suffix = Path(relative_path).suffix.lower()
    if suffix == ".py":
        return _find_python_block(file_lines, line_number)
    if suffix == ".jl":
        return _find_julia_block(file_lines, line_number)
    if suffix in {".js", ".jsx", ".ts", ".tsx", ".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".java", ".go", ".rs", ".swift", ".scala"}:
        return _find_brace_block(relative_path, file_lines, line_number)
    return _find_region_block(file_lines, line_number)


def _find_python_block(
    file_lines: list[str],
    line_number: int,
) -> tuple[int, int, str, str] | None:
    definition_index: int | None = None
    for index in range(min(line_number - 1, len(file_lines) - 1), -1, -1):
        stripped = file_lines[index].strip()
        if re.match(r"^(async\s+def|def|class)\s+\w+", stripped):
            definition_index = index
            break
    if definition_index is None:
        return _find_region_block(file_lines, line_number)

    start = definition_index
    while start > 0 and file_lines[start - 1].lstrip().startswith("@"):
        start -= 1
    signature_line = file_lines[definition_index].strip()
    indent = len(file_lines[definition_index]) - len(file_lines[definition_index].lstrip(" "))
    end = len(file_lines)
    for index in range(definition_index + 1, len(file_lines)):
        stripped = file_lines[index].strip()
        if not stripped:
            continue
        current_indent = len(file_lines[index]) - len(file_lines[index].lstrip(" "))
        if current_indent <= indent and not file_lines[index].lstrip().startswith("#"):
            end = index
            break
    return start + 1, end, _python_block_type(signature_line), signature_line


def _python_block_type(signature_line: str) -> str:
    if signature_line.startswith("class "):
        return "class"
    return "function"


def _find_brace_block(
    relative_path: str,
    file_lines: list[str],
    line_number: int,
) -> tuple[int, int, str, str] | None:
    suffix = Path(relative_path).suffix.lower()
    patterns = DEFINITION_PATTERNS.get(suffix, GENERIC_DEFINITION_PATTERNS)
    definition_index: int | None = None
    for index in range(min(line_number - 1, len(file_lines) - 1), -1, -1):
        stripped = file_lines[index].strip()
        if any(pattern.search(stripped) for pattern in patterns):
            definition_index = index
            break
    if definition_index is None:
        return _find_region_block(file_lines, line_number)

    signature_line = file_lines[definition_index].strip()
    brace_balance = 0
    seen_open_brace = False
    end = definition_index + 1
    for index in range(definition_index, len(file_lines)):
        line = file_lines[index]
        brace_balance += line.count("{")
        if line.count("{"):
            seen_open_brace = True
        brace_balance -= line.count("}")
        end = index + 1
        if seen_open_brace and brace_balance <= 0:
            break
        if not seen_open_brace and index > definition_index and not line.strip():
            break
    return definition_index + 1, end, "definition", signature_line


def _find_julia_block(
    file_lines: list[str],
    line_number: int,
) -> tuple[int, int, str, str] | None:
    definition_index: int | None = None
    for index in range(min(line_number - 1, len(file_lines) - 1), -1, -1):
        stripped = file_lines[index].strip()
        if re.match(
            r"^(function|struct|mutable\s+struct|abstract\s+type|module)\b",
            stripped,
            re.IGNORECASE,
        ) or re.match(r"^[\w!]+\s*\([^)]*\)\s*=", stripped):
            definition_index = index
            break
    if definition_index is None:
        return _find_region_block(file_lines, line_number)

    signature_line = file_lines[definition_index].strip()
    if "=" in signature_line and not signature_line.lower().startswith(
        ("function", "struct", "mutable struct", "abstract type", "module")
    ):
        return definition_index + 1, definition_index + 1, "function", signature_line

    depth = 0
    end = definition_index + 1
    for index in range(definition_index, len(file_lines)):
        stripped = file_lines[index].strip()
        if re.match(
            r"^(function|struct|mutable\s+struct|abstract\s+type|module)\b",
            stripped,
            re.IGNORECASE,
        ):
            depth += 1
        elif stripped == "end":
            depth -= 1
            if depth <= 0:
                end = index + 1
                break
    return definition_index + 1, end, "definition", signature_line


def _find_region_block(
    file_lines: list[str],
    line_number: int,
) -> tuple[int, int, str, str] | None:
    if not file_lines:
        return None
    index = min(max(line_number - 1, 0), len(file_lines) - 1)
    start = index
    while start > 0 and file_lines[start - 1].strip():
        start -= 1
    end = index + 1
    while end < len(file_lines) and file_lines[end].strip():
        end += 1
    signature = file_lines[start].strip() if file_lines[start].strip() else f"lines {start + 1}-{end}"
    return start + 1, end, "region", signature


def _log_search_summary(
    root: Path,
    keywords: list[str],
    files_considered: int,
    records: list[FileRecord],
    ranked_records: list[FileRecord],
    snippets: list[dict[str, Any]],
    budget_truncated: bool,
) -> None:
    coverage_by_path: dict[str, dict[str, int]] = {}
    for snippet in snippets:
        shown_lines = snippet["line_end"] - snippet["line_start"] + 1
        coverage = coverage_by_path.setdefault(
            snippet["path"],
            {"shown_lines": 0, "snippet_count": 0},
        )
        coverage["shown_lines"] += shown_lines
        coverage["snippet_count"] += 1

    LOGGER.info(
        "repo search root=%s keywords=%s files_considered=%d files_ranked=%d files_returned=%d snippets_returned=%d budget_truncated=%s",
        root,
        ", ".join(keywords),
        files_considered,
        len(records),
        len(ranked_records),
        len(snippets),
        budget_truncated,
    )
    if not ranked_records:
        LOGGER.info("repo search returned no ranked files")
        return

    for index, record in enumerate(ranked_records[:5], start=1):
        coverage = coverage_by_path.get(
            record.relative_path,
            {"shown_lines": 0, "snippet_count": 0},
        )
        shown_lines = coverage["shown_lines"]
        shown_percent = (
            0.0 if record.line_count == 0 else (shown_lines / record.line_count) * 100.0
        )
        LOGGER.info(
            "rank=%d path=%s score=%.3f hits=%d distinct_keywords=%d definition_hits=%d snippets=%d shown_lines=%d/%d (%.1f%%)",
            index,
            record.relative_path,
            record.score,
            record.keyword_hits,
            len(record.distinct_keywords),
            record.definition_hits,
            coverage["snippet_count"],
            shown_lines,
            record.line_count,
            shown_percent,
        )


def _build_snippet_windows(
    matches: list[MatchRecord],
    line_count: int,
    lines_before: int,
    lines_after: int,
) -> list[SnippetWindow]:
    if not matches:
        return []
    max_window_lines = max(1, lines_before + lines_after + 1)
    sorted_matches = sorted(matches, key=lambda match: match.line_number)

    grouped: list[list[MatchRecord]] = []
    current_group: list[MatchRecord] = []
    current_end = 1
    for match in sorted_matches:
        start = max(1, match.line_number - lines_before)
        end = min(line_count, match.line_number + lines_after)
        if not current_group:
            current_group = [match]
            current_end = end
            continue
        if start <= current_end + 1:
            current_group.append(match)
            current_end = max(current_end, end)
            continue
        grouped.append(current_group)
        current_group = [match]
        current_end = end
    if current_group:
        grouped.append(current_group)

    windows: list[SnippetWindow] = []
    for group in grouped:
        anchor = _select_anchor_line(group)
        start = max(1, anchor - lines_before)
        end = min(line_count, anchor + lines_after)
        if end - start + 1 > max_window_lines:
            end = start + max_window_lines - 1
        group_hits = [match for match in group if start <= match.line_number <= end]
        if not group_hits:
            group_hits = [group[0]]
            start = max(1, group[0].line_number - lines_before)
            end = min(line_count, start + max_window_lines - 1)
        matched_keywords = set().union(
            *(match.matched_keywords for match in group_hits)
        )
        match_count = sum(match.match_count for match in group_hits)
        window_length = max(1, end - start + 1)
        windows.append(
            SnippetWindow(
                start=start,
                end=end,
                match_count=match_count,
                matched_keywords=matched_keywords,
                has_definition_hit=any(match.definition_hit for match in group_hits),
                density=match_count / window_length,
            )
        )

    windows.sort(
        key=lambda window: (
            -int(window.has_definition_hit),
            -len(window.matched_keywords),
            -window.match_count,
            -window.density,
            window.start,
        )
    )
    return windows


def _select_anchor_line(group: list[MatchRecord]) -> int:
    definition_hits = [match for match in group if match.definition_hit]
    if definition_hits:
        return definition_hits[0].line_number
    richest_match = max(
        group,
        key=lambda match: (
            len(match.matched_keywords),
            match.match_count,
            -match.line_number,
        ),
    )
    return richest_match.line_number


def _read_file_lines(path: Path) -> list[str]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return handle.readlines()
    except OSError:
        return []
