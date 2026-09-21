"""二部图匹配核心算法（基于 NetworkX）。

约定
----
- 评审人节点为 ``("reviewer", id)``，稿件节点为 ``("manuscript", id)``，
  从而两侧 ID 即使重名也不会冲突。
- 确定性分配定义为：按评审人 ID 升序逐个决策，序列
  ``(m_{r_1}, m_{r_2}, …)`` 字典序最小；未匹配位置记为 ``null``，
  且 ``null`` 小于任何稿件 ID。
- 每次决策都用「剩余子图的最大匹配基数」做可行性预言，
  预言由 Hopcroft–Karp 完成：``networkx.bipartite.hopcroft_karp_matching``。

Hall 定理证人（409 时）
------------------------
不存在完美匹配时，取确定性最大匹配 M，从未匹配评审人出发沿
M-交替路径（同一交替有向图定向）遍历可达节点 Z：

- Z 中的稿件必然全部已被 M 匹配，否则该路径即增广路，与最大性矛盾；
- 取 S = Z ∩ 评审人，则其在原图中的邻居恰为 Z ∩ 稿件，
  且 |S| - |N(S)| = 未匹配评审人数 = 全局缺口（Kőnig–Ore 定理）。

该 S 即违反 Hall 条件的子集，作为 409 响应中的 ``hall_witness``。

强制性判定与替代分配
--------------------
对完美匹配 M 构造交替有向图 D：

- 匹配边定向为 稿件 -> 评审人；
- 非匹配边定向为 评审人 -> 稿件。

经典结论（Berge）：匹配边 (r, m) 非强制（存在另一个不含它的完美匹配）
当且仅当它落在某个 M-交替环上，当且仅当 r 与 m 在 D 中处于同一个
强连通分量（SCC）。因此：

- 强制边：r 与 m 分属不同 SCC；
- 非强制边：在 D 中 BFS 求 r -> m 的最短路径，与匹配边 (m -> r)
  合成为交替环；环上「匹配边 / 非匹配边」身份翻转后即得到一个
  **完整的**、合法的、不含 (r, m) 的完美匹配，作为替代分配（witness）。

邻接按节点 ID 排序，使 BFS 与全部输出确定性。
"""

from dataclasses import dataclass, field

import networkx as nx

REVIEWER = "reviewer"
MANUSCRIPT = "manuscript"


def _rnode(reviewer_id: str) -> tuple[str, str]:
    return (REVIEWER, reviewer_id)


def _mnode(manuscript_id: str) -> tuple[str, str]:
    return (MANUSCRIPT, manuscript_id)


def _node_key(node: tuple[str, str]) -> tuple[str, str]:
    """邻接排序键：先按侧再按 ID。"""
    return node


def build_graph(
    reviewers: list[str],
    manuscripts: list[str],
    pairings: list[tuple[str, str]],
) -> nx.Graph:
    """由已通过校验的数据构造二部图。"""
    graph = nx.Graph()
    graph.add_nodes_from((_rnode(r) for r in reviewers), side=REVIEWER)
    graph.add_nodes_from((_mnode(m) for m in manuscripts), side=MANUSCRIPT)
    graph.add_edges_from(
        (_rnode(reviewer_id), _mnode(manuscript_id))
        for reviewer_id, manuscript_id in pairings
    )
    return graph


def _reviewer_nodes(graph: nx.Graph) -> list[tuple[str, str]]:
    return [n for n, data in graph.nodes(data=True) if data.get("side") == REVIEWER]


def _residual_view(
    graph: nx.Graph,
    excluded: set[tuple[str, str]],
) -> nx.Graph:
    """剩余子图：排除已定稿评审人节点与已占用稿件节点。"""
    return nx.subgraph_view(graph, filter_node=lambda node: node not in excluded)


def _max_matching_size(graph: nx.Graph) -> int:
    """图中（子图内的）最大匹配基数。"""
    top_nodes = _reviewer_nodes(graph)
    if not top_nodes:
        return 0
    matching = nx.bipartite.hopcroft_karp_matching(graph, top_nodes=top_nodes)
    return len(matching) // 2


