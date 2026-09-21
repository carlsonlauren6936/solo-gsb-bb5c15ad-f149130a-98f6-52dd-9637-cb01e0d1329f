"""FastAPI 应用：POST /audit 与 POST /repair。

- 200：存在完美匹配（/repair 时为最小成本补边后的完整图），返回字典序
  最小的确定性分配及逐对强制性。
- 409：不存在完美匹配，返回最大匹配、未匹配双方 ID 与 Hall 定理证人
  （无部分成功）。/repair 仅在「合并全部候选边后仍不可行」时返回 409，
  Hall 证人在合并图上构造。
- 422：重复 ID、重复配对、未知引用、数量不等、空列表、候选边与原边重叠、
  成本非正整数等，错误定位到具体字段/下标。
"""

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .matching import RepairResult, run_audit, run_repair
from .schemas import (
    AssignedPair,
    AddedPairing,
    AuditRequest,
    AuditResponse,
    ConflictResponse,
    HallWitness,
    OutputPair,
    RepairRequest,
    RepairResponse,
)

app = FastAPI(
    title="匿名评审分配可辨识性审计 API",
    description=(
        "基于二部图完美匹配，判定每条配对是否在所有完美匹配中被强制选择；"
        "并支持以最小成本补入候选边修复不可行的分配。"
    ),
    version="1.1.0",
)


def _error(
    loc: list[str | int],
    msg: str,
    err_type: str,
    value: object,
) -> dict:
    return {"type": err_type, "loc": ["body", *loc], "msg": msg, "input": value}


@app.exception_handler(RequestValidationError)
async def _validation_handler(request: Request, exc: RequestValidationError):
    # jsonable_encoder 会剔除 Pydantic 放在 ctx 中的不可序列化异常对象。
    return JSONResponse(
        status_code=422,
        content={"detail": jsonable_encoder(exc.errors())},
    )


@app.get("/", include_in_schema=False)
async def root() -> dict:
    return {
        "service": "review-assignment-identifiability-audit",
        "docs": "/docs",
        "endpoints": ["/audit", "/repair"],
    }


@app.get("/healthz", include_in_schema=False)
async def healthz() -> dict:
    return {"status": "ok"}


def _build_assignment(pairs: list) -> list[AssignedPair]:
    """由 run_audit 的成功条目组装 assignment（沿用 /audit 的字典序规则）。"""
    assignment: list[AssignedPair] = []
    for reviewer_id, manuscript_id, forced, alternative in pairs:
        assignment.append(
            AssignedPair(
                reviewer_id=reviewer_id,
                manuscript_id=manuscript_id,
                forced=forced,
                alternative=(
                    None
                    if forced
                    else [
                        OutputPair(reviewer_id=r, manuscript_id=m)
                        for r, m in alternative
                    ]
                ),
            )
        )
    return assignment


def _conflict_response(result, detail: str) -> JSONResponse:
    """由 run_audit/run_repair 的失败结果组装 409（Hall 证人复用）。"""
    witness = result.hall_witness
    # 409 分支保证证人存在；防御性断言。
    assert witness is not None
    conflict = ConflictResponse(
        detail=detail,
        maximum_matching=[
            OutputPair(reviewer_id=r, manuscript_id=m) for r, m in result.pairs
        ],
        unmatched_reviewer_ids=result.unmatched_reviewers,
        unmatched_manuscript_ids=result.unmatched_manuscripts,
        hall_witness=HallWitness(
            reviewer_ids=witness.reviewer_ids,
            neighboring_manuscript_ids=witness.neighboring_manuscript_ids,
            deficiency=witness.deficiency,
        ),
    )
    return JSONResponse(status_code=409, content=conflict.model_dump())


CONFLICT_DETAIL_AUDIT = (
    "不存在完美匹配；以下为基数最大的匹配、未匹配双方及 Hall 定理证人。"
)
CONFLICT_DETAIL_REPAIR = (
    "合并全部候选配对后仍不存在完美匹配；以下为合并图上基数最大的匹配、"
    "未匹配双方及 Hall 定理证人，证人评审人的可评稿件缺口即无法修复之处。"
)


@app.post(
    "/audit",
    response_model=AuditResponse,
    responses={
        409: {"model": ConflictResponse, "description": "不存在完美匹配"},
        422: {"description": "输入校验失败"},
    },
)
async def audit(request: AuditRequest) -> AuditResponse | JSONResponse:
    errors = _validate_semantics(
        request.reviewers, request.manuscripts, request.allowed_pairings
    )
    if errors:
        raise RequestValidationError(errors)

    pairings = [(p.reviewer_id, p.manuscript_id) for p in request.allowed_pairings]
    result = run_audit(
        reviewers=request.reviewers,
        manuscripts=request.manuscripts,
        pairings=pairings,
    )

    if not result.perfect_match_exists:
        return _conflict_response(result, CONFLICT_DETAIL_AUDIT)
    return AuditResponse(assignment=_build_assignment(result.pairs))


