from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_MAX_FILE_SIZE_BYTES = 1_000_000
DEFAULT_MAX_SNIPPETS_PER_FILE = 3
LOGGER = logging.getLogger(__name__)
GUIDANCE = (
    "Prefer source files over docs when scores are similar. Prefer files with "
    "multiple distinct keywords and clustered hits. Use the snippets to decide "
    "which files deserve deeper reading. If the returned context is insufficient, "
    "fall back to normal repository context search."
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
    definition_hit: bool


@dataclass(slots=True)
class FileRecord:
    path: Path
    relative_path: str
    classification: FileClassification
    line_count: int = 0
    keyword_hits: int = 0
    distinct_keywords: set[str] = field(default_factory=set)
    definition_hits: int = 0
    matches: list[MatchRecord] = field(default_factory=list)
    score: float = 0.0
    reason: str = ""


@dataclass(slots=True)
class ExcludedFileRecord:
    path: Path
    relative_path: str
    reason: str


@dataclass(slots=True)
class SnippetWindow:
    start: int
    end: int
    match_count: int
    matched_keywords: set[str]
    has_definition_hit: bool
    density: float


def search_repo_context_result(
    keywords: list[str],
    directory: str | None = None,
    max_files: int = 12,
    max_snippets: int = 24,
    lines_before: int = 8,
    lines_after: int = 12,
    prefer_source_files: bool = True,
    max_total_lines: int = 400,
    max_snippets_per_file: int = DEFAULT_MAX_SNIPPETS_PER_FILE,
    max_file_size_bytes: int = DEFAULT_MAX_FILE_SIZE_BYTES,
) -> dict[str, Any]:
    normalized_keywords = _normalize_keywords(keywords)
    if not normalized_keywords:
        raise ValueError("keywords must contain at least one non-empty keyword")
    if max_files < 1 or max_snippets < 1 or max_total_lines < 1:
        raise ValueError("max_files, max_snippets, and max_total_lines must be >= 1")
    if lines_before < 0 or lines_after < 0:
        raise ValueError("lines_before and lines_after must be >= 0")

    root = Path(directory).expanduser().resolve() if directory else Path.cwd().resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError(f"directory does not exist or is not a directory: {root}")

    file_paths, excluded_paths = _discover_candidate_files(
        root, max_file_size_bytes=max_file_size_bytes
    )
    records = _collect_matches(root, file_paths, normalized_keywords)
    excluded_keyword_matches = _collect_excluded_keyword_matches(
        root,
        excluded_paths,
        normalized_keywords,
    )
    _rank_records(
        records,
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
        omitted_ranked_records=omitted_ranked_records,
        snippets=snippets,
        excluded_keyword_matches=excluded_keyword_matches,
        budget_truncated=budget_truncated,
    )

    return {
        "searched_directory": str(root),
        "summary": {
            "files_considered": len(file_paths),
            "files_ranked": len(records),
            "files_returned": len(ranked_records),
            "omitted_ranked_files": len(omitted_ranked_records),
            "snippets_returned": len(snippets),
            "excluded_keyword_matches": len(excluded_keyword_matches),
            "budget_truncated": budget_truncated,
        },
        "ranked_files": [
            {
                "path": record.relative_path,
                "is_source_file": record.classification.is_source_file,
                "keyword_hits": record.keyword_hits,
                "distinct_keywords_matched": len(record.distinct_keywords),
                "score": round(record.score, 3),
                "recommended_read": index < min(5, max_files)
                or record.definition_hits > 0,
                "reason": record.reason,
            }
            for index, record in enumerate(ranked_records)
        ],
        "omitted_ranked_files": [
            {
                "path": record.relative_path,
                "is_source_file": record.classification.is_source_file,
                "keyword_hits": record.keyword_hits,
                "distinct_keywords_matched": len(record.distinct_keywords),
                "score": round(record.score, 3),
                "reason": record.reason,
            }
            for record in omitted_ranked_records
        ],
        "snippets": snippets,
        "excluded_keyword_matches": excluded_keyword_matches,
        "usage_guidance": GUIDANCE,
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


def _discover_candidate_files(
    root: Path,
    max_file_size_bytes: int,
) -> tuple[list[Path], list[ExcludedFileRecord]]:
    rg_path = shutil.which("rg")
    raw_paths = _discover_with_ripgrep(root, rg_path) if rg_path else None
    if raw_paths is None:
        raw_paths = _discover_with_walk(root)

    candidates: list[Path] = []
    excluded: list[ExcludedFileRecord] = []
    for relative_path in raw_paths:
        path = root / relative_path
        if not path.is_file():
            continue
        exclusion_reason = _excluded_reason_for_path(
            relative_path,
            path,
            max_file_size_bytes=max_file_size_bytes,
        )
        if exclusion_reason is not None:
            excluded.append(
                ExcludedFileRecord(
                    path=path,
                    relative_path=relative_path.as_posix(),
                    reason=exclusion_reason,
                )
            )
            continue
        candidates.append(path)
    candidates.sort()
    excluded.sort(key=lambda record: record.relative_path)
    return candidates, excluded


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
    root: Path, file_paths: list[Path], keywords: list[str]
) -> list[FileRecord]:
    eligible = {path.relative_to(root).as_posix(): path for path in file_paths}
    rg_path = shutil.which("rg")
    if rg_path is not None:
        records = _collect_matches_with_ripgrep(root, keywords, eligible, rg_path)
        if records is not None:
            return records
    return _collect_matches_with_python(keywords, eligible)


def _collect_excluded_keyword_matches(
    root: Path,
    excluded_paths: list[ExcludedFileRecord],
    keywords: list[str],
) -> list[dict[str, Any]]:
    if not excluded_paths:
        return []

    excluded_by_path = {record.relative_path: record for record in excluded_paths}
    eligible = {record.relative_path: record.path for record in excluded_paths}
    rg_path = shutil.which("rg")
    if rg_path is not None:
        matched_keywords_by_path = _collect_keyword_presence_with_ripgrep(
            root,
            keywords,
            eligible,
            rg_path,
            treat_binary_as_text=True,
        )
        if matched_keywords_by_path is not None:
            return _format_excluded_keyword_matches(
                excluded_by_path, matched_keywords_by_path
            )

    matched_keywords_by_path = _collect_keyword_presence_with_python(eligible, keywords)
    return _format_excluded_keyword_matches(excluded_by_path, matched_keywords_by_path)


def _collect_matches_with_ripgrep(
    root: Path,
    keywords: list[str],
    eligible: dict[str, Path],
    rg_path: str,
) -> list[FileRecord] | None:
    compiled = _compile_keyword_patterns(keywords)
    records_by_path: dict[str, FileRecord] = {}
    eligible_paths = sorted(eligible)
    for index in range(0, len(eligible_paths), 200):
        batch = eligible_paths[index : index + 200]
        command = [rg_path, "--json", "-n", "-i", "--color", "never"]
        for keyword in keywords:
            command.extend(["-e", re.escape(keyword)])
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
            matched_keywords, match_count = _line_match_details(line_text, compiled)
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
            record.distinct_keywords.update(matched_keywords)
            record.matches.append(
                MatchRecord(
                    line_number=int(data["line_number"]),
                    matched_keywords=matched_keywords,
                    match_count=match_count,
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
    eligible: dict[str, Path],
) -> list[FileRecord]:
    compiled = _compile_keyword_patterns(keywords)
    records: list[FileRecord] = []
    for relative_path, path in eligible.items():
        matches: list[MatchRecord] = []
        keyword_hits = 0
        distinct_keywords: set[str] = set()
        definition_hits = 0
        line_count = 0
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for line_count, raw_line in enumerate(handle, start=1):
                    line_text = raw_line.rstrip("\n")
                    matched_keywords, match_count = _line_match_details(
                        line_text, compiled
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
                            definition_hit=definition_hit,
                        )
                    )
                    keyword_hits += match_count
                    distinct_keywords.update(matched_keywords)
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
                distinct_keywords=distinct_keywords,
                definition_hits=definition_hits,
                matches=matches,
            )
        )
    return records


