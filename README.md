# 匿名评审分配可辨识性审计 API

给定评审人、稿件及二者之间的允许配对（二部图），本服务：

1. 判断是否存在**完美匹配**（每位评审人恰好评审一篇稿件，每篇稿件恰好被评审一次）；
2. 若存在，返回一个**确定性分配**：按评审人 ID 升序排序后，分配序列字典序最小
   （未匹配位置视为 `null` 且小于任何稿件 ID；完美匹配下所有评审人均被匹配）；
3. 对该分配中的**每一对**判断其是否为**强制配对**——即在所有完美匹配中都必然被选择；
4. 对每个非强制配对，附一组**完整的替代分配**：它本身也是完美匹配，且不包含被审计的这对边。

此外，`POST /repair` 在原图不存在完美匹配时，允许**付费补入候选配对**：

5. 仅从 `candidate_pairings` 中挑选边补入，求使（补入后的图）存在完美匹配的
   **最小总成本**方案；成本相同时，按新增配对 `(reviewer_id, manuscript_id)`
   排序后的列表作**字典序择优**；
6. 成功返回 `added_pairings`、`total_cost` 以及补入后完整图上的 `assignment`
   （规则与 `/audit` 完全一致）；原图已可行时返回成本 `0` 的空补充；
   合并全部候选仍不可行时返回 `409`，并在**合并图**上给出 Hall 定理证人。

- 存在完美匹配 → `200`
- 不存在完美匹配 → `409`，返回基数最大的匹配、未匹配的评审人/稿件 ID，
  以及一份 **Hall 定理证人**（`hall_witness`），**不返回部分成功**
  （`/repair` 仅在「合并全部候选边后仍不可行」时返回 409）
- 重复 ID、重复配对、未知引用、数量不等、空列表、候选边与原边重叠、
  候选成本非正整数等输入问题 → `422`，错误定位到具体字段与下标

技术栈：Python · FastAPI · Pydantic v2 · NetworkX（Hopcroft–Karp + 交替有向图强连通分量）。

---

## 单条命令启动（依赖随容器安装）

```bash
docker compose up --build
```

服务地址（监听 **8000** 端口）：

- API 根地址：<http://localhost:8000/>
- 审计端点：`POST http://localhost:8000/audit`
- 修复端点：`POST http://localhost:8000/repair`
- 交互式文档（Swagger UI）：<http://localhost:8000/docs>
- OpenAPI 模式：<http://localhost:8000/openapi.json>
- 健康检查：<http://localhost:8000/healthz>

> 若使用不带 compose 子命令的旧版 Docker，请改用 `docker-compose up --build`。
> 容器镜像基于 `python:3.11-slim`，构建时通过 `requirements.txt` 安装全部依赖，无需本机准备 Python 环境。

后台运行：`docker compose up --build -d`；停止：`docker compose down`。

---

## 请求格式

`POST /audit`，`Content-Type: application/json`：

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `reviewers` | `string[]` | 非空、无重复、无空字符串 |
| `manuscripts` | `string[]` | 非空、无重复、无空字符串，数量与 `reviewers` 相等 |
| `allowed_pairings` | `{reviewer_id, manuscript_id}[]` | 非空、无重复配对；引用的 ID 必须分别存在于上面两个列表 |

额外字段会被拒绝（422）。

### 示例请求

```bash
curl -s -X POST http://localhost:8000/audit \
  -H 'Content-Type: application/json' \
  -d '{
    "reviewers": ["r0", "r1", "r2"],
    "manuscripts": ["m0", "m1", "m2"],
    "allowed_pairings": [
      {"reviewer_id": "r0", "manuscript_id": "m0"},
      {"reviewer_id": "r0", "manuscript_id": "m1"},
      {"reviewer_id": "r1", "manuscript_id": "m0"},
      {"reviewer_id": "r1", "manuscript_id": "m1"},
      {"reviewer_id": "r2", "manuscript_id": "m2"}
    ]
  }'
```

### 200 响应（存在完美匹配）

