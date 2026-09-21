"""/repair 算法层测试：最小成本与字典序平局，暴力枚举交叉验证。"""

import itertools
import random

import pytest

from app.matching import RepairResult, run_audit, run_repair


def all_perfect_matchings(reviewers, manuscripts, edges):
    adj = {r: [] for r in reviewers}
    for r, m in edges:
        adj[r].append(m)
    results = set()
    for perm in itertools.permutations(sorted(manuscripts)):
        mapping = dict(zip(sorted(reviewers), perm))
        if all(mapping[r] in adj[r] for r in sorted(reviewers)):
            results.add(frozenset(mapping.items()))
    return results


def brute_force_repair(reviewers, manuscripts, original, candidates):
    """枚举合并图上所有完美匹配，返回 (最优成本, 最优新增边集合) 或 None。

    成本相同时，按新增配对排序后的列表取字典序最优。
    """
    cand_lookup = {(r, m): cost for r, m, cost in candidates}
    candidate_edges = set(cand_lookup)
    merged = list(set(original) | candidate_edges)
    perfects = all_perfect_matchings(reviewers, manuscripts, merged)
    if not perfects:
        return None
    best = None
    for matching in perfects:
        added = matching & candidate_edges
        cost = sum(cand_lookup[e] for e in added)
        key = sorted(added)
        if best is None or (cost, key) < (best[0], best[1]):
            best = (cost, key)
    return best


@pytest.mark.parametrize("seed", range(400))
def test_repair_matches_bruteforce(seed):
    rng = random.Random(seed)
    n = rng.randint(1, 4)
    reviewers = [f"r{i}" for i in range(n)]
    manuscripts = [f"m{i}" for i in range(n)]
    all_edges = [(r, m) for r in reviewers for m in manuscripts]

    # 随机划分原图边 / 候选边 / 不可用边；候选边带 1..9 的随机成本。
    shuffled = list(all_edges)
    rng.shuffle(shuffled)
    k0 = rng.randint(0, len(all_edges))
    k1 = rng.randint(k0, len(all_edges))
    original = shuffled[:k0]
    candidates = [(r, m, rng.randint(1, 9)) for r, m in shuffled[k0:k1]]

    expected = brute_force_repair(reviewers, manuscripts, original, candidates)
    result = run_repair(reviewers, manuscripts, original, candidates)

    if expected is None:
        # 合并仍不可行：返回的是合并图的失败 AuditResult。
        assert not isinstance(result, RepairResult)
        assert result.perfect_match_exists is False
        merged = list(set(original) | {(r, m) for r, m, _ in candidates})
        witness = result.hall_witness
        assert witness is not None
        edge_set = set(merged)
        true_neighbors = {
            m for r in witness.reviewer_ids for m in manuscripts if (r, m) in edge_set
        }
        assert set(witness.neighboring_manuscript_ids) == true_neighbors
        assert witness.deficiency == len(reviewers) - result.max_matching_size
        return

    expected_cost, expected_added = expected
    assert isinstance(result, RepairResult)
    got_added = [(r, m) for r, m, _ in result.added]
    assert got_added == expected_added
    assert result.total_cost == expected_cost
    # added_pairings 按 (reviewer_id, manuscript_id) 升序。
    assert got_added == sorted(got_added)
    # 成本之和与各条 cost 自洽。
    assert sum(c for _, _, c in result.added) == expected_cost

    # 原图已可行的特殊情形：必然空补充、成本 0。
    if all_perfect_matchings(reviewers, manuscripts, original):
        assert result.added == []
        assert result.total_cost == 0

    # assignment 是合并图（原图 + 实际新增边）上合法、完整、字典序最小的
    # 完美匹配；每条新增边在该图中必为强制边（无替代候选可用）。
    repaired_edges = list(set(original) | set(expected_added))
    repaired_perfects = all_perfect_matchings(
        reviewers, manuscripts, repaired_edges
    )
    assignment_map = {r: m for r, m, _, _ in result.audit.pairs}
    assert frozenset(assignment_map.items()) in repaired_perfects
    # 字典序最小：按评审人升序的稿件序列。
    lex_min = min(
        tuple(dict(p)[r] for r in sorted(reviewers)) for p in repaired_perfects
    )
    assert tuple(assignment_map[r] for r in sorted(reviewers)) == lex_min
    # 所有新增边均强制（其余候选边未被补入，分配只能用原图边 + 新增边）。
    by_edge = {(r, m): forced for r, m, forced, _ in result.audit.pairs}
    for edge in expected_added:
        assert by_edge[edge] is True


