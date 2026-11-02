"""Exercise a running API with real MongoDB, Redis, PostgreSQL, and LLM.

Usage: python scripts/e2e_live_chat.py --base-url http://127.0.0.1:18000
Requires a development JWT_SECRET. Uses synthetic, isolated session IDs.
"""
from __future__ import annotations

import argparse
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import jwt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config.settings import get_settings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:18000")
    args = parser.parse_args()
    settings = get_settings()
    if settings.env != "development" or len(settings.jwt_secret) < 32:
        print("Requires development mode and a configured JWT secret.")
        return 2

    session_id = "e2e-" + uuid.uuid4().hex[:16]
    now = datetime.now(timezone.utc)

    def headers(user_id: int, role: str = "CLIENTE") -> dict[str, str]:
        claims = {
            "sub": str(user_id), "empresa_id": 1, "role": role,
            "iat": now, "exp": now + timedelta(minutes=10), "jti": str(uuid.uuid4()),
        }
        if settings.jwt_issuer:
            claims["iss"] = settings.jwt_issuer
        if settings.jwt_audience:
            claims["aud"] = settings.jwt_audience
        return {"Authorization": "Bearer " + jwt.encode(
            claims, settings.jwt_secret, algorithm=settings.jwt_algorithm
        )}

    base = args.base_url.rstrip("/")

    def check(name: str, method: str, path: str, expected: int, *, auth=None, body=None):
        response = httpx.request(method, base + path, headers=auth, json=body, timeout=120)
        print(f"{name}: HTTP {response.status_code}")
        if response.status_code != expected:
            print(response.text[:500])
            raise AssertionError(f"{name}: expected HTTP {expected}")
        return response.json()

    owner = headers(900001)
    outsider = headers(900002)
    manager = headers(900003, "DONO")
    check("ready", "GET", "/readyz", 200)
    check("metrics role denied", "GET", "/metrics/summary", 403, auth=owner)
    check("owner metrics", "GET", "/metrics/summary", 200, auth=manager)
    check("anonymous chat denied", "POST", "/chat", 401,
          body={"message": "Oi", "session_id": session_id})
    chat = check("live chat", "POST", "/chat", 200, auth=owner,
                 body={"message": "O que você pode fazer por um cliente?", "session_id": session_id})
    assert chat["response"].strip() and chat["judge_approved"] is True
    print("agent:", chat["agent"], "judge_score:", chat["judge_score"])
    print("response:", chat["response"])
    print("sources:", len(chat["sources"]))
    sessions = check("session list", "GET", "/chat/sessions", 200, auth=owner)
    assert any(item["sessionId"] == session_id for item in sessions["sessions"])
    history = check("history", "GET", f"/chat/history/{session_id}", 200, auth=owner)
    assert [item["role"] for item in history["messages"][-2:]] == ["user", "assistant"]
    check("other user denied", "GET", f"/chat/history/{session_id}", 403, auth=outsider)
    check("close session", "POST", f"/chat/sessions/{session_id}/close", 200, auth=owner)
    print("E2E PASS")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="replace")
    try:
        raise SystemExit(main())
    except (AssertionError, httpx.RequestError, KeyError, ValueError) as exc:
        print("E2E FAIL:", type(exc).__name__, str(exc)[:300])
        raise SystemExit(1) from exc
