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
absent) and threads the correlation ID through logs/spans. Content-Type/
size limits and the shared error envelope (Task 4), cancellation
propagation (Task 5) and health endpoints (Task 6) are not yet wired -
each lands in its own task rather than being front-loaded here.

Task 2 IS wired: `get_trusted_identity` is a required dependency on
`POST /ask`, so an untrusted caller never reaches `GroundedAnswerService`
(see identity.py). The `IdentityError` exception handler below is a Task 2
stopgap plain-401 response - Task 4 folds it into the shared error
envelope every other 4xx/5xx uses.
"""
from __future__ import annotations

import uuid

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from aico.api.contracts import AskRequest, AskResponse, ask_response_from_result
from aico.api.dependencies import get_answer_service
from aico.api.identity import IdentityError, TrustedIdentity, get_trusted_identity
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


@app.exception_handler(IdentityError)
def _identity_error_handler(_request: Request, exc: IdentityError) -> JSONResponse:
    # Task 2 stopgap: 401, safe reason only - never the token, the secret,
    # or a raw library exception (identity.py already guarantees
    # exc.reason is safe to return). Task 4 replaces this body shape with
    # the shared error envelope every 4xx/5xx response uses.
    return JSONResponse(status_code=401, content={"error_code": "trusted_identity_rejected", "message": exc.reason})


@app.post(
    "/ask",
    response_model=AskResponse,
    summary="Ask a grounded question",
    tags=["ask"],
)
def ask(
    request: AskRequest,
    identity: TrustedIdentity = Depends(get_trusted_identity),
    service: GroundedAnswerService = Depends(get_answer_service),
) -> AskResponse:
    request_id = str(uuid.uuid4())
    correlation_id = str(uuid.uuid4())

    # `identity` is required for this call to reach here at all (an
    # untrusted caller was already rejected by the dependency above) but
    # is not yet threaded into the pipeline/response - Day 5's
    # GroundedAnswerService has no multi-tenant retrieval to scope by
    # tenant_id, and the response contract intentionally never echoes
    # authorization context back to the caller (Day 6 rule). Structured
    # logging (Task 7) and tracing (Task 9) are where `identity` becomes
    # observable - as sanitized tenant/user identifiers, never raw claims.
    del identity

    result = service.answer(request.question)

    return ask_response_from_result(result, request_id=request_id, correlation_id=correlation_id)
