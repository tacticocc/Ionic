"""Local contract registry.

SQLite-backed, single file, no server, no network. Every write records a
version-history row so you can see how a contract drifted over time.

Resolution order for the registry location:

1. ``$IONIC_REGISTRY``            -- explicit path to the db file
2. ``$IONIC_HOME/registry.db``    -- explicit home directory
3. nearest ``.ionic/registry.db`` walking up from cwd
4. ``<git root or cwd>/.ionic/registry.db`` (created on demand)
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from contextlib import closing
from pathlib import Path
from typing import Any, Iterable

from .models import (
    Contract,
    DependencyGraph,
    GraphEdge,
    GraphNode,
    utcnow,
)

REGISTRY_DIRNAME = ".ionic"
REGISTRY_FILENAME = "registry.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS contracts (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    version      TEXT NOT NULL,
    fingerprint  TEXT NOT NULL,
    data         TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS contract_history (
    row_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    id           TEXT NOT NULL,
    version      TEXT NOT NULL,
    fingerprint  TEXT NOT NULL,
    data         TEXT NOT NULL,
    recorded_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_history_id ON contract_history (id, row_id);

CREATE TABLE IF NOT EXISTS dependencies (
    source       TEXT NOT NULL,
    target       TEXT NOT NULL,
    PRIMARY KEY (source, target)
);

CREATE INDEX IF NOT EXISTS idx_dependencies_target ON dependencies (target);

CREATE TABLE IF NOT EXISTS meta (
    key          TEXT PRIMARY KEY,
    value        TEXT NOT NULL
);
"""


class RegistryError(RuntimeError):
    """Raised for registry-level problems (missing / duplicate contracts)."""


class ContractNotFound(RegistryError):
    def __init__(self, contract_id: str) -> None:
        super().__init__(f"No contract registered with id {contract_id!r}")
        self.contract_id = contract_id


class ContractExists(RegistryError):
    def __init__(self, contract_id: str) -> None:
        super().__init__(
            f"Contract {contract_id!r} is already registered. "
            "Use update_contract (or --force) to change it."
        )
        self.contract_id = contract_id


class RegistryStateChanged(RegistryError):
    """Raised when an atomic workspace apply no longer matches its plan."""

    def __init__(self, expected: str, actual: str) -> None:
        super().__init__("registry changed after the sync plan was reviewed")
        self.expected = expected
        self.actual = actual


def find_project_root(start: Path | None = None) -> Path:
    """Walk up looking for an existing .ionic dir, else a git root, else cwd."""
    current = (start or Path.cwd()).resolve()
    for candidate in [current, *current.parents]:
        if (candidate / REGISTRY_DIRNAME).is_dir():
            return candidate
    for candidate in [current, *current.parents]:
        if (candidate / ".git").exists():
            return candidate
    return current


def default_registry_path(start: Path | None = None) -> Path:
    explicit = os.environ.get("IONIC_REGISTRY")
    if explicit:
        return Path(explicit).expanduser().resolve()
    home = os.environ.get("IONIC_HOME")
    if home:
        return (Path(home).expanduser() / REGISTRY_FILENAME).resolve()
    return (find_project_root(start) / REGISTRY_DIRNAME / REGISTRY_FILENAME).resolve()


