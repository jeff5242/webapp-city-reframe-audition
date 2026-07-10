"""產生單一自足 HTML 標註工具：內嵌 14 張圖 + 標註資料，供人工核對/修正/匯出。"""
import base64, io, json, os
from PIL import Image

ROOT = "/Users/jef/CodeRepository/webapp-city-reframe-audition"
ANN = f"{ROOT}/審議資料表_訓練標註"
PNG = f"{ROOT}/審議資料表PDF圖檔轉PNG for OCR訓練"
OUT = f"{ANN}/標註工具.html"

manifest = json.load(open(f"{ANN}/manifest.json", encoding="utf-8"))
FIELDS = manifest["field_keys"]


def img_data_uri(rel_path: str) -> str:
    im = Image.open(f"{PNG}/{rel_path}").convert("RGB")
    # 原解析度轉 JPEG（比 PNG 小很多；縮放交給前端 CSS）
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=88)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


samples = []
for s in manifest["samples"]:
    ann = json.load(open(f"{ANN}/{s['annotation']}", encoding="utf-8"))
    samples.append({
        "id": s["id"],
        "name": os.path.basename(ann["image"]),
        "meta": ann["meta"],
        "group": ann.get("group", ""),
        "fields": {k: ann["fields"].get(k) for k in FIELDS},
        "ocr": ann.get("_ocr_text_reference", []),
        "img": img_data_uri(ann["image"]),
    })
    print(f"  embedded {s['id']:>2}. {samples[-1]['name'][:40]}", flush=True)

DATA_JSON = json.dumps(samples, ensure_ascii=False)
FIELDS_JSON = json.dumps(FIELDS, ensure_ascii=False)

