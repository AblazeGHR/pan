import json, math, random, time
random.seed(42)
from packages.core.memory.search import HybridSearcher

# 20k random 768-dim vectors (realistic ST dims) + a query
N, D = 20000, 768
chunks = []
for i in range(N):
    v = [random.uniform(-1, 1) for _ in range(D)]
    chunks.append({"id": f"c{i:05d}", "path": f"/m/{i}.md", "source": "memory",
                   "start_line": 1, "end_line": 2, "text": f"text {i}",
                   "embedding": json.dumps(v)})
query = [random.uniform(-1, 1) for _ in range(D)]

class FakeStore:
    def get_chunks_for_search(self): return chunks
    def search_fts(self, q, m): return []

class FakeEmbedder:
    dims = D
    def embed(self, text): return query

searcher = HybridSearcher(FakeStore(), FakeEmbedder())

# numpy path
t0 = time.perf_counter()
res_np = searcher.search("q", min_score=0.0, max_results=5)
t_np = time.perf_counter() - t0
print(f"numpy: {t_np*1000:.1f}ms top={[(r.chunk_id, round(r.score,4)) for r in res_np]}")

# pure-python reference (direct cosine loop)
t0 = time.perf_counter()
scores = {}
for c in chunks:
    emb = json.loads(c["embedding"])
    dot = sum(x*y for x,y in zip(query, emb))
    na = math.sqrt(sum(x*x for x in query)); nb = math.sqrt(sum(x*x for x in emb))
    scores[c["id"]] = max(0.0, dot/(na*nb)) if na and nb else 0.0
top_pp = sorted(scores.items(), key=lambda kv: -kv[1])[:5]
t_pp = time.perf_counter() - t0
print(f"pure-py: {t_pp*1000:.1f}ms top={[(k, round(v,4)) for k,v in top_pp]}")

# identical top-5?
assert [r.chunk_id for r in res_np] == [k for k, _ in top_pp], "top-N mismatch"
for r, (k, v) in zip(res_np, top_pp):
    assert abs(r.score - v) < 1e-9, (r.score, v)
print(f"results identical; speedup {t_pp/t_np:.1f}x")
