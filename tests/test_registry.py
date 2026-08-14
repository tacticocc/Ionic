from __future__ import annotations

import pytest

from ionic.models import Contract
from ionic.registry import ContractExists, ContractNotFound, Registry


def test_roundtrip_preserves_the_contract(registry, planner):
    registry.register(planner)
    loaded = registry.get("planner")
    assert loaded.fingerprint() == planner.fingerprint()
    assert [t.name for t in loaded.tools] == [t.name for t in planner.tools]
    assert loaded.constraints[0].id == "source-required"


def test_duplicate_registration_is_refused_then_allowed_with_force(registry, planner):
    registry.register(planner)
    with pytest.raises(ContractExists):
        registry.register(planner)
    registry.register(planner.model_copy(update={"version": "1.1.0"}), force=True)
    assert registry.get("planner").version == "1.1.0"


def test_missing_contract_raises(registry):
    with pytest.raises(ContractNotFound):
        registry.get("nope")
    assert registry.get("nope", missing_ok=True) is None
    assert registry.exists("nope") is False


def test_patch_merges_top_level_fields(registry, planner):
    registry.register(planner)
    updated = registry.patch("planner", {"version": "1.1.0", "capabilities": ["only one"]})
    assert updated.version == "1.1.0"
    assert updated.capabilities == ["only one"]
    assert [t.name for t in updated.tools] == [t.name for t in planner.tools]


def test_created_at_survives_updates(registry, planner):
    original = registry.register(planner)
    updated = registry.patch("planner", {"version": "1.2.0"})
    assert updated.created_at == original.created_at
    assert updated.updated_at >= original.updated_at


def test_history_records_every_write(registry, planner):
    registry.register(planner)
    registry.patch("planner", {"version": "1.1.0"})
    registry.patch("planner", {"version": "1.2.0"})
    history = registry.history("planner")
    assert [h["version"] for h in history] == ["1.2.0", "1.1.0", "1.0.0"]

    previous = registry.previous("planner")
    assert previous is not None and previous.version == "1.1.0"


def test_dependents_and_dependencies(registry, planner, researcher):
    registry.register(planner)
    registry.register(researcher)
    assert [c.id for c in registry.dependents("planner")] == ["researcher"]
    assert [c.id for c in registry.dependencies("researcher")] == ["planner"]


def test_transitive_dependents(registry, planner, researcher):
    registry.register(planner)
    registry.register(researcher)
    registry.register(
        Contract.model_validate({"id": "publisher", "depends_on": ["researcher"]})
    )
    assert sorted(c.id for c in registry.transitive_dependents("planner")) == [
        "publisher",
        "researcher",
    ]
    assert [c.id for c in registry.dependents("planner")] == ["researcher"]


def test_dependency_edges_are_rewritten_on_update(registry, planner, researcher):
    registry.register(planner)
    registry.register(researcher)
    registry.patch("researcher", {"depends_on": []})
    assert registry.dependents("planner") == []


def test_graph_marks_unresolved_edges(registry, researcher):
    registry.register(researcher)  # planner is not registered
    graph = registry.graph()
    assert len(graph.unresolved()) == 1
    assert graph.unresolved()[0].target == "planner"


def test_graph_root_restricts_to_the_neighbourhood(registry, planner, researcher):
    registry.register(planner)
    registry.register(researcher)
    registry.register(Contract(id="unrelated"))
    graph = registry.graph(root="planner")
    assert sorted(n.id for n in graph.nodes) == ["planner", "researcher"]


def test_delete(registry, planner):
    registry.register(planner)
    registry.delete("planner")
    assert registry.exists("planner") is False
    with pytest.raises(ContractNotFound):
        registry.delete("planner")


def test_export_import_roundtrip(registry, planner, researcher, tmp_path):
    registry.register(planner)
    registry.register(researcher)
    payload = registry.export()

    other = Registry(tmp_path / "other.db")
    other.import_contracts(payload)
    assert sorted(c.id for c in other.list()) == ["planner", "researcher"]
    assert [c.id for c in other.dependents("planner")] == ["researcher"]
    other.close()