HTML = """<!doctype html><html lang="zh-TW"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>審議資料表 標註工具</title>
<style>
:root{--bg:#f5f7fb;--surface:#fff;--surface2:#eef2f8;--ink:#10203a;--soft:#465873;--faint:#8091a9;--line:#dbe3ef;--accent:#1f5fbf;--good:#17864a;--warn:#b7791f}
@media(prefers-color-scheme:dark){:root{--bg:#0b1220;--surface:#111b2e;--surface2:#16233a;--ink:#e8eefb;--soft:#a6b6d0;--faint:#6c7f9c;--line:#243350;--accent:#4d90f0;--good:#3fc47e;--warn:#e0b24a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:"PingFang TC","Noto Sans TC","Microsoft JhengHei",system-ui,sans-serif;height:100vh;display:flex;flex-direction:column;overflow:hidden}
header{background:var(--surface);border-bottom:1px solid var(--line);padding:10px 16px;display:flex;align-items:center;gap:14px;flex-wrap:wrap}
header h1{font-size:15px;margin:0;font-weight:800}
.nav{display:flex;align-items:center;gap:8px}
button{font-family:inherit;font-size:13px;border:1px solid var(--line);background:var(--surface2);color:var(--ink);border-radius:8px;padding:6px 12px;cursor:pointer}
button:hover{border-color:var(--accent)}
button.primary{background:var(--accent);color:#fff;border-color:var(--accent);font-weight:700}
select{font-family:inherit;font-size:13px;border:1px solid var(--line);background:var(--surface);color:var(--ink);border-radius:8px;padding:6px 8px;max-width:340px}
.counter{font-weight:700;font-variant-numeric:tabular-nums}
.tag{font-size:11px;padding:2px 8px;border-radius:99px;background:var(--surface2);color:var(--soft)}
.tag.rev{background:color-mix(in srgb,var(--good) 18%,transparent);color:var(--good)}
main{flex:1;display:grid;grid-template-columns:1fr 420px;overflow:hidden}
@media(max-width:820px){main{grid-template-columns:1fr;overflow:auto}}
.imgpane{overflow:auto;background:var(--surface2);position:relative;padding:12px}
.imgpane img{display:block;background:#fff;box-shadow:0 2px 12px rgba(0,0,0,.15)}
.zoombar{position:sticky;top:0;z-index:5;display:flex;align-items:center;gap:8px;background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:6px 10px;margin-bottom:10px;width:fit-content;font-size:12px;color:var(--soft)}
.zoombar input{width:160px}
.form{overflow:auto;background:var(--surface);border-left:1px solid var(--line);padding:16px}
.field{margin-bottom:11px}
.field label{display:block;font-size:12px;font-weight:600;color:var(--soft);margin-bottom:3px}
.field input{width:100%;font-family:inherit;font-size:14px;padding:7px 9px;border:1px solid var(--line);border-radius:7px;background:var(--surface);color:var(--ink)}
.field input:focus{outline:none;border-color:var(--accent)}
.field input.auto{border-left:3px solid var(--warn)}
.field .hint{font-size:11px;color:var(--faint);margin-top:2px}
.section-h{font-size:12px;font-weight:800;color:var(--accent);text-transform:uppercase;letter-spacing:.06em;margin:16px 0 8px;border-bottom:1px solid var(--line);padding-bottom:4px}
details.ocr{margin-top:16px;border:1px solid var(--line);border-radius:8px}
details.ocr summary{cursor:pointer;padding:8px 12px;font-size:12px;font-weight:600;color:var(--soft)}
details.ocr .ocrtext{padding:0 12px 12px;font-size:12px;color:var(--soft);line-height:1.9;max-height:220px;overflow:auto}
.ocrtext mark{background:color-mix(in srgb,var(--accent) 20%,transparent);color:inherit;border-radius:3px}
.meta{font-size:12px;color:var(--faint);margin-bottom:8px}
.bar{display:flex;gap:8px;align-items:center;margin-left:auto}
kbd{font-size:11px;background:var(--surface2);border:1px solid var(--line);border-radius:4px;padding:1px 5px}
</style></head><body>
<header>
  <h1>審議資料表 標註</h1>
  <div class="nav">
    <button onclick="go(-1)">◀ 上一張</button>
    <span class="counter" id="counter"></span>
    <button onclick="go(1)">下一張 ▶</button>
    <select id="jump" onchange="jump(this.value)"></select>
    <span class="tag" id="revtag"></span>
  </div>
  <div class="bar">
    <span style="font-size:11px;color:var(--faint)">已改 <b id="revcount">0</b>/__N__</span>
    <button onclick="resetOne()">還原此張</button>
    <button class="primary" onclick="exportJSON()">⬇ 匯出標註 JSON</button>
  </div>
</header>
<main>
  <div class="imgpane">
    <div class="zoombar">🔍 縮放 <input type="range" id="zoom" min="40" max="260" value="100" oninput="setZoom(this.value)"> <span id="zoomval">100%</span></div>
    <img id="img" alt="審議資料表">
  </div>
  <div class="form" id="form"></div>
</main>
<script>
const DATA = __DATA__;
const FIELDS = __FIELDS__;
const N = DATA.length;
const LS = "shenyi_annot_v1";
let cur = 0;
let store = JSON.parse(localStorage.getItem(LS) || "{}"); // {id:{fields, reviewed}}

function curFields(id){ return (store[id] && store[id].fields) || Object.assign({}, DATA.find(d=>d.id==id).fields); }
function isRev(id){ return !!(store[id] && store[id].reviewed); }

function render(){
  const d = DATA[cur];
  document.getElementById('img').src = d.img;
  document.getElementById('counter').textContent = (cur+1)+" / "+N;
  const rt = document.getElementById('revtag');
  rt.textContent = isRev(d.id) ? "已核對" : "未核對";
  rt.className = "tag" + (isRev(d.id)?" rev":"");
  document.getElementById('jump').value = cur;
  const f = curFields(d.id);
  const numKeys = FIELDS.filter(k=>/停車|容積|面積|比率|建蔽|戶數|樓地板/.test(k));
  let html = '<div class="meta">案：'+(d.meta.case||'?')+' · '+(d.meta.doc_type||'')+' · '+(d.meta.version||'-')+'　<span style="color:var(--faint)">'+d.name+'</span></div>';
  html += '<div class="section-h">欄位（改錯／補漏，空白=null）</div>';
  FIELDS.forEach(k=>{
    const v = f[k]==null? "" : f[k];
    const auto = (DATA[cur].fields[k]!=null);
    html += '<div class="field"><label>'+k+(auto?' <span style="color:var(--warn)">· 自動填</span>':'')+'</label>'+
      '<input class="'+(auto?'auto':'')+'" data-k="'+k+'" value="'+String(v).replace(/"/g,'&quot;')+'" oninput="onEdit()" placeholder="'+(auto?'':'（讀不到／空白留空=null）')+'"></div>';
  });
  html += '<details class="ocr"><summary>📄 OCR 文字參照（數字可直接抄；中文標籤可能有錯字，以原圖為準）</summary><div class="ocrtext" id="ocrtext"></div></details>';
  document.getElementById('form').innerHTML = html;
  // OCR reference with number highlight
  const oc = d.ocr.map(t=> /[0-9]/.test(t)? '<mark>'+esc(t)+'</mark>' : esc(t)).join(' ｜ ');
  document.getElementById('ocrtext').innerHTML = oc || '（無）';
  updateRevCount();
}
function esc(s){return String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
function onEdit(){
  const d = DATA[cur]; const fields = {};
  document.querySelectorAll('#form input[data-k]').forEach(inp=>{
    const val = inp.value.trim();
    fields[inp.dataset.k] = val===""? null : val;
  });
  store[d.id] = {fields, reviewed:true};
  localStorage.setItem(LS, JSON.stringify(store));
  const rt=document.getElementById('revtag'); rt.textContent="已核對"; rt.className="tag rev";
  updateRevCount();
}
function updateRevCount(){ document.getElementById('revcount').textContent = Object.values(store).filter(x=>x.reviewed).length; }
function go(dir){ cur=Math.max(0,Math.min(N-1,cur+dir)); render(); document.querySelector('.imgpane').scrollTop=0; }
function jump(i){ cur=+i; render(); }
function setZoom(v){ document.getElementById('img').style.width=v+'%'; document.getElementById('zoomval').textContent=v+'%'; }
function resetOne(){ const id=DATA[cur].id; delete store[id]; localStorage.setItem(LS,JSON.stringify(store)); render(); }
function exportJSON(){
  const out = DATA.map(d=>({image:d.image||d.name, name:d.name, meta:d.meta,
    fields: curFields(d.id), reviewed: isRev(d.id)}));
  const blob = new Blob([JSON.stringify(out,null,2)],{type:"application/json"});
  const a=document.createElement('a'); a.href=URL.createObjectURL(blob);
  a.download="審議資料表_標註_已修正.json"; a.click();
}
document.addEventListener('keydown',e=>{ if(e.target.tagName==='INPUT')return;
  if(e.key==='ArrowLeft')go(-1); if(e.key==='ArrowRight')go(1); });
// init jump dropdown
document.getElementById('jump').innerHTML = DATA.map((d,i)=>'<option value="'+i+'">'+(i+1)+'. '+(d.meta.case||'?')+' '+(d.meta.doc_type||'')+'</option>').join('');
setZoom(100); render();
</script></body></html>"""

html = (HTML.replace("__DATA__", DATA_JSON).replace("__FIELDS__", FIELDS_JSON)
        .replace("__N__", str(len(samples))))
open(OUT, "w", encoding="utf-8").write(html)
print(f"\n✓ 標註工具 → {OUT}  ({len(html)/1024/1024:.1f} MB)")
