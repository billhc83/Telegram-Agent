#!/usr/bin/env python3
"""
RAG Retrieval Test Suite
Mirrors the 4-pass Knowledge Handler logic and evaluates output against
ground truth derived from reading actual project source files.

Usage:
  python3 workspace/tests/rag_test_suite.py
  python3 workspace/tests/rag_test_suite.py --project Learngentic
  python3 workspace/tests/rag_test_suite.py --verbose
"""

import json
import argparse
import requests
import sys
from datetime import datetime

QDRANT_URL  = "http://localhost:6333"
OLLAMA_URL  = "http://localhost:11434"
EMBED_MODEL = "qwen3-embedding:4b"
CHAT_MODEL  = "qwen3-14b-nothink:latest"

# ── Role-weight policy matrix (mirrors Knowledge Handler) ─────────────────────
ROLE_MATRIX = {
    "explain_code":    [1.15, 0.85, 0.92, 0.88],
    "write_code":      [1.12, 0.88, 0.92, 0.88],
    "debug_code":      [1.15, 0.90, 0.90, 0.88],
    "review_code":     [1.10, 0.95, 0.92, 0.88],
    "find_in_files":   [1.15, 0.85, 0.90, 0.85],
    "explain_project": [0.90, 0.82, 1.12, 0.88],
    "project_summary": [0.85, 0.80, 1.15, 0.85],
    "recall_work":     [0.88, 0.80, 1.10, 0.85],
}
DEFAULT_W   = [1.00, 0.40, 0.70, 0.30]
ROLE_IDX    = {"source": 0, "test": 1, "docs": 2, "config": 3}
CONF_DISC   = {"HIGH": 1.00, "MEDIUM": 0.90, "LOW": 0.75}
ANCHOR_PATS = {"cli.py","main.py","server.py","app.py","__init__.py","api.py","routes.py"}

# ── Ground truth test cases ───────────────────────────────────────────────────
TEST_CASES = [

    # ── Learngentic ────────────────────────────────────────────────────────────

    {
        "id": "lgn-cli-commands",
        "project": "Learngentic",
        "goal": "find_in_files",
        "query": "What CLI commands does Learngentic provide?",
        "expected_source_files": ["src/learngentic/cli.py"],
        "expected_facts": [
            "sessions",
            "logs",
            "init",
            "sync-patterns",
            "score",
            "status",
            "track-tool-use",
        ],
        "expected_not": [
            "trend",          # test artifact, not a real command
            "--version",      # does not exist in cli.py
            "learngentic mcp server",  # that's python -m, not a CLI command
        ],
        "difficulty": "medium",
        "rationale": "All 7 commands are in cli.py; test file has 'trend' which is a hallucination risk",
    },

    {
        "id": "lgn-logs-detail",
        "project": "Learngentic",
        "goal": "explain_code",
        "query": "What does the learngentic logs command show?",
        "expected_source_files": ["src/learngentic/cli.py"],
        "expected_facts": [
            "daily breakdown",
            "sparkline",
            "rolling window",
            "days",
            "score",
            "prompt_quality",
            "execution_efficiency",
            "durability",
            "turn",
        ],
        "expected_not": [
            "report",
            "sync",
        ],
        "difficulty": "medium",
        "rationale": "Detail only in cli.py logs() function, not in README summary",
    },

    {
        "id": "lgn-config-keys",
        "project": "Learngentic",
        "goal": "explain_project",
        "query": "What configuration does Learngentic require?",
        "expected_source_files": ["src/learngentic/cli.py", "README.md"],
        "expected_facts": [
            "turso_url",
            "turso_auth_token",
            "ollama_base_url",
            "ollama_model",
        ],
        "expected_not": [
            "openai",
            "anthropic_api_key",
        ],
        "difficulty": "easy",
        "rationale": "Config keys appear in both README and cli.py check_config()",
    },

    {
        "id": "lgn-init-command",
        "project": "Learngentic",
        "goal": "explain_code",
        "query": "What does learngentic init do?",
        "expected_source_files": ["src/learngentic/cli.py"],
        "expected_facts": [
            "CLAUDE.md",
            "scaffold",
            "project",
            "record_task",
            "workflow rules",
        ],
        "expected_not": [
            "database",
            "turso",
        ],
        "difficulty": "medium",
        "rationale": "init() in cli.py appends CLAUDE.md rules; easy to confuse with db setup",
    },

    {
        "id": "lgn-scoring-dimensions",
        "project": "Learngentic",
        "goal": "explain_project",
        "query": "What are the five scoring dimensions in Learngentic?",
        "expected_source_files": ["README.md"],
        "expected_facts": [
            "execution_efficiency",
            "durability",
            "outcome_quality",
            "prompt_quality",
            "robustness_delta",
        ],
        "expected_not": [],
        "difficulty": "easy",
        "rationale": "Clearly in README scoring table; tests docs-prioritised retrieval for explain_project",
    },

    {
        "id": "lgn-mcp-tools",
        "project": "Learngentic",
        "goal": "explain_project",
        "query": "What MCP tools does Learngentic expose?",
        "expected_source_files": ["README.md"],
        "expected_facts": [
            "record_task",
            "run_local_task",
            "report_local_result",
            "report_outcome",
            "query_risk_assessment",
            "query_outcome_history",
            "get_file_stability",
        ],
        "expected_not": [],
        "difficulty": "easy",
        "rationale": "All 7 tools in README MCP tools table; source file coverage check",
    },

    {
        "id": "lgn-track-tool-use",
        "project": "Learngentic",
        "goal": "explain_code",
        "query": "What does track-tool-use do and how does it work?",
        "expected_source_files": ["src/learngentic/cli.py"],
        "expected_facts": [
            "PostToolUse",
            "stdin",
            "buffer",
            "jsonl",
            "session_id",
            "tool_name",
            "exits 0",
        ],
        "expected_not": [
            "network",
            "HTTP",
        ],
        "difficulty": "hard",
        "rationale": "Detail only in cli.py track_tool_use(); tests anchor file retrieval",
    },

    {
        "id": "lgn-what-is",
        "project": "Learngentic",
        "goal": "explain_project",
        "query": "What is Learngentic and what problem does it solve?",
        "expected_source_files": ["README.md"],
        "expected_facts": [
            "quality signal",
            "coding agent",
            "session",
            "score",
            "historical",
            "standard",
        ],
        "expected_not": [],
        "difficulty": "easy",
        "rationale": "README intro; baseline docs-priority test for explain_project goal",
    },
]