@dataclass
class HallWitness:
    """Hall 定理证人：违反 Hall 条件的评审人子集。

    - ``reviewer_ids``：缺口评审人集合 S（已按 ID 排序）；
    - ``neighboring_manuscript_ids``：S 在原允许配对中的**全部**邻居
      （已按 ID 排序）；
    - ``deficiency``：``|S| - |N(S)|``，恒等于全局未匹配评审人数。
    """

    reviewer_ids: list[str]
    neighboring_manuscript_ids: list[str]
    deficiency: int


@dataclass
class AuditResult:
    """算法层审计结果。"""

    perfect_match_exists: bool
    #: 成功时为 (rid, mid, forced, alternative_pairs)；失败时为 (rid, mid)
    pairs: list
    max_matching_size: int
    unmatched_reviewers: list[str] = field(default_factory=list)
    unmatched_manuscripts: list[str] = field(default_factory=list)
    #: 仅 409（不存在完美匹配）时填充。
    hall_witness: HallWitness | None = None


def lex_smallest_matching(
    graph: nx.Graph,
    reviewers_sorted: list[str],
    target_size: int,
) -> list[tuple[str, str]] | None:
    """求达到指定基数 ``target_size`` 的字典序最小确定性匹配。

    评审人按 ``reviewers_sorted`` 顺序决策；每个评审人优先保持未匹配
    （``null`` 字典序最小），否则在仍能完成目标基数的前提下选择最小稿件。
    无法达到目标基数时返回 ``None``。
    """
    decided: set[tuple[str, str]] = set()
    used_manuscripts: set[tuple[str, str]] = set()
    result: list[tuple[str, str]] = []
    remaining = target_size

    for reviewer_id in reviewers_sorted:
        rnode = _rnode(reviewer_id)

        # 1) 尝试让该评审人未匹配（null）：仅当剩余评审人仍能凑满基数。
        #    必须同时排除所有已定稿评审人（其边固定，不可重算）
        #    与已占用稿件（不可再分配），否则基数预言会虚高。
        view = _residual_view(graph, decided | {rnode} | used_manuscripts)
        if _max_matching_size(view) >= remaining:
            decided.add(rnode)
            continue

        # 2) 必须匹配：按稿件 ID 升序枚举，选第一个仍可完成目标基数的候选。
        chosen: str | None = None
        candidates = sorted(
            manuscript_id for _, manuscript_id in graph.adj[rnode]
        )
        for manuscript_id in candidates:
            mnode = _mnode(manuscript_id)
            if mnode in used_manuscripts:
                continue
            view = _residual_view(
                graph, decided | {rnode, mnode} | used_manuscripts
            )
            if _max_matching_size(view) >= remaining - 1:
                chosen = manuscript_id
                break

        decided.add(rnode)
        if chosen is None:
            # 理论上不会发生（目标基数可达），保险处理使整轮失败。
            continue

        used_manuscripts.add(_mnode(chosen))
        result.append((reviewer_id, chosen))
        remaining -= 1

    if remaining != 0:
        return None
    return result


def _build_alternating_digraph(
    graph: nx.Graph,
    matching: list[tuple[str, str]],
) -> nx.DiGraph:
    """由匹配 matching（不必完美）构造交替有向图 D。

    匹配边定向 稿件 -> 评审人；非匹配边定向 评审人 -> 稿件。
    """
    digraph = nx.DiGraph()
    digraph.add_nodes_from(graph.nodes())
    matched_edges = {
        (_rnode(reviewer_id), _mnode(manuscript_id))
        for reviewer_id, manuscript_id in matching
    }
    for u, v in graph.edges():
        rnode = u if u[0] == REVIEWER else v
        mnode = u if u[0] == MANUSCRIPT else v
        if (rnode, mnode) in matched_edges:
            digraph.add_edge(mnode, rnode)  # 匹配边
        else:
            digraph.add_edge(rnode, mnode)  # 非匹配边
    return digraph