def _collect_keyword_presence_with_ripgrep(
    root: Path,
    keywords: list[str],
    eligible: dict[str, Path],
    rg_path: str,
    *,
    treat_binary_as_text: bool,
) -> dict[str, set[str]] | None:
    matched_keywords_by_path: dict[str, set[str]] = {}
    eligible_paths = sorted(eligible)
    for keyword in keywords:
        for index in range(0, len(eligible_paths), 200):
            batch = eligible_paths[index : index + 200]
            command = [rg_path, "-l", "-i", "-F", "--color", "never"]
            if treat_binary_as_text:
                command.append("--text")
            command.extend(["-e", keyword])
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
                relative_path = Path(raw_line).as_posix()
                if relative_path in eligible:
                    matched_keywords_by_path.setdefault(relative_path, set()).add(
                        keyword
                    )
    return matched_keywords_by_path


def _collect_keyword_presence_with_python(
    eligible: dict[str, Path],
    keywords: list[str],
) -> dict[str, set[str]]:
    encoded_keywords = {
        keyword: keyword.lower().encode("utf-8", errors="ignore")
        for keyword in keywords
        if keyword
    }
    matched_keywords_by_path: dict[str, set[str]] = {}
    for relative_path, path in eligible.items():
        matched_keywords = _file_keyword_presence(path, encoded_keywords)
        if matched_keywords:
            matched_keywords_by_path[relative_path] = matched_keywords
    return matched_keywords_by_path


