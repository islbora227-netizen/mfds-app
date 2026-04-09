"""
식약처 안내서/지침 AI 검색 웹
- Claude API 사용
- PDF 저장 없이 식약처 원문 링크 제공
- Railway 배포용
"""

import os, re, sqlite3, time
import anthropic
from flask import Flask, request, jsonify, render_template_string

# ─────────────────────────────────────────────
# 설정값 (환경변수로 읽기 - Railway에서 설정)
# ─────────────────────────────────────────────
DB_PATH        = os.environ.get("DB_PATH", "data/mfds.db")
CLAUDE_API_KEY = os.environ.get("CLAUDE_API_KEY", "")
TOP_K          = 5
MAX_CHARS      = 6000

app    = Flask(__name__)
client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)


def extract_keywords(query: str) -> str:
    cleaned = re.sub(r"[?？!！.,;:'\"\(\)\[\]{}/\\@#$%^&*+=~`|<>]", " ", query)
    stopwords = {
        "이","가","을","를","은","는","의","에","도","로","으로",
        "와","과","하고","이고","이며","하는","하면","하여","해서",
        "어떻게","어떤","무엇","언제","어디","왜","뭔가","뭐가",
        "알려줘","알려주세요","알고싶어","궁금해","뭔지","뭔가요",
        "있나요","있어요","있을까요","되나요","되는지","해야하나요",
        "해야하는지","필요한가요","무엇인가요","무엇인지","어떠한",
        "관련","대한","위한","대해","있는","없는","같은","경우",
        "그리고","또한","그런데","하지만","그러나","따라서",
    }
    words = cleaned.split()
    keywords = [w for w in words if len(w) >= 2 and w not in stopwords]
    return " OR ".join(keywords) if keywords else cleaned


def search_db(query: str, top_k: int = TOP_K) -> list:
    fts_query = extract_keywords(query)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("""
            SELECT d.id AS doc_id, d.title, d.date, d.filename,
                   d.source_url, f.page_num, f.content, rank AS score
            FROM pages_fts f
            JOIN documents d ON d.id = f.doc_id
            WHERE pages_fts MATCH ?
            ORDER BY rank
            LIMIT ?
        """, (fts_query, top_k * 3)).fetchall()

        docs = {}
        for row in rows:
            did = row["doc_id"]
            if did not in docs:
                docs[did] = {
                    "doc_id": did, "title": row["title"],
                    "date": row["date"], "filename": row["filename"],
                    "source_url": row["source_url"],
                    "pages": [], "score": row["score"],
                }
            if len(docs[did]["pages"]) < 3:
                docs[did]["pages"].append({
                    "page_num": row["page_num"],
                    "content":  row["content"][:400]
                })
        return list(docs.values())[:top_k]

    except Exception as e:
        print(f"DB 검색 오류: {e}")
        return []
    finally:
        conn.close()


def get_full_content(doc_id: int, max_chars: int = MAX_CHARS) -> str:
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("""
        SELECT content FROM pages
        WHERE doc_id = ? ORDER BY page_num
    """, (doc_id,)).fetchall()
    conn.close()
    return "\n".join(r[0] for r in rows)[:max_chars]


def ask_ai(query: str, search_results: list) -> str:
    if not search_results:
        return (
            "❌ 관련 문서를 찾지 못했습니다.\n\n"
            "다른 키워드로 다시 검색해보세요.\n"
            "예) '임상시험' 대신 '임상시험 계획서 승인'처럼 구체적으로 입력하면 더 잘 찾습니다."
        )

    context_parts = []
    for i, doc in enumerate(search_results[:3], 1):
        full_text = get_full_content(doc["doc_id"])
        context_parts.append(f"[문서{i}: {doc['title']} ({doc['date']})]\n{full_text}")
    context = "\n\n---\n\n".join(context_parts)

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1500,
            system=(
                "당신은 식품의약품안전처 안내서/지침 문서 검색 도우미입니다.\n\n"
                "【절대 규칙】\n"
                "1. 아래 제공된 [문서] 내용만을 근거로 답변하세요.\n"
                "2. 문서에 없는 내용은 절대 답변하지 마세요.\n"
                "3. 문서에서 찾을 수 없으면 '제공된 문서에서 해당 내용을 찾을 수 없습니다.'라고만 답하세요.\n"
                "4. 답변 마지막에 '📄 참고문서: [문서명]' 형식으로 출처를 표시하세요.\n\n"
                "【답변 형식】\n"
                "- 핵심 내용을 먼저 답변\n"
                "- 필요시 번호 목록으로 정리\n"
                "- 간결하고 명확하게 작성"
            ),
            messages=[{"role": "user", "content": f"[제공 문서]\n{context}\n\n[질문]\n{query}"}]
        )
        return response.content[0].text
    except Exception as e:
        return f"AI 답변 생성 중 오류가 발생했습니다: {e}"


