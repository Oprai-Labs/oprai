"""
OPRAI DeFi Query Service — FastAPI + CLI

POST /query  { question, jwt_token? }  →  { html, plain, tools_called }
GET  /health                           →  { status }

CLI:
  python3 main.py "What's the Jito tip floor?"
  python3 main.py "Show my wallet balance" --jwt <token>
  python3 main.py "Is BONK safe to buy?" --jwt <token>
"""

import asyncio
import os
import sys

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from orchestrator import query

app = FastAPI(title="OPRAI DeFi Query Service", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class QueryRequest(BaseModel):
    question: str
    jwt_token: str | None = None


class QueryResponse(BaseModel):
    html: str
    plain: str
    tools_called: list[str]


@app.get("/health")
async def health():
    return {"status": "ok", "service": "defi-query"}


@app.post("/query", response_model=QueryResponse)
async def handle_query(req: QueryRequest):
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="question must not be empty")
    try:
        result = await query(req.question, req.jwt_token)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Query failed: {str(e)}")


# ─── CLI ─────────────────────────────────────────────────────────────────────

async def _cli(question: str, jwt_token: str = None):
    print(f"\n🤔 {question}\n{'─'*60}")
    result = await query(question, jwt_token)
    print(f"Tools: {', '.join(result['tools_called']) or 'none'}\n")
    print(result["plain"])
    print("\n─── HTML (first 600 chars) ───")
    print(result["html"][:600] + ("..." if len(result["html"]) > 600 else ""))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 main.py <question> [--jwt <token>]")
        sys.exit(1)

    question = sys.argv[1]
    jwt = None
    if "--jwt" in sys.argv:
        idx = sys.argv.index("--jwt")
        if idx + 1 < len(sys.argv):
            jwt = sys.argv[idx + 1]

    asyncio.run(_cli(question, jwt))