def _sorted_successors(digraph: nx.DiGraph, node: tuple[str, str]):
    return sorted(digraph.successors(node), key=_node_key)


def _cycle_alternative(
    digraph: nx.DiGraph,
    canonical: list[tuple[str, str]],
    reviewer_id: str,
    manuscript_id: str,
) -> list[tuple[str, str]]:
    """沿含匹配边 (r, m) 的最短交替环翻转，构造不含该边的完美匹配。

    BFS 求 D 中 r -> m 的最短有向路径（邻接有序 => 结果确定），
    路径边全部为非匹配边方向（r→m），再补上匹配边 (m→r) 成环；
    环上身份翻转即得替代完美匹配。
    """
    rnode = _rnode(reviewer_id)
    mnode = _mnode(manuscript_id)

    # BFS：r -> ... -> m。
    parents: dict[tuple[str, str], tuple[str, str] | None] = {rnode: None}
    queue = [rnode]
    found = False
    head = 0
    while head < len(queue):
        current = queue[head]
        head += 1
        if current == mnode:
            found = True
            break
        for nxt in _sorted_successors(digraph, current):
            if nxt not in parents:
                parents[nxt] = current
                queue.append(nxt)
    if not found:
        # 与 SCC 结论矛盾：非强制边必有环。
        raise RuntimeError(f"未找到含 ({reviewer_id}, {manuscript_id}) 的交替环")

    # 还原路径 r -> ... -> m；路径在二部图上交替经过 评审人/稿件。
    path: list[tuple[str, str]] = []
    current = mnode
    while current is not None:
        path.append(current)
        current = parents[current]
    path.reverse()

    # 路径上的无向边（全部是非匹配边）变为匹配边。
    flipped_non_matched: set[tuple[tuple[str, str], tuple[str, str]]] = set()
    for a, b in zip(path, path[1:]):
        flipped_non_matched.add((a, b))
        flipped_non_matched.add((b, a))

    # 以原完美匹配为基底，在环上翻转：移除路径对应的匹配边（m -> r 配对），
    # 加入路径上的非匹配边（r -> m 配对）。
    # 路径节点（不含起点 r）依次为 m?, r?, m?, ... 成对：
    # path = [r0, m_a, r_a, m_b, r_b, ..., m_target]
    alternative: list[tuple[str, str]] = []
    on_path = set(path)
    for rid, mid in canonical:
        rn = _rnode(rid)
        mn = _mnode(mid)
        if rn in on_path and mn in on_path:
            # 环上原匹配边（包括被审计边本身）：丢弃，由非匹配边取代。
            continue
        alternative.append((rid, mid))
    for a, b in zip(path, path[1:]):
        if a[0] == REVIEWER and b[0] == MANUSCRIPT:
            alternative.append((a[1], b[1]))

    alternative.sort(key=lambda pair: pair[0])
    return alternative


def hall_witness(
    graph: nx.Graph,
    matching: list[tuple[str, str]],
    unmatched_reviewers: list[str],
) -> HallWitness:
    """由最大匹配出发，沿交替路径构造 Hall 定理证人。

    以未匹配评审人为源，在交替有向图（匹配边 稿件→评审人，非匹配边
    评审人→稿件）中遍历可达节点集 Z：

    - Z 中的稿件全部已被匹配，否则该交替路径即增广路，与最大性矛盾；
    - 取 S = Z ∩ 评审人：S 中已匹配者的匹配边沿路径反向可达，未匹配者
      的任意邻居沿非匹配边可达，故 S 在原图中的邻居恰为 Z ∩ 稿件；
    - Z ∩ 稿件与 S 中已匹配者一一配对，故
      ``|S| - |N(S)|`` = 未匹配评审人数 = 全局缺口。

    源点与邻接均按 ID 排序，且可达集与遍历顺序无关，结果完全确定。
    """
    digraph = _build_alternating_digraph(graph, matching)
    sources = sorted(
        (_rnode(reviewer_id) for reviewer_id in unmatched_reviewers),
        key=_node_key,
    )
    reachable: set[tuple[str, str]] = set(sources)
    queue = list(sources)
    head = 0
    while head < len(queue):
        current = queue[head]
        head += 1
        for nxt in _sorted_successors(digraph, current):
            if nxt not in reachable:
                reachable.add(nxt)
                queue.append(nxt)

    witness_reviewers = sorted(
        node_id for side, node_id in reachable if side == REVIEWER
    )
    witness_manuscripts = sorted(
        node_id for side, node_id in reachable if side == MANUSCRIPT
    )
    deficiency = len(witness_reviewers) - len(witness_manuscripts)
    # 交替路径理论保证缺口恰为全局未匹配人数；防御性断言。
    assert deficiency == len(unmatched_reviewers)
    return HallWitness(
        reviewer_ids=witness_reviewers,
        neighboring_manuscript_ids=witness_manuscripts,
        deficiency=deficiency,
    )


