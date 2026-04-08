from __future__ import annotations

import asyncio
import os
from pathlib import Path
import tempfile
import unittest

from quick_search import search_repo_context
from repo_context_search import search_repo_context_result


class SearchRepoContextTests(unittest.TestCase):
    class _FakeRootsResult:
        def __init__(self, roots: list[object]) -> None:
            self.roots = roots

    class _FakeRoot:
        def __init__(self, uri: str) -> None:
            self.uri = uri

    class _FakeSession:
        def __init__(self, roots: list[object]) -> None:
            self._roots = roots

        async def list_roots(self) -> object:
            return SearchRepoContextTests._FakeRootsResult(self._roots)

    class _FakeRequestContext:
        def __init__(self, session: object) -> None:
            self.session = session

    class _FakeContext:
        def __init__(self, roots: list[object]) -> None:
            self.request_context = SearchRepoContextTests._FakeRequestContext(
                SearchRepoContextTests._FakeSession(roots)
            )

    def test_prefers_source_and_definition_hits_over_docs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "src").mkdir()
            (root / "docs").mkdir()
            (root / "tests").mkdir()
            (root / "src" / "snow_model.py").write_text(
                "\n".join(
                    [
                        "def snow_melt_balance(snowpack, melt_factor):",
                        "    return snowpack * melt_factor",
                        "",
                        "snow_state = snow_melt_balance(2.0, 0.5)",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (root / "docs" / "snow-notes.md").write_text(
                "\n".join(
                    [
                        "snow melt notes",
                        "snow melt overview",
                        "snow melt commentary",
                        "snow melt checklist",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (root / "tests" / "test_snow_model.py").write_text(
                "def test_snow_melt_balance():\n    assert True\n",
                encoding="utf-8",
            )

            result = search_repo_context_result(
                keywords=["snow", "melt"],
                directory=str(root),
            )

            self.assertEqual(result["ranked_files"][0]["path"], "src/snow_model.py")
            self.assertIn("definition hits", result["ranked_files"][0]["reason"])
            returned_paths = [item["path"] for item in result["ranked_files"]]
            self.assertIn("docs/snow-notes.md", returned_paths)
            self.assertIn("tests/test_snow_model.py", returned_paths)

    def test_exposes_richer_ranked_file_and_snippet_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "src").mkdir()
            (root / "src" / "snow_model.py").write_text(
                "\n".join(
                    [
                        "def snow_melt_balance(snowpack, melt_factor):",
                        "    return snowpack * melt_factor",
                        "snow_value = snow_melt_balance(2.0, 0.5)",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            result = search_repo_context_result(
                keywords=["snow", "melt"],
                directory=str(root),
                lines_before=0,
                lines_after=0,
                output_mode="full",
            )

            ranked = result["ranked_files"][0]
            self.assertEqual(ranked["category"], "source")
            self.assertFalse(ranked["is_test_file"])
            self.assertEqual(ranked["line_count"], 3)
            self.assertEqual(ranked["definition_hits"], 1)
            self.assertAlmostEqual(ranked["keyword_density"], 3.0, places=6)

            snippet = result["snippets"][0]
            self.assertEqual(snippet["match_count"], 4)
            self.assertTrue(snippet["has_definition_hit"])

    def test_word_match_mode_rejects_partial_token_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "src").mkdir()
            (root / "src" / "model.py").write_text(
                "\n".join(
                    [
                        "snowpack = 1",
                        "snow = 2",
                        "snow_model = 3",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            result = search_repo_context_result(
                keywords=["snow"],
                directory=str(root),
                match_mode="word",
                lines_before=0,
                lines_after=0,
            )

            self.assertEqual(result["ranked_files"][0]["keyword_hits"], 1)
            self.assertEqual(len(result["snippets"]), 1)
            self.assertIn("snow = 2", result["snippets"][0]["snippet"])

    def test_identifier_match_mode_matches_snake_and_camel_case(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "src").mkdir()
            (root / "src" / "model.py").write_text(
                "\n".join(
                    [
                        "snowModel = 1",
                        "snow_model = 2",
                        "snowfall = 3",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            result = search_repo_context_result(
                keywords=["snow"],
                directory=str(root),
                match_mode="identifier",
                lines_before=0,
                lines_after=0,
            )

            self.assertEqual(result["ranked_files"][0]["keyword_hits"], 2)
            self.assertEqual(result["snippets"][0]["matched_keywords"], ["snow"])
            self.assertNotIn("snowfall = 3", result["snippets"][0]["snippet"])

    def test_rejects_invalid_match_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            with self.assertRaisesRegex(
                ValueError,
                "match_mode must be one of",
            ):
                search_repo_context_result(
                    keywords=["snow"],
                    directory=str(root),
                    match_mode="bad-mode",
                )

    def test_prompt_only_query_derives_keywords_and_finds_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "src").mkdir()
            (root / "src" / "snow_model.py").write_text(
                "def snow_melt_balance():\n    return 1\n",
                encoding="utf-8",
            )

            result = search_repo_context_result(
                prompt="find the snow melt balance implementation",
                directory=str(root),
            )

            self.assertTrue(result["query"]["prompt_used"])
            self.assertIn("snow", result["query"]["resolved_keywords"])
            self.assertIn("melt", result["query"]["resolved_keywords"])
            self.assertEqual(result["ranked_files"][0]["path"], "src/snow_model.py")

    def test_prompt_terms_are_added_after_explicit_keywords(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "src").mkdir()
            (root / "src" / "snow_model.py").write_text(
                "def snow_melt_balance():\n    return 1\n",
                encoding="utf-8",
            )

            result = search_repo_context_result(
                keywords=["balance"],
                prompt="find the snow melt balance implementation",
                directory=str(root),
                output_mode="full",
            )

            self.assertEqual(result["query"]["explicit_keywords"], ["balance"])
            self.assertEqual(result["query"]["resolved_keywords"][0], "balance")
            self.assertIn("snow", result["query"]["resolved_keywords"])
            self.assertIn("melt", result["query"]["resolved_keywords"])

    def test_prompt_derived_keywords_are_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = search_repo_context_result(
                prompt=(
                    "please find the snow melt energy balance temperature runoff albedo "
                    "density grain metamorphism compaction conductivity implementation"
                ),
                directory=tmpdir,
                output_mode="full",
            )

            self.assertLessEqual(len(result["query"]["derived_keywords"]), 8)
            self.assertLessEqual(len(result["query"]["resolved_keywords"]), 8)

    def test_prompt_identifier_terms_are_tokenized(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "src").mkdir()
            (root / "src" / "model.py").write_text(
                "snowModel = 1\n",
                encoding="utf-8",
            )

            result = search_repo_context_result(
                prompt="inspect `snowModel` handling",
                directory=str(root),
                match_mode="identifier",
            )

            self.assertIn("snow", result["query"]["resolved_keywords"])
            self.assertIn("model", result["query"]["resolved_keywords"])
            self.assertEqual(result["ranked_files"][0]["path"], "src/model.py")

    def test_requires_keywords_or_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            with self.assertRaisesRegex(
                ValueError,
                "provide at least one non-empty keyword or a prompt",
            ):
                search_repo_context_result(directory=str(root))

    def test_compact_output_omits_full_metadata_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "src").mkdir()
            (root / "src" / "snow_model.py").write_text(
                "def snow_component():\n    return 1\n",
                encoding="utf-8",
            )

            result = search_repo_context_result(
                keywords=["snow"],
                directory=str(root),
            )

            ranked = result["ranked_files"][0]
            self.assertNotIn("is_source_file", ranked)
            self.assertNotIn("is_test_file", ranked)
            self.assertNotIn("line_count", ranked)
            self.assertNotIn("keyword_density", ranked)
            snippet = result["snippets"][0]
            self.assertNotIn("match_count", snippet)
            self.assertNotIn("has_definition_hit", snippet)
            self.assertEqual(sorted(result["query"].keys()), ["prompt_used", "resolved_keywords"])
            self.assertIn("query_id", result)

    def test_includes_diagnostics_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "src").mkdir()
            (root / "tests").mkdir()
            (root / "src" / "snow_model.py").write_text(
                "def snow_component():\n    return 1\n",
                encoding="utf-8",
            )
            (root / "tests" / "test_snow.py").write_text(
                "def test_snow_component():\n    assert True\n",
                encoding="utf-8",
            )

            result = search_repo_context_result(
                keywords=["snow"],
                directory=str(root),
                paths_exclude_glob="tests/*",
                include_diagnostics=True,
            )

            self.assertIn("diagnostics", result)
            self.assertIn(result["diagnostics"]["discovery_backend"], {"ripgrep", "walk"})
            self.assertIn(result["diagnostics"]["matching_backend"], {"ripgrep", "python"})
            self.assertEqual(result["diagnostics"]["excluded_file_counts"]["exclude_glob"], 1)

    def test_can_expand_cached_compact_result_by_query_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "src").mkdir()
            (root / "src" / "snow_model.py").write_text(
                "def snow_melt_balance():\n    return 1\n",
                encoding="utf-8",
            )

            compact = search_repo_context_result(
                keywords=["snow", "melt"],
                directory=str(root),
                output_mode="compact",
            )
            expanded = search_repo_context_result(
                query_id=compact["query_id"],
                output_mode="full",
                include_diagnostics=True,
            )

            self.assertEqual(expanded["query_id"], compact["query_id"])
            self.assertIn("is_source_file", expanded["ranked_files"][0])
            self.assertIn("match_count", expanded["snippets"][0])
            self.assertIn("diagnostics", expanded)

    def test_rejects_unknown_query_id(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown query_id"):
            search_repo_context_result(query_id="does-not-exist")

    def test_merges_overlapping_windows(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "src").mkdir()
            (root / "src" / "model.py").write_text(
                "\n".join(
                    [
                        "line 1",
                        "snow starts here",
                        "middle context",
                        "melt also here",
                        "line 5",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            result = search_repo_context_result(
                keywords=["snow", "melt"],
                directory=str(root),
                lines_before=1,
                lines_after=1,
            )

            model_snippets = [
                snippet for snippet in result["snippets"] if snippet["path"] == "src/model.py"
            ]
            self.assertEqual(len(model_snippets), 1)
            self.assertEqual(model_snippets[0]["line_start"], 1)
            self.assertEqual(model_snippets[0]["line_end"], 3)

    def test_enforces_total_line_budget_and_marks_truncation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "src").mkdir()
            (root / "src" / "model.py").write_text(
                "\n".join(
                    [
                        "def snow_handler():",
                        "    pass",
                        "line 3",
                        "line 4",
                        "line 5",
                        "melt appears later",
                        "line 7",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            result = search_repo_context_result(
                keywords=["snow", "melt"],
                directory=str(root),
                lines_before=1,
                lines_after=2,
                max_total_lines=3,
            )

            self.assertTrue(result["summary"]["budget_truncated"])
            total_lines = sum(
                snippet["line_end"] - snippet["line_start"] + 1 for snippet in result["snippets"]
            )
            self.assertLessEqual(total_lines, 3)

    def test_excludes_generated_binary_and_large_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "src").mkdir()
            (root / "generated").mkdir()
            (root / "src" / "model.py").write_text(
                "def snow_component():\n    return 'melt'\n",
                encoding="utf-8",
            )
            (root / "generated" / "autogen.py").write_text(
                "def snow_generated():\n    return 'melt'\n",
                encoding="utf-8",
            )
            (root / "data.bin").write_bytes(b"\x00\x01snow")
            (root / "large.txt").write_text("snow\n" * 600_000, encoding="utf-8")

            result = search_repo_context_result(
                keywords=["snow", "melt"],
                directory=str(root),
                output_mode="full",
            )

            ranked_paths = [item["path"] for item in result["ranked_files"]]
            self.assertEqual(ranked_paths, ["src/model.py"])
            self.assertEqual(result["summary"]["files_considered"], 1)

    def test_returns_guidance_when_no_matches_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "src").mkdir()
            (root / "src" / "model.py").write_text("def runoff():\n    return 1\n", encoding="utf-8")

            result = search_repo_context_result(
                keywords=["snow"],
                directory=str(root),
            )

            self.assertEqual(result["ranked_files"], [])
            self.assertEqual(result["snippets"], [])
            self.assertIn(
                "Only do further searches across the repository if this context is insufficient.",
                result["usage_guidance"],
            )

    def test_returns_docs_when_only_docs_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "docs").mkdir()
            (root / "docs" / "snow.md").write_text(
                "snow guidance\nmelt guidance\n",
                encoding="utf-8",
            )
            (root / "src").mkdir()
            (root / "src" / "model.py").write_text("def runoff():\n    return 1\n", encoding="utf-8")

            result = search_repo_context_result(
                keywords=["snow", "melt"],
                directory=str(root),
            )

            self.assertEqual(result["ranked_files"][0]["path"], "docs/snow.md")
            self.assertEqual(result["ranked_files"][0]["category"], "doc")

    def test_uses_cwd_when_directory_is_omitted(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "model.py").write_text("def snow_component():\n    return 1\n", encoding="utf-8")
            previous_cwd = Path.cwd()
            os.chdir(root)
            try:
                result = search_repo_context_result(keywords=["snow"])
            finally:
                os.chdir(previous_cwd)

            self.assertEqual(result["searched_directory"], str(root.resolve()))
            self.assertEqual(result["ranked_files"][0]["path"], "model.py")

    def test_accepts_dot_directory_argument(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "model.py").write_text("def snow_component():\n    return 1\n", encoding="utf-8")
            previous_cwd = Path.cwd()
            os.chdir(root)
            try:
                result = search_repo_context_result(keywords=["snow"], directory=".")
            finally:
                os.chdir(previous_cwd)

            self.assertEqual(result["searched_directory"], str(root.resolve()))
            self.assertEqual(result["ranked_files"][0]["path"], "model.py")

    def test_tool_resolves_dot_directory_against_client_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "model.py").write_text(
                "def snow_component():\n    return 1\n",
                encoding="utf-8",
            )
            ctx = self._FakeContext([self._FakeRoot(root.resolve().as_uri())])
            previous_cwd = Path.cwd()
            os.chdir(Path("/tmp"))
            try:
                result = asyncio.run(
                    search_repo_context(
                        keywords=["snow"],
                        directory=".",
                        ctx=ctx,
                    )
                )
            finally:
                os.chdir(previous_cwd)

            self.assertEqual(result["searched_directory"], str(root.resolve()))
            self.assertEqual(result["ranked_files"][0]["path"], "model.py")

    def test_tool_rejects_relative_directory_without_client_roots(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "relative directory paths such as '.' are not reliable across MCP clients",
        ):
            asyncio.run(search_repo_context(keywords=["snow"], directory="."))

    def test_restricts_search_to_subpath_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "src").mkdir()
            (root / "docs").mkdir()
            (root / "src" / "snow_model.py").write_text(
                "def snow_component():\n    return 1\n",
                encoding="utf-8",
            )
            (root / "docs" / "snow.md").write_text(
                "snow guidance\n",
                encoding="utf-8",
            )

            result = search_repo_context_result(
                keywords=["snow"],
                directory=str(root),
                subpath="src",
            )

            self.assertEqual(result["summary"]["files_considered"], 1)
            self.assertEqual(result["ranked_files"][0]["path"], "src/snow_model.py")

    def test_restricts_search_to_subpath_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "src").mkdir()
            (root / "src" / "snow_model.py").write_text(
                "def snow_component():\n    return 1\n",
                encoding="utf-8",
            )
            (root / "src" / "melt_model.py").write_text(
                "def melt_component():\n    return 1\n",
                encoding="utf-8",
            )

            result = search_repo_context_result(
                keywords=["snow", "melt"],
                directory=str(root),
                subpath="src/snow_model.py",
            )

            self.assertEqual(result["summary"]["files_considered"], 1)
            self.assertEqual(
                [item["path"] for item in result["ranked_files"]],
                ["src/snow_model.py"],
            )

    def test_filters_candidates_with_include_glob(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "src").mkdir()
            (root / "docs").mkdir()
            (root / "src" / "snow_model.py").write_text(
                "def snow_component():\n    return 1\n",
                encoding="utf-8",
            )
            (root / "docs" / "snow.md").write_text(
                "snow guidance\n",
                encoding="utf-8",
            )

            result = search_repo_context_result(
                keywords=["snow"],
                directory=str(root),
                paths_include_glob="src/*.py",
            )

            self.assertEqual(result["summary"]["files_considered"], 1)
            self.assertEqual(result["ranked_files"][0]["path"], "src/snow_model.py")

    def test_filters_candidates_with_exclude_glob(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "src").mkdir()
            (root / "tests").mkdir()
            (root / "src" / "snow_model.py").write_text(
                "def snow_component():\n    return 1\n",
                encoding="utf-8",
            )
            (root / "tests" / "test_snow.py").write_text(
                "def test_snow_component():\n    assert True\n",
                encoding="utf-8",
            )

            result = search_repo_context_result(
                keywords=["snow"],
                directory=str(root),
                paths_exclude_glob="tests/*",
            )

            self.assertEqual(result["summary"]["files_considered"], 1)
            self.assertEqual(result["ranked_files"][0]["path"], "src/snow_model.py")

    def test_combines_subpath_and_glob_filters(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "src").mkdir()
            (root / "src" / "nested").mkdir()
            (root / "src" / "nested" / "snow_model.py").write_text(
                "def snow_component():\n    return 1\n",
                encoding="utf-8",
            )
            (root / "src" / "nested" / "snow_notes.md").write_text(
                "snow notes\n",
                encoding="utf-8",
            )
            (root / "src" / "other.py").write_text(
                "def snow_other():\n    return 1\n",
                encoding="utf-8",
            )

            result = search_repo_context_result(
                keywords=["snow"],
                directory=str(root),
                subpath="src/nested",
                paths_include_glob="src/nested/*.py",
                paths_exclude_glob="*.md",
            )

            self.assertEqual(result["summary"]["files_considered"], 1)
            self.assertEqual(
                [item["path"] for item in result["ranked_files"]],
                ["src/nested/snow_model.py"],
            )

    def test_rejects_invalid_subpath(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            with self.assertRaisesRegex(
                ValueError,
                "subpath does not exist within directory",
            ):
                search_repo_context_result(
                    keywords=["snow"],
                    directory=str(root),
                    subpath="missing",
                )

    def test_tool_reads_budget_defaults_from_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "src").mkdir()
            for index in range(3):
                (root / "src" / f"model_{index}.py").write_text(
                    f"def snow_component_{index}():\n    return 'snow melt'\n",
                    encoding="utf-8",
                )
            previous_env = {
                "QUICK_SEARCH_MAX_FILES": os.environ.get("QUICK_SEARCH_MAX_FILES"),
                "QUICK_SEARCH_MAX_SNIPPETS": os.environ.get("QUICK_SEARCH_MAX_SNIPPETS"),
                "QUICK_SEARCH_MAX_TOTAL_LINES": os.environ.get("QUICK_SEARCH_MAX_TOTAL_LINES"),
            }
            os.environ["QUICK_SEARCH_MAX_FILES"] = "2"
            os.environ["QUICK_SEARCH_MAX_SNIPPETS"] = "1"
            os.environ["QUICK_SEARCH_MAX_TOTAL_LINES"] = "1"
            try:
                result = asyncio.run(
                    search_repo_context(
                        keywords=["snow", "melt"],
                        directory=str(root),
                    )
                )
            finally:
                for key, value in previous_env.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

            self.assertEqual(len(result["ranked_files"]), 2)
            self.assertEqual(len(result["snippets"]), 1)
            total_lines = sum(
                snippet["line_end"] - snippet["line_start"] + 1 for snippet in result["snippets"]
            )
            self.assertLessEqual(total_lines, 1)

    def test_logs_ranked_files_and_shown_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "src").mkdir()
            (root / "docs").mkdir()
            (root / "src" / "model.py").write_text(
                "\n".join(
                    [
                        "def snow_component():",
                        "    return 'snow'",
                        "melt = snow_component()",
                        "print(melt)",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (root / "docs" / "snow.md").write_text(
                "snow overview\nmelt overview\n",
                encoding="utf-8",
            )

            with self.assertLogs("repo_context_search", level="INFO") as captured:
                search_repo_context_result(
                    keywords=["snow", "melt"],
                    directory=str(root),
                    lines_before=0,
                    lines_after=0,
                )

            joined_logs = "\n".join(captured.output)
            self.assertIn("repo search root=", joined_logs)
            self.assertIn("rank=1 path=src/model.py", joined_logs)
            self.assertIn("shown_lines=1/4 (25.0%)", joined_logs)


if __name__ == "__main__":
    unittest.main()