def test_list_filters_by_tag(registry, planner):
    registry.register(planner.model_copy(update={"tags": ["core"]}))
    registry.register(Contract(id="other", tags=["edge"]))
    assert [c.id for c in registry.list(tag="core")] == ["planner"]


def test_stats(registry, planner, researcher):
    registry.register(planner)
    registry.register(researcher)
    stats = registry.stats()
    assert stats["contracts"] == 2
    assert stats["dependencies"] == 1
    assert stats["revisions"] == 2


def test_registry_is_usable_from_another_thread(registry, planner):
    """The MCP server dispatches sync tools on a worker pool, so the registry
    must not be pinned to the thread that opened it."""
    import threading

    registry.register(planner)
    result: dict = {}

    def worker():
        try:
            result["contract"] = registry.get("planner")
            registry.patch("planner", {"version": "1.1.0"})
            result["stats"] = registry.stats()
        except Exception as exc:  # pragma: no cover - the failure we are guarding
            result["error"] = exc

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()

    assert "error" not in result, result.get("error")
    assert result["contract"].id == "planner"
    assert registry.get("planner").version == "1.1.0"


def test_concurrent_writes_do_not_corrupt_the_registry(registry):
    import threading

    from ionic.models import Contract

    def writer(index: int):
        registry.register(Contract(id=f"agent-{index}"), force=True)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(registry.list()) == 12


def test_cycles_are_detected(registry):
    from ionic.models import Contract

    registry.register(Contract(id="a", depends_on=["b"]))
    registry.register(Contract(id="b", depends_on=["c"]))
    registry.register(Contract(id="c", depends_on=["a"]))

    cycles = registry.graph().cycles()
    assert len(cycles) == 1
    assert set(cycles[0]) == {"a", "b", "c"}


def test_an_acyclic_graph_reports_no_cycles(registry, planner, researcher):
    registry.register(planner)
    registry.register(researcher)
    assert registry.graph().cycles() == []


def test_self_dependency_is_a_cycle(registry):
    from ionic.models import Contract

    registry.register(Contract(id="ouroboros", depends_on=["ouroboros"]))
    assert registry.graph().cycles() == [["ouroboros", "ouroboros"]]


def test_sync_batch_holds_the_sqlite_writer_lock_from_state_check_to_commit(
    tmp_path, monkeypatch
):
    import threading

    path = tmp_path / "shared.db"
    first = Registry(path)
    second = Registry(path)
    expected = first.state_fingerprint()
    entered_write = threading.Event()
    release_write = threading.Event()
    competitor_started = threading.Event()
    competitor_done = threading.Event()
    errors: list[BaseException] = []
    original = first._write_in_transaction

    def paused_write(contract):
        entered_write.set()
        if not release_write.wait(2):
            raise TimeoutError("test did not release the paused registry write")
        original(contract)

    monkeypatch.setattr(first, "_write_in_transaction", paused_write)

    def apply_batch():
        try:
            first.sync_batch(
                [Contract(id="planned")], expected_state=expected
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    def competing_write():
        competitor_started.set()
        try:
            second.register(Contract(id="competitor"))
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)
        finally:
            competitor_done.set()

    applying = threading.Thread(target=apply_batch)
    applying.start()
    assert entered_write.wait(2)
    competitor = threading.Thread(target=competing_write)
    competitor.start()
    assert competitor_started.wait(2)
    # BEGIN IMMEDIATE was acquired before the expected-state SELECT, so the
    # second connection cannot commit while the first batch is paused.
    assert competitor_done.wait(0.2) is False
    release_write.set()
    applying.join(2)
    competitor.join(2)

    assert not applying.is_alive()
    assert not competitor.is_alive()
    assert errors == []
    assert first.exists("planned") is True
    assert first.exists("competitor") is True
    second.close()
    first.close()
