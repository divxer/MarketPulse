"""Phase 5e lock #22: meta-test verifying the taxonomy enforcement hook fires."""
from __future__ import annotations

import textwrap


def test_phase5e_taxonomy_hook_rejects_untagged_test(pytester) -> None:
    """# Layer: invariant
    The pytest_collection_modifyitems hook in tests/conftest.py raises
    pytest.UsageError when a Phase 5e-named test lacks the # Layer: tag.

    Uses pytester (built-in pytest plugin for testing pytest itself) to run
    a synthetic test file with a deliberately-untagged Phase 5e test, then
    asserts the run failed at collection.
    """
    # Copy the project's conftest.py so the hook is active in the pytester
    # subprocess. The pytester rootdir gets a minimal conftest that re-exports
    # the hook.
    import pathlib
    project_root = pathlib.Path(__file__).parent.parent.parent
    pytester.makefile(
        ".toml",
        pyproject=textwrap.dedent(
            """
            [tool.pytest.ini_options]
            asyncio_default_fixture_loop_scope = "function"
            """
        ),
    )
    pytester.makepyfile(
        conftest=textwrap.dedent(
            f"""
            import sys
            sys.path.insert(0, {str(project_root)!r})
            from tests.conftest import pytest_collection_modifyitems  # noqa: F401
            """
        ),
        test_untagged=textwrap.dedent(
            """
            def test_phase5e_deliberately_untagged():
                # No docstring at all — should be flagged by the hook
                assert True
            """
        ),
    )
    result = pytester.runpytest("-v")
    # The hook raises UsageError, which manifests as a collection error
    assert result.ret != 0, "Expected collection failure on untagged Phase 5e test"
    # pytest.UsageError prints to stderr (not stdout).
    result.stderr.fnmatch_lines(
        ["*Phase 5e+ tests missing required '# Layer:*"]
    )