def run_audit(
    reviewers: list[str],
    manuscripts: list[str],
    pairings: list[tuple[str, str]],
) -> AuditResult:
    """执行完整审计：最大匹配 / 确定性完美匹配 / 逐对强制性。"""
    graph = build_graph(reviewers, manuscripts, pairings)
    reviewers_sorted = sorted(reviewers)
    max_size = _max_matching_size(graph)

    # 不存在完美匹配：给出确定性的最大匹配、未匹配双方及 Hall 定理证人。
    if max_size != len(reviewers):
        pairs = lex_smallest_matching(graph, reviewers_sorted, max_size)
        # 目标基数由全局最大匹配保证可行；防御性断言。
        assert pairs is not None
        matched_reviewers = {r for r, _ in pairs}
        matched_manuscripts = {m for _, m in pairs}
        unmatched_reviewers = sorted(set(reviewers) - matched_reviewers)
        return AuditResult(
            perfect_match_exists=False,
            pairs=pairs,
            max_matching_size=max_size,
            unmatched_reviewers=unmatched_reviewers,
            unmatched_manuscripts=sorted(set(manuscripts) - matched_manuscripts),
            hall_witness=hall_witness(graph, pairs, unmatched_reviewers),
        )

    # 存在完美匹配：取字典序最小的确定性分配。
    canonical = lex_smallest_matching(graph, reviewers_sorted, len(reviewers))
    assert canonical is not None

    # 交替有向图 + 强连通分量：一次 O(V+E) 得到全部强制边。
    digraph = _build_alternating_digraph(graph, canonical)
    component: dict[tuple[str, str], int] = {}
    for index, members in enumerate(nx.strongly_connected_components(digraph)):
        for node in members:
            component[node] = index

    entries: list[tuple[str, str, bool, list[tuple[str, str]] | None]] = []
    for reviewer_id, manuscript_id in canonical:
        same_scc = (
            component[_rnode(reviewer_id)] == component[_mnode(manuscript_id)]
        )
        if not same_scc:
            # 两端不同 SCC：该边不在任何交替环上，为强制边。
            entries.append((reviewer_id, manuscript_id, True, None))
            continue
        # 同一 SCC：非强制，给出环翻转得到的完整替代分配。
        alternative = _cycle_alternative(
            digraph, canonical, reviewer_id, manuscript_id
        )
        entries.append((reviewer_id, manuscript_id, False, alternative))

    return AuditResult(
        perfect_match_exists=True,
        pairs=entries,
        max_matching_size=max_size,
    )



