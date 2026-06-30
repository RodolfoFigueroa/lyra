from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from lyra_app.plugin_state import (
    DEFAULT_PLUGIN_STATE_PATH,
    PLUGIN_STATE_SCHEMA_VERSION,
    PluginRepoRecord,
    PluginState,
    PluginStateLoadError,
    PluginStateNotFoundError,
    PluginStateStore,
    PluginStateValidationError,
    generate_repo_id,
    load_plugin_state,
    make_repo_record,
    normalize_repo_source,
    render_plugin_state_toml,
    save_plugin_state,
)

if TYPE_CHECKING:
    from pathlib import Path


ALLOWED_QUEUES = ("interactive", "batch")


def _state_path(tmp_path: Path) -> Path:
    return tmp_path / "state" / "plugins.toml"


def test_default_plugin_state_path_uses_lyra_data_state_tree() -> None:
    assert DEFAULT_PLUGIN_STATE_PATH.as_posix() == "/lyra_data/state/plugins.toml"


def test_load_missing_plugin_state_returns_empty_state_without_writing(
    tmp_path: Path,
) -> None:
    path = _state_path(tmp_path)

    state = load_plugin_state(path, allowed_queues=ALLOWED_QUEUES)

    assert state == PluginState.empty()
    assert not path.exists()


def test_plugin_state_round_trips_as_toml(tmp_path: Path) -> None:
    state = PluginState(
        repos=[
            PluginRepoRecord(
                id="owner-example-plugin",
                source="owner/example-plugin",
                ref="main",
                enabled=True,
            ),
            PluginRepoRecord(
                id="local-metrics",
                source=(tmp_path / "local-metrics").as_uri(),
                enabled=False,
            ),
        ],
        metric_queues={
            "walkability_score": "interactive",
            "regional.accessibility": "batch",
        },
    )
    path = _state_path(tmp_path)

    save_plugin_state(state, path, allowed_queues=ALLOWED_QUEUES)
    loaded = load_plugin_state(path, allowed_queues=ALLOWED_QUEUES)
    rendered = path.read_text(encoding="utf-8")

    assert loaded == state
    assert "schema_version = 1" in rendered
    assert 'id = "owner-example-plugin"' in rendered
    assert '"regional.accessibility" = "batch"' in rendered
    assert not list(path.parent.glob(".plugins.toml.*.tmp"))


def test_render_plugin_state_empty_file_shape() -> None:
    rendered = render_plugin_state_toml(PluginState.empty())

    assert rendered == "schema_version = 1\n\n[metric_queues]\n"


@pytest.mark.parametrize(
    ("raw", "source", "ref"),
    [
        ("owner/example-plugin", "owner/example-plugin", None),
        ("owner/example-plugin@main", "owner/example-plugin", "main"),
        ("https://github.com/owner/example-plugin", "owner/example-plugin", None),
        (
            "https://github.com/owner/example-plugin@v1.2.0",
            "owner/example-plugin",
            "v1.2.0",
        ),
    ],
)
def test_normalize_repo_source_accepts_github_shapes(
    raw: str,
    source: str,
    ref: str | None,
) -> None:
    normalized = normalize_repo_source(raw)

    assert normalized.source == source
    assert normalized.ref == ref
    assert normalized.source_kind == "github"


def test_normalize_repo_source_accepts_local_file_uri(tmp_path: Path) -> None:
    local_repo = tmp_path / "local-plugin"

    normalized = normalize_repo_source(f"file://localhost{local_repo}")

    assert normalized.source == local_repo.as_uri()
    assert normalized.ref is None
    assert normalized.source_kind == "local"


def test_make_repo_record_normalizes_source_and_generates_id() -> None:
    record = make_repo_record("https://github.com/owner/example-plugin@main")

    assert record.id == generate_repo_id("owner/example-plugin")
    assert record.source == "owner/example-plugin"
    assert record.ref == "main"
    assert record.enabled is True


