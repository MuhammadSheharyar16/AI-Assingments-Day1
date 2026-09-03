"""
Day 6 Task 1 — the typed FastAPI service.

Exposes `POST /ask` over the Day 5 grounded RAG pipeline
(`aico.rag.answer_service.GroundedAnswerService`). This module never
reimplements retrieval/generation itself (working rule) - it only:

    1. accepts a public, typed `AskRequest` (contracts.py)
    2. calls `GroundedAnswerService.answer()` (Day 5, unchanged) via an
       injected dependency (dependencies.py, Task 10)
    3. maps the typed `AnswerResult` onto the public `AskResponse`
       (contracts.py's `ask_response_from_result`)

Request/correlation ID generation here is a Task 1 placeholder (every
call gets a fresh server-generated UUID) - Task 3 replaces this with
header-aware logic (accept a caller-supplied ID, generate one only when
absent) and threads the correlation ID through logs/spans. Identity
(Task 2), Content-Type/size limits and the shared error envelope (Task 4),
cancellation propagation (Task 5) and health endpoints (Task 6) are not
yet wired - each lands in its own task rather than being front-loaded
here.
"""
from __future__ import annotations

import uuid

from fastapi import Depends, FastAPI

from aico.api.contracts import AskRequest, AskResponse, ask_response_from_result
from aico.api.dependencies import get_answer_service
from aico.rag.answer_service import GroundedAnswerService

app = FastAPI(
    title="AICO Grounded RAG API",
    version="1.0",
    description=(
        "Typed HTTP boundary over the Day 5 grounded retrieval-augmented "
        "answer pipeline. See api_contract_guidance.md for the contract "
        "boundary this service maintains."
    ),
)


@app.post(
    "/ask",
    response_model=AskResponse,
    summary="Ask a grounded question",
    tags=["ask"],
)
def ask(
    request: AskRequest,
    service: GroundedAnswerService = Depends(get_answer_service),
) -> AskResponse:
    request_id = str(uuid.uuid4())
    correlation_id = str(uuid.uuid4())

    result = service.answer(request.question)

    return ask_response_from_result(result, request_id=request_id, correlation_id=correlation_id)
