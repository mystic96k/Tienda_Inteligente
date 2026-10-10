"""Gemini integration (Generative Language REST API).

Design rule: Gemini NEVER computes numbers. The ML/optimisation engine produces every figure; Gemini only
explains, prioritises and phrases them (grounded generation). Without GEMINI_API_KEY we fall back to a
deterministic template so the dashboard still works offline.
"""
import json

import httpx

from ..config import settings

URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

SYSTEM = ("You are the operations copilot for Glazed Co., a coffee and donut chain in airports and transit hubs. "
          "Use ONLY the JSON context provided; never invent numbers, products or stores. Answer in Spanish (Mexico), "
          "concise, concrete and action-oriented. Currency is " + settings.CURRENCY + ". The data is synthetic.")


def available() -> bool:
    return bool(settings.GEMINI_API_KEY)


def _call(prompt: str, json_mode: bool, temperature: float = 0.3) -> str:
    body = {"systemInstruction": {"parts": [{"text": SYSTEM}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": temperature,
                                 **({"responseMimeType": "application/json"} if json_mode else {})}}
    r = httpx.post(URL.format(model=settings.GEMINI_MODEL), json=body, timeout=40,
                   headers={"x-goog-api-key": settings.GEMINI_API_KEY})
    r.raise_for_status()
    return r.json()["candidates"][0]["content"]["parts"][0]["text"]


def brief(context: dict) -> dict:
    if available():
        try:
            prompt = ("Write the daily briefing for the store/network manager. Return JSON with keys: headline (string), "
                      "key_points (3-5 strings), top_actions (3 strings, ordered by impact), risks (0-3 strings).\n"
                      "CONTEXT:\n" + json.dumps(context, default=str))
            out = json.loads(_call(prompt, True))
            out["source"] = "gemini:" + settings.GEMINI_MODEL
            return out
        except Exception as e:  # network, quota, bad JSON -> degrade gracefully
            err = str(e)[:120]
    else:
        err = "GEMINI_API_KEY not set"
    recs = context.get("top_recommendations", [])
    k = context["kpis"]
    return {"headline": f"{context['scope']}: {k['net_sales_30d_fmt']} en 30 días ({k['change_pct']:+.1f}% vs los 30 previos).",
            "key_points": [f"Merma de {k['waste_usd_30d_fmt']} y {k['stockout_hours_30d']:,.0f} horas de quiebre de stock en 30 días.",
                           f"Personal: {k['short_hours']} h por agregar y {k['over_hours']} h por reducir en los próximos 14 días.",
                           f"Venta prevista a 14 días: {k['forecast_14d_fmt']}."],
            "top_actions": [r["title"] for r in recs[:3]], "risks": [],
            "source": f"respaldo ({err})"}


def ask(question: str, context: dict) -> dict:
    if not available():
        return {"answer": "Gemini no está configurado (define GEMINI_API_KEY). Mientras tanto, revisa el panel de recomendaciones.",
                "source": "fallback"}
    prompt = f"Question: {question}\nAnswer in <=120 words, cite figures from the context.\nCONTEXT:\n{json.dumps(context, default=str)}"
    try:
        return {"answer": _call(prompt, False), "source": "gemini:" + settings.GEMINI_MODEL}
    except Exception as e:
        return {"answer": f"Gemini request failed: {str(e)[:150]}", "source": "error"}


def promo_copy(rec: dict) -> dict:
    if not available():
        p = rec["payload"]
        return {"es": f"¡{int(p['discount']*100)}% de descuento en {p['product']}! Solo hoy.",
                "en": f"{int(p['discount']*100)}% off {p['product']} - today only.", "source": "respaldo"}
    prompt = ("Create shelf-sign copy for this promotion. Return JSON {\"es\": string, \"en\": string, \"social\": string}, "
              "each max 90 chars, no emojis in es/en.\nPROMO:\n" + json.dumps(rec["payload"], default=str))
    try:
        out = json.loads(_call(prompt, True, 0.8))
        out["source"] = "gemini:" + settings.GEMINI_MODEL
        return out
    except Exception as e:
        return {"error": str(e)[:150], "source": "error"}
