"""طبقة الاتصال بـ Groq: اكتشاف النماذج المتاحة + fallback + قراءة JSON بأمان."""
import json
import logging
import re
import time

from groq import APIConnectionError, APIStatusError, Groq, RateLimitError

from .config import GROQ_API_KEY, MODELS

log = logging.getLogger("qadiya.llm")
_client = None
_avail_cache = {"t": 0, "ids": None}

# نماذج مش للمحادثة، بنتجاهلها
SKIP = ("whisper", "tts", "guard", "orpheus", "embed", "compound", "safeguard")
# ترتيب تفضيلنا لو كانت متاحة (بعد النماذج اللي في .env)
PREFERRED = ["llama-3.3-70b-versatile", "openai/gpt-oss-120b", "openai/gpt-oss-20b",
             "llama-3.1-8b-instant", "qwen/qwen3.6-27b", "qwen/qwen3.8-27b"]


class LLMError(Exception):
    """خطأ بيتعرض للاعب برسالة مفهومة."""


def _get_client() -> Groq:
    global _client
    if not GROQ_API_KEY:
        raise LLMError("مفتاح GROQ_API_KEY مش متظبط على السيرفر.")
    if _client is None:
        _client = Groq(api_key=GROQ_API_KEY, timeout=60.0, max_retries=0)
    return _client


def _discover():
    """بيسأل Groq على النماذج المتاحة لحسابك (بيتخزّن 10 دقايق)."""
    if _avail_cache["ids"] is not None and time.time() - _avail_cache["t"] < 600:
        return _avail_cache["ids"]
    try:
        ids = [m.id for m in _get_client().models.list().data]
        _avail_cache.update(t=time.time(), ids=ids)
        log.info("نماذج Groq المتاحة: %s", ids)
        return ids
    except LLMError:
        raise
    except Exception as e:
        log.warning("تعذّر جلب قايمة النماذج: %s", e)
        return None


def model_order(requested=None):
    requested = requested or MODELS
    avail = _discover()
    if not avail:
        return list(requested)
    order = [m for m in requested if m in avail]
    order += [m for m in PREFERRED if m in avail and m not in order]
    order += [m for m in avail if m not in order and not any(s in m for s in SKIP)]
    if not order:
        raise LLMError("مفيش نماذج محادثة متاحة لحسابك على Groq.")
    return order[:4]


def _clean(text: str) -> str:
    # بعض نماذج الـ reasoning بتطلّع <think>...</think> قبل الرد
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()


def chat(messages, *, temperature=0.7, max_tokens=700, json_mode=False, models=None) -> str:
    rate_limited = False
    for model in model_order(models):
        reasoning = "gpt-oss" in model or "qwen" in model
        for attempt in range(2):
            try:
                kwargs = dict(model=model, messages=messages, temperature=temperature,
                              # نماذج الـ reasoning بتصرف tokens في التفكير، فبنوسّع الحد
                              max_tokens=min(max_tokens * 3 + 1000, 8000) if reasoning else max_tokens)
                if json_mode:
                    kwargs["response_format"] = {"type": "json_object"}
                if "gpt-oss" in model:
                    kwargs["extra_body"] = {"reasoning_effort": "low"}
                resp = _get_client().chat.completions.create(**kwargs)
                text = _clean(resp.choices[0].message.content or "")
                if text:
                    return text
            except RateLimitError:
                rate_limited = True
                log.warning("rate limit on %s", model)
                time.sleep(0.6)
                break
            except APIStatusError as e:
                log.warning("groq error on %s (attempt %s): %s", model, attempt + 1, e)
                if e.status_code == 404:  # النموذج مش موجود، انقل للّي بعده
                    break
                time.sleep(0.8)
            except APIConnectionError as e:
                log.warning("connection error on %s: %s", model, e)
                time.sleep(0.8)
    if rate_limited:
        raise LLMError("السيرفر زحمة دلوقتي (وصلنا لحد الاستخدام المجاني). جرّب بعد دقيقة.")
    raise LLMError("حصلت مشكلة في الاتصال بالـ AI. جرّب تاني.")


def parse_json(text: str) -> dict:
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    raise ValueError("invalid json from model")
