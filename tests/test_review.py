"""端到端测试：覆盖四类节点语义、行影响传播与整单拒绝场景。"""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def post(payload):
    return client.post("/review", json=payload)


def test_clean_export_passes():
    resp = post(
        {
            "nodes": [
                {
                    "id": "users",
                    "type": "source",
                    "columns": [
                        {"name": "id", "sensitive": False},
                        {"name": "city", "sensitive": False},
                    ],
                },
                {
                    "id": "out",
                    "type": "projection",
                    "input": "users",
                    "outputs": [{"name": "city", "from": "city"}],
                },
            ],
            "export": "out",
        }
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "passed"
    assert body["columns"] == [
        {"name": "city", "blocked": False, "content_reasons": [], "row_reasons": []}
    ]


def test_sensitive_copy_blocks_with_content_reason():
    resp = post(
        {
            "nodes": [
                {
                    "id": "users",
                    "type": "source",
                    "columns": [
                        {"name": "id"},
                        {"name": "email", "sensitive": True},
                    ],
                },
                {
                    "id": "out",
                    "type": "projection",
                    "input": "users",
                    "outputs": [{"name": "contact", "from": "email"}],
                },
            ],
            "export": "out",
        }
    )
    body = resp.json()
    assert resp.status_code == 200
    assert body["status"] == "blocked"
    col = body["columns"][0]
    assert col["blocked"] is True
    assert col["content_reasons"] == [{"node": "users", "column": "email"}]
    assert col["row_reasons"] == []


def test_constant_replacement_clears_content_source():
    resp = post(
        {
            "nodes": [
                {
                    "id": "users",
                    "type": "source",
                    "columns": [{"name": "email", "sensitive": True}],
                },
                {
                    "id": "out",
                    "type": "projection",
                    "input": "users",
                    "outputs": [{"name": "contact", "constant": "REDACTED"}],
                },
            ],
            "export": "out",
        }
    )
    body = resp.json()
    assert body["status"] == "passed"
    assert body["columns"][0]["blocked"] is False


def test_filter_on_sensitive_column_row_influence_survives_replacement():
    # 筛选引用敏感列 -> 行影响；之后投影只留常量列也无法消除
    resp = post(
        {
            "nodes": [
                {
                    "id": "users",
                    "type": "source",
                    "columns": [
                        {"name": "id"},
                        {"name": "ssn", "sensitive": True},
                    ],
                },
                {"id": "f", "type": "filter", "input": "users", "condition_columns": ["ssn"]},
                {
                    "id": "out",
                    "type": "projection",
                    "input": "f",
                    "outputs": [{"name": "label", "constant": "x"}],
                },
            ],
            "export": "out",
        }
    )
    body = resp.json()
    assert body["status"] == "blocked"
    col = body["columns"][0]
    assert col["content_reasons"] == []
    assert col["row_reasons"] == [{"node": "users", "column": "ssn"}]


def test_join_merges_row_influence_and_sensitive_keys():
    resp = post(
        {
            "nodes": [
                {
                    "id": "accounts",
                    "type": "source",
                    "columns": [
                        {"name": "acct_id"},
                        {"name": "region"},
                    ],
                },
                {
                    "id": "orders",
                    "type": "source",
                    "columns": [
                        {"name": "acct_id"},
                        {"name": "amount"},
                        {"name": "card_no", "sensitive": True},
                    ],
                },
                {"id": "f", "type": "filter", "input": "orders", "condition_columns": ["card_no"]},
                {
                    "id": "j",
                    "type": "join",
                    "left": "accounts",
                    "right": "f",
                    "left_keys": ["acct_id"],
                    "right_keys": ["acct_id"],
                    "outputs": [
                        {"name": "region", "from": {"side": "left", "column": "region"}},
                        {"name": "amount", "from": {"side": "right", "column": "amount"}},
                    ],
                },
            ],
            "export": "j",
        }
    )
    body = resp.json()
    assert body["status"] == "blocked"
    for col in body["columns"]:
        assert col["blocked"] is True
        assert col["content_reasons"] == []
        assert col["row_reasons"] == [{"node": "orders", "column": "card_no"}]


def test_join_sensitive_key_blocks_even_without_filter():
    resp = post(
        {
            "nodes": [
                {
                    "id": "a",
                    "type": "source",
                    "columns": [{"name": "k", "sensitive": True}, {"name": "v1"}],
                },
                {
                    "id": "b",
                    "type": "source",
                    "columns": [{"name": "k"}, {"name": "v2"}],
                },
                {
                    "id": "j",
                    "type": "join",
                    "left": "a",
                    "right": "b",
                    "left_keys": ["k"],
                    "right_keys": ["k"],
                    "outputs": [{"name": "v2", "from": {"side": "right", "column": "v2"}}],
                },
            ],
            "export": "j",
        }
    )
    body = resp.json()
    assert body["status"] == "blocked"
    assert body["columns"][0]["row_reasons"] == [{"node": "a", "column": "k"}]


def test_duplicate_node_id_rejected():
    resp = post(
        {
            "nodes": [
                {"id": "s", "type": "source", "columns": [{"name": "a"}]},
                {"id": "s", "type": "source", "columns": [{"name": "b"}]},
            ],
            "export": "s",
        }
    )
    assert resp.status_code == 422
    assert any("duplicate node id" in e for e in resp.json()["detail"])


def test_duplicate_column_name_rejected():
    resp = post(
        {
            "nodes": [
                {"id": "s", "type": "source", "columns": [{"name": "a"}, {"name": "a"}]},
            ],
            "export": "s",
        }
    )
    assert resp.status_code == 422
    assert any("duplicate column name" in e for e in resp.json()["detail"])


def test_unknown_reference_rejected():
    resp = post(
        {
            "nodes": [
                {"id": "s", "type": "source", "columns": [{"name": "a"}]},
                {
                    "id": "p",
                    "type": "projection",
                    "input": "s",
                    "outputs": [{"name": "x", "from": "nope"}],
                },
            ],
            "export": "p",
        }
    )
    assert resp.status_code == 422
    assert any("unknown column" in e for e in resp.json()["detail"])


def test_cycle_rejected():
    resp = post(
        {
            "nodes": [
                {"id": "s", "type": "source", "columns": [{"name": "a"}]},
                {"id": "f1", "type": "filter", "input": "f2", "condition_columns": ["a"]},
                {"id": "f2", "type": "filter", "input": "f1", "condition_columns": ["a"]},
            ],
            "export": "f1",
        }
    )
    assert resp.status_code == 422
    assert any("cycle" in e for e in resp.json()["detail"])


def test_too_many_nodes_rejected():
    nodes = [{"id": f"s{i}", "type": "source", "columns": [{"name": "a"}]} for i in range(61)]
    resp = post({"nodes": nodes, "export": "s0"})
    assert resp.status_code == 422


def test_projection_requires_from_or_constant():
    resp = post(
        {
            "nodes": [
                {"id": "s", "type": "source", "columns": [{"name": "a"}]},
                {"id": "p", "type": "projection", "input": "s", "outputs": [{"name": "x"}]},
            ],
            "export": "p",
        }
    )
    assert resp.status_code == 422


def test_unordered_nodes_accepted():
    # 节点乱序给出也应正常分析
    resp = post(
        {
            "nodes": [
                {
                    "id": "out",
                    "type": "projection",
                    "input": "f",
                    "outputs": [{"name": "id", "from": "id"}],
                },
                {"id": "f", "type": "filter", "input": "s", "condition_columns": ["id"]},
                {"id": "s", "type": "source", "columns": [{"name": "id"}]},
            ],
            "export": "out",
        }
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "passed"