# ---------------------------------------------------------------------------
# POST /repair：最小成本补边修复
# ---------------------------------------------------------------------------
#
# 建模
# ----
# 原图边（allowed_pairings）成本为 0；候选边成本为各自的正整数 cost。
# 任一完美匹配的成本 = 其中候选边的成本之和，故「最小总成本的补边方案」
# 等价于合并图上的最小费用完美匹配，实际被补入的边即该匹配使用的候选边。
#
# 字典序平局裁决（成本相同时，按新增配对 (reviewer_id, manuscript_id)
# 排序后的列表作字典序择优）
# ---------------------------------------------------------------------
# 该顺序无法用「每条边一个独立权」的单目标权值编码：同时有
#   {e0} ≺ {e1,e2}（首位更小者胜，即使边数更多）
#   {e0}  ≺ {e0,e1}（短表是长表前缀时，边数更少者胜）
# 前一条要求小边的「入选奖励」压过其后所有边之和，后一条又要求多入选边
# 必须付出正代价——任何固定边权都不能两全。故采用前缀贪心：
#
#   P 为已确定入选的候选边（按 ID 升序的前缀）。每轮：
#   1. 若仅靠原图边 + 强制 P 即可完美匹配（Hopcroft–Karp 判定），
#      则 P 已构成某个最优方案，立即终止（短表优先）；
#   2. 否则在「P 强制、编号早于扫描位置的候选边禁用」的图上求一个
#      最优匹配 M，取 M 使用的最小候选边 t 并入 P，扫描位置推进到 t 之后。
#
# 正确性（为什么 t 恰为最小可延伸边）
# ----------------------------------
# 最优匹配在真实费用之外叠加「超递增入选奖励」扰动：候选边 e_i
# （按 (reviewer_id, manuscript_id) 升序编号 0..K-1）权值为
#       cost_i · 2^K − 2^(K-1-i)
# 总扰幅 < 2^K，故首目标仍是真实费用；在同费用匹配中，扰动最优者
# 「尽可能在最小编号位上入选」。若存在 s<t 能延伸前缀（强制 P∪{s}
# 仍有费用 OPT 的匹配 N），则 N 含 s 而 M 不含任何编号 <t 的候选边，
# 由超递增性 pert(N) < pert(M)，与 M 的扰动最优矛盾；故最小的可延伸边
# 恰为 t。而 M 自身又证明强制 P∪{t} 可行（其用到的其余候选边编号均 ≥t，
# 在该轮允许范围内）。每轮新增一条边，轮数不超过 n。
#
# 费用匹配用 Johnson 初始势 + Dijkstra 的连续最短路（SSP）自行实现：
# 前向网络 S→评审人→稿件→T 是三层 DAG，初始势直接按层松弛得到，
# 扰动产生的负边权亦被势函数消解为非负折算费用；全部运算为精确整数。

import heapq


@dataclass
class RepairResult:
    """算法层修复结果（成功时）。"""

    added: list[tuple[str, str, int]]
    total_cost: int
    #: 补入后完整图上的审计结果（必然存在完美匹配）。
    audit: AuditResult


class _NoPerfectMatching(Exception):
    """费用网络中不存在铺满全部评审人的完美匹配。"""


def _add_flow_edge(graph: list[list[list]], u: int, v: int, weight: int) -> None:
    """在邻接表网络中加入容量 1 的前向边及其容量 0 的反向残余边。"""
    forward = [v, len(graph[v]), 1, weight]
    backward = [u, len(graph[u]), 0, -weight]
    graph[u].append(forward)
    graph[v].append(backward)