# ── Retrieval engine (mirrors Knowledge Handler) ──────────────────────────────

def embed(text):
    r = requests.post(f"{OLLAMA_URL}/api/embeddings",
                      json={"model": EMBED_MODEL, "prompt": text}, timeout=60)
    return r.json().get("embedding", [])


def qdrant_search(vector, project, k=30, threshold=0.40):
    body = {
        "vector": vector, "limit": k,
        "with_payload": True, "score_threshold": threshold,
        "filter": {"must": [{"key": "metadata.filepath", "match": {"text": project}}]}
    }
    r = requests.post(f"{QDRANT_URL}/collections/project_rag/points/search",
                      json=body, timeout=30)
    return r.json().get("result", [])


def qdrant_scroll(filter_body, limit=25):
    r = requests.post(f"{QDRANT_URL}/collections/project_rag/points/scroll",
                      json={"filter": filter_body, "limit": limit, "with_payload": True},
                      timeout=30)
    return r.json().get("result", {}).get("points", [])


def decompose_query(query):
    r = requests.post(f"{OLLAMA_URL}/api/chat", json={
        "model": CHAT_MODEL,
        "messages": [
            {"role": "system", "content":
             "Rewrite this search query into 3 alternative forms for RAG retrieval. "
             "Return ONLY a JSON array of 3 strings."},
            {"role": "user", "content": query}
        ],
        "stream": False, "options": {"temperature": 0.2, "num_predict": 200}
    }, timeout=60)
    content = r.json().get("message", {}).get("content", "")
    try:
        import re
        m = re.search(r'\[.*?\]', content, re.DOTALL)
        if m:
            return [s for s in json.loads(m.group()) if isinstance(s, str)][:4]
    except Exception:
        pass
    return []


def role_weight(goal, sr, rc):
    w  = (ROLE_MATRIX.get(goal, DEFAULT_W))[ROLE_IDX.get(sr, 0)]
    cd = CONF_DISC.get(rc, 0.80)
    return w * cd


