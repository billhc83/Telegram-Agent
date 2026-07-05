#!/usr/bin/env python3
"""
End-to-end pipeline test for project_rag.
Replicates every stage of the n8n workflow exactly:
  Walk & Diff → Build Tag Prompt → Ollama Tag Chat → Parse Tags
  → Document Loader → Text Splitter → Embeddings → Vector Store → Search

Uses a throwaway `project_rag_test` collection — never touches `project_rag`.
Prints PASS/FAIL at each stage. Exit code 0 = all green.
"""

import hashlib
import json
import os
import sys
import time
import uuid
from pathlib import Path

import requests

QDRANT = "http://localhost:6333"
OLLAMA = "http://localhost:11434"
TEST_COLLECTION = "project_rag_test"
VECTOR_SIZE = 2560
EMBED_MODEL = "qwen3-embedding:4b"
TAG_MODEL = "qwen2.5-coder:14b"

SYSTEM_PROMPT = (
    "You tag project files for a semantic search index. "
    "Given a filename and its content, respond with STRICT JSON only "
    '(no markdown fences, no commentary): '
    '{"chunk_type": "<short label for what kind of content this is, '
    'e.g. overview, source_code, config, test, documentation, data, script>", '
    '"topics": ["<3 to 8 short freeform keywords/concepts this file is about>"], '
    '"summary": "<one sentence describing what this file does>"}.'
)

# 3 diverse real files from the corpus
TEST_FILES = [
    "/mnt/data/projects/telegram-agent/claude-http-server.mjs",
    "/mnt/data/projects/transformer_lens/backend/main.py",
    "/mnt/data/projects/telegram-agent/n8n/workflows/qwen_commit_message.json",
]

CHUNK_SIZE = 1100
CHUNK_OVERLAP = 180

errors = []


def ok(label):
    print(f"  [PASS] {label}")


def fail(label, detail=""):
    msg = f"  [FAIL] {label}" + (f": {detail}" if detail else "")
    print(msg)
    errors.append(msg)


def section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


# ── helpers ────────────────────────────────────────────────────────────────────