```json
{
  "perfect_match_exists": true,
  "assignment": [
    {
      "reviewer_id": "r0",
      "manuscript_id": "m0",
      "forced": false,
      "alternative": [
        {"reviewer_id": "r0", "manuscript_id": "m1"},
        {"reviewer_id": "r1", "manuscript_id": "m0"},
        {"reviewer_id": "r2", "manuscript_id": "m2"}
      ]
    },
    {
      "reviewer_id": "r1",
      "manuscript_id": "m1",
      "forced": false,
      "alternative": [
        {"reviewer_id": "r0", "manuscript_id": "m1"},
        {"reviewer_id": "r1", "manuscript_id": "m0"},
        {"reviewer_id": "r2", "manuscript_id": "m2"}
      ]
    },
    {
      "reviewer_id": "r2",
      "manuscript_id": "m2",
      "forced": true,
      "alternative": null
    }
  ]
}
```

字段含义：

- `assignment` 按 `reviewer_id` 升序排列，为字典序最小的确定性完美匹配；
- `forced=true` 表示该对出现在**所有**完美匹配中（评审身份可辨识/无法匿名化），此时 `alternative` 为 `null`；
- `forced=false` 时 `alternative` 给出一组**完整替代分配**（覆盖全部评审人），
  它是另一个合法完美匹配且不含当前这对边——证明该配对不是强制的。

上例中 `r2–m2` 是唯一可行选择，故为强制配对；`r0/r1` 可互换，故二者均非强制。

### 409 响应（不存在完美匹配）

```json
{
  "perfect_match_exists": false,
  "detail": "不存在完美匹配；以下为基数最大的匹配、未匹配双方及 Hall 定理证人。",
  "maximum_matching": [
    {"reviewer_id": "r0", "manuscript_id": "m0"},
    {"reviewer_id": "r1", "manuscript_id": "m1"}
  ],
  "unmatched_reviewer_ids": ["r2"],
  "unmatched_manuscript_ids": ["m2"],
  "hall_witness": {
    "reviewer_ids": ["r2"],
    "neighboring_manuscript_ids": [],
    "deficiency": 1
  }
}
```

`maximum_matching` 为完整展开的基数最大匹配（确定性规则：同样按评审人 ID 升序、
字典序最小），并分别给出两侧无法匹配的 ID。响应不包含 `assignment`，
不存在“部分成功”。

`hall_witness` 是 **Hall 定理证人**——一组可评稿件总数少于人数的评审人，
直接解释了为何不可能完美分配：

- `reviewer_ids`：违反 Hall 条件的评审人子集 S（按 ID 升序，非空）；
- `neighboring_manuscript_ids`：S 在原 `allowed_pairings` 中的**全部**邻居
  （按 ID 升序，可为空）——不多不少，恰为这群评审人所有可评的稿件；
- `deficiency`：`|S| − |N(S)|`，恒等于全局未匹配评审人数
  （即 `unmatched_reviewer_ids` 的长度）。

证人由确定性最大匹配出发、沿交替路径构造：从未匹配评审人开始，
沿「非匹配边 评审人→稿件、匹配边 稿件→评审人」遍历所有可达节点，
其中的评审人即 S、稿件即 N(S)。相同输入必得相同证人。

一个非平凡例子（`r0` 未匹配，但它自己有两个邻居，单看并不违规；
沿交替路径可达 `r1`、`r2` 后才暴露出 3 人共享 2 篇稿件的缺口）：

```bash
curl -s -X POST http://localhost:8000/audit \
  -H 'Content-Type: application/json' \
  -d '{
    "reviewers": ["r0", "r1", "r2"],
    "manuscripts": ["m0", "m1", "m2"],
    "allowed_pairings": [
      {"reviewer_id": "r0", "manuscript_id": "m0"},
      {"reviewer_id": "r0", "manuscript_id": "m1"},
      {"reviewer_id": "r1", "manuscript_id": "m0"},
      {"reviewer_id": "r2", "manuscript_id": "m1"}
    ]
  }'
```

