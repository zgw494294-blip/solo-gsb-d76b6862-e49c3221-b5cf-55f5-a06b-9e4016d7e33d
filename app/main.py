"""本地数据导出风险审查 API。"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException

from .analysis import build_response, review_request, sensitive_sources
from .models import ReviewRequest, ReviewResponse

app = FastAPI(
    title="Data Export Risk Review",
    version="1.0.0",
    description="按依赖关系审查导出节点：内容来源或行影响含敏感来源即判阻断。",
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/review", response_model=ReviewResponse)
def review(req: ReviewRequest) -> ReviewResponse:
    errors, infos = review_request(req)
    if errors:
        raise HTTPException(status_code=422, detail=errors)
    return build_response(req, infos, sensitive_sources(req))
