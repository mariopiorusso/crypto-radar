import json, os
from openai import OpenAI

SYSTEM = """You are the evidence-analysis component of an experimental crypto
early-warning research system. You do not give trading instructions.
Distinguish leading signals from reactions to an already-completed price move.
Be skeptical of coordinated promotion, recycled news, weak sources and illiquid
assets. Use only the evidence supplied. Return JSON only."""

def investigate(coin, stats, news):
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    model = os.getenv("OPENAI_MODEL", "gpt-5.4-mini")
    payload = {
        "coin": {
            "id": coin["id"], "name": coin["name"], "symbol": coin["symbol"],
            "price": coin.get("current_price"), "market_cap": coin.get("market_cap"),
            "24h_volume": coin.get("total_volume"),
            "24h_change_pct": coin.get("price_change_percentage_24h"),
        },
        "local_anomaly": stats,
        "recent_news": news,
    }
    prompt = f"""Assess whether this dossier contains an unusually interesting
EARLY upward-move setup or merely noise/lagging attention.

Return exactly one JSON object with:
surge_score: integer 0-100
stage: one of early, developing, already_moved, noise, insufficient_data
catalyst_strength: integer 0-10
manipulation_risk: integer 0-10
confidence: number 0-1
thesis: string <= 350 chars
risks: string <= 350 chars

Do not infer whale or social evidence because V1 has not collected those yet.
A high score requires unusually strong supplied evidence, not generic optimism.

DOSSIER:
{json.dumps(payload, ensure_ascii=False)}
"""
    response = client.responses.create(
        model=model,
        instructions=SYSTEM,
        input=prompt,
    )
    text = response.output_text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    return json.loads(text)