```json
{
  "perfect_match_exists": false,
  "detail": "不存在完美匹配；以下为基数最大的匹配、未匹配双方及 Hall 定理证人。",
  "maximum_matching": [
    {"reviewer_id": "r1", "manuscript_id": "m0"},
    {"reviewer_id": "r2", "manuscript_id": "m1"}
  ],
  "unmatched_reviewer_ids": ["r0"],
  "unmatched_manuscript_ids": ["m2"],
  "hall_witness": {
    "reviewer_ids": ["r0", "r1", "r2"],
    "neighboring_manuscript_ids": ["m0", "m1"],
    "deficiency": 1
  }
}
```

### 422 响应（输入校验失败）

错误以数组形式返回于 `detail`，每条含 `type` / `loc` / `msg` / `input`，
`loc` 精确定位到字段与列表下标，例如重复配对：

```json
{
  "detail": [
    {
      "type": "duplicate_pairing",
      "loc": ["body", "allowed_pairings", 1],
      "msg": "配对重复：('r0', 'm0')",
      "input": {"reviewer_id": "r0", "manuscript_id": "m0"}
    },
    {
      "type": "unknown_reviewer",
      "loc": ["body", "allowed_pairings", 2, "reviewer_id"],
      "msg": "未知评审人引用：'ghost' 不在 reviewers 列表中",
      "input": "ghost"
    }
  ]
}
```

| 错误类型 | 触发条件 | `loc` 定位 |
| --- | --- | --- |
| `count_mismatch` | 评审人与稿件数量不等 | `body.manuscripts` |
| `duplicate_reviewer_id` | 评审人 ID 重复 | `body.reviewers.<下标>` |
| `duplicate_manuscript_id` | 稿件 ID 重复 | `body.manuscripts.<下标>` |
| `duplicate_pairing` | 允许配对重复 | `body.allowed_pairings.<下标>` |
| `unknown_reviewer` | 配对引用了不存在的评审人 | `body.allowed_pairings.<下标>.reviewer_id` |
| `unknown_manuscript` | 配对引用了不存在的稿件 | `body.allowed_pairings.<下标>.manuscript_id` |
| `missing` / `string_too_short` | 字段缺失、列表为空（Pydantic 内建） | 对应字段 |
| `value_error` | ID 为空串/纯空白（Pydantic 内建） | 对应字段 |

一次请求会聚合返回所有可定位的错误。

---

## 修复端点 `POST /repair`

在 `/audit` 的三个字段之外，再提供一组**候选配对** `candidate_pairings`，
每条候选是 `{reviewer_id, manuscript_id, cost}`，其中 `cost` 为**正整数**。
服务只允许从候选中补入边（原图边不产生费用），求使补入后的图存在完美匹配的
**最小总成本**方案：

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `reviewers` | `string[]` | 同 `/audit` |
| `manuscripts` | `string[]` | 同 `/audit` |
| `allowed_pairings` | `{reviewer_id, manuscript_id}[]` | 同 `/audit`：原图边（费用视为 0） |
| `candidate_pairings` | `{reviewer_id, manuscript_id, cost}[]` | 可为空列表但字段必填；`cost` 为正整数；不得重复、不得与原边重叠、引用 ID 必须存在，额外字段拒绝 |

平局规则：总成本相同时，按实际新增配对 `(reviewer_id, manuscript_id)`
**排序后的列表**作字典序取最优（首位更小者优先；短表是长表前缀时短表优先）。

### 示例请求

原图中 `r2` 没有任何允许配对，因此 `/audit` 会返回 409；提供候选边后请求修复：

```bash
curl -s -X POST http://localhost:8000/repair \
  -H 'Content-Type: application/json' \
  -d '{
    "reviewers": ["r0", "r1", "r2"],
    "manuscripts": ["m0", "m1", "m2"],
    "allowed_pairings": [
      {"reviewer_id": "r0", "manuscript_id": "m0"},
      {"reviewer_id": "r1", "manuscript_id": "m1"}
    ],
    "candidate_pairings": [
      {"reviewer_id": "r2", "manuscript_id": "m2", "cost": 5},
      {"reviewer_id": "r2", "manuscript_id": "m1", "cost": 1},
      {"reviewer_id": "r1", "manuscript_id": "m2", "cost": 2}
    ]
  }'
```