HTML = """
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>식약처 안내서/지침 AI 검색</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Apple SD Gothic Neo','맑은 고딕',sans-serif;
         background:#f4f6f9; color:#333; }
  .header { background:#005bac; color:white; padding:20px 40px; }
  .header h1 { font-size:22px; font-weight:700; }
  .header p  { font-size:13px; opacity:0.8; margin-top:4px; }
  .container { max-width:900px; margin:30px auto; padding:0 20px; }
  .search-box { background:white; border-radius:12px; padding:24px;
                box-shadow:0 2px 12px rgba(0,0,0,0.08); margin-bottom:24px; }
  .search-box textarea { width:100%; border:1.5px solid #d0d7e2;
    border-radius:8px; padding:12px; font-size:15px; resize:vertical;
    min-height:80px; font-family:inherit; outline:none; }
  .search-box textarea:focus { border-color:#005bac; }
  .search-btn { margin-top:12px; background:#005bac; color:white; border:none;
    border-radius:8px; padding:12px 32px; font-size:15px; cursor:pointer;
    font-weight:600; transition:background 0.2s; }
  .search-btn:hover { background:#004a8f; }
  .search-btn:disabled { background:#aaa; cursor:not-allowed; }
  .search-tip { font-size:12px; color:#999; margin-top:8px; }
  .example-queries { margin-top:12px; }
  .example-queries span { font-size:12px; color:#888; }
  .example-queries button { margin:4px; padding:5px 10px;
    border:1px solid #d0d7e2; border-radius:20px; background:white;
    cursor:pointer; font-size:12px; color:#555; transition:all 0.2s; }
  .example-queries button:hover { border-color:#005bac; color:#005bac; }
  .loading { text-align:center; padding:40px; color:#666; display:none; }
  .spinner { display:inline-block; width:28px; height:28px;
    border:3px solid #e0e0e0; border-top-color:#005bac;
    border-radius:50%; animation:spin 0.8s linear infinite; }
  @keyframes spin { to { transform:rotate(360deg); } }
  .result-section { display:none; }
  .stats-bar { background:#e8f4fd; border-radius:8px; padding:10px 16px;
    font-size:13px; color:#005bac; margin-bottom:20px; }
  .ai-answer { background:white; border-radius:12px; padding:24px;
    box-shadow:0 2px 12px rgba(0,0,0,0.08); margin-bottom:24px; }
  .ai-answer h2 { font-size:16px; color:#005bac; margin-bottom:14px; }
  .ai-answer .answer-text { font-size:15px; line-height:1.8; white-space:pre-wrap; }
  .docs-section h2 { font-size:15px; color:#666; margin-bottom:12px; }
  .doc-card { background:white; border-radius:10px; padding:18px;
    box-shadow:0 2px 8px rgba(0,0,0,0.06); margin-bottom:12px;
    border-left:4px solid #005bac; }
  .doc-card h3 { font-size:15px; font-weight:600; margin-bottom:6px; }
  .doc-card .meta { font-size:12px; color:#888; margin-bottom:10px; }
  .doc-card .snippet { font-size:13px; color:#555; line-height:1.6;
    background:#f8f9fb; padding:10px; border-radius:6px; margin-top:6px; }
  .page-badge { display:inline-block; background:#e8f0fe; color:#005bac;
    font-size:11px; padding:2px 8px; border-radius:10px; margin:4px 0; }
  .doc-actions { display:flex; gap:8px; margin-top:12px; flex-wrap:wrap; }
  .btn-source { display:inline-flex; align-items:center; gap:5px;
    padding:7px 14px; border-radius:6px; font-size:13px; font-weight:600;
    cursor:pointer; text-decoration:none; background:#f1f3f4; color:#555;
    border:none; transition:all 0.2s; }
  .btn-source:hover { background:#005bac; color:white; }
  .no-result { text-align:center; padding:40px; color:#888; }
</style>
</head>
<body>

<div class="header">
  <h1>🔍 식품의약품안전처 안내서/지침 AI 검색</h1>
  <p>수집된 문서를 AI가 분석하여 질문에 답변합니다 (문서 외 내용은 답변하지 않습니다)</p>
</div>

<div class="container">
  <div class="search-box">
    <textarea id="query"
      placeholder="질문을 자유롭게 입력하세요&#10;예) 임상시험 계획서 제출 절차가 어떻게 되나요?"></textarea>
    <div style="display:flex;align-items:center;gap:12px;margin-top:12px;">
      <button class="search-btn" id="searchBtn" onclick="doSearch()">AI 검색</button>
    </div>
    <p class="search-tip">💡 Ctrl+Enter로도 검색할 수 있습니다.</p>
    <div class="example-queries">
      <span>예시: </span>
      <button onclick="setQuery(this)">임상시험 계획서 제출 절차가 어떻게 되나요?</button>
      <button onclick="setQuery(this)">의약품 제조업 허가 요건은 무엇인가요?</button>
      <button onclick="setQuery(this)">화장품 안전기준에 대해 알려주세요</button>
      <button onclick="setQuery(this)">바이오의약품 GMP 기준이 궁금합니다</button>
    </div>
  </div>

  <div class="loading" id="loading">
    <div class="spinner"></div>
    <p style="margin-top:12px;">문서를 분석하고 있습니다...</p>
  </div>

  <div class="result-section" id="resultSection">
    <div class="stats-bar" id="statsBar"></div>
    <div class="ai-answer">
      <h2>🤖 AI 답변</h2>
      <div class="answer-text" id="aiAnswer"></div>
    </div>
    <div class="docs-section">
      <h2>📄 참고 문서</h2>
      <div id="docList"></div>
    </div>
  </div>
</div>

<script>
function setQuery(btn) {
  document.getElementById('query').value = btn.textContent;
}

async function doSearch() {
  const query = document.getElementById('query').value.trim();
  if (!query) { alert('검색어를 입력하세요.'); return; }

  document.getElementById('searchBtn').disabled = true;
  document.getElementById('loading').style.display = 'block';
  document.getElementById('resultSection').style.display = 'none';

  try {
    const res = await fetch('/api/search', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query })
    });
    const data = await res.json();
    document.getElementById('loading').style.display = 'none';
    if (data.error) { alert('오류: ' + data.error); return; }

    document.getElementById('statsBar').textContent =
      `검색어: "${query}" | 관련 문서 ${data.doc_count}건 | 검색 시간 ${data.elapsed}초`;
    document.getElementById('aiAnswer').textContent = data.ai_answer;

    const docList = document.getElementById('docList');
    docList.innerHTML = '';

    if (!data.docs || data.docs.length === 0) {
      docList.innerHTML = '<div class="no-result">관련 문서를 찾지 못했습니다.</div>';
    } else {
      data.docs.forEach((doc, i) => {
        const pages = doc.pages.map(p =>
          `<div>
             <span class="page-badge">p.${p.page_num}</span>
             <div class="snippet">${p.content}</div>
           </div>`
        ).join('');

        const srcBtn = doc.source_url
          ? `<a class="btn-source" href="${doc.source_url}"
               target="_blank" rel="noopener">🔗 식약처 원문 보기</a>`
          : '';

        docList.innerHTML += `
          <div class="doc-card">
            <h3>${i+1}. ${doc.title}</h3>
            <div class="meta">📅 ${doc.date||'-'} &nbsp;|&nbsp; 📁 ${doc.filename}</div>
            ${pages}
            <div class="doc-actions">${srcBtn}</div>
          </div>`;
      });
    }
    document.getElementById('resultSection').style.display = 'block';

  } catch(e) {
    alert('서버 오류: ' + e.message);
  } finally {
    document.getElementById('searchBtn').disabled = false;
    document.getElementById('loading').style.display = 'none';
  }
}

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('query').addEventListener('keydown', e => {
    if (e.key === 'Enter' && e.ctrlKey) doSearch();
  });
});
</script>
</body>
</html>
"""

@app.route("/")
def index():
    return render_template_string(HTML)

@app.route("/api/search", methods=["POST"])
def api_search():
    data  = request.get_json()
    query = data.get("query", "").strip()
    if not query:
        return jsonify({"error": "검색어가 없습니다."}), 400

    start     = time.time()
    results   = search_db(query)
    ai_answer = ask_ai(query, results)
    elapsed   = round(time.time() - start, 1)

    return jsonify({
        "query":     query,
        "doc_count": len(results),
        "elapsed":   elapsed,
        "ai_answer": ai_answer,
        "docs":      results,
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)