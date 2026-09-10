"""
Day 10 Task 9 -- deterministic redaction: masking a protected value, never
inventing or generating one. "Do not invent/redact values using a
generative model" / `disclosure_rules.md`'s listed failure mode "redaction
performed by LLM" are exactly what this module refuses to be -- every
function here is pure string manipulation, no model call, no randomness,
same input always produces the same output (Day 10 Task 9: "redactable
fields are transformed deterministically").

Three masking shapes, matching `disclosure_rules.md`'s own synthetic
examples verbatim:

    email:      alice@example.test   -> a***@example.test
    phone:      +1-555-0102          -> ***-***-0102
    identifier: SYN-ID-123456        -> **********3456

None of the three is length-preserving by design, and deliberately so: a
mask that reveals the exact original length of an identifier or phone
number is itself a small disclosure (Day 10's own "safe disclosure"
principle applied to the mask itself, not just the plaintext) --
`_IDENTIFIER_MASK_PREFIX`/`_PHONE_MASK_PREFIX` are fixed-width regardless
of input length. Email is the one deliberate exception: the domain half is
disclosed in full and the local part always gets exactly three stars,
matching the pack's own worked example exactly rather than inventing a
different (equally defensible, but untested) convention -- see
`disclosure_rules.md`: "The exact mask format may differ if it is
deterministic, documented, and tested."

Which of the three shapes applies is decided from the *value's own shape*
(`mask_value()`'s dispatch below) -- contains `@` -> email; looks
phone-shaped -> phone; anything else -> generic identifier fallback --
rather than from the field's governed PII category. `disclosure_rules.md`
documents these examples by data *format*, not by category, and a single
governed category (e.g. `contact`) can legitimately hold either an email
or a phone number; sniffing the value's own shape is what actually decides
which worked example applies, deterministically, without needing the
caller to separately declare a format alongside the PII category.

A value that is empty, not a string, or otherwise unrecognizable falls
back to a fully opaque mask (`***`) rather than guessing -- never a
partial mask that might leak more of an unrecognized shape than intended.
"""
from __future__ import annotations

_OPAQUE_MASK = "***"
_EMAIL_LOCAL_MASK = "***"
_PHONE_MASK_PREFIX = "***-***-"
_IDENTIFIER_MASK_PREFIX = "*" * 10

# A phone-shaped value is mostly digits/phone punctuation and carries at
# least this many digits -- high enough that a short, mostly-numeric
# identifier (e.g. "SYN-ID-123456", 6 digits) is never misclassified as a
# phone number; disclosure_rules.md's own phone example ("+1-555-0102")
# carries 8.
_PHONE_DIGIT_THRESHOLD = 7
_PHONE_ALLOWED_CHARS = frozenset("0123456789+-() ")


def _looks_like_email(value: str) -> bool:
    """Deliberately just "contains an `@`" -- this module does not
    validate email syntax, it only decides which of the pack's worked
    masking examples applies. A value with no `@` at all can never be the
    email shape."""
    return "@" in value


def _looks_like_phone(value: str) -> bool:
    """A phone-shaped value is built entirely from digits and ordinary
    phone punctuation (`+`, `-`, parentheses, spaces) and carries enough
    digits to plausibly be a phone number, not a short numeric suffix
    inside a longer alphanumeric identifier."""
    digit_count = sum(char.isdigit() for char in value)
    return digit_count >= _PHONE_DIGIT_THRESHOLD and all(char in _PHONE_ALLOWED_CHARS for char in value)


def mask_email(value: str) -> str:
    """`alice@example.test` -> `a***@example.test`
    (`disclosure_rules.md`'s own worked example, matched exactly). The
    local part's first character is kept, the rest of the local part is
    always exactly `***` regardless of its own length, and the domain is
    disclosed in full -- the pack's example does not redact the domain at
    all, and this module does not invent a stricter convention beyond
    what was actually specified and tested."""
    local, _, domain = value.partition("@")
    first_char = local[0] if local else ""
    return f"{first_char}{_EMAIL_LOCAL_MASK}@{domain}"


def mask_phone(value: str) -> str:
    """`+1-555-0102` -> `***-***-0102` (`disclosure_rules.md`'s own worked
    example, matched exactly). Fixed-shape mask -- the country/area code
    portion is always exactly `***-***-`, regardless of the original
    value's own length; only the last four characters are ever disclosed."""
    return f"{_PHONE_MASK_PREFIX}{value[-4:]}"


def mask_identifier(value: str) -> str:
    """`SYN-ID-123456` -> `**********3456` (`disclosure_rules.md`'s own
    worked example, matched exactly: ten stars, then the last four
    characters). The generic fallback shape for any protected value that
    is neither email- nor phone-shaped -- deliberately fixed-width rather
    than proportional to the original value's length."""
    return f"{_IDENTIFIER_MASK_PREFIX}{value[-4:]}"


def mask_value(value: str) -> str:
    """The one dispatcher every caller (Task 9's `disclosure.py`) uses --
    never call `mask_email`/`mask_phone`/`mask_identifier` directly from
    outside this module, so the format-detection rule lives in exactly
    one place. Deterministic and total: every `str` input (including one
    this module cannot classify) produces some mask, and a non-`str`/empty
    input safely falls back to a fully opaque `***` rather than raising or
    guessing."""
    if not isinstance(value, str) or not value:
        return _OPAQUE_MASK
    if _looks_like_email(value):
        return mask_email(value)
    if _looks_like_phone(value):
        return mask_phone(value)
    return mask_identifier(value)