def retrieve(project, goal, query):
    """4-pass retrieval, mirrors Knowledge Handler."""
    # Named-entity routing: check symbol index first for code goals
    CODE_ENTITY_GOALS = {"explain_code","find_in_files","debug_code","review_code"}
    if goal in CODE_ENTITY_GOALS and project:
        ne_chunks, ne_sym = named_entity_route(project, goal, query)
        if ne_chunks and ne_sym:
            avg = sum(c["final"] for c in ne_chunks)/len(ne_chunks) if ne_chunks else 0
            diag = {
                "retrieved_total": len(ne_chunks), "after_dedup": len(ne_chunks),
                "top_n": len(ne_chunks), "avg_score": round(avg,3),
                "confidence": round(min(1.0,avg),2),
                "role_dist": {"source": len(ne_chunks)},
                "top_sources": [{"fp": ne_sym.get("file",""), "sr":"source", "score": round(avg,3)}],
                "query_forms": [], "route": "named_entity:" + ne_sym.get("name","")
            }
            return ne_chunks, diag

    base_vec = embed(query)
    if not base_vec:
        return [], {}

    # Pass 1
    chunks = qdrant_search(base_vec, project, k=30, threshold=0.40)

    # Pass 2 — query decomposition
    forms = decompose_query(query)
    for qf in forms:
        qv = embed(qf)
        if qv:
            chunks += qdrant_search(qv, project, k=10, threshold=0.38)

    # Pass 3 — anchor files
    CODE_ANCHOR_GOALS = {"explain_code","write_code","debug_code","review_code","find_in_files"}
    DOC_GOALS = {"explain_project","project_summary","recall_work"}
    active_anchors = (ANCHOR_PATS if goal in CODE_ANCHOR_GOALS
                      else set() if goal in DOC_GOALS
                      else {"cli.py","main.py"})
    for af in active_anchors:
        pts = qdrant_scroll({
            "must": [
                {"key": "metadata.filepath", "match": {"text": project}},
                {"key": "metadata.filepath", "match": {"text": af}}
            ]
        })
        for p in pts:
            p["_anchor"] = True
            p["score"] = p.get("score") or 0.85
        chunks += pts

    # Pass 4 — dedup + rerank
    # Anchor chunks must survive dedup: if a chunk appears in both semantic
    # search (no anchor) and Pass 3 scroll (anchor), keep anchor version
    seen   = {}   # key → index in deduped
    deduped = []
    for c in chunks:
        pay  = c.get("payload", {})
        text = (pay.get("content") or pay.get("text") or pay.get("pageContent") or "").strip()
        key  = text[:120]
        if not key:
            continue
        fp  = pay.get("metadata", {}).get("filepath", "")
        ext = fp.rsplit(".", 1)[-1].lower()
        sr  = pay.get("source_role") or (
              "source" if ext in ("py","js","ts","jsx","tsx","go") else
              "docs"   if ext in ("md","txt","rst") else "config")
        rc  = pay.get("role_confidence", "MEDIUM")
        if key in seen:
            # Already seen — upgrade to anchor if this occurrence is an anchor
            if c.get("_anchor") and not deduped[seen[key]]["is_anchor"]:
                deduped[seen[key]]["is_anchor"] = True
        else:
            seen[key] = len(deduped)
            deduped.append({
                "text": text, "fp": fp, "sr": sr, "rc": rc,
                "semantic": c.get("score", 0),
                "is_anchor": bool(c.get("_anchor")),
            })

    for d in deduped:
        rw = role_weight(goal, d["sr"], d["rc"])
        CODE_GOALS = {"explain_code","write_code","debug_code","review_code","find_in_files"}
        anch = 1.07 if (d["is_anchor"] and goal in CODE_GOALS) else 1.0  # tiebreaker
        d["final"] = d["semantic"] * rw * anch

    deduped.sort(key=lambda x: x["final"], reverse=True)
    top = deduped[:10]

    avg = sum(c["final"] for c in top) / len(top) if top else 0
    role_dist = {}
    for c in top:
        role_dist[c["sr"]] = role_dist.get(c["sr"], 0) + 1

    diag = {
        "retrieved_total": len(chunks),
        "after_dedup":     len(deduped),
        "top_n":           len(top),
        "avg_score":       round(avg, 3),
        "confidence":      round(min(1.0, avg), 2),
        "role_dist":       role_dist,
        "top_sources":     [
            {"fp": c["fp"].replace("/mnt/data/projects/", ""),
             "sr": c["sr"], "rc": c["rc"],
             "score": round(c["final"], 3)}
            for c in top[:6]
        ],
        "query_forms":     forms,
    }
    return top, diag


