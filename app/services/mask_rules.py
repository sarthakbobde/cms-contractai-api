"""The defined set of sensitive fields masked by A.8.

Edit this list to change what gets masked. Each rule has:
  name     - field name reported back to the caller
  pattern  - compiled regex
  group    - regex group whose text is masked (0 = whole match). Labelled
             rules mask only the value, so "Account Number:" stays readable.
  check    - optional extra validation on the matched value
"""
import re
from dataclasses import dataclass
from typing import Callable


def _luhn(value: str) -> bool:
    digits = [int(c) for c in value if c.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        total += d
    return total % 10 == 0


def _phone_digits(value: str) -> bool:
    return 9 <= sum(c.isdigit() for c in value) <= 15


@dataclass(frozen=True)
class MaskRule:
    name: str
    pattern: re.Pattern
    group: int = 0
    check: Callable[[str], bool] | None = None


def _labelled(labels: str, value: str = r"[A-Za-z0-9][A-Za-z0-9 \-/]{2,40}[A-Za-z0-9]") -> re.Pattern:
    # "<label> [:|-|#] <value>" ; value is group 1
    return re.compile(rf"\b(?:{labels})\b\s*[:#\-]?\s*({value})", re.IGNORECASE)


MASK_RULES: list[MaskRule] = [
    MaskRule("email", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
    MaskRule(
        "bank_account_number",
        _labelled(r"account\s*(?:number|no\.?|num|#)|a/c\s*(?:no\.?|number)?|bank\s*account",
                  r"\d[\d \-]{4,30}\d"),
        group=1,
    ),
    MaskRule("iban", re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?\b")),
    MaskRule("swift_bic", _labelled(r"swift(?:\s*code)?|bic", r"[A-Z]{6}[A-Z0-9]{2}(?:[A-Z0-9]{3})?"), group=1),
    MaskRule("sort_code", _labelled(r"sort\s*code", r"\d{2}-\d{2}-\d{2}|\d{6}"), group=1),
    MaskRule("ifsc_code", _labelled(r"ifsc(?:\s*code)?", r"[A-Z]{4}0[A-Z0-9]{6}"), group=1),
    MaskRule("routing_number", _labelled(r"routing\s*(?:number|no\.?)|aba", r"\d{9}"), group=1),
    MaskRule(
        "tax_id",
        _labelled(r"vat\s*(?:no\.?|number|reg(?:istration)?\s*(?:no\.?|number)?)?|gstin|gst\s*(?:no\.?|number)"
                  r"|tax\s*(?:id|identification\s*number)|tin|ein",
                  r"[A-Z0-9][A-Z0-9 \-]{5,20}[A-Z0-9]"),
        group=1,
    ),
    MaskRule("pan", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")),
    MaskRule("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    MaskRule("credit_card", re.compile(r"\b(?:\d[ \-]?){12,18}\d\b"), check=_luhn),
    MaskRule("aadhaar", re.compile(r"\b[2-9]\d{3}[ \-]?\d{4}[ \-]?\d{4}\b")),
    MaskRule(
        "phone",
        re.compile(r"(?<![\w+])(?:\+\d{1,3}[\s.\-]?)?(?:\(\d{1,4}\)[\s.\-]?)?\d{2,5}(?:[\s.\-]?\d{2,5}){1,3}(?!\w)"),
        check=_phone_digits,
    ),
    MaskRule(
        "contract_value",
        _labelled(r"(?:total\s+)?contract\s+value|contract\s+amount|total\s+consideration",
                  r"(?:[A-Z]{3}\s?|[$£€₹]\s?)?\d[\d,]*(?:\.\d+)?(?:\s?[A-Z]{3})?"),
        group=1,
    ),
]