### 200 响应（最小成本修复成功）

```json
{
  "perfect_match_exists": true,
  "added_pairings": [
    {"reviewer_id": "r1", "manuscript_id": "m2", "cost": 2},
    {"reviewer_id": "r2", "manuscript_id": "m1", "cost": 1}
  ],
  "total_cost": 3,
  "assignment": [
    {"reviewer_id": "r0", "manuscript_id": "m0", "forced": true, "alternative": null},
    {"reviewer_id": "r1", "manuscript_id": "m2", "forced": true, "alternative": null},
    {"reviewer_id": "r2", "manuscript_id": "m1", "forced": true, "alternative": null}
  ]
}
```

- 单补 `(r2,m2)` 需成本 5；而 `(r1→m2, r2→m1)` 腾挪总成本 3，故取后者；
- `added_pairings` 按 `(reviewer_id, manuscript_id)` 升序，含各自支付的成本，
  `total_cost` 为其成本之和；
- `assignment` 是在「原图边 + 实际新增边」构成的图上，按与 `/audit`
  **完全相同**的字典序最小规则求出的完整完美匹配（并逐对标注强制性）。
  未被选中的候选边并未补入，因此每条新增边在该图的所有完美匹配中都会被使用，
  上例中三对均为 `forced=true`。

**原图已可行**时不补入任何候选边（即使存在更便宜的边），
返回 `"added_pairings": []`、`"total_cost": 0`，
且 `assignment` 与对同一输入调用 `/audit` 的结果逐字段一致。

### 平局字典序示例

把上例中 `(r2,m2)` 的成本改为 `3`，则两条修复路径成本同为 3：

- 单条 `[(r2, m2)]`；
- 双条 `[(r1, m2), (r2, m1)]`（排序后首元素 `(r1,m2) < (r2,m2)`）。

按「排序后列表的字典序」，双条方案胜出，响应中的 `added_pairings` 为
`[("r1","m2"), ("r2","m1")]`、`total_cost` 为 `3`。

### 409 响应（合并全部候选仍不可行）

若所有候选边都补入后仍不存在完美匹配，返回 `409`，结构与 `/audit` 的
409 完全一致，但最大匹配与 `hall_witness` 都在**合并图**（原图边 + 全部候选边）
上构造——证人评审人的可评稿件缺口即「付费也无法修复」之处：

```json
{
  "perfect_match_exists": false,
  "detail": "合并全部候选配对后仍不存在完美匹配；以下为合并图上基数最大的匹配、未匹配双方及 Hall 定理证人，证人评审人的可评稿件缺口即无法修复之处。",
  "maximum_matching": [
    {"reviewer_id": "r0", "manuscript_id": "m0"},
    {"reviewer_id": "r1", "manuscript_id": "m1"}
  ],
  "unmatched_reviewer_ids": ["r2"],
  "unmatched_manuscript_ids": ["m2"],
  "hall_witness": {
    "reviewer_ids": ["r2"],
    "neighboring_manuscript_ids": [],
    "deficiency": 1
  }
}
```

### 422 响应（候选输入校验失败）

候选相关的语义错误同样聚合返回并精确定位：

| 错误类型 | 触发条件 | `loc` 定位 |
| --- | --- | --- |
| `duplicate_candidate_pairing` | 候选配对彼此重复 | `body.candidate_pairings.<下标>` |
| `overlaps_existing_pairing` | 候选配对已存在于 `allowed_pairings`（无需补入） | `body.candidate_pairings.<下标>` |
| `unknown_reviewer` / `unknown_manuscript` | 候选引用了不存在的 ID | `body.candidate_pairings.<下标>.reviewer_id` / `.manuscript_id` |
| `non_positive_cost` | `cost` ≤ 0（合法整数但非正数） | `body.candidate_pairings.<下标>.cost` |
| `int_type` / `value_error` | `cost` 不是整数（如 `1.5`、`"7"`、布尔值 `true/false`）或字段缺失（Pydantic 内建，严格整数） | `body.candidate_pairings.<下标>.cost` |