def summarize(project, goal, query, chunks):
    if not chunks:
        return ""
    context = "\n\n---\n\n".join(
        f"[{i+1}] ({c['fp'].replace('/mnt/data/projects/','')} | role:{c['sr']} conf:{c['rc']}):\n{c['text'][:500]}"
        for i, c in enumerate(chunks)
    )
    r = requests.post(f"{OLLAMA_URL}/api/chat", json={
        "model": CHAT_MODEL,
        "messages": [
            {"role": "system", "content":
             f"You are a knowledgeable assistant with access to project source code and documentation. "
             f"Answer based only on the provided context. Be specific and accurate. "
             f"If context is incomplete say so. Active project: {project}."},
            {"role": "user",
             "content": f"Context from project files:\n\n{context}\n\nQuestion: {query}"}
        ],
        "stream": False,
        "options": {"temperature": 0.1, "num_predict": 1000}
    }, timeout=120)
    return r.json().get("message", {}).get("content", "").strip()


# ── Evaluation ────────────────────────────────────────────────────────────────

def evaluate(answer, top_chunks, tc):
    answer_lower = answer.lower()
    sources_used = {c["fp"] for c in top_chunks}

    # Fact coverage
    facts_found   = [f for f in tc["expected_facts"]     if f.lower() in answer_lower]
    facts_missing = [f for f in tc["expected_facts"]     if f.lower() not in answer_lower]

    # Hallucination check
    hallucinated  = [f for f in tc.get("expected_not", []) if f.lower() in answer_lower]

    # Source file coverage
    expected_fps = tc.get("expected_source_files", [])
    sources_hit  = [
        ef for ef in expected_fps
        if any(ef in fp for fp in sources_used)
    ]
    sources_miss = [ef for ef in expected_fps if ef not in sources_hit]

    fact_pct   = len(facts_found)   / len(tc["expected_facts"])     if tc["expected_facts"] else 1.0
    source_pct = len(sources_hit)   / len(expected_fps)             if expected_fps else 1.0
    halluc_pct = len(hallucinated)  / len(tc["expected_not"])       if tc["expected_not"] else 0.0

    return {
        "fact_coverage":    round(fact_pct * 100),
        "source_coverage":  round(source_pct * 100),
        "hallucination_pct": round(halluc_pct * 100),
        "facts_found":      facts_found,
        "facts_missing":    facts_missing,
        "hallucinated":     hallucinated,
        "sources_hit":      sources_hit,
        "sources_miss":     sources_miss,
    }


# ── Report ────────────────────────────────────────────────────────────────────