def _min_cost_matching_ssp(
    free_reviewers: list[str],
    free_manuscripts: list[str],
    weighted_edges: list[tuple[str, str, int]],
) -> list[tuple[str, str]]:
    """三层网络上的最小费用完美匹配（SSP + Johnson 势 + Dijkstra）。

    ``weighted_edges`` 为 ``(reviewer_id, manuscript_id, 整数权)``；
    返回被选中的边列表。权允许为负（精确整数），不存在完美匹配时抛
    :class:`_NoPerfectMatching`。
    """
    nr = len(free_reviewers)
    nm = len(free_manuscripts)
    if nr != nm:
        raise _NoPerfectMatching

    # 节点编号：0 = S；评审人 1..nr；稿件 nr+1..nr+nm；T = nr+nm+1。
    r_index = {r: i + 1 for i, r in enumerate(free_reviewers)}
    m_index = {m: nr + 1 + i for i, m in enumerate(free_manuscripts)}
    source = 0
    sink = nr + nm + 1
    node_count = sink + 1
    graph: list[list[list]] = [[] for _ in range(node_count)]

    for r in free_reviewers:
        _add_flow_edge(graph, source, r_index[r], 0)
    for m in free_manuscripts:
        _add_flow_edge(graph, m_index[m], sink, 0)

    # 记录前向评审人→稿件边及其在邻接表中的下标，便于最终抽取匹配。
    forward_refs: list[tuple[int, int, str, str]] = []
    for r, m, weight in weighted_edges:
        u = r_index[r]
        v = m_index[m]
        ei = len(graph[u])
        _add_flow_edge(graph, u, v, weight)
        forward_refs.append((u, ei, r, m))

    # 初始势：前向网络为 S→R(权0)→M(权可能为负)→T(权0) 的三层 DAG，
    # 按层做一次最短路径松弛即得各节点最短距离，作为 Johnson 初始势。
    potential: list[int | None] = [None] * node_count
    potential[source] = 0
    for r in free_reviewers:
        potential[r_index[r]] = 0
    for u, ei, _r, _m in forward_refs:
        weight = graph[u][ei][3]
        v = graph[u][ei][0]
        cand = potential[u] + weight
        if potential[v] is None or cand < potential[v]:
            potential[v] = cand
    sink_pot: int | None = None
    for m in free_manuscripts:
        p = potential[m_index[m]]
        if p is not None and (sink_pot is None or p < sink_pot):
            sink_pot = p
    potential[sink] = sink_pot

    # SSP：每轮以非负折算费用做 Dijkstra，增广一条单位流。
    for _ in range(nr):
        dist: list[int | None] = [None] * node_count
        parent_node = [-1] * node_count
        parent_edge = [-1] * node_count
        dist[source] = 0
        heap: list[tuple[int, int]] = [(0, source)]
        while heap:
            d, u = heapq.heappop(heap)
            if dist[u] is not None and d > dist[u]:
                continue
            pu = potential[u]
            for ei, edge in enumerate(graph[u]):
                v, _rev, cap, weight = edge
                if cap == 0 or potential[v] is None or pu is None:
                    continue
                reduced = weight + pu - potential[v]  # 折算费用恒非负。
                nd = d + reduced
                if dist[v] is None or nd < dist[v]:
                    dist[v] = nd
                    parent_node[v] = u
                    parent_edge[v] = ei
                    heapq.heappush(heap, (nd, v))
        if dist[sink] is None:
            raise _NoPerfectMatching
        for v in range(node_count):
            if dist[v] is not None and potential[v] is not None:
                potential[v] += dist[v]

        # 沿父指针增广单位流（单容量网络，瓶颈恒为 1）。
        v = sink
        while v != source:
            u = parent_node[v]
            ei = parent_edge[v]
            edge = graph[u][ei]
            edge[2] -= 1
            graph[v][edge[1]][2] += 1
            v = u

    # 前向边容量已由 1 变 0 者，即匹配选中的评审人→稿件边。
    chosen: list[tuple[str, str]] = []
    for u, ei, r, m in forward_refs:
        if graph[u][ei][2] == 0:
            chosen.append((r, m))
    return chosen


def _original_only_feasible(
    reviewers: list[str],
    manuscripts: list[str],
    zero_edges: list[tuple[str, str]],
    used_reviewers: set[str],
    used_manuscripts: set[str],
) -> bool:
    """仅靠原图边（费用 0）+ 已强制占用节点，剩余部分是否可完美匹配。"""
    free_reviewers = [r for r in reviewers if r not in used_reviewers]
    free_manuscripts = [m for m in manuscripts if m not in used_manuscripts]
    if len(free_reviewers) != len(free_manuscripts):
        return False
    if not free_reviewers:
        return True
    # 只保留两端都自由的边：若把指向已占用节点的边也传入，build_graph
    # 会为缺失端点创建无 side 属性的节点，导致匹配基数虚高、误判可行。
    free_edges = [
        (r, m)
        for r, m in zero_edges
        if r in free_reviewers and m in free_manuscripts
    ]
    graph = build_graph(free_reviewers, free_manuscripts, free_edges)
    return _max_matching_size(graph) == len(free_reviewers)


