# Day 6 API Contract Guidance

The Day 6 program requires:

- `POST /ask`
- public request/response contracts separate from internal domain models
- trusted tenant/user identity context
- request and correlation IDs
- Content-Type validation
- request-size protection
- consistent safe 4xx/5xx error responses

## Contract boundary

Exact field names are implementation choices unless the Day 6 task states otherwise.

The reviewer should be able to distinguish:

```text
HTTP API Contract
        ↓ mapping
Internal RAG / Domain Contract
```

Do not expose internal Pydantic/domain objects automatically just because they already exist.

## Error behavior

Use one documented public error shape.

It should expose stable, safe information such as:

- error category/code
- safe message
- request ID
- correlation ID

Do not expose:

- stack trace
- raw provider exception
- prompt/model content
- authorization claims
- secrets

## Request-size tests

The exact byte ceiling is implementation-defined.

Use a named/configurable value and prove:

```text
payload below limit → may continue
payload above limit → 4xx before RAG/model work
```
