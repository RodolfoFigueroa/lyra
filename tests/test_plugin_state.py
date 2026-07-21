from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from lyra_app.plugin_state import (
    DEFAULT_PLUGIN_STATE_PATH,
    PLUGIN_STATE_SCHEMA_VERSION,
    MetricQueueRecord,
    PluginRepoRecord,
    PluginState,
    PluginStateLoadError,
    PluginStateNotFoundError,
    PluginStateStore,
    PluginStateValidationError,
    generate_repo_id,
    load_plugin_state,
    make_repo_record,
    metric_queue_mapping,
    normalize_repo_source,
    render_plugin_state_toml,
    repo_record_to_source,
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
            "walkability_score": MetricQueueRecord(
                queue="interactive",
                repo_id="owner-example-plugin",
            ),
            "regional.accessibility": MetricQueueRecord(
                queue="batch",
                repo_id="owner-example-plugin",
            ),
        },
    )
    path = _state_path(tmp_path)

    save_plugin_state(state, path, allowed_queues=ALLOWED_QUEUES)
    loaded = load_plugin_state(path, allowed_queues=ALLOWED_QUEUES)
    rendered = path.read_text(encoding="utf-8")

    assert loaded == state
    assert "schema_version = 1" in rendered
    assert 'id = "owner-example-plugin"' in rendered
    assert '[metric_queues."regional.accessibility"]' in rendered
    assert 'queue = "batch"' in rendered
    assert 'repo_id = "owner-example-plugin"' in rendered
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


def test_normalize_repo_source_accepts_directory_uri(tmp_path: Path) -> None:
    source = tmp_path / "mock-plugin"

    normalized = normalize_repo_source(f"dir://localhost{source}")

    assert normalized.source == f"dir://{source.resolve().as_posix()}"
    assert normalized.ref is None
    assert normalized.source_kind == "directory"
    assert normalized.generated_id.startswith("dir__mock-plugin__")
    assert normalized.generated_id != generate_repo_id(source.as_uri())


def test_make_repo_record_normalizes_source_and_generates_id() -> None:
    record = make_repo_record("https://github.com/owner/example-plugin@main")

    assert record.id == generate_repo_id("owner/example-plugin")
    assert record.source == "owner/example-plugin"
    assert record.ref == "main"
    assert record.enabled is True


def test_make_repo_record_normalizes_directory_source_and_serializes(
    tmp_path: Path,
) -> None:
    source = tmp_path / "mock-plugin"

    record = make_repo_record(f"dir://localhost{source}")

    assert record.id == generate_repo_id(record.source)
    assert record.source == f"dir://{source.resolve().as_posix()}"
    assert record.ref is None
    assert record.enabled is True
    assert repo_record_to_source(record) == record.source


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
            {
                "repos": [
                    {
                        "id": "directory-plugin",
                        "source": "dir:///tmp/mock-plugin",
                        "ref": "main",
                    }
                ]
            },
            "directory plugin sources cannot include refs",
        ),
        (
            {"metric_queues": {" ": "interactive"}},
            "valid dictionary",
        ),
        (
            {"metric_queues": {"metric": {"queue": " ", "repo_id": "repo-one"}}},
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


def test_plugin_state_rejects_metric_queue_for_unknown_repo() -> None:
    with pytest.raises(ValueError, match="repo_ids must reference configured repos"):
        PluginState(
            repos=[PluginRepoRecord(id="plugin-a", source="owner/plugin")],
            metric_queues={
                "walkability_score": MetricQueueRecord(
                    queue="interactive",
                    repo_id="missing",
                ),
            },
        )


def test_load_plugin_state_rejects_invalid_queue_assignments(tmp_path: Path) -> None:
    path = _state_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(
        """
schema_version = 1

	[[repos]]
	id = "repo-one"
	source = "owner/repo"
	enabled = true

	[metric_queues.walkability_score]
	queue = "priority"
	repo_id = "repo-one"
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
    assert deleted.deleted is True
    assert deleted.removed_metric_queues == []
    assert store.delete_repo("example").deleted is False
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
    store.add_repo("owner/repo", repo_id="repo-one")

    queue = store.set_metric_queue(" walkability_score ", " batch ", repo_id="repo-one")
    deleted = store.delete_metric_queue("walkability_score")

    assert queue == "batch"
    assert deleted is True
    assert store.delete_metric_queue("walkability_score") is False
    assert store.load().metric_queues == {}


def test_state_store_rejects_metric_queue_outside_allowed_queues(
    tmp_path: Path,
) -> None:
    store = PluginStateStore(_state_path(tmp_path), allowed_queues=ALLOWED_QUEUES)
    store.add_repo("owner/repo", repo_id="repo-one")

    with pytest.raises(
        PluginStateValidationError,
        match=r"plugins\.allowed_queues",
    ):
        store.set_metric_queue("walkability_score", "priority", repo_id="repo-one")


def test_state_store_syncs_metric_queues(tmp_path: Path) -> None:
    store = PluginStateStore(_state_path(tmp_path), allowed_queues=ALLOWED_QUEUES)
    store.add_repo("owner/repo", repo_id="repo-one")
    store.set_metric_queue("existing_metric", "batch", repo_id="repo-one")

    result = store.sync_metric_queues(
        {
            "existing_metric": "repo-one",
            " new_metric ": "repo-one",
        },
        default_queue="interactive",
    )

    assert result.assigned == ["new_metric"]
    assert result.removed == []
    assert metric_queue_mapping(store.load()) == {
        "existing_metric": "batch",
        "new_metric": "interactive",
    }


def test_state_store_deletes_repo_metric_queues(tmp_path: Path) -> None:
    store = PluginStateStore(_state_path(tmp_path), allowed_queues=ALLOWED_QUEUES)
    store.add_repo("owner/repo", repo_id="repo-one")
    store.add_repo("owner/other-repo", repo_id="repo-two")
    store.set_metric_queue("first_metric", "interactive", repo_id="repo-one")
    store.set_metric_queue("second_metric", "batch", repo_id="repo-two")

    result = store.delete_repo("repo-one")

    assert result.deleted is True
    assert result.removed_metric_queues == ["first_metric"]
    assert metric_queue_mapping(store.load()) == {"second_metric": "batch"}


def test_state_store_sync_removes_stale_and_moved_metric_queues(tmp_path: Path) -> None:
    store = PluginStateStore(_state_path(tmp_path), allowed_queues=ALLOWED_QUEUES)
    store.add_repo("owner/repo", repo_id="repo-one")
    store.add_repo("owner/other-repo", repo_id="repo-two")
    store.set_metric_queue("moved_metric", "batch", repo_id="repo-one")
    store.set_metric_queue("stale_metric", "batch", repo_id="repo-two")

    result = store.sync_metric_queues(
        {"moved_metric": "repo-two"},
        default_queue="interactive",
    )

    assert result.assigned == ["moved_metric"]
    assert result.removed == ["moved_metric", "stale_metric"]
    loaded = store.load()
    assert metric_queue_mapping(loaded) == {"moved_metric": "interactive"}
    assert loaded.metric_queues["moved_metric"].repo_id == "repo-two"


def test_state_file_includes_schema_version(tmp_path: Path) -> None:
    store = PluginStateStore(_state_path(tmp_path), allowed_queues=ALLOWED_QUEUES)
    store.add_repo("owner/repo", repo_id="repo-one")

    store.set_metric_queue("walkability_score", "interactive", repo_id="repo-one")

    assert store.load().schema_version == PLUGIN_STATE_SCHEMA_VERSION