class Registry:
    """A local, file-backed contract registry.

    Safe to share across threads: the MCP server dispatches sync tool calls on
    a worker pool, so the connection is opened with `check_same_thread=False`
    and every access is serialised behind a reentrant lock. The transactions
    here are all sub-millisecond, so serialising them costs nothing.
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path).expanduser().resolve() if path else default_registry_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        with closing(self._conn.cursor()) as cur:
            cur.executescript(_SCHEMA)
        self._conn.commit()

    # -- lifecycle -----------------------------------------------------

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "Registry":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- writes --------------------------------------------------------

    def register(self, contract: Contract, *, force: bool = False) -> Contract:
        """Add a new contract. Raises if it already exists unless `force`."""
        with self._lock:
            existing = self.get(contract.id, missing_ok=True)
            if existing is not None and not force:
                raise ContractExists(contract.id)
            if existing is not None:
                contract = contract.model_copy(update={"created_at": existing.created_at})
            return self._write(contract)

    def update(self, contract: Contract) -> Contract:
        """Replace an existing contract, preserving its creation time."""
        with self._lock:
            existing = self.get(contract.id)
            contract = contract.model_copy(update={"created_at": existing.created_at})
            return self._write(contract)

    def patch(self, contract_id: str, changes: dict[str, Any]) -> Contract:
        """Apply a partial update to a registered contract."""
        with self._lock:
            existing = self.get(contract_id)
            payload = existing.model_dump(mode="json")
            payload.update(changes)
            payload["id"] = contract_id
            updated = Contract.model_validate(payload)
            return self.update(updated)

    def upsert(self, contract: Contract) -> Contract:
        return self.register(contract, force=True)

    def sync_batch(
        self,
        contracts: Iterable[Contract],
        *,
        prune_ids: Iterable[str] = (),
        expected_state: str | None = None,
    ) -> dict[str, list[str]]:
        """Atomically reconcile a fully validated set of contracts.

        Workspace scans happen before this method is called.  Keeping the
        database write in one transaction means an interrupted multi-repo sync
        can never leave half of the repositories on a new snapshot.  Identical
        contracts are deliberately skipped so repeated scans do not manufacture
        history revisions.

        ``prune_ids`` is explicit rather than prefix based.  The workspace layer
        owns repository scoping and hands the registry only the exact stale ids
        it is allowed to remove.
        """
        incoming = list(contracts)
        by_id = {contract.id: contract for contract in incoming}
        if len(by_id) != len(incoming):
            raise RegistryError("a batch cannot contain the same contract id twice")

        to_prune = {
            contract_id.strip().lower()
            for contract_id in prune_ids
            if contract_id.strip().lower() not in by_id
        }
        result: dict[str, list[str]] = {
            "added": [],
            "updated": [],
            "unchanged": [],
            "pruned": [],
        }

        with self._lock:
            # SQLite's default deferred transaction does not reserve the writer
            # lock for a SELECT. BEGIN IMMEDIATE makes the state check and every
            # following write one cross-process compare-and-swap operation.
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                if expected_state is not None:
                    actual_state = self._state_fingerprint_unlocked()
                    if actual_state != expected_state:
                        raise RegistryStateChanged(expected_state, actual_state)
                for contract_id in sorted(by_id):
                    contract = by_id[contract_id]
                    row = self._conn.execute(
                        "SELECT data FROM contracts WHERE id = ?", (contract_id,)
                    ).fetchone()
                    existing = (
                        Contract.model_validate(json.loads(row["data"]))
                        if row is not None
                        else None
                    )

                    if existing is not None:
                        comparable = contract.model_copy(
                            update={
                                "created_at": existing.created_at,
                                "updated_at": existing.updated_at,
                            }
                        )
                        if comparable.model_dump(mode="json") == existing.model_dump(mode="json"):
                            result["unchanged"].append(contract_id)
                            continue
                        contract = contract.model_copy(update={"created_at": existing.created_at})
                        result["updated"].append(contract_id)
                    else:
                        result["added"].append(contract_id)

                    stored = contract.model_copy(update={"updated_at": utcnow()})
                    self._write_in_transaction(stored)

                for contract_id in sorted(to_prune):
                    deleted = self._conn.execute(
                        "DELETE FROM contracts WHERE id = ?", (contract_id,)
                    ).rowcount
                    if deleted:
                        self._conn.execute(
                            "DELETE FROM dependencies WHERE source = ?", (contract_id,)
                        )
                        result["pruned"].append(contract_id)
                self._conn.commit()
            except BaseException:
                self._conn.rollback()
                raise
        return result

    def state_fingerprint(self) -> str:
        """A deterministic token for all current contracts and dependency data."""
        with self._lock:
            return self._state_fingerprint_unlocked()

    def snapshot(self) -> dict[str, Any]:
        """Return contracts and their state id from one SQLite read snapshot."""
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                rows = self._conn.execute(
                    "SELECT id, data FROM contracts ORDER BY id"
                ).fetchall()
                contracts = [
                    Contract.model_validate(json.loads(row["data"])) for row in rows
                ]
                state_id = self._state_fingerprint_from_rows(rows)
                self._conn.commit()
            except BaseException:
                self._conn.rollback()
                raise
        return {
            "path": str(self.path),
            "state_id": state_id,
            "contracts": contracts,
        }

    def _state_fingerprint_unlocked(self) -> str:
        rows = self._conn.execute(
            "SELECT id, data FROM contracts ORDER BY id"
        ).fetchall()
        return self._state_fingerprint_from_rows(rows)

    @staticmethod
    def _state_fingerprint_from_rows(rows: Iterable[sqlite3.Row]) -> str:
        canonical = [(row["id"], json.loads(row["data"])) for row in rows]
        blob = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def _write(self, contract: Contract) -> Contract:
        contract = contract.model_copy(update={"updated_at": utcnow()})
        with self._lock, self._conn:
            self._write_in_transaction(contract)
        return contract

    def _write_in_transaction(self, contract: Contract) -> None:
        """Write one contract using the caller's active transaction."""
        blob = json.dumps(contract.model_dump(mode="json"), sort_keys=True)
        fingerprint = contract.fingerprint()
        now = contract.updated_at.isoformat()
        self._conn.execute(
            """
            INSERT INTO contracts (id, name, version, fingerprint, data, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                version = excluded.version,
                fingerprint = excluded.fingerprint,
                data = excluded.data,
                updated_at = excluded.updated_at
            """,
            (
                contract.id,
                contract.name,
                contract.version,
                fingerprint,
                blob,
                contract.created_at.isoformat(),
                now,
            ),
        )
        self._conn.execute(
            """
            INSERT INTO contract_history (id, version, fingerprint, data, recorded_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (contract.id, contract.version, fingerprint, blob, now),
        )
        self._conn.execute("DELETE FROM dependencies WHERE source = ?", (contract.id,))
        for dep in contract.depends_on:
            self._conn.execute(
                "INSERT OR IGNORE INTO dependencies (source, target) VALUES (?, ?)",
                (contract.id, dep.contract_id),
            )

    def delete(self, contract_id: str) -> None:
        contract_id = contract_id.strip().lower()
        with self._lock:
            self.get(contract_id)  # raises if missing
            with self._conn:
                self._conn.execute("DELETE FROM contracts WHERE id = ?", (contract_id,))
                self._conn.execute(
                    "DELETE FROM dependencies WHERE source = ?", (contract_id,)
                )

    # -- reads ---------------------------------------------------------

    def get(self, contract_id: str, *, missing_ok: bool = False) -> Contract:
        contract_id = contract_id.strip().lower()
        with self._lock:
            row = self._conn.execute(
                "SELECT data FROM contracts WHERE id = ?", (contract_id,)
            ).fetchone()
        if row is None:
            if missing_ok:
                return None  # type: ignore[return-value]
            raise ContractNotFound(contract_id)
        return Contract.model_validate(json.loads(row["data"]))

    def exists(self, contract_id: str) -> bool:
        return self.get(contract_id, missing_ok=True) is not None

    def list(self, *, tag: str | None = None) -> list[Contract]:
        with self._lock:
            rows = self._conn.execute("SELECT data FROM contracts ORDER BY id").fetchall()
        contracts = [Contract.model_validate(json.loads(r["data"])) for r in rows]
        if tag:
            contracts = [c for c in contracts if tag in c.tags]
        return contracts

    def history(self, contract_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT version, fingerprint, recorded_at, data
                FROM contract_history
                WHERE id = ?
                ORDER BY row_id DESC
                LIMIT ?
                """,
                (contract_id.strip().lower(), limit),
            ).fetchall()
        return [
            {
                "version": r["version"],
                "fingerprint": r["fingerprint"],
                "recorded_at": r["recorded_at"],
                "contract": json.loads(r["data"]),
            }
            for r in rows
        ]

    def previous(self, contract_id: str) -> Contract | None:
        """The contract as it stood before the most recent write."""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT data FROM contract_history
                WHERE id = ? ORDER BY row_id DESC LIMIT 2
                """,
                (contract_id.strip().lower(),),
            ).fetchall()
        if len(rows) < 2:
            return None
        return Contract.model_validate(json.loads(rows[1]["data"]))

    # -- graph ---------------------------------------------------------

    def dependents(self, contract_id: str) -> list[Contract]:
        """Contracts that declare a dependency on this one."""
        contract_id = contract_id.strip().lower()
        with self._lock:
            rows = self._conn.execute(
                "SELECT source FROM dependencies WHERE target = ? ORDER BY source",
                (contract_id,),
            ).fetchall()
        out: list[Contract] = []
        for row in rows:
            contract = self.get(row["source"], missing_ok=True)
            if contract is not None:
                out.append(contract)
        return out

    def dependencies(self, contract_id: str) -> list[Contract]:
        contract = self.get(contract_id)
        out: list[Contract] = []
        for dep in contract.depends_on:
            resolved = self.get(dep.contract_id, missing_ok=True)
            if resolved is not None:
                out.append(resolved)
        return out

    def graph(self, *, root: str | None = None) -> DependencyGraph:
        """Build the dependency graph, optionally restricted to one contract's
        neighbourhood (its transitive dependents and direct dependencies)."""
        contracts = {c.id: c for c in self.list()}
        if root is not None:
            root = root.strip().lower()
            if root not in contracts:
                raise ContractNotFound(root)
            keep = self._neighbourhood(contracts, root)
            contracts = {cid: c for cid, c in contracts.items() if cid in keep}

        nodes = [
            GraphNode(id=c.id, name=c.name, version=c.version, tags=list(c.tags))
            for c in contracts.values()
        ]
        edges: list[GraphEdge] = []
        for contract in contracts.values():
            for dep in contract.depends_on:
                edges.append(
                    GraphEdge(
                        source=contract.id,
                        target=dep.contract_id,
                        requires_tools=list(dep.requires_tools),
                        requires_capabilities=list(dep.requires_capabilities),
                        expects_outputs=list(dep.expects_outputs),
                        resolved=self.exists(dep.contract_id),
                    )
                )
        return DependencyGraph(nodes=nodes, edges=edges)

    def _neighbourhood(self, contracts: dict[str, Contract], root: str) -> set[str]:
        keep = {root}
        # transitive dependents (who breaks if root changes)
        frontier = [root]
        while frontier:
            current = frontier.pop()
            for contract in contracts.values():
                if current in contract.dependency_ids() and contract.id not in keep:
                    keep.add(contract.id)
                    frontier.append(contract.id)
        # direct dependencies (what root leans on)
        root_contract = contracts.get(root)
        if root_contract:
            keep.update(root_contract.dependency_ids())
        return keep

    def transitive_dependents(self, contract_id: str) -> list[Contract]:
        """Every contract that would be affected, however indirectly."""
        contract_id = contract_id.strip().lower()
        seen: set[str] = set()
        order: list[str] = []
        frontier = [contract_id]
        while frontier:
            current = frontier.pop(0)
            for dependent in self.dependents(current):
                if dependent.id in seen or dependent.id == contract_id:
                    continue
                seen.add(dependent.id)
                order.append(dependent.id)
                frontier.append(dependent.id)
        return [self.get(cid) for cid in order]

    # -- portability ---------------------------------------------------

    def export(self) -> dict[str, Any]:
        return {
            "ionic_export": 1,
            "exported_at": utcnow().isoformat(),
            "contracts": [c.model_dump(mode="json") for c in self.list()],
        }

    def import_contracts(
        self, payload: dict[str, Any] | Iterable[dict[str, Any]], *, force: bool = True
    ) -> list[Contract]:
        if isinstance(payload, dict):
            raw = payload.get("contracts", [])
        else:
            raw = list(payload)
        imported: list[Contract] = []
        for item in raw:
            contract = Contract.model_validate(item)
            imported.append(self.register(contract, force=force))
        return imported

    def stats(self) -> dict[str, Any]:
        with self._lock:
            contracts = self.list()
            edges = self._conn.execute(
                "SELECT COUNT(*) AS n FROM dependencies"
            ).fetchone()["n"]
            revisions = self._conn.execute(
                "SELECT COUNT(*) AS n FROM contract_history"
            ).fetchone()["n"]
        return {
            "path": str(self.path),
            "contracts": len(contracts),
            "dependencies": edges,
            "revisions": revisions,
        }


def open_registry(path: Path | str | None = None) -> Registry:
    return Registry(path)
