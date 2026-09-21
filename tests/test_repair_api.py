"""/repair HTTP 层测试：200 / 409 / 422、平局字典序与错误定位。"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def payload(**overrides):
    base = {
        "reviewers": ["r0", "r1", "r2"],
        "manuscripts": ["m0", "m1", "m2"],
        "allowed_pairings": [
            {"reviewer_id": "r0", "manuscript_id": "m0"},
            {"reviewer_id": "r1", "manuscript_id": "m1"},
        ],
        "candidate_pairings": [],
    }
    base.update(overrides)
    return base


def test_repair_min_cost_200():
    # r2 孤立；两种修复：单条 (r2,m2)=5，或腾挪两条合计 3 => 选后者。
    resp = client.post(
        "/repair",
        json=payload(
            candidate_pairings=[
                {"reviewer_id": "r2", "manuscript_id": "m2", "cost": 5},
                {"reviewer_id": "r2", "manuscript_id": "m1", "cost": 1},
                {"reviewer_id": "r1", "manuscript_id": "m2", "cost": 2},
            ]
        ),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["perfect_match_exists"] is True
    assert [(p["reviewer_id"], p["manuscript_id"]) for p in data["added_pairings"]] == [
        ("r1", "m2"),
        ("r2", "m1"),
    ]
    assert [p["cost"] for p in data["added_pairings"]] == [2, 1]
    assert data["total_cost"] == 3
    # 完整 assignment：三对全覆盖，且新增边均为强制。
    assignment = {a["reviewer_id"]: a for a in data["assignment"]}
    assert assignment["r1"]["manuscript_id"] == "m2"
    assert assignment["r2"]["manuscript_id"] == "m1"
    assert all(a["forced"] is True for a in data["assignment"])
    assert all(a["alternative"] is None for a in data["assignment"])


def test_repair_tiebreak_lexicographic():
    # 两种方案成本相同（3）：含更小边 (r1,m2) 的双条方案胜出。
    resp = client.post(
        "/repair",
        json=payload(
            candidate_pairings=[
                {"reviewer_id": "r2", "manuscript_id": "m2", "cost": 3},
                {"reviewer_id": "r2", "manuscript_id": "m1", "cost": 1},
                {"reviewer_id": "r1", "manuscript_id": "m2", "cost": 2},
            ]
        ),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total_cost"] == 3
    assert [
        (p["reviewer_id"], p["manuscript_id"]) for p in data["added_pairings"]
    ] == [("r1", "m2"), ("r2", "m1")]


def test_repair_tiebreak_picks_smaller_single_edge():
    # 两个单边方案同价时，按 (reviewer_id, manuscript_id) 取更小者。
    body = payload(
        reviewers=["r0", "r1"],
        manuscripts=["m0", "m1"],
        allowed_pairings=[
            {"reviewer_id": "r0", "manuscript_id": "m0"},
            {"reviewer_id": "r0", "manuscript_id": "m1"},
        ],
        candidate_pairings=[
            {"reviewer_id": "r1", "manuscript_id": "m1", "cost": 5},
            {"reviewer_id": "r1", "manuscript_id": "m0", "cost": 5},
        ],
    )
    resp = client.post("/repair", json=body)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert [(p["reviewer_id"], p["manuscript_id"]) for p in data["added_pairings"]] == [
        ("r1", "m0")
    ]
    assert data["total_cost"] == 5


def test_repair_already_feasible_empty_addition():
    # 原图已可行：空补充、成本 0；assignment 与 /audit 完全一致。
    body = {
        "reviewers": ["r0", "r1", "r2"],
        "manuscripts": ["m0", "m1", "m2"],
        "allowed_pairings": [
            {"reviewer_id": "r0", "manuscript_id": "m0"},
            {"reviewer_id": "r0", "manuscript_id": "m1"},
            {"reviewer_id": "r1", "manuscript_id": "m0"},
            {"reviewer_id": "r1", "manuscript_id": "m1"},
            {"reviewer_id": "r2", "manuscript_id": "m2"},
        ],
        "candidate_pairings": [
            # 即便候选更便宜，原图可行时也一条不补。
            {"reviewer_id": "r2", "manuscript_id": "m0", "cost": 1}
        ],
    }
    repair_resp = client.post("/repair", json=body)
    audit_resp = client.post(
        "/audit",
        json={k: body[k] for k in ("reviewers", "manuscripts", "allowed_pairings")},
    )
    assert repair_resp.status_code == 200, repair_resp.text
    data = repair_resp.json()
    assert data["added_pairings"] == []
    assert data["total_cost"] == 0
    assert data["assignment"] == audit_resp.json()["assignment"]


def test_repair_infeasible_even_with_candidates_409():
    # r2 只能再连 m1（已被 r1 占用）：合并后仍缺一篇 => 409 + Hall 证人。
    resp = client.post(
        "/repair",
        json=payload(
            candidate_pairings=[
                {"reviewer_id": "r2", "manuscript_id": "m1", "cost": 1}
            ]
        ),
    )
    assert resp.status_code == 409, resp.text
    data = resp.json()
    assert data["perfect_match_exists"] is False
    assert "assignment" not in data
    assert data["unmatched_manuscript_ids"] == ["m2"]
    witness = data["hall_witness"]
    assert set(witness["reviewer_ids"]) == {"r1", "r2"}
    assert witness["neighboring_manuscript_ids"] == ["m1"]
    assert witness["deficiency"] == 1
    # 邻居恰为证人评审人在【合并图】中的全部邻居。
    body_edges = payload(
        candidate_pairings=[
            {"reviewer_id": "r2", "manuscript_id": "m1", "cost": 1}
        ]
    )
    merged = body_edges["allowed_pairings"] + [
        {"reviewer_id": p["reviewer_id"], "manuscript_id": p["manuscript_id"]}
        for p in body_edges["candidate_pairings"]
    ]
    true_neighbors = {
        p["manuscript_id"]
        for p in merged
        if p["reviewer_id"] in witness["reviewer_ids"]
    }
    assert set(witness["neighboring_manuscript_ids"]) == true_neighbors


def test_repair_empty_candidates_infeasible_409():
    resp = client.post("/repair", json=payload(candidate_pairings=[]))
    assert resp.status_code == 409, resp.text


def test_repair_duplicate_candidate_422():
    resp = client.post(
        "/repair",
        json=payload(
            candidate_pairings=[
                {"reviewer_id": "r2", "manuscript_id": "m2", "cost": 1},
                {"reviewer_id": "r2", "manuscript_id": "m2", "cost": 9},
            ]
        ),
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail[-1]["loc"] == ["body", "candidate_pairings", 1]
    assert detail[-1]["type"] == "duplicate_candidate_pairing"


def test_repair_overlaps_existing_pairing_422():
    resp = client.post(
        "/repair",
        json=payload(
            candidate_pairings=[
                {"reviewer_id": "r0", "manuscript_id": "m0", "cost": 1}
            ]
        ),
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail[-1]["loc"] == ["body", "candidate_pairings", 0]
    assert detail[-1]["type"] == "overlaps_existing_pairing"


def test_repair_unknown_ids_422():
    resp = client.post(
        "/repair",
        json=payload(
            candidate_pairings=[
                {"reviewer_id": "ghost", "manuscript_id": "m2", "cost": 1},
                {"reviewer_id": "r2", "manuscript_id": "ghost-m", "cost": 1},
            ]
        ),
    )
    assert resp.status_code == 422
    locs = {tuple(e["loc"]) for e in resp.json()["detail"]}
    assert ("body", "candidate_pairings", 0, "reviewer_id") in locs
    assert ("body", "candidate_pairings", 1, "manuscript_id") in locs


def test_repair_non_positive_cost_422():
    for bad in (0, -3):
        resp = client.post(
            "/repair",
            json=payload(
                candidate_pairings=[
                    {"reviewer_id": "r2", "manuscript_id": "m2", "cost": bad}
                ]
            ),
        )
        assert resp.status_code == 422, bad
        detail = resp.json()["detail"]
        assert detail[-1]["loc"] == ["body", "candidate_pairings", 0, "cost"]
        assert detail[-1]["type"] == "non_positive_cost"


def test_repair_non_integer_cost_422():
    # 布尔值与浮点都不接受（须为正整数）。
    for bad in (True, 1.5, "7"):
        resp = client.post(
            "/repair",
            json=payload(
                candidate_pairings=[
                    {"reviewer_id": "r2", "manuscript_id": "m2", "cost": bad}
                ]
            ),
        )
        assert resp.status_code == 422, bad
        locs = [tuple(e["loc"]) for e in resp.json()["detail"]]
        assert ("body", "candidate_pairings", 0, "cost") in locs


def test_repair_missing_cost_field_422():
    resp = client.post(
        "/repair",
        json=payload(
            candidate_pairings=[{"reviewer_id": "r2", "manuscript_id": "m2"}]
        ),
    )
    assert resp.status_code == 422


def test_repair_missing_candidate_list_422():
    body = {
        "reviewers": ["r0", "r1", "r2"],
        "manuscripts": ["m0", "m1", "m2"],
        "allowed_pairings": [
            {"reviewer_id": "r0", "manuscript_id": "m0"},
            {"reviewer_id": "r1", "manuscript_id": "m1"},
        ],
    }
    resp = client.post("/repair", json=body)
    assert resp.status_code == 422
    assert tuple(resp.json()["detail"][0]["loc"]) == (
        "body",
        "candidate_pairings",
    )


def test_repair_extra_field_422():
    resp = client.post(
        "/repair",
        json=payload(
            candidate_pairings=[
                {
                    "reviewer_id": "r2",
                    "manuscript_id": "m2",
                    "cost": 1,
                    "currency": "CNY",
                }
            ]
        ),
    )
    assert resp.status_code == 422


def test_repair_aggregates_all_errors_422():
    """一次请求聚合返回所有可定位错误（重复/重叠/未知/非正成本）。"""
    resp = client.post(
        "/repair",
        json=payload(
            candidate_pairings=[
                {"reviewer_id": "r2", "manuscript_id": "m2", "cost": 1},
                {"reviewer_id": "r2", "manuscript_id": "m2", "cost": 2},   # 重复
                {"reviewer_id": "r0", "manuscript_id": "m0", "cost": 3},   # 重叠
                {"reviewer_id": "ghost", "manuscript_id": "m2", "cost": 4},  # 未知 r
                {"reviewer_id": "r2", "manuscript_id": "ghost", "cost": 5},  # 未知 m
                {"reviewer_id": "r2", "manuscript_id": "m1", "cost": 0},   # 非正成本
            ]
        ),
    )
    assert resp.status_code == 422
    errors = resp.json()["detail"]
    types = {e["type"] for e in errors}
    assert {
        "duplicate_candidate_pairing",
        "overlaps_existing_pairing",
        "unknown_reviewer",
        "unknown_manuscript",
        "non_positive_cost",
    } <= types
    # 各自下标定位正确。
    locs = {tuple(e["loc"]): e["type"] for e in errors}
    assert locs[("body", "candidate_pairings", 1)] == "duplicate_candidate_pairing"
    assert locs[("body", "candidate_pairings", 2)] == "overlaps_existing_pairing"
    assert (
        locs[("body", "candidate_pairings", 3, "reviewer_id")] == "unknown_reviewer"
    )
    assert (
        locs[("body", "candidate_pairings", 4, "manuscript_id")] == "unknown_manuscript"
    )
    assert locs[("body", "candidate_pairings", 5, "cost")] == "non_positive_cost"


def test_repair_deterministic():
    body = payload(
        candidate_pairings=[
            {"reviewer_id": "r2", "manuscript_id": "m2", "cost": 3},
            {"reviewer_id": "r2", "manuscript_id": "m1", "cost": 1},
            {"reviewer_id": "r1", "manuscript_id": "m2", "cost": 2},
        ]
    )
    first = client.post("/repair", json=body)
    second = client.post("/repair", json=body)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()


def test_repair_does_not_change_audit():
    """/repair 的引入不改变 /audit 的既有行为。"""
    body = {
        "reviewers": ["r0", "r1", "r2"],
        "manuscripts": ["m0", "m1", "m2"],
        "allowed_pairings": [
            {"reviewer_id": "r0", "manuscript_id": "m0"},
            {"reviewer_id": "r0", "manuscript_id": "m1"},
            {"reviewer_id": "r1", "manuscript_id": "m0"},
            {"reviewer_id": "r1", "manuscript_id": "m1"},
            {"reviewer_id": "r2", "manuscript_id": "m2"},
        ],
    }
    resp = client.post("/audit", json=body)
    assert resp.status_code == 200
    by_r = {a["reviewer_id"]: a for a in resp.json()["assignment"]}
    assert by_r["r0"]["manuscript_id"] == "m0"
    assert by_r["r0"]["forced"] is False
    assert by_r["r2"]["forced"] is True
