from __future__ import annotations

import json
import random
import re
from typing import Any

import httpx

from app.core.exceptions import ShortCircuitFault
from app.schemas.chaos_config import ChaosConfig, RbacJailbreakerConfig
from app.services.chaos.base import BaseChaosStrategy
from app.utils.logger import get_logger

logger = get_logger("rbac_jailbreak")

_SQL_MUTATION_PATTERN = re.compile(
    r"\b(UPDATE|INSERT|DELETE|DROP|ALTER|GRANT)\b",
    re.IGNORECASE,
)
_SQL_COMMENT_PATTERN = re.compile(r"/\*.*?\*/|--[^\r\n]*|#[^\r\n]*", re.DOTALL)
_QUOTE_VARIANTS_PATTERN = re.compile(r"\\?['`‘’“”\u2018\u2019\u201c\u201d]")
_OPERATOR_PUNCTUATION_PATTERN = re.compile(r"\s*([=,;()<>!+\-*/])\s*")
_WHITESPACE_PATTERN = re.compile(r"\s+")
_MAX_REDACTION_DEPTH: int = 100


def _normalize_text(text: str) -> str:
    """Normalize text for SQL comments, quotes, whitespace, and operator padding."""
    t = _SQL_COMMENT_PATTERN.sub(" ", text)
    t = _QUOTE_VARIANTS_PATTERN.sub("'", t)
    t = t.lower()
    t = _OPERATOR_PUNCTUATION_PATTERN.sub(r" \1 ", t)
    t = _WHITESPACE_PATTERN.sub(" ", t)
    return t.strip()


class RbacJailbreakStrategy(BaseChaosStrategy):
    """Detects privilege-escalation attempts and redacts protected fields."""

    name: str = "rbac_jailbreak"
    default_severity: float = 0.9

    def __init__(self, rng: random.Random | None = None) -> None:
        self._rng = rng if rng is not None else random.Random()

    def should_apply(self, request: httpx.Request, config: ChaosConfig) -> bool:
        profile = config.fuzzing_profiles.rbac_jailbreaker
        if profile is None or not profile.enabled:
            return False
        return True

    async def mutate_request(self, request: httpx.Request, config: ChaosConfig) -> httpx.Request:
        profile = config.fuzzing_profiles.rbac_jailbreaker
        if profile is None or not profile.enabled:
            return request

        try:
            raw_body = request.content
        except Exception:
            raw_body = b""

        text_body = raw_body.decode("utf-8", errors="replace")
        norm_body = _normalize_text(text_body)

        # 1. Scan for prohibit_mutations rules
        for rule in profile.prohibit_mutations:
            norm_rule = _normalize_text(rule)
            if norm_rule and norm_rule in norm_body:
                self._raise_rbac_fault(blocked_rule=rule, severity=profile.severity)

        # 2. Check if mutating context
        is_mutating = request.method.upper() in (
            "POST",
            "PUT",
            "PATCH",
            "DELETE",
        ) or bool(_SQL_MUTATION_PATTERN.search(text_body))

        if is_mutating and profile.protected_fields:
            for field in profile.protected_fields:
                field_clean = field.strip()
                if not field_clean:
                    continue
                # Match field name case-insensitively with boundary
                field_pattern = re.compile(rf"\b{re.escape(field_clean)}\b", re.IGNORECASE)
                if field_pattern.search(text_body):
                    self._raise_rbac_fault(blocked_rule=field_clean, severity=profile.severity)

        return request

    def _raise_rbac_fault(self, blocked_rule: str, severity: float) -> None:
        error_payload = {
            "error": "rbac_violation",
            "blocked_rule": blocked_rule,
            "injected_by": "rbac_jailbreak",
        }
        res = httpx.Response(
            status_code=403,
            headers={"content-type": "application/json"},
            content=json.dumps(error_payload).encode("utf-8"),
        )
        raise ShortCircuitFault(
            response=res,
            fault_name=self.name,
            severity=severity,
        )

    async def mutate_response(self, response: httpx.Response, config: ChaosConfig) -> httpx.Response | None:
        profile = config.fuzzing_profiles.rbac_jailbreaker
        if profile is None or not profile.enabled or not profile.protected_fields:
            return None

        content_type = response.headers.get("content-type", "")
        if content_type and "application/json" not in content_type and "+json" not in content_type:
            return None

        try:
            data = response.json()
        except (ValueError, json.JSONDecodeError):
            return None

        protected_lower = {f.strip().lower() for f in profile.protected_fields if f.strip()}
        if not protected_lower:
            return None

        redacted = self._redact(data, protected_lower)
        if not redacted:
            return None

        new_content = json.dumps(data).encode("utf-8")
        headers = {
            k: v
            for k, v in response.headers.items()
            if k.lower() not in ("content-length", "content-encoding")
        }
        if "content-type" not in [k.lower() for k in headers]:
            headers["content-type"] = "application/json"

        return httpx.Response(
            status_code=response.status_code,
            headers=headers,
            content=new_content,
        )

    def _redact(
        self,
        node: Any,
        protected_lower: set[str],
        depth: int = 0,
    ) -> bool:
        if depth > _MAX_REDACTION_DEPTH:
            logger.warning(
                "Redaction recursion depth exceeded safety bound (%d); aborting recursion",
                _MAX_REDACTION_DEPTH,
            )
            return False

        changed = False
        if isinstance(node, dict):
            for k, v in list(node.items()):
                if isinstance(k, str) and k.lower() in protected_lower:
                    node[k] = "[REDACTED]"
                    changed = True
                elif isinstance(v, (dict, list)):
                    if self._redact(v, protected_lower, depth + 1):
                        changed = True
        elif isinstance(node, list):
            for item in node:
                if isinstance(item, (dict, list)):
                    if self._redact(item, protected_lower, depth + 1):
                        changed = True
        return changed