def _file_keyword_presence(
    path: Path,
    encoded_keywords: dict[str, bytes],
    chunk_size: int = 64 * 1024,
) -> set[str]:
    if not encoded_keywords:
        return set()

    remaining = {
        keyword: keyword_bytes
        for keyword, keyword_bytes in encoded_keywords.items()
        if keyword_bytes
    }
    if not remaining:
        return set()

    max_keyword_len = max(len(keyword_bytes) for keyword_bytes in remaining.values())
    matched: set[str] = set()
    carry = b""
    try:
        with path.open("rb") as handle:
            while remaining:
                chunk = handle.read(chunk_size)
                if not chunk:
                    break
                haystack = (carry + chunk).lower()
                for keyword, keyword_bytes in list(remaining.items()):
                    if keyword_bytes in haystack:
                        matched.add(keyword)
                        del remaining[keyword]
                if max_keyword_len > 1:
                    carry = haystack[-(max_keyword_len - 1) :]
                else:
                    carry = b""
    except OSError:
        return set()
    return matched


def _format_excluded_keyword_matches(
    excluded_by_path: dict[str, ExcludedFileRecord],
    matched_keywords_by_path: dict[str, set[str]],
) -> list[dict[str, Any]]:
    def sort_key(item: tuple[str, set[str]]) -> tuple[int, str]:
        relative_path, matched_keywords = item
        return (-len(matched_keywords), relative_path)

    return [
        {
            "path": relative_path,
            "reason": excluded_by_path[relative_path].reason,
            "matched_keywords": sorted(matched_keywords),
            "distinct_keywords_matched": len(matched_keywords),
        }
        for relative_path, matched_keywords in sorted(
            matched_keywords_by_path.items(),
            key=sort_key,
        )
    ]


def _compile_keyword_patterns(keywords: list[str]) -> dict[str, re.Pattern[str]]:
    return {
        keyword: re.compile(re.escape(keyword), re.IGNORECASE) for keyword in keywords
    }


def _line_match_details(
    line_text: str,
    compiled_keywords: dict[str, re.Pattern[str]],
) -> tuple[set[str], int]:
    matched_keywords: set[str] = set()
    match_count = 0
    for keyword, pattern in compiled_keywords.items():
        occurrences = len(pattern.findall(line_text))
        if occurrences:
            matched_keywords.add(keyword)
            match_count += occurrences
    return matched_keywords, match_count


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
    keyword_count: int,
    prefer_source_files: bool,
) -> None:
    if not records:
        return
    max_hits = max(record.keyword_hits for record in records)
    max_density = max(
        record.keyword_hits / max(record.line_count, 1) for record in records
    )
    max_definition_hits = max(record.definition_hits for record in records)
    for record in records:
        hit_score = record.keyword_hits / max_hits if max_hits else 0.0
        distinct_score = (
            len(record.distinct_keywords) / keyword_count if keyword_count else 0.0
        )
        density = record.keyword_hits / max(record.line_count, 1)
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
            0.30 * hit_score
            + 0.20 * distinct_score
            + 0.20 * source_bonus
            + 0.10 * density_score
            + 0.20 * definition_score
            - penalty
        )
        record.score = max(0.0, min(1.0, score))
        record.reason = _reason_for_record(record)

    records.sort(
        key=lambda record: (
            -record.score,
            -record.definition_hits,
            -len(record.distinct_keywords),
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
    if record.definition_hits:
        reasons.append("definition hits")
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
                    "snippet": "".join(snippet_lines),
                }
            )
            remaining_lines -= window_length

    return snippets, budget_truncated


def _log_search_summary(
    root: Path,
    keywords: list[str],
    files_considered: int,
    records: list[FileRecord],
    ranked_records: list[FileRecord],
    omitted_ranked_records: list[FileRecord],
    snippets: list[dict[str, Any]],
    excluded_keyword_matches: list[dict[str, Any]],
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
        "repo search root=%s keywords=%s files_considered=%d files_ranked=%d files_returned=%d omitted_ranked_files=%d snippets_returned=%d excluded_keyword_matches=%d budget_truncated=%s",
        root,
        ", ".join(keywords),
        files_considered,
        len(records),
        len(ranked_records),
        len(omitted_ranked_records),
        len(snippets),
        len(excluded_keyword_matches),
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
    for excluded in excluded_keyword_matches[:5]:
        LOGGER.info(
            "excluded path=%s reason=%s matched_keywords=%s",
            excluded["path"],
            excluded["reason"],
            ", ".join(excluded["matched_keywords"]),
        )
    for omitted in omitted_ranked_records[:5]:
        LOGGER.info(
            "omitted path=%s score=%.3f hits=%d distinct_keywords=%d reason=%s",
            omitted.relative_path,
            omitted.score,
            omitted.keyword_hits,
            len(omitted.distinct_keywords),
            omitted.reason,
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
