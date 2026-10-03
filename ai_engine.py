"""Small, testable runtime for Cipher Tech Hosting AI provider calls.

The application owns product context, plan eligibility, and Telegram UI. This
module owns the provider boundary: request sizing, transport, response parsing,
answer quality checks, safe diagnostics, and per-model circuit breakers.
"""
from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlencode


@dataclass(frozen=True)
class ModelRoute:
    """One provider endpoint and its request-parameter builder."""

    endpoint: str
    build_params: Callable[[str], Mapping[str, Any]]
    timeout: Tuple[float, float] = (6, 30)
    public_label: Optional[str] = None


@dataclass(frozen=True)
class ModelResult:
    """Outcome for one operative; errors are deliberately prompt-free."""

    model: str
    text: Optional[str] = None
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return bool(self.text and self.text.strip())


class AIEngine:
    """Provider adapter shared by chat, file analysis, Sentinel, and scanning."""

    _IDENTITY_DRIFT_RE = re.compile(
        r"^\s*(?:i'?m|i am|this is|you(?:'re| are) (?:talking|speaking) (?:to|with))\s+"
        r"(?P<claimed>(?:gpt|chatgpt|openai|anthropic|claude|gemini|copilot|deepseek|llama|mistral|qwen)\b"
        r"(?:[\s._-]+(?:code|chat|fable|sonnet|haiku|opus|pro|mini|4o|5|4|3|2|1|80b|70b|r1|v4|v3|flash|cli))*)\b",
        re.IGNORECASE,
    )
    _GENERIC_WELCOME_RE = re.compile(
        r"^(?:(?:hello|hi|hey|good morning|good afternoon|good evening)[!,. ]*)?"
        r"(?:what can i (?:help|assist) you with(?: today)?|"
        r"how (?:can|may) i (?:help|assist) you(?: today)?|"
        r"how are you(?: doing)?(?: today)?|"
        r"what would you like to talk about|what(?:'s| is) on your mind|"
        r"what would you like to (?:work on|know|do))[?.! ]*$",
        re.IGNORECASE,
    )
    _READY_AND_ASK_RE = re.compile(
        r"^(?:(?:hello|hi|hey)[!,. ]*)?"
        r"(?:i(?:'m| am) (?:ready|here) to (?:assist|help)\b|"
        r"i can help with any questions you have\b|"
        r"i(?:'m| am) (?:happy|glad) to help\b)"
        r"[\s\S]{0,260}\b(?:"
        r"(?:how can|how may|what can) i (?:help|assist|do)|"
        r"what(?:'s| is) on your mind|"
        r"what would you like to (?:work on|know|do|talk about)"
        r")\b[^.!?]*[.!?]?$",
        re.IGNORECASE,
    )
    _SMALLTALK_ONLY_RE = re.compile(
        r"^(?:(?:hello|hi|hey|good morning|good afternoon|good evening)[!,. ]*)?"
        r"(?:how are you(?: doing)?(?: today)?[?!. ]*)?"
        r"(?:is there (?:anything|something) i can (?:help|assist) you with|"
        r"would you like to (?:chat|talk)|do you have (?:any )?questions|"
        r"what would you like to (?:chat|talk about))"
        r"(?:\s+or would you like to (?:chat|talk))?[^.!?]*[.!?]?$",
        re.IGNORECASE,
    )
    _GREETING_REQUESTS = {
        "hi", "hello", "hey", "sup", "yo", "morning", "evening", "afternoon",
        "goodmorning", "goodevening", "goodafternoon", "goodday", "howdy", "greetings",
        "hellothere", "hithere", "heythere", "howareyou", "howareyoudoing", "howsitgoing",
        "whatsup", "nicetomeetyou", "hola", "bonjour", "ciao", "hallo", "namaste",
        "salam", "assalamualaikum", "nihao", "konnichiwa", "privet",
    }
    _UNUSABLE_PREFIXES = (
        "maaf,", "sign up", "api key", "unauthorized", "rate limit",
        "i received your message, but the ai provider returned no usable text",
    )
    _UNUSABLE_PHRASES = (
        "too many requests", "usage limit reached", "upgrade to vip",
        "account is now required to use vibe", "temporarily unavailable",
        "service unavailable", "provider returned no usable text",
    )

    def __init__(
        self,
        *,
        session: Any,
        base_urls: Sequence[str],
        routes: Mapping[str, ModelRoute],
        context_builder: Callable[[str], str],
        enabled: Callable[[str], bool],
        logger: Optional[Callable[[str], None]] = None,
        on_circuit_open: Optional[Callable[[str, int], None]] = None,
        max_encoded_query: int = 14000,
        failure_threshold: int = 5,
        cooldown_seconds: int = 300,
        transient_retries: int = 1,
        retry_backoff_seconds: float = 0.25,
        failure_counts: Optional[Dict[str, int]] = None,
        circuit_open_until: Optional[Dict[str, float]] = None,
    ) -> None:
        self.session = session
        self.base_urls = tuple(str(url).rstrip("/") for url in base_urls if str(url).strip())
        self.routes = {str(key).strip().lower(): value for key, value in routes.items()}
        self.context_builder = context_builder
        self.enabled = enabled
        self.logger = logger or (lambda _message: None)
        self.on_circuit_open = on_circuit_open or (lambda _model, _seconds: None)
        self.max_encoded_query = max_encoded_query
        self.failure_threshold = max(1, int(failure_threshold))
        self.cooldown_seconds = max(1, int(cooldown_seconds))
        self.transient_retries = max(0, int(transient_retries))
        self.retry_backoff_seconds = max(0.0, float(retry_backoff_seconds))
        self.failure_counts = failure_counts if failure_counts is not None else {}
        self.circuit_open_until = circuit_open_until if circuit_open_until is not None else {}
        self.lock = threading.RLock()

    @classmethod
    def extract_reply(cls, payload: Any) -> Optional[str]:
        """Extract text from OmegaTech, OpenAI-style, and nested JSON replies."""
        if isinstance(payload, str):
            text = payload.strip()
            if not text:
                return None
            if text.startswith(("{", "[")):
                try:
                    decoded = json.loads(text)
                except Exception:
                    return text
                if decoded is not payload:
                    nested = cls.extract_reply(decoded)
                    if nested:
                        return nested
            return text.replace("-=-n--", "\n").strip() or None

        if isinstance(payload, list):
            pieces = []
            for item in payload:
                text = cls.extract_reply(item)
                if text:
                    pieces.append(text)
            return "\n".join(pieces).strip() or None

        if not isinstance(payload, dict):
            return None

        # OpenAI-compatible chat completion shape.
        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            choice = choices[0]
            if isinstance(choice, dict):
                message = choice.get("message") or choice.get("delta") or {}
                text = cls.extract_reply(message)
                if text:
                    return text
                text = cls.extract_reply(choice.get("text"))
                if text:
                    return text

        # Gemini-like candidate shape.
        candidates = payload.get("candidates")
        if isinstance(candidates, list) and candidates:
            text = cls.extract_reply(candidates[0])
            if text:
                return text

        parts = payload.get("parts")
        if isinstance(parts, list):
            text = cls.extract_reply(parts)
            if text:
                return text

        # Prefer content-bearing result fields before any generic message.
        for key in ("reply", "answer", "response", "result", "output_text", "text", "content"):
            if key in payload:
                text = cls.extract_reply(payload[key])
                if text:
                    return text

        # OmegaTech and similar APIs wrap replies in data/result objects.
        for key in ("data", "output", "message", "content_block"):
            if key in payload:
                value = payload[key]
                if key == "message" and isinstance(value, str):
                    status = payload.get("status")
                    if payload.get("success") is not True and status not in (True, "ok", "success"):
                        continue
                text = cls.extract_reply(value)
                if text:
                    return text
        return None

    @classmethod
    def _is_greeting_request(cls, prompt: Optional[str]) -> bool:
        text = str(prompt or "").strip().lower()
        text = re.sub(r"^verified active plan:\s*[^\n]+\n", "", text)
        if "current user request:" in text:
            text = text.rsplit("current user request:", 1)[1].strip()
        normalized = re.sub(r"[^a-z]", "", text)
        return normalized in cls._GREETING_REQUESTS

    @classmethod
    def is_usable(
        cls,
        text: Optional[str],
        prompt: Optional[str] = None,
        expected_model: Optional[str] = None,
    ) -> bool:
        """Reject provider errors and generic welcome text that ignores a request."""
        if not isinstance(text, str) or not text.strip():
            return False
        reply = text.strip()
        lowered = reply.lower()
        if lowered.startswith(cls._UNUSABLE_PREFIXES):
            return False
        if any(phrase in lowered for phrase in cls._UNUSABLE_PHRASES):
            return False
        if (("standard ai chat" in lowered or "deepai" in lowered)
                and ("official ai assistant" in lowered or "serve as" in lowered
                     or "what can i help" in lowered)):
            return False
        if "hotbot chat" in lowered and "how can i help" in lowered:
            return False
        identity = cls._IDENTITY_DRIFT_RE.match(reply)
        if identity:
            expected = re.sub(r"[^a-z0-9]", "", str(expected_model or "").lower())
            claimed = re.sub(r"[^a-z0-9]", "", identity.group("claimed").lower())
            # A provider may describe itself as a different product (for
            # example Claude Code on the Claude Chat route). Accept only a
            # claim that is an exact match or a less-specific prefix of the
            # configured public operative label.
            if not expected or not claimed or not expected.startswith(claimed):
                return False
        if (cls._GENERIC_WELCOME_RE.fullmatch(reply)
                or cls._READY_AND_ASK_RE.fullmatch(reply)
                or cls._SMALLTALK_ONLY_RE.fullmatch(reply)) \
                and not cls._is_greeting_request(prompt):
            return False
        return True

    @staticmethod
    def _is_provider_error(payload: Any) -> bool:
        if not isinstance(payload, dict):
            return False
        if payload.get("success") is False or payload.get("status") is False:
            return True
        if str(payload.get("success", "")).strip().lower() in {"false", "error", "failed"}:
            return True
        if str(payload.get("status", "")).strip().lower() in {"error", "failed", "failure"}:
            return True
        try:
            return int(payload.get("statusCode", 0)) >= 400
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _is_transient_provider_error(payload: Any) -> bool:
        if not isinstance(payload, dict):
            return False
        try:
            code = int(payload.get("statusCode", 0))
        except (TypeError, ValueError):
            code = 0
        if code in {500, 502, 503, 504}:
            return True
        message = str(payload.get("error") or payload.get("message") or "")
        return bool(re.search(r"\bHTTP\s+50[0-4]\b", message, re.IGNORECASE))

    def fit_prompt(self, prefix: str, prompt: str, route: ModelRoute) -> Optional[str]:
        """Keep platform instructions intact while fitting the encoded GET query."""
        def encoded_size(full_text: str) -> int:
            try:
                return len(urlencode(route.build_params(full_text)))
            except Exception:
                return self.max_encoded_query + 1

        full = prefix + prompt
        if encoded_size(full) <= self.max_encoded_query:
            return full

        def shrink(keep: int) -> str:
            head_size = keep * 2 // 3
            tail_size = keep - head_size
            head = prompt[:head_size]
            tail = prompt[-tail_size:] if tail_size else ""
            return prefix + head + "\n[... request shortened to fit the provider limit ...]\n" + tail

        if encoded_size(shrink(0)) > self.max_encoded_query:
            return None
        low, high = 0, len(prompt)
        while low < high:
            mid = (low + high + 1) // 2
            if encoded_size(shrink(mid)) <= self.max_encoded_query:
                low = mid
            else:
                high = mid - 1
        return shrink(low)

    @staticmethod
    def _safe_exception(error: Exception) -> str:
        # Never log exception URLs; requests exceptions may include query strings.
        name = type(error).__name__.lower()
        if "timeout" in name or "timed out" in str(error).lower():
            return "request timeout"
        if "connection" in name:
            return "connection error"
        return f"transport error ({type(error).__name__})"

    def _record_failure(self, model: str, error: str) -> None:
        opened = False
        with self.lock:
            failures = self.failure_counts.get(model, 0) + 1
            self.failure_counts[model] = failures
            if failures >= self.failure_threshold:
                self.circuit_open_until[model] = time.time() + self.cooldown_seconds
                self.failure_counts.pop(model, None)
                opened = True
        try:
            self.logger(f"[ai] {model} failed: {error}")
        except Exception:
            pass
        if opened:
            try:
                self.on_circuit_open(model, self.cooldown_seconds)
            except Exception:
                pass

    def complete(self, model_name: str, prompt: str) -> ModelResult:
        """Call one configured operative and return either text or a safe error."""
        model = str(model_name or "").strip().lower()
        route = self.routes.get(model)
        if route is None:
            return ModelResult(model, error="unregistered model")
        if not self.base_urls:
            return ModelResult(model, error="no provider host configured")
        try:
            if not self.enabled(model):
                return ModelResult(model, error="model disabled")
        except Exception:
            return ModelResult(model, error="model eligibility check failed")

        now = time.time()
        with self.lock:
            blocked_until = self.circuit_open_until.get(model, 0.0)
            if blocked_until > now:
                return ModelResult(model, error="model circuit open")
            if blocked_until:
                self.circuit_open_until.pop(model, None)
                self.failure_counts.pop(model, None)

        try:
            prefix = str(self.context_builder(model) or "") + "USER REQUEST:\n"
            fitted = self.fit_prompt(prefix, str(prompt or ""), route)
            if fitted is None:
                return ModelResult(model, error="prompt exceeds provider URL limit")
            params = dict(route.build_params(fitted))
        except Exception:
            self._record_failure(model, "request construction failed")
            return ModelResult(model, error="request construction failed")

        last_error = "provider returned no usable text"
        for base_url in self.base_urls:
            url = f"{base_url}/api/ai/{route.endpoint.strip('/')}"
            for attempt in range(self.transient_retries + 1):
                try:
                    response = self.session.get(url, params=params, timeout=route.timeout)
                except Exception as error:
                    last_error = self._safe_exception(error)
                    break

                status_code = getattr(response, "status_code", None)
                if status_code in (413, 414, 431):
                    return ModelResult(model, error=f"request too large (HTTP {status_code})")
                if status_code in (500, 502, 503, 504):
                    last_error = f"HTTP {status_code}"
                    if attempt < self.transient_retries:
                        if self.retry_backoff_seconds:
                            time.sleep(self.retry_backoff_seconds * (attempt + 1))
                        continue
                    break
                if status_code != 200:
                    last_error = f"HTTP {status_code}" if status_code else "invalid HTTP response"
                    break
                try:
                    payload = response.json()
                except Exception:
                    last_error = "invalid JSON response"
                    break
                if self._is_provider_error(payload):
                    last_error = "provider reported an error"
                    if self._is_transient_provider_error(payload) and attempt < self.transient_retries:
                        if self.retry_backoff_seconds:
                            time.sleep(self.retry_backoff_seconds * (attempt + 1))
                        continue
                    break

                text = self.extract_reply(payload)
                if not text:
                    last_error = "empty provider response"
                    break
                if not self.is_usable(text, prompt, route.public_label):
                    last_error = "provider returned a non-answer"
                    break

                with self.lock:
                    self.failure_counts.pop(model, None)
                    self.circuit_open_until.pop(model, None)
                return ModelResult(model, text=text)

        self._record_failure(model, last_error)
        return ModelResult(model, error=last_error)

    def complete_chain(
        self,
        models: Sequence[str],
        prompt: str,
        invoke: Optional[Callable[[str, str], Any]] = None,
    ) -> Tuple[ModelResult, ...]:
        """Run operatives in priority order; stop only on a usable answer."""
        attempts = []
        for model in models:
            key = str(model or "").strip().lower()
            if invoke is None:
                outcome = self.complete(key, prompt)
            else:
                try:
                    raw = invoke(key, prompt)
                except Exception as error:
                    outcome = ModelResult(key, error=self._safe_exception(error))
                    try:
                        self.logger(f"[ai] {key} chain attempt failed: {outcome.error}")
                    except Exception:
                        pass
                else:
                    if isinstance(raw, ModelResult):
                        outcome = raw
                    else:
                        route = self.routes.get(key)
                        expected_model = route.public_label if route else None
                        if self.is_usable(raw, prompt, expected_model):
                            outcome = ModelResult(key, text=str(raw).strip())
                        else:
                            outcome = ModelResult(
                                key,
                                error="provider returned a non-answer" if raw else "provider returned no usable text",
                            )
            attempts.append(outcome)
            if outcome.ok:
                break
        return tuple(attempts)