@app.post(
    "/repair",
    response_model=RepairResponse,
    responses={
        409: {"model": ConflictResponse, "description": "合并全部候选后仍不可行"},
        422: {"description": "输入校验失败"},
    },
)
async def repair(request: RepairRequest) -> RepairResponse | JSONResponse:
    errors = _validate_semantics(
        request.reviewers, request.manuscripts, request.allowed_pairings
    )
    errors.extend(
        _validate_candidates(
            request.candidate_pairings,
            reviewer_set=set(request.reviewers),
            manuscript_set=set(request.manuscripts),
            allowed_pairs={
                (p.reviewer_id, p.manuscript_id) for p in request.allowed_pairings
            },
        )
    )
    if errors:
        raise RequestValidationError(errors)

    pairings = [(p.reviewer_id, p.manuscript_id) for p in request.allowed_pairings]
    candidates = [
        (p.reviewer_id, p.manuscript_id, p.cost)
        for p in request.candidate_pairings
    ]
    result = run_repair(
        reviewers=request.reviewers,
        manuscripts=request.manuscripts,
        pairings=pairings,
        candidates=candidates,
    )

    # run_repair 失败时返回的即合并图的 AuditResult；Hall 证人原样复用。
    if not isinstance(result, RepairResult):
        return _conflict_response(result, CONFLICT_DETAIL_REPAIR)

    return RepairResponse(
        added_pairings=[
            AddedPairing(reviewer_id=r, manuscript_id=m, cost=c)
            for r, m, c in result.added
        ],
        total_cost=result.total_cost,
        assignment=_build_assignment(result.audit.pairs),
    )


def _validate_semantics(reviewers, manuscripts, allowed_pairings) -> list[dict]:
    """跨字段语义校验（/audit 与 /repair 共用），返回错误列表。"""
    errors: list[dict] = []

    # 评审人与稿件数量必须相等。
    if len(reviewers) != len(manuscripts):
        errors.append(
            _error(
                ["manuscripts"],
                f"评审人与稿件数量必须相等：{len(reviewers)} != {len(manuscripts)}",
                "count_mismatch",
                len(manuscripts),
            )
        )

    # 重复评审人 ID（定位到每个重复出现的位置）。
    seen: set[str] = set()
    for index, reviewer_id in enumerate(reviewers):
        if reviewer_id in seen:
            errors.append(
                _error(
                    ["reviewers", index],
                    f"评审人 ID 重复：{reviewer_id!r}",
                    "duplicate_reviewer_id",
                    reviewer_id,
                )
            )
        seen.add(reviewer_id)

    # 重复稿件 ID。
    seen.clear()
    for index, manuscript_id in enumerate(manuscripts):
        if manuscript_id in seen:
            errors.append(
                _error(
                    ["manuscripts", index],
                    f"稿件 ID 重复：{manuscript_id!r}",
                    "duplicate_manuscript_id",
                    manuscript_id,
                )
            )
        seen.add(manuscript_id)

    reviewer_set = set(reviewers)
    manuscript_set = set(manuscripts)

    # 未知引用与重复配对（每条配对分别定位）。
    seen_pairs: set[tuple[str, str]] = set()
    for index, pairing in enumerate(allowed_pairings):
        if pairing.reviewer_id not in reviewer_set:
            errors.append(
                _error(
                    ["allowed_pairings", index, "reviewer_id"],
                    f"未知评审人引用：{pairing.reviewer_id!r} 不在 reviewers 列表中",
                    "unknown_reviewer",
                    pairing.reviewer_id,
                )
            )
        if pairing.manuscript_id not in manuscript_set:
            errors.append(
                _error(
                    ["allowed_pairings", index, "manuscript_id"],
                    f"未知稿件引用：{pairing.manuscript_id!r} 不在 manuscripts 列表中",
                    "unknown_manuscript",
                    pairing.manuscript_id,
                )
            )
        key = (pairing.reviewer_id, pairing.manuscript_id)
        if key in seen_pairs:
            errors.append(
                _error(
                    ["allowed_pairings", index],
                    f"配对重复：({pairing.reviewer_id!r}, {pairing.manuscript_id!r})",
                    "duplicate_pairing",
                    {"reviewer_id": key[0], "manuscript_id": key[1]},
                )
            )
        seen_pairs.add(key)

    return errors


def _validate_candidates(candidate_pairings, reviewer_set, manuscript_set,
                         allowed_pairs) -> list[dict]:
    """候选配对语义校验：非正成本、重复、与原边重叠、未知引用，逐下标定位。"""
    errors: list[dict] = []
    seen_pairs: set[tuple[str, str]] = set()

    for index, pairing in enumerate(candidate_pairings):
        # 非正整数成本（字段类型/布尔值由 Pydantic strict 校验拦截）。
        if pairing.cost <= 0:
            errors.append(
                _error(
                    ["candidate_pairings", index, "cost"],
                    f"候选配对成本必须为正整数，收到：{pairing.cost}",
                    "non_positive_cost",
                    pairing.cost,
                )
            )

        if pairing.reviewer_id not in reviewer_set:
            errors.append(
                _error(
                    ["candidate_pairings", index, "reviewer_id"],
                    f"未知评审人引用：{pairing.reviewer_id!r} 不在 reviewers 列表中",
                    "unknown_reviewer",
                    pairing.reviewer_id,
                )
            )
        if pairing.manuscript_id not in manuscript_set:
            errors.append(
                _error(
                    ["candidate_pairings", index, "manuscript_id"],
                    f"未知稿件引用：{pairing.manuscript_id!r} 不在 manuscripts 列表中",
                    "unknown_manuscript",
                    pairing.manuscript_id,
                )
            )

        key = (pairing.reviewer_id, pairing.manuscript_id)
        if key in allowed_pairs:
            errors.append(
                _error(
                    ["candidate_pairings", index],
                    f"候选配对与已有允许配对重叠："
                    f"({pairing.reviewer_id!r}, {pairing.manuscript_id!r}) "
                    f"已在 allowed_pairings 中，无需补入",
                    "overlaps_existing_pairing",
                    {"reviewer_id": key[0], "manuscript_id": key[1]},
                )
            )
        if key in seen_pairs:
            errors.append(
                _error(
                    ["candidate_pairings", index],
                    f"候选配对重复：({pairing.reviewer_id!r}, {pairing.manuscript_id!r})",
                    "duplicate_candidate_pairing",
                    {"reviewer_id": key[0], "manuscript_id": key[1]},
                )
            )
        seen_pairs.add(key)

    return errors