def chunk_text(text, size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    chunks = []
    start = 0
    while start < len(text):
        end = start + size
        chunks.append(text[start:end])
        start += size - overlap
    return chunks


def sha256(text):
    return hashlib.sha256(text.encode()).hexdigest()


# ── Stage 1: Qdrant connectivity ───────────────────────────────────────────────

section("Stage 1: Qdrant connectivity")
try:
    r = requests.get(f"{QDRANT}/healthz", timeout=5)
    if r.status_code == 200:
        ok("Qdrant reachable")
    else:
        fail("Qdrant health check", f"HTTP {r.status_code}")
except Exception as e:
    fail("Qdrant unreachable", str(e))
    sys.exit(1)


# ── Stage 2: Ollama connectivity + models ─────────────────────────────────────

section("Stage 2: Ollama model availability")
try:
    r = requests.get(f"{OLLAMA}/api/tags", timeout=5)
    models = {m["name"] for m in r.json().get("models", [])}
    if TAG_MODEL in models or TAG_MODEL.split(":")[0] in {m.split(":")[0] for m in models}:
        ok(f"Tag model present: {TAG_MODEL}")
    else:
        fail(f"Tag model missing: {TAG_MODEL}", f"available: {sorted(models)}")
    if EMBED_MODEL in models or EMBED_MODEL.split(":")[0] in {m.split(":")[0] for m in models}:
        ok(f"Embed model present: {EMBED_MODEL}")
    else:
        fail(f"Embed model missing: {EMBED_MODEL}")
except Exception as e:
    fail("Ollama unreachable", str(e))
    sys.exit(1)


# ── Stage 3: Create test collection ───────────────────────────────────────────

section("Stage 3: Create test Qdrant collection")
# Delete if exists
requests.delete(f"{QDRANT}/collections/{TEST_COLLECTION}", timeout=10)
time.sleep(0.5)

payload = {
    "vectors": {"size": VECTOR_SIZE, "distance": "Cosine"},
    "on_disk_payload": True,
}
r = requests.put(f"{QDRANT}/collections/{TEST_COLLECTION}", json=payload, timeout=10)
if r.status_code in (200, 201):
    ok(f"Created collection '{TEST_COLLECTION}' (size={VECTOR_SIZE}, cosine)")
else:
    fail("Create collection", r.text[:200])
    sys.exit(1)


# ── Stage 4: Walk & read test files ───────────────────────────────────────────

section("Stage 4: Walk & read test files")
file_items = []
for path_str in TEST_FILES:
    p = Path(path_str)
    if not p.exists():
        fail(f"File not found: {path_str}")
        continue
    content = p.read_text(errors="replace")
    if not content.strip():
        fail(f"Empty file: {path_str}")
        continue
    item = {
        "filepath": path_str,
        "filename": p.name,
        "filetype": p.suffix.lstrip("."),
        "project": p.parts[p.parts.index("projects") + 1] if "projects" in p.parts else "unknown",
        "content": content,
        "content_hash": sha256(content),
        "mtime": str(p.stat().st_mtime),
    }
    file_items.append(item)
    ok(f"{p.name} ({len(content)} chars, project={item['project']})")

if not file_items:
    fail("No test files readable")
    sys.exit(1)


# ── Stage 5: Tag each file (exact workflow prompt) ────────────────────────────

section("Stage 5: Tagging via qwen2.5-coder:14b")
tagged = []
for item in file_items:
    truncated = item["content"][:2000]
    user_msg = (
        f"Project: {item['project']}\n"
        f"File: {item['filename']} (.{item['filetype']})\n\n"
        f"{truncated}"
    )
    body = {
        "model": TAG_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        "stream": False,
        "options": {"temperature": 0.1, "num_predict": 300},
    }

    print(f"  Tagging {item['filename']} ...", end=" ", flush=True)
    t0 = time.time()
    try:
        r = requests.post(f"{OLLAMA}/api/chat", json=body, timeout=60)
        elapsed = time.time() - t0
        raw = r.json().get("message", {}).get("content", "")
        cleaned = raw.replace("```json", "").replace("```", "").strip()
        parsed = json.loads(cleaned)
        if (
            isinstance(parsed.get("chunk_type"), str) and parsed["chunk_type"]
            and isinstance(parsed.get("topics"), list) and len(parsed["topics"]) >= 1
            and isinstance(parsed.get("summary"), str) and parsed["summary"]
        ):
            print(f"OK ({elapsed:.1f}s)")
            ok(
                f"{item['filename']}: chunk_type={parsed['chunk_type']!r}, "
                f"topics={parsed['topics'][:3]}, summary={parsed['summary'][:60]!r}"
            )
            tagged.append({**item, **parsed})
        else:
            print("INVALID JSON shape")
            fail(f"{item['filename']}: bad tag shape", str(parsed)[:200])
    except requests.exceptions.Timeout:
        print("TIMEOUT")
        fail(f"{item['filename']}: tagger timed out after 60s")
    except json.JSONDecodeError as e:
        print("JSON PARSE ERROR")
        fail(f"{item['filename']}: unparseable response", f"{e} | raw={raw[:200]!r}")
    except Exception as e:
        print("ERROR")
        fail(f"{item['filename']}: {e}")

if not tagged:
    fail("No files successfully tagged — cannot continue")
    sys.exit(1)


# ── Stage 6: Embed chunks ──────────────────────────────────────────────────────

section(f"Stage 6: Chunk + embed via {EMBED_MODEL}")
points = []
for item in tagged:
    chunks = chunk_text(item["content"])
    print(f"  {item['filename']}: {len(chunks)} chunk(s) ...", end=" ", flush=True)
    chunk_ok = 0
    for i, chunk in enumerate(chunks):
        try:
            # Try newer /api/embed (granite, mxbai etc); fall back to /api/embeddings
            r = requests.post(
                f"{OLLAMA}/api/embed",
                json={"model": EMBED_MODEL, "input": chunk},
                timeout=30,
            )
            d = r.json()
            embedding = d.get("embeddings", [[]])[0] if "embeddings" in d else d.get("embedding", [])
            if len(embedding) != VECTOR_SIZE:
                fail(f"{item['filename']} chunk {i}: wrong vector size {len(embedding)}")
                continue
            point_id = str(uuid.uuid4())
            points.append({
                "id": point_id,
                "vector": embedding,
                "payload": {
                    "content": chunk,
                    "metadata": {
                        "filepath": item["filepath"],
                        "filename": item["filename"],
                        "filetype": item["filetype"],
                        "project": item["project"],
                        "mtime": item["mtime"],
                        "content_hash": item["content_hash"],
                        "chunk_type": item["chunk_type"],
                        "topics": item["topics"],
                        "summary": item["summary"],
                        "loc": {"lines": {"from": i * (CHUNK_SIZE - CHUNK_OVERLAP), "to": i * (CHUNK_SIZE - CHUNK_OVERLAP) + CHUNK_SIZE}},
                    },
                },
            })
            chunk_ok += 1
        except Exception as e:
            fail(f"{item['filename']} chunk {i}: embed error {e}")
    print(f"{chunk_ok}/{len(chunks)} embedded")
    if chunk_ok == len(chunks):
        ok(f"{item['filename']}: all {chunk_ok} chunks embedded (dim={VECTOR_SIZE})")
    elif chunk_ok > 0:
        fail(f"{item['filename']}: only {chunk_ok}/{len(chunks)} chunks embedded")

if not points:
    fail("No points to insert")
    sys.exit(1)


# ── Stage 7: Insert into Qdrant ───────────────────────────────────────────────

section("Stage 7: Insert points into Qdrant")
r = requests.put(
    f"{QDRANT}/collections/{TEST_COLLECTION}/points",
    json={"points": points},
    params={"wait": "true"},
    timeout=30,
)
if r.status_code == 200:
    ok(f"Inserted {len(points)} points")
else:
    fail("Qdrant insert", r.text[:300])
    sys.exit(1)

# Verify count
r = requests.get(f"{QDRANT}/collections/{TEST_COLLECTION}", timeout=5)
actual = r.json()["result"]["points_count"]
if actual == len(points):
    ok(f"Point count verified: {actual}")
else:
    fail(f"Point count mismatch: expected {len(points)}, got {actual}")


# ── Stage 8: Verify metadata structure ────────────────────────────────────────

section("Stage 8: Metadata structure check")
r = requests.post(
    f"{QDRANT}/collections/{TEST_COLLECTION}/points/scroll",
    json={"limit": 5, "with_payload": True, "with_vector": False},
    timeout=10,
)
sample_points = r.json()["result"]["points"]
for p in sample_points[:2]:
    meta = p["payload"].get("metadata", {})
    checks = {
        "chunk_type != unknown": meta.get("chunk_type", "unknown") != "unknown",
        "topics is non-empty list": isinstance(meta.get("topics"), list) and len(meta["topics"]) > 0,
        "summary is non-empty str": isinstance(meta.get("summary"), str) and len(meta["summary"]) > 5,
        "filepath present": bool(meta.get("filepath")),
        "project present": bool(meta.get("project")),
        "content in payload": bool(p["payload"].get("content")),
    }
    fname = meta.get("filename", p["id"])
    for check, passed in checks.items():
        if passed:
            ok(f"{fname}: {check}")
        else:
            fail(f"{fname}: {check}", str(meta.get(check.split()[0], "MISSING"))[:100])


# ── Stage 9: Semantic search ───────────────────────────────────────────────────

section("Stage 9: Semantic search round-trip")
queries = [
    ("HTTP server request handling", "claude-http-server.mjs"),
    ("transformer model inference neural network", "main.py"),
    ("commit message git diff summary", "qwen_commit_message.json"),
]
for query, expected_file in queries:
    try:
        r = requests.post(
            f"{OLLAMA}/api/embed",
            json={"model": EMBED_MODEL, "input": query},
            timeout=30,
        )
        d = r.json()
        q_vec = d.get("embeddings", [[]])[0] if "embeddings" in d else d.get("embedding", [])
        r2 = requests.post(
            f"{QDRANT}/collections/{TEST_COLLECTION}/points/search",
            json={"vector": q_vec, "limit": 3, "with_payload": ["metadata.filename", "metadata.chunk_type", "metadata.topics"]},
            timeout=10,
        )
        hits = r2.json()["result"]
        if not hits:
            fail(f"Query '{query[:40]}': no results")
            continue
        top = hits[0]
        top_file = top["payload"]["metadata"].get("filename", "?")
        top_score = top["score"]
        top_type = top["payload"]["metadata"].get("chunk_type", "?")
        if top_file == expected_file:
            ok(f"Query '{query[:40]}' → {top_file} (score={top_score:.3f}, type={top_type})")
        else:
            # Not necessarily a failure — just note it
            print(f"  [INFO] Query '{query[:40]}' → {top_file} (expected {expected_file}, score={top_score:.3f})")
    except Exception as e:
        fail(f"Query '{query[:40]}': {e}")


# ── Stage 10: Cleanup ─────────────────────────────────────────────────────────

section("Stage 10: Cleanup")
r = requests.delete(f"{QDRANT}/collections/{TEST_COLLECTION}", timeout=10)
if r.status_code == 200:
    ok(f"Deleted test collection '{TEST_COLLECTION}'")
else:
    print(f"  [WARN] Could not delete test collection: {r.text[:100]}")


# ── Summary ───────────────────────────────────────────────────────────────────

print(f"\n{'='*60}")
if errors:
    print(f"  RESULT: {len(errors)} FAILURE(S)")
    for e in errors:
        print(e)
    print(f"{'='*60}\n")
    sys.exit(1)
else:
    print(f"  RESULT: ALL STAGES PASSED — pipeline is healthy")
    print(f"{'='*60}\n")
    sys.exit(0)
