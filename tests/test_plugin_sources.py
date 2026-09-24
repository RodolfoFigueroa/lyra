from pathlib import Path

import pytest
from lyra.sdk.config import PluginRepoConfig, PluginsConfig
from lyra.sdk.plugin_sources import parse_plugin_source
from pydantic import ValidationError


@pytest.mark.parametrize(
    "source",
    [
        "owner/repo",
        "owner/repo.git",
        "https://github.com/owner/repo",
        "https://github.com/owner/repo.git",
    ],
)
def test_github_sources_have_one_canonical_form(source: str) -> None:
    repo = PluginRepoConfig(id="example", source=source, ref="release/v1")
    parsed = repo.parsed_source
    assert repo.source == "owner/repo"
    assert repo.source_kind == "github"
    assert parsed.path is None
    assert parsed.git_url == "https://github.com/owner/repo.git"
    assert repo.ref == "release/v1"
    assert parse_plugin_source(parsed.canonical) == parsed


@pytest.mark.parametrize("scheme", ["dir", "file"])
@pytest.mark.parametrize("host", ["", "localhost"])
def test_local_sources_normalize_encoding_and_allow_at_in_names(
    scheme: str, host: str
) -> None:
    parsed = parse_plugin_source(f"{scheme}://{host}/plugins/plugin @demo%23%3F%25")
    assert parsed.path == Path("/plugins/plugin @demo#?%")
    assert parsed.canonical == f"{scheme}:///plugins/plugin%20%40demo%23%3F%25"
    assert parse_plugin_source(parsed.canonical) == parsed
    repo = PluginRepoConfig(id="local", source=parsed.canonical)
    assert repo.parsed_source == parsed
    assert repo.source_kind == ("directory" if scheme == "dir" else "local")
    if scheme == "file":
        assert parsed.git_url == parsed.canonical
    else:
        with pytest.raises(ValueError, match="Git URL"):
            _ = parsed.git_url


@pytest.mark.parametrize(
    "source",
    [
        "",
        " owner/repo",
        "owner/repo ",
        "owner/repo@main",
        "/plugins/plugin",
        "https://github.com/owner/repo@main",
        "https://example.com/owner/repo",
        "http://github.com/owner/repo",
        "owner/repo/extra",
        "owner/repo.git.git",
        "owner/..",
        "file:relative",
        "dir:relative",
        "dir://relative",
        "file://host/path",
        "dir://host/path",
        "file:///plugins/plugin?x=1",
        "dir:///plugins/plugin#main",
        "dir:///plugins/plugin?",
        "dir:///plugins/plugin#",
        "dir:///plugins/%xx",
        "dir:///plugins/%00",
        "dir:///plugins/%FF",
        "dir:///plugins/a\nb",
    ],
)
def test_config_and_runtime_parser_reject_the_same_source_syntax(source: str) -> None:
    with pytest.raises(ValueError, match=r"Plugin|Local|decode"):
        parse_plugin_source(source)
    with pytest.raises(ValidationError):
        PluginRepoConfig(id="invalid", source=source)


@pytest.mark.parametrize("source", ["owner/repo", "file:///missing/repo"])
@pytest.mark.parametrize("ref", ["main", "release/v1", "v1.0", "a" * 40])
def test_git_revisions_are_separate_from_sources(source: str, ref: str) -> None:
    assert PluginRepoConfig(id="git", source=source, ref=ref).ref == ref


@pytest.mark.parametrize("ref", ["", " ", " main", "main ", "bad ref", "-option"])
def test_invalid_git_revisions_are_rejected(ref: str) -> None:
    with pytest.raises(ValidationError):
        PluginRepoConfig(id="git", source="owner/repo", ref=ref)


def test_directory_sources_cannot_specify_revisions() -> None:
    with pytest.raises(ValidationError, match="cannot specify ref"):
        PluginRepoConfig(id="dir", source="dir:///missing/plugin@main", ref="main")


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("owner/repo", "https://github.com/owner/repo.git"),
        ("dir:///plugins/plugin @demo", "dir://localhost/plugins/plugin%20%40demo"),
        ("file:///plugins/plugin", "file://localhost/plugins/plugin"),
    ],
)
def test_duplicate_sources_are_detected_after_normalization(
    first: str, second: str
) -> None:
    with pytest.raises(ValidationError, match="must be unique"):
        PluginsConfig(
            default_queue="interactive",
            allowed_queues=["interactive"],
            repos=[
                PluginRepoConfig(id="first", source=first),
                PluginRepoConfig(id="second", source=second),
            ],
        )


def test_source_validation_does_not_access_the_filesystem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_filesystem_access(*_: object, **__: object) -> None:
        pytest.fail("Offline parsing must not access the filesystem")

    for method in ("exists", "resolve", "stat", "is_dir"):
        monkeypatch.setattr(Path, method, unexpected_filesystem_access)
    repo = PluginRepoConfig(id="missing", source="dir://localhost/missing/plugin@dev")
    assert repo.source == "dir:///missing/plugin%40dev"
    assert repo.parsed_source.path == Path("/missing/plugin@dev")


def test_parsed_source_does_not_cache_mutable_configuration() -> None:
    repo = PluginRepoConfig(id="example", source="owner/repo")
    assert repo.source_kind == "github"
    repo.source = "dir:///plugins/plugin"
    assert repo.source_kind == "directory"
    assert repo.parsed_source.path == Path("/plugins/plugin")