@pytest.mark.parametrize(
    ("raw", "match"),
    [
        ({"unexpected": True}, "Extra inputs are not permitted"),
        ({"schema_version": 2}, "Input should be 1"),
        (
            {
                "repos": [
                    {
                        "id": "repo-one",
                        "source": "owner/plugin",
                        "extra": "surprise",
                    }
                ]
            },
            "Extra inputs are not permitted",
        ),
        (
            {
                "repos": [
                    {
                        "id": "bad/id",
                        "source": "owner/plugin",
                    }
                ]
            },
            "repos.id",
        ),
        (
            {
                "repos": [
                    {
                        "id": "repo-one",
                        "source": "owner/plugin@main",
                    }
                ]
            },
            "inline ref",
        ),
        (
            {
                "repos": [
                    {
                        "id": "repo-one",
                        "source": "https://github.com/owner/plugin",
                    }
                ]
            },
            "must be normalized",
        ),
        (
            {
                "repos": [
                    {
                        "id": "local-repo",
                        "source": "file:///tmp/local-plugin",
                        "ref": "main",
                    }
                ]
            },
            "local plugin repo sources cannot include refs",
        ),
        (
            {"metric_queues": {" ": "interactive"}},
            "metric name must be a non-empty string",
        ),
        (
            {"metric_queues": {"metric": " "}},
            "queue name must be a non-empty string",
        ),
    ],
)
def test_plugin_state_rejects_invalid_shapes(
    raw: dict[str, object],
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        PluginState.model_validate({"schema_version": 1, **raw})


def test_plugin_state_rejects_duplicate_repo_ids() -> None:
    with pytest.raises(ValueError, match="duplicate plugin repo IDs"):
        PluginState(
            repos=[
                PluginRepoRecord(id="plugin", source="owner/plugin-a"),
                PluginRepoRecord(id="plugin", source="owner/plugin-b"),
            ],
        )


def test_plugin_state_rejects_duplicate_enabled_sources() -> None:
    with pytest.raises(ValueError, match="duplicate enabled plugin repo sources"):
        PluginState(
            repos=[
                PluginRepoRecord(id="plugin-a", source="owner/plugin"),
                PluginRepoRecord(id="plugin-b", source="owner/plugin", ref="main"),
            ],
        )


def test_plugin_state_allows_disabled_duplicate_sources() -> None:
    state = PluginState(
        repos=[
            PluginRepoRecord(id="plugin-a", source="owner/plugin"),
            PluginRepoRecord(id="plugin-b", source="owner/plugin", enabled=False),
        ],
    )

    assert [repo.id for repo in state.repos] == ["plugin-a", "plugin-b"]


def test_load_plugin_state_rejects_invalid_queue_assignments(tmp_path: Path) -> None:
    path = _state_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(
        """
schema_version = 1

[metric_queues]
walkability_score = "priority"
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(PluginStateLoadError, match=r"plugins\.allowed_queues"):
        load_plugin_state(path, allowed_queues=ALLOWED_QUEUES)


def test_load_plugin_state_rejects_invalid_toml(tmp_path: Path) -> None:
    path = _state_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("[metric_queues\n", encoding="utf-8")

    with pytest.raises(PluginStateLoadError, match="not valid TOML"):
        load_plugin_state(path, allowed_queues=ALLOWED_QUEUES)


def test_state_store_repo_crud_persists_to_disk(tmp_path: Path) -> None:
    path = _state_path(tmp_path)
    store = PluginStateStore(path, allowed_queues=ALLOWED_QUEUES)

    created = store.add_repo(
        "https://github.com/owner/example-plugin@main",
        repo_id="example",
    )
    updated = store.update_repo("example", enabled=False)
    deleted = store.delete_repo("example")

    assert created.source == "owner/example-plugin"
    assert created.ref == "main"
    assert updated.enabled is False
    assert deleted is True
    assert store.delete_repo("example") is False
    assert store.load().repos == []
    assert path.exists()


def test_state_store_add_repo_rejects_duplicate_generated_id(
    tmp_path: Path,
) -> None:
    store = PluginStateStore(_state_path(tmp_path), allowed_queues=ALLOWED_QUEUES)

    store.add_repo("owner/example-plugin")

    with pytest.raises(PluginStateValidationError, match="provide a unique id"):
        store.add_repo("owner/example-plugin")


def test_state_store_rejects_duplicate_enabled_sources(tmp_path: Path) -> None:
    store = PluginStateStore(_state_path(tmp_path), allowed_queues=ALLOWED_QUEUES)

    store.add_repo("owner/example-plugin", repo_id="one")

    with pytest.raises(
        PluginStateValidationError,
        match="duplicate enabled plugin repo sources",
    ):
        store.add_repo("owner/example-plugin@main", repo_id="two")


def test_state_store_reports_unknown_repo_for_update(tmp_path: Path) -> None:
    store = PluginStateStore(_state_path(tmp_path), allowed_queues=ALLOWED_QUEUES)

    with pytest.raises(PluginStateNotFoundError, match="unknown plugin repo id"):
        store.update_repo("missing", enabled=False)


def test_state_store_metric_routing_crud_persists_to_disk(tmp_path: Path) -> None:
    store = PluginStateStore(_state_path(tmp_path), allowed_queues=ALLOWED_QUEUES)

    queue = store.set_metric_queue(" walkability_score ", " batch ")
    deleted = store.delete_metric_queue("walkability_score")

    assert queue == "batch"
    assert deleted is True
    assert store.delete_metric_queue("walkability_score") is False
    assert store.load().metric_queues == {}


def test_state_store_rejects_metric_queue_outside_allowed_queues(
    tmp_path: Path,
) -> None:
    store = PluginStateStore(_state_path(tmp_path), allowed_queues=ALLOWED_QUEUES)

    with pytest.raises(
        PluginStateValidationError,
        match=r"plugins\.allowed_queues",
    ):
        store.set_metric_queue("walkability_score", "priority")


def test_state_store_assigns_missing_metric_queues(tmp_path: Path) -> None:
    store = PluginStateStore(_state_path(tmp_path), allowed_queues=ALLOWED_QUEUES)
    store.set_metric_queue("existing_metric", "batch")

    assigned = store.assign_missing_metric_queues(
        ["existing_metric", " new_metric "],
        default_queue="interactive",
    )

    assert assigned == ["new_metric"]
    assert store.load().metric_queues == {
        "existing_metric": "batch",
        "new_metric": "interactive",
    }


def test_state_file_includes_schema_version(tmp_path: Path) -> None:
    store = PluginStateStore(_state_path(tmp_path), allowed_queues=ALLOWED_QUEUES)

    store.set_metric_queue("walkability_score", "interactive")

    assert store.load().schema_version == PLUGIN_STATE_SCHEMA_VERSION