`/audit` 的全部错误类型（数量不等、重复 ID 等）在 `/repair` 上同样适用。

---

## 算法说明

- **最大匹配 / 完美匹配判定**：NetworkX 的 Hopcroft–Karp
  （`networkx.bipartite.hopcroft_karp_matching`），二部图两侧节点用
  `("reviewer", id)`、`("manuscript", id)` 区分，ID 同名也不冲突。
- **字典序最小确定性分配**：按评审人 ID 升序贪心，每位评审人优先不匹配
  （`null` 最小），否则在候选稿件中按 ID 升序选择「仍能在剩余子图达成目标基数」者；
  每步以一次 Hopcroft–Karp 作可行性预言，因此结果对相同输入完全确定。
- **强制性判定**：对完美匹配 M 构造交替有向图（匹配边 `稿件→评审人`，非匹配边
  `评审人→稿件`）。由 Berge 交替环定理，匹配边非强制 ⇔ 它位于某个 M-交替环上
  ⇔ 两端在同一强连通分量中；一次 SCC 分解（`networkx.strongly_connected_components`）
  即可求出全部强制边。
- **替代分配**：对非强制边，在交替有向图中用邻接有序的 BFS 找最短交替环，
  环上匹配/非匹配边身份翻转，得到一个完整、合法、确定性且不含被审计边的完美匹配。
- **Hall 定理证人（409）**：取字典序最小的最大匹配 M，从未匹配评审人出发，
  在同一交替有向图中遍历可达节点集 Z。Z 中的稿件必已全部匹配
  （否则存在增广路，与最大性矛盾），故取 S = Z ∩ 评审人时，
  N(S) 恰为 Z ∩ 稿件，且 `|S| − |N(S)|` = 未匹配评审人数 = 全局缺口
  （Kőnig–Ore 定理）。源点与邻接均按 ID 排序，可达集与遍历顺序无关，
  证人对相同输入完全确定。
- **最小成本修复（/repair）**：原图边费用为 0、候选边费用为正整数，
  最小总成本方案即合并图上的最小费用完美匹配（实现为三层流网络上的
  Johnson 初始势 + Dijkstra 连续最短路，全程精确整数，无需 SciPy）。
  成本相同时的「按新增配对排序列表取字典序」无法用单一边权编码
  （小边优先与「短表优先」方向相反），故采用前缀贪心：每轮先用
  Hopcroft–Karp 判定「仅原图边 + 已选前缀」是否已可行（可行即按短表终止），
  否则在候选边上叠加超递增入选奖励权（`cost·2^K − 2^(K-1-i)`）求一次
  费用匹配，其使用的最小候选边即下一条必入边；轮数不超过评审人数。
  最终在补入后的完整图上复用上述字典序最小分配 + SCC 强制性审计。

适用规模：典型会议量级（两侧各 ≤100、每人若干候选）亚秒级完成；
数百规模也可在数秒级返回。

## 项目结构

```
app/
  __init__.py
  main.py        # FastAPI 路由（/audit、/repair）、422/409 语义校验与响应组装
  matching.py    # 二部图、确定性匹配、SCC 强制性、交替环替代分配、Hall 证人、
                 # 最小费用修复（SSP + 超递增扰动前缀贪心）
  schemas.py     # Pydantic 请求/响应模型
tests/
  test_api.py       # /audit HTTP 层：200/409/422、Hall 证人与错误定位
  test_matching.py  # /audit 算法层：200 组随机图全排列暴力交叉验证 + Hall 不变量
  test_repair.py    # /repair 算法层：400 组随机图暴力交叉验证（成本/字典序/Hall）
  test_repair_api.py# /repair HTTP 层：最小成本、平局、409、422 精确定位
Dockerfile
docker-compose.yml
requirements.txt
```

## 本地（非 Docker）开发与测试

```bash
python3 -m pip install -r requirements.txt
python3 -m pip install pytest httpx
python3 -m pytest -q
uvicorn app.main:app --reload --port 8000
```