def print_report(results, verbose=False):
    W = 72
    print("=" * W)
    print(f"  RAG Test Suite — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * W)

    total = len(results)
    if total == 0:
        print("  No results.")
        return

    avg_fact  = sum(r["eval"]["fact_coverage"]   for r in results) / total
    avg_src   = sum(r["eval"]["source_coverage"] for r in results) / total
    avg_hall  = sum(r["eval"]["hallucination_pct"] for r in results) / total
    avg_conf  = sum(r["diag"]["confidence"]      for r in results) / total

    print(f"  Tests: {total}   Avg fact coverage: {avg_fact:.0f}%   "
          f"Avg source hit: {avg_src:.0f}%   "
          f"Avg hallucination: {avg_hall:.0f}%   "
          f"Avg confidence: {avg_conf*100:.0f}%")
    print("-" * W)

    for r in results:
        tc   = r["tc"]
        ev   = r["eval"]
        diag = r["diag"]

        fact_bar  = "█" * (ev["fact_coverage"] // 10) + "░" * (10 - ev["fact_coverage"] // 10)
        hall_flag = " ⚠️ HALLUC" if ev["hallucinated"] else ""

        print(f"\n  [{tc['id']}]  difficulty:{tc['difficulty']}  goal:{tc['goal']}")
        print(f"  Q: {tc['query'][:65]}")
        print(f"  Facts:   {fact_bar} {ev['fact_coverage']}%   "
              f"Sources: {ev['source_coverage']}%   "
              f"Confidence: {diag['confidence']*100:.0f}%{hall_flag}")
        print(f"  Roles: {diag['role_dist']}   "
              f"Retrieved: {diag['retrieved_total']} → dedup: {diag['after_dedup']} → top: {diag['top_n']}")

        if ev["facts_missing"]:
            print(f"  ✗ Missing facts: {ev['facts_missing']}")
        if ev["hallucinated"]:
            print(f"  ✗ Hallucinated: {ev['hallucinated']}")
        if ev["sources_miss"]:
            print(f"  ✗ Expected sources not hit: {ev['sources_miss']}")

        if verbose:
            print(f"\n  Top sources:")
            for s in diag["top_sources"]:
                print(f"    {s['score']:.3f}  {s['sr']:6} {s['rc']:6}  {s['fp']}")
            print(f"\n  Answer:\n  " + r["answer"][:400].replace("\n", "\n  "))

    print("\n" + "=" * W)
    print(f"  Summary: fact_coverage={avg_fact:.0f}%  "
          f"source_coverage={avg_src:.0f}%  "
          f"hallucination={avg_hall:.0f}%  "
          f"confidence={avg_conf*100:.0f}%")
    print("=" * W)


# ── Runner ────────────────────────────────────────────────────────────────────


def named_entity_route(project, goal, query):
    """
    If the query references a known symbol name (CLI command, tool, route),
    look it up in the symbol index and retrieve implementation chunks from
    the specific file. Returns chunks or empty list if no match.
    """
    # Scroll all symbols for this project
    r = requests.post(f"{QDRANT_URL}/collections/symbol_index/points/scroll", json={
        "filter": {"must": [{"key": "project", "match": {"value": project}}]},
        "limit": 200, "with_payload": True
    }, timeout=10)
    syms = r.json().get("result", {}).get("points", [])
    if not syms:
        return [], None

    # Find a symbol whose name appears in the query
    query_lower = query.lower()
    matched = next(
        (s["payload"] for s in syms
         if len(s["payload"].get("name","")) > 2
         and s["payload"]["name"].lower() in query_lower),
        None
    )
    if not matched:
        return [], None

    # Targeted semantic search within the symbol's file
    vec = embed(query)
    if not vec:
        return [], matched

    filename = matched.get("file","").split("/")[-1]
    r2 = requests.post(f"{QDRANT_URL}/collections/project_rag/points/search", json={
        "vector": vec, "limit": 8, "with_payload": True, "score_threshold": 0.25,
        "filter": {"must": [
            {"key": "metadata.filepath", "match": {"text": project}},
            {"key": "metadata.filepath", "match": {"text": filename}}
        ]}
    }, timeout=30)
    impl_chunks = r2.json().get("result", [])

    # Convert to the same dict shape as retrieve() output
    result = []
    for c in impl_chunks:
        pay  = c.get("payload", {})
        text = (pay.get("content") or pay.get("text") or pay.get("pageContent") or "").strip()
        fp   = pay.get("metadata", {}).get("filepath", "")
        ext  = fp.rsplit(".", 1)[-1].lower()
        sr   = pay.get("source_role") or ("source" if ext in ("py","js","ts") else "docs")
        rc   = pay.get("role_confidence", "HIGH")
        result.append({"text": text, "fp": fp, "sr": sr, "rc": rc,
                       "semantic": c.get("score", 0), "is_anchor": False,
                       "final": c.get("score", 0)})
    return result, matched


def run(project_filter=None, verbose=False):
    cases = TEST_CASES
    if project_filter:
        cases = [tc for tc in cases if tc["project"] == project_filter]

    results = []
    for i, tc in enumerate(cases):
        print(f"  [{i+1}/{len(cases)}] {tc['id']} ... ", end="", flush=True)
        try:
            chunks, diag = retrieve(tc["project"], tc["goal"], tc["query"])
            answer       = summarize(tc["project"], tc["goal"], tc["query"], chunks)
            ev           = evaluate(answer, chunks, tc)
            results.append({"tc": tc, "eval": ev, "diag": diag, "answer": answer})
            flag = "✓" if ev["fact_coverage"] >= 70 and not ev["hallucinated"] else "✗"
            print(f"{flag}  facts:{ev['fact_coverage']}%  hall:{ev['hallucination_pct']}%")
        except Exception as e:
            print(f"ERROR: {e}")
            results.append({"tc": tc, "eval": {
                "fact_coverage": 0, "source_coverage": 0, "hallucination_pct": 0,
                "facts_found": [], "facts_missing": tc["expected_facts"],
                "hallucinated": [], "sources_hit": [], "sources_miss": tc.get("expected_source_files", [])
            }, "diag": {"confidence": 0, "role_dist": {}, "retrieved_total": 0,
                        "after_dedup": 0, "top_n": 0, "top_sources": [], "query_forms": []},
            "answer": ""})

    print()
    print_report(results, verbose=verbose)

    # Write results to file
    out_path = "/mnt/data/projects/telegram-agent/memory/rag_test_results.jsonl"
    with open(out_path, "a") as f:
        for r in results:
            f.write(json.dumps({
                "ts": datetime.now().isoformat(),
                "id": r["tc"]["id"],
                "fact_coverage": r["eval"]["fact_coverage"],
                "source_coverage": r["eval"]["source_coverage"],
                "hallucination_pct": r["eval"]["hallucination_pct"],
                "confidence": r["diag"]["confidence"],
                "role_dist": r["diag"]["role_dist"],
            }) + "\n")
    print(f"\n  Results appended to {out_path}")


def rank_analysis(project_filter=None):
    """
    For each failed test case: retrieve k=200, find where the chunk
    containing each expected fact first appears. Distinguishes recall
    failure (chunk never in candidate set) from ranking failure (chunk
    present but ranked low).
    """
    cases = [tc for tc in TEST_CASES
             if (not project_filter or tc["project"] == project_filter)]

    W = 72
    print("=" * W)
    print(f"  Rank Analysis — top 200 candidates — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * W)

    for tc in cases:
        print(f"\n  [{tc['id']}]  {tc['query'][:60]}")
        base_vec = embed(tc["query"])
        if not base_vec:
            print("  ERROR: embedding failed"); continue

        # Retrieve k=200 without dedup or reranking
        r = requests.post(f"{QDRANT_URL}/collections/project_rag/points/search", json={
            "vector": base_vec,
            "limit": 200,
            "with_payload": True,
            "score_threshold": 0.0,
            "filter": {"must": [{"key": "metadata.filepath",
                                  "match": {"text": tc["project"]}}]}
        }, timeout=60)
        candidates = r.json().get("result", [])

        # For each expected fact, find the first candidate rank containing it
        fact_ranks = {}
        for fact in tc["expected_facts"]:
            for rank, cand in enumerate(candidates, start=1):
                pay  = cand.get("payload", {})
                text = (pay.get("content") or pay.get("text") or
                        pay.get("pageContent") or "").lower()
                if fact.lower() in text:
                    fact_ranks[fact] = {
                        "rank": rank,
                        "score": round(cand.get("score", 0), 3),
                        "fp": pay.get("metadata", {}).get("filepath","").replace("/mnt/data/projects/",""),
                        "sr": pay.get("source_role", "?")
                    }
                    break
            else:
                fact_ranks[fact] = None  # never found

        # Determine recall vs ranking problem
        found_in_200 = {f: v for f, v in fact_ranks.items() if v is not None}
        not_in_200   = [f for f, v in fact_ranks.items() if v is None]
        in_top_10    = {f: v for f, v in found_in_200.items() if v["rank"] <= 10}
        in_11_100    = {f: v for f, v in found_in_200.items() if 10 < v["rank"] <= 100}
        in_101_200   = {f: v for f, v in found_in_200.items() if v["rank"] > 100}

        print(f"  Total candidates: {len(candidates)}  |  "
              f"Facts in top-10: {len(in_top_10)}  "
              f"ranks 11-100: {len(in_11_100)}  "
              f"ranks 101-200: {len(in_101_200)}  "
              f"NEVER found: {len(not_in_200)}")

        # Diagnosis
        if len(not_in_200) > 0:
            print(f"  🔴 RECALL FAILURE — {len(not_in_200)} facts never in top 200:")
            for f in not_in_200:
                print(f"       · {f}")
        if in_11_100:
            print(f"  🟡 RANKING FAILURE — {len(in_11_100)} facts present but ranked too low:")
            for f, v in sorted(in_11_100.items(), key=lambda x: x[1]["rank"]):
                print(f"       rank {v['rank']:3d}  score:{v['score']}  {v['sr']:6}  {v['fp'][:55]}  → '{f}'")
        if in_top_10:
            print(f"  🟢 Already in top-10:")
            for f, v in sorted(in_top_10.items(), key=lambda x: x[1]["rank"]):
                print(f"       rank {v['rank']:3d}  score:{v['score']}  {v['sr']:6}  → '{f}'")

        # Verdict
        if len(not_in_200) > len(found_in_200):
            verdict = "PRIMARY: recall (most facts never retrieved at k=200)"
        elif in_11_100 and not not_in_200:
            verdict = "PRIMARY: ranking (all facts retrievable, wrong order)"
        elif not_in_200 and in_11_100:
            verdict = "MIXED: recall + ranking"
        else:
            verdict = "OK"
        print(f"  → {verdict}")

    print("\n" + "=" * W)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", help="Filter to one project")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--rank", action="store_true",
                        help="Run rank analysis (recall vs ranking diagnosis)")
    args = parser.parse_args()
    if args.rank:
        rank_analysis(project_filter=args.project)
    else:
        run(project_filter=args.project, verbose=args.verbose)