def test_repair_tiebreak_prefers_smallest_pair():
    """成本相同：含更小 (reviewer, manuscript) 边的方案优先。

    r2 孤立。候选：(r2,m2) 一条 cost 3；(r2,m1) cost 1 + (r1,m2) cost 2
    合计也是 3。后者含全表最小边 (r1,m2)，故字典序更优。
    """
    reviewers = ["r0", "r1", "r2"]
    manuscripts = ["m0", "m1", "m2"]
    original = [("r0", "m0"), ("r1", "m1")]
    candidates = [("r2", "m2", 3), ("r2", "m1", 1), ("r1", "m2", 2)]
    result = run_repair(reviewers, manuscripts, original, candidates)
    assert isinstance(result, RepairResult)
    assert [(r, m) for r, m, _ in result.added] == [("r1", "m2"), ("r2", "m1")]
    assert result.total_cost == 3


def test_repair_tiebreak_prefix_rule():
    """平局裁决的「短表前缀」语义：{e0} 优于 {e1,e2}。"""
    reviewers = ["r0", "r1", "r2"]
    manuscripts = ["m0", "m1", "m2"]
    # r2 孤立；r0-r1 已锁死各自稿件。
    original = [("r0", "m0"), ("r1", "m1")]
    # e0 = (r2,m0) 单条无法独立修复（会和 r0 抢 m0 且 r0 无替代边），
    # 故改用可行构造：两种修复路径成本相同——
    #   方案 A：单条 (r2,m2)，cost 4
    #   方案 B：(r2,m1) cost 1 + (r1,m2) cost 3 = 4（含更小边 (r1,m2)）
    # 方案 B 胜出（其列表首元素 (r1,m2) < (r2,m2)）。
    candidates = [("r2", "m2", 4), ("r2", "m1", 1), ("r1", "m2", 3)]
    result = run_repair(reviewers, manuscripts, original, candidates)
    assert [(r, m) for r, m, _ in result.added] == [("r1", "m2"), ("r2", "m1")]


def test_repair_tiebreak_single_edge_smallest_wins():
    """两个互不相同的单边方案同成本：取 (reviewer,manuscript) 更小者。"""
    reviewers = ["r0", "r1"]
    manuscripts = ["m0", "m1"]
    # r1 孤立；两种对称修复（经 r0 腾挪），成本相同。
    original = [("r0", "m0"), ("r0", "m1")]
    candidates = [("r1", "m1", 5), ("r1", "m0", 5)]
    result = run_repair(reviewers, manuscripts, original, candidates)
    # (r1,m0) < (r1,m1)
    assert [(r, m) for r, m, _ in result.added] == [("r1", "m0")]
    assert result.total_cost == 5


def test_repair_already_feasible_empty():
    """原图已可行：空补充、成本 0，审计结果等同 run_audit。"""
    reviewers = ["r0", "r1", "r2"]
    manuscripts = ["m0", "m1", "m2"]
    original = [
        ("r0", "m0"), ("r0", "m1"),
        ("r1", "m0"), ("r1", "m1"),
        ("r2", "m2"),
    ]
    # 即使存在更「便宜」的候选边，也不补入任何边。
    candidates = [("r2", "m0", 1)]
    result = run_repair(reviewers, manuscripts, original, candidates)
    assert isinstance(result, RepairResult)
    assert result.added == []
    assert result.total_cost == 0
    audit = run_audit(reviewers, manuscripts, original)
    assert result.audit.pairs == audit.pairs


def test_repair_infeasible_returns_hall_witness():
    """合并全部候选仍不可行：返回失败 AuditResult（含合并图 Hall 证人）。"""
    reviewers = ["r0", "r1", "r2"]
    manuscripts = ["m0", "m1", "m2"]
    original = [("r0", "m0"), ("r1", "m1")]
    # r2 只能再连 m1：{r1,r2} 争一篇，仍缺一口。
    candidates = [("r2", "m1", 1)]
    result = run_repair(reviewers, manuscripts, original, candidates)
    assert not isinstance(result, RepairResult)
    assert result.perfect_match_exists is False
    witness = result.hall_witness
    assert witness.deficiency == 1
    assert set(witness.neighboring_manuscript_ids) == {"m1"}
    assert set(witness.reviewer_ids) == {"r1", "r2"}