def run_repair(
    reviewers: list[str],
    manuscripts: list[str],
    pairings: list[tuple[str, str]],
    candidates: list[tuple[str, str, int]],
):
    """求最小成本补边方案；不可行时返回合并图的 409 审计结果。

    - 原图已存在完美匹配：直接审计原图（补边为空、成本 0）；
    - 合并全部候选仍不可行：返回合并图的 ``run_audit`` 失败结果
      （调用方据此复用 Hall 证人）；
    - 否则返回 :class:`RepairResult`。
    """
    candidate_edges = [(r, m) for r, m, _ in candidates]

    # 1) 原图已可行：成本 0 的空补充；assignment 完全沿用 /audit 规则。
    original_audit = run_audit(reviewers, manuscripts, pairings)
    if original_audit.perfect_match_exists:
        return RepairResult(added=[], total_cost=0, audit=original_audit)

    merged = [*pairings, *candidate_edges]

    # 2) 合并全部候选仍不可行：对合并图取 Hall 证人，解释残留缺口。
    merged_audit = run_audit(reviewers, manuscripts, merged)
    if not merged_audit.perfect_match_exists:
        return merged_audit

    # 3) 前缀贪心（候选边按 (reviewer_id, manuscript_id) 升序编号）。
    ordered = sorted(candidate_edges, key=lambda edge: edge)
    k_total = len(ordered)
    scale = 1 << k_total  # 2^K：总扰幅 < scale，故首目标恒为真实费用。
    cost_lookup = {(r, m): c for r, m, c in candidates}

    prefix: list[tuple[str, str]] = []
    scan = 0
    while True:
        used_reviewers = {r for r, _ in prefix}
        used_manuscripts = {m for _, m in prefix}

        # 3.1 短表优先：仅原图边 + 强制 P 即可行 => P 已是最优方案。
        if _original_only_feasible(
            reviewers, manuscripts, pairings, used_reviewers, used_manuscripts
        ):
            break

        free_reviewers = [r for r in reviewers if r not in used_reviewers]
        free_manuscripts = [m for m in manuscripts if m not in used_manuscripts]
        free_r_set = set(free_reviewers)
        free_m_set = set(free_manuscripts)

        # 3.2 在 P 强制、编号 < scan 的候选边禁用的自由子图上，求
        #     （真实费用，超递增入选奖励）字典序最优匹配 M。
        weighted: list[tuple[str, str, int]] = []
        for r, m in pairings:
            if r in free_r_set and m in free_m_set:
                weighted.append((r, m, 0))
        for index, edge in enumerate(ordered):
            if index < scan:
                continue
            r, m = edge
            if r in free_r_set and m in free_m_set:
                reward = 1 << (k_total - 1 - index)
                weighted.append((r, m, cost_lookup[edge] * scale - reward))

        matching = set(
            _min_cost_matching_ssp(free_reviewers, free_manuscripts, weighted)
        )

        # M 使用的最小候选边即下一条必入前缀（正确性见模块文档）。
        next_indices = [
            index
            for index, edge in enumerate(ordered)
            if index >= scan and edge in matching
        ]
        # 自由部分只用原图边时，3.1 已应终止；故此处必能取到候选边。
        assert next_indices, "内部错误：前缀贪心无法继续"
        t_index = next_indices[0]
        prefix.append(ordered[t_index])
        scan = t_index + 1

    # 4) 对补入后的完整图执行与 /audit 完全一致的审计（字典序最小分配）。
    total_cost = sum(cost_lookup[edge] for edge in prefix)
    added = [(r, m, cost_lookup[(r, m)]) for r, m in prefix]
    return RepairResult(
        added=added,
        total_cost=total_cost,
        audit=run_audit(reviewers, manuscripts, [*pairings, *prefix]),
    )
