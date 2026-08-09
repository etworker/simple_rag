// ============================================================
// PDF.js 初始化
// ============================================================
pdfjsLib.GlobalWorkerOptions.workerSrc='https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js';

// ============================================================
// 工具函数
// ============================================================
function esc(s){s=s==null?'':String(s);return s?s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'):'';} 
function escA(s){return s?s.replace(/"/g,'&quot;'):'';}
function renderMd(s){return (typeof marked!=='undefined')?marked.parse(s||''):esc(s||'');}
function fmtDocName(name,hash){const h=hash?hash.slice(-8).toUpperCase():'';return h?`${name} [${h}]`:name;}
function splitFileHash(sf){
  if(!sf)return {name:'',hash:''};
  const i=sf.lastIndexOf('#');
  if(i<0)return {name:sf,hash:''};
  return {name:sf.slice(0,i),hash:sf.slice(i+1)};
}
function fileColoredHtml(sf){
  const {name,hash}=splitFileHash(sf||'');
  return hash?`${esc(name)} <span class="src-file-hash">#${esc(hash)}</span>`:esc(name);
}
function stripOldSourceLines(s){
  if(!s)return s;
  // 防御：LLM 若仍按旧格式在末尾堆叠多段 "来源：xxx.pdf#SHA1234 第N页 ..." 或 "[来源: ...]",
  // 则从首次出现处到文末整段移除；正文行不会以 "来源：[来源:" 或 "来源：...pdf#..." 开头，不会误伤
  return s.replace(/(?:\n|^)\s*(?:来源[：:]|\[来源:)\s*[\s\S]*$/,'').replace(/\n{3,}/g,'\n\n').trim();
}
function scrollToRef(n){const el=document.getElementById('ref-'+n);if(el){el.scrollIntoView({behavior:'smooth',block:'start'});el.classList.add('ref-flash');setTimeout(()=>el.classList.remove('ref-flash'),1500);}else{console.warn('[scrollToRef] ref-'+n+' not found');}}

// ============================================================
// 页面切换
// ============================================================
function showPage(name){
    const pages={kb:'pageKb',qa:'pageQa',settings:'pageSettings'};
    for(const [k,id] of Object.entries(pages))
        document.getElementById(id).classList.toggle('active',name===k);
    document.querySelectorAll('.topbar .tab-btn').forEach(b=>{
        b.classList.toggle('active',b.textContent.includes(name==='kb'?'知识':name==='qa'?'问答':'设置'));
    });
    if(name==='settings'&&!_settingsConfig) loadSettings();
}

// ============================================================
// 侧栏折叠
// ============================================================
function toggleKbSidebar(){
    const left=document.getElementById('kbLeft');
    const btn=document.getElementById('kbToggle');
    left.classList.toggle('collapsed');
    btn.textContent=left.classList.contains('collapsed')?'▶':'◀';
}

// ============================================================
// 左侧栏拖拽调整宽度
// ============================================================
(function(){
    const handle=document.getElementById('kbResizeHandle');
    if(!handle)return;
    let isDragging=false,startX=0,startWidth=0;

    handle.addEventListener('mousedown',function(e){
        isDragging=true;
        startX=e.clientX;
        startWidth=document.getElementById('kbLeft').offsetWidth;
        handle.classList.add('dragging');
        document.body.style.cursor='col-resize';
        document.body.style.userSelect='none';
        e.preventDefault();
    });

    document.addEventListener('mousemove',function(e){
        if(!isDragging)return;
        const delta=e.clientX-startX;
        const newWidth=Math.max(180,Math.min(500,startWidth+delta));
        document.getElementById('kbLeft').style.width=newWidth+'px';
    });

    document.addEventListener('mouseup',function(e){
        if(!isDragging)return;
        isDragging=false;
        handle.classList.remove('dragging');
        document.body.style.cursor='';
        document.body.style.userSelect='';
    });
})();

// ============================================================
// 知识库管理 — 状态
// ============================================================
let docMap={}, reviewResult=null, reviewTaskId=null, newDocFile=null, newDocHash='', activeFileName=null;
let currentDoc=null, currentPage=1, totalPages=1;

// ============================================================
// 侧边栏文档列表
// ============================================================
async function refreshDocList(){
    const res=await fetch('/api/documents/list');
    const data=await res.json();
    docMap={};
    const el=document.getElementById('docList');
    let pendingHtml='', ingestedHtml='';
// 待审核文档（放上面）
if(newDocFile&&!docMap[newDocFile]){
docMap[newDocFile]='X';
const hashShort=newDocHash?newDocHash.slice(-8).toUpperCase():'';
const pendingName=hashShort?`${esc(newDocFile)} <span style="color:var(--text3);font-size:10px;">[${hashShort}]</span>`:esc(newDocFile);
const cls=(currentDoc===newDocFile)?'doc-item pending active':'doc-item pending';
pendingHtml=`<div class="doc-list-title" style="color:var(--warn);">待审核</div><div class="${cls}" onclick="selectPendingDoc('${escA(newDocFile)}')" title="${escA(newDocFile)}${hashShort?' ['+hashShort+']':''}"><span class="id" style="color:var(--warn);">X</span><div class="info"><div class="name">${pendingName}</div><div class="stats">等待人工确认</div></div></div>`;
}
    // 已入库文档
    if(!data.documents||!data.documents.length){
        ingestedHtml='<div class="doc-list-title">已入库文档<span class="reset-btn" onclick="resetKnowledgeBase()" title="清空全部文档和缓存，恢复初始状态">🔴 重置</span></div><div style="color:#ccc;padding:10px;font-size:11px;">暂无文档</div>';
    } else {
        ingestedHtml='<div class="doc-list-title">已入库文档<span class="reset-btn" onclick="resetKnowledgeBase()" title="清空全部文档和缓存，恢复初始状态">🔴 重置</span></div>';
data.documents.forEach((d,i)=>{const id='B'+(i+1);const hashShort=d.file_hash?d.file_hash.slice(-8).toUpperCase():'';const docId=d.doc_id||d.filename;docMap[docId]=id;docMap[d.filename]=id;const cls=(currentDoc===docId||currentDoc===d.filename)?'doc-item active':'doc-item';const pg=d.page_count||kbTotalPagesCache[d.filename]||'';const stats=[];if(pg)stats.push(pg+'页');if(d.char_count)stats.push(d.char_count+'字');if(d.paragraph_count)stats.push(d.paragraph_count+'段');if(d.table_count)stats.push(d.table_count+'表');const displayName=hashShort?`${esc(d.filename)} <span style="color:var(--text3);font-size:10px;">[${hashShort}]</span>`:esc(d.filename);ingestedHtml+=`<div class="${cls}" onclick="selectDoc('${escA(docId)}')" title="${escA(d.filename)}${hashShort?' ['+hashShort+']':''}"><span class="id">${id}</span><div class="info"><div class="name">${displayName}</div>${stats.length?'<div class="stats">'+stats.join(' · ')+'</div>':''}</div><span class="del-btn" onclick="event.stopPropagation();removeDoc('${escA(docId)}')">🗑️</span></div>`;});
    }
    el.innerHTML=pendingHtml+ingestedHtml;
}

async function removeDoc(name){
    if(!confirm('确定删除「'+name+'」？'))return;
    await fetch('/api/documents/remove/'+encodeURIComponent(name),{method:'DELETE'});
    if(currentDoc===name){currentDoc=null;showPreviewEmpty();}
    refreshDocList();
}

async function resetKnowledgeBase(){
    if(!confirm('🔴 确定重置知识库？\n\n这将：\n• 删除所有已入库文档\n• 清除全部缓存（解析/向量/页面/预审核）\n• 清除所有任务状态\n\n操作不可恢复！页面将自动刷新。'))return;
    if(!confirm('再次确认：真的要重置吗？'))return;
    try{
        const res=await fetch('/api/documents/clear',{method:'POST'});
        if(!res.ok){const e=await res.json();alert(e.detail||'重置失败');return;}
        const data=await res.json();
        alert(data.message+'\n\n页面将自动刷新...');
        location.reload();
    }catch(e){alert('重置请求失败: '+e.message);}
}

// ============================================================
// 文档预览
// ============================================================
// 预览待审核文档（使用 review API）
async function selectPendingDoc(filename){
    // 如果正在预审核 tab，弹窗确认
    const reviewTabActive=document.getElementById('reviewPanel').classList.contains('active');
    if(reviewTabActive){
        if(!confirm('当前正在查看预审核结果，确定切换到文档预览？'))return;
        switchKbTab('preview');
    }
    currentDoc=filename;
    refreshDocList();
    document.getElementById('previewEmpty').style.display='none';
    document.getElementById('previewContent').style.display='flex';
    document.getElementById('previewTitle').textContent='X '+filename;
    // 用 review PDF 接口
    const url=`/api/documents/review/pdf?task_id=${reviewTaskId}`;
    kbPdfUrl=null;  // 强制重新加载
    kbPdfDoc=await pdfjsLib.getDocument(url).promise;
    totalPages=kbPdfDoc.numPages;
    currentPage=getDocPage(filename);
    document.getElementById('kbPageInput').value=currentPage;
    document.getElementById('kbTotalPages').textContent=totalPages;
    buildPageSlots('kbPdfContainer', totalPages);
    setupLazyRender('kbPdfContainer', kbPdfDoc);
    scrollToPage('kbPdfContainer', currentPage);
}

let kbPageCache={};  // {filename: page}
let kbTotalPagesCache={};  // {filename: totalPages}

function saveDocPage(filename, page){
    if(!filename||!page)return;
    kbPageCache[filename]=page;
    try{sessionStorage.setItem('docPage_'+filename, String(page));}catch(e){}
}
function getDocPage(filename){
    if(kbPageCache[filename])return kbPageCache[filename];
    try{return parseInt(sessionStorage.getItem('docPage_'+filename))||1;}catch(e){return 1;}
}

async function selectDoc(filename){
    // 如果正在预审核 tab，弹窗确认
    const reviewTabActive=document.getElementById('reviewPanel').classList.contains('active');
    if(reviewTabActive){
        if(!confirm('当前正在查看预审核结果，确定切换到文档预览？')){
            return;
        }
        switchKbTab('preview');
    }
    // 保存当前文档页码
    if(currentDoc){
        const input=document.getElementById('kbPageInput');
        saveDocPage(currentDoc, parseInt(input.value)||1);
    }
    currentDoc=filename;
    currentPage=getDocPage(filename);
    refreshDocList();
    loadKbPreview();
}

// PDF.js 渲染核心
let kbPdfDoc=null, kbPdfUrl=null;

async function loadKbPreview(){
    if(!currentDoc)return;
    // 非 PDF 文件显示文本预览
    const basename = currentDoc.split('#')[0];  // 去掉 #hash 后缀
    const ext = basename.split('.').pop().toLowerCase();
    if(ext !== 'pdf'){
        loadTextPreview(basename);
        return;
    }
    document.getElementById('previewEmpty').style.display='none';
    document.getElementById('previewContent').style.display='flex';
    document.getElementById('previewTitle').textContent=(docMap[currentDoc]||'')+' '+currentDoc;
    // 确保翻页控件可见（从文本预览切回 PDF 时）
    const pager=document.getElementById('kbPager');
    if(pager)pager.style.display='';
    const url=`/api/documents/pdf?name=${encodeURIComponent(basename)}`;
    // 如果换了文档才重新加载 PDF（否则只渲染页面）
    if(kbPdfUrl!==url){
        kbPdfUrl=url;
        kbPdfDoc=await pdfjsLib.getDocument(url).promise;
        totalPages=kbPdfDoc.numPages;
        kbTotalPagesCache[currentDoc]=totalPages;
        buildPageSlots('kbPdfContainer', totalPages);
        setupLazyRender('kbPdfContainer', kbPdfDoc);
        refreshDocList();  // 更新侧栏页数显示
    }
    document.getElementById('kbPageInput').value=currentPage;
    document.getElementById('kbTotalPages').textContent=totalPages;
    // 滚动到记忆的页码
    scrollToPage('kbPdfContainer', currentPage);
}

// 非 PDF 文件（如 docx）的文本段落预览
async function loadTextPreview(filename){
    document.getElementById('previewEmpty').style.display='none';
    document.getElementById('previewContent').style.display='flex';
    document.getElementById('previewTitle').textContent=(docMap[filename]||'')+' '+filename;
    const container=document.getElementById('kbPdfContainer');
    container.innerHTML='<div style="padding:16px;color:var(--text3);">加载中...</div>';
    // 隐藏翻页控件（文本预览不需要）
    const pager=document.getElementById('kbPager');
    if(pager)pager.style.display='none';
    try{
        const resp=await fetch(`/api/documents/paragraphs?name=${encodeURIComponent(filename)}`);
        const data=await resp.json();
        if(!data.paragraphs||!data.paragraphs.length){
            container.innerHTML='<div style="padding:16px;color:var(--text3);">该文档无段落内容</div>';
            return;
        }
        let html='<div style="padding:16px;max-width:800px;">';
        data.paragraphs.forEach((p,i)=>{
            const loc=p.location?`<span style="color:var(--text3);font-size:11px;margin-left:8px;">${esc(p.location)}</span>`:'';
            html+=`<div style="margin-bottom:12px;padding:8px 12px;background:var(--bg2);border-radius:6px;font-size:13px;line-height:1.6;"><span style="color:var(--primary);font-size:11px;margin-right:6px;">¶${i+1}</span>${esc(p.text)}${loc}</div>`;
        });
        html+='</div>';
        container.innerHTML=html;
    }catch(e){
        container.innerHTML=`<div style="padding:16px;color:var(--danger);">预览加载失败: ${esc(e.message)}</div>`;
    }
}

// 创建所有页面的占位 div（用于懒加载）
function buildPageSlots(containerId, numPages){
    const container=document.getElementById(containerId);
    container.innerHTML='';
    for(let i=1;i<=numPages;i++){
        const slot=document.createElement('div');
        slot.className='page-slot';
        slot.dataset.page=i;
        slot.style.cssText='width:100%;min-height:800px;display:flex;align-items:center;justify-content:center;color:#ccc;font-size:12px;';
        slot.textContent='第 '+i+' 页';
        container.appendChild(slot);
    }
}

// IntersectionObserver 懒加载渲染
function setupLazyRender(containerId, pdfDoc){
    const container=document.getElementById(containerId);
    const observer=new IntersectionObserver((entries)=>{
        entries.forEach(entry=>{
            if(entry.isIntersecting){
                const slot=entry.target;
                if(slot.dataset.rendered)return;
                slot.dataset.rendered='1';
                const pageNum=parseInt(slot.dataset.page);
                renderPageInSlot(pdfDoc, pageNum, slot);
            }
        });
    },{root:container, rootMargin:'200px'});
    container.querySelectorAll('.page-slot').forEach(s=>observer.observe(s));
    // 滚动时追踪当前页码
    if(container._observer) container._observer.disconnect();
    container._observer=observer;
    container.onscroll=()=>{
        const slots=container.querySelectorAll('.page-slot');
        const containerTop=container.scrollTop;
        let visPage=1;
        for(let s of slots){
            if(s.offsetTop<=containerTop+100)visPage=parseInt(s.dataset.page);
            else break;
        }
        currentPage=visPage;
        document.getElementById('kbPageInput').value=visPage;
        saveDocPage(currentDoc, visPage);
    };
}

async function renderPageInSlot(pdfDoc, pageNum, slot){
    const page=await pdfDoc.getPage(pageNum);
    const dpr=window.devicePixelRatio||1;
    const cssScale=1.5;
    const renderScale=cssScale*dpr;
    const viewport=page.getViewport({scale:renderScale});
    const cssViewport=page.getViewport({scale:cssScale});
    const canvas=document.createElement('canvas');
    canvas.width=Math.floor(viewport.width);
    canvas.height=Math.floor(viewport.height);
    // 不设固定 px 宽高，用 max-width + height:auto 保持长宽比
    canvas.style.maxWidth=Math.floor(cssViewport.width)+'px';
    canvas.style.width='100%';
    canvas.style.height='auto';
    slot.innerHTML='';
    slot.style.minHeight='auto';
    slot.appendChild(canvas);
    const ctx=canvas.getContext('2d');
    await page.render({canvasContext:ctx, viewport}).promise;
}

function scrollToPage(containerId, pageNum){
    const container=document.getElementById(containerId);
    const slot=container.querySelector(`.page-slot[data-page="${pageNum}"]`);
    if(slot)slot.scrollIntoView({block:'start'});
}

function kbGoToPage(){
    const v=parseInt(document.getElementById('kbPageInput').value)||1;
    currentPage=Math.max(1, Math.min(v, totalPages));
    document.getElementById('kbPageInput').value=currentPage;
    saveDocPage(currentDoc, currentPage);
    scrollToPage('kbPdfContainer', currentPage);
}
function kbPrevPage(){if(currentPage>1){currentPage--;document.getElementById('kbPageInput').value=currentPage;saveDocPage(currentDoc,currentPage);scrollToPage('kbPdfContainer',currentPage);}}
function kbNextPage(){if(currentPage<totalPages){currentPage++;document.getElementById('kbPageInput').value=currentPage;saveDocPage(currentDoc,currentPage);scrollToPage('kbPdfContainer',currentPage);}}

function showPreviewEmpty(){document.getElementById('previewEmpty').style.display='flex';document.getElementById('previewContent').style.display='none';}

// ============================================================
// KB Tab 切换
// ============================================================
function switchKbTab(tab){
    document.querySelectorAll('.kb-tabs .kb-tab').forEach(b=>b.classList.remove('active'));
    if(tab==='preview'){
        document.querySelector('.kb-tabs .kb-tab:first-child').classList.add('active');
        document.getElementById('previewPanel').classList.add('active');
        document.getElementById('reviewPanel').classList.remove('active');
    } else {
        document.getElementById('reviewTabBtn').classList.add('active');
        document.getElementById('previewPanel').classList.remove('active');
        document.getElementById('reviewPanel').classList.add('active');
    }
}

function showReviewTab(){
document.getElementById('reviewTabBtn').style.display='';
document.getElementById('reviewTabBtn').textContent=`⚠️ 预审核 (${reviewResult?reviewResult.inconsistencies.length:0})`;
switchKbTab('review');
}

function onReviewBtnClick(){
    // 始终展示审核面板（含确认/取消按钮），让用户明确操作
    showReviewTab();
}

function hideReviewTab(){
    document.getElementById('reviewTabBtn').style.display='none';
    document.getElementById('reviewBtn').classList.remove('show');
    switchKbTab('preview');
}

// ============================================================
// 上传 & SSE 预审核
// ============================================================
const uploadZone=document.getElementById('uploadZone');
uploadZone.addEventListener('dragover',e=>{e.preventDefault();uploadZone.style.borderColor='var(--primary)';});
uploadZone.addEventListener('dragleave',()=>{uploadZone.style.borderColor='';});
uploadZone.addEventListener('drop',e=>{e.preventDefault();uploadZone.style.borderColor='';if(!uploadZone.classList.contains('disabled')&&e.dataTransfer.files.length)handleUpload(e.dataTransfer.files[0]);});

async function handleUpload(file){
if(!file)return;
newDocFile=file.name;newDocHash='';activeFileName=file.name;
uploadZone.classList.add('disabled');
document.getElementById('stepArea').classList.add('show');
document.getElementById('stepTitle').textContent='上传: '+file.name;
document.getElementById('stepItems').innerHTML='<div class="step-item"><div class="dot active">⏳</div><span>上传中...</span></div>';
const fd=new FormData();fd.append('file',file);
try{
const res=await fetch('/api/documents/upload',{method:'POST',body:fd});
if(!res.ok){const e=await res.json();alert(e.detail||'上传失败');resetUpload();return;}
const data=await res.json();
reviewTaskId=data.task_id;
newDocHash=data.file_hash||'';
document.getElementById('stepTitle').textContent='预审核: '+fmtDocName(data.filename,newDocHash);
refreshDocList();
connectSSE(data.task_id);
}catch(e){alert('上传失败: '+e.message);resetUpload();}
}

function connectSSE(taskId){
    const es=new EventSource(`/api/documents/review/${taskId}/progress`);
    es.onmessage=function(event){
        const d=JSON.parse(event.data);
        renderSteps(d);
        if(d.status==='done'||d.status==='error'||d.status==='cancelled'){
            es.close();
            if(d.status==='done'&&d.result){
                reviewResult=d.result;
                document.getElementById('stepArea').classList.remove('show');
                const n=d.result.inconsistencies?d.result.inconsistencies.length:0;
                if(n>0){
                    document.getElementById('reviewBtn').textContent=`⚠️ 预审核完成，发现 ${n} 处矛盾 → 查看`;
                    document.getElementById('reviewBtn').classList.add('show');
                    buildReviewPanel();
                } else {
                    document.getElementById('reviewBtn').textContent='✅ 预审核通过，无矛盾 → 确认入库';
                    document.getElementById('reviewBtn').classList.add('show');
                    buildReviewPanel();
                }
            } else {
                resetUpload();
            }
        }
    };
}

let stepTimer=null,lastSSEElapsed=0,lastSSETime=Date.now();
function renderSteps(d){
    const all=d.all_steps||[],comp=d.completed_steps||[],compIds=comp.map(s=>s.id);
    if(!all.length){document.getElementById('stepItems').innerHTML=`<div class="step-item"><div class="dot active">⏳</div><span>${esc(d.current_step||'')}</span></div>`;return;}
    const doneCount=comp.filter(c=>c.elapsed!=null).length;
    document.getElementById('stepTitle').textContent=`预审核: ${esc(fmtDocName(activeFileName||'',newDocHash))} (${doneCount}/${all.length})`;
    lastSSEElapsed=d.current_elapsed||0;lastSSETime=Date.now();
    let html='';
    for(let i=0;i<all.length;i++){
        const s=all[i],cs=comp.find(x=>x.id===s.id);
        const hasEl=cs&&cs.elapsed!=null,isIn=compIds.includes(s.id);
        // isDone: 已完成(在completed中且有elapsed)
        // isAct: 正在执行(在completed中但还没elapsed)
        // pending: 尚未开始
        const isDone=isIn&&hasEl,isAct=isIn&&!hasEl;
        const cls=isDone?'done':(isAct?'active':'pending');
        const icon=isDone?'✓':(isAct?'▸':'·');
        let time='';
        if(isDone&&cs)time=`<span class="time">${Math.round(cs.elapsed)}s</span>`;
        else if(isAct)time=`<span class="time" id="activeTimer">${Math.floor(d.current_elapsed||0)}s</span>`;
        html+=`<div class="step-item"><div class="dot ${cls}">${icon}</div><span>${esc(s.label)}</span>${time}</div>`;
    }
    document.getElementById('stepItems').innerHTML=html;
    if(!stepTimer&&doneCount<all.length)stepTimer=setInterval(tickTimer,1000);
    if(doneCount>=all.length){clearInterval(stepTimer);stepTimer=null;}
}
function tickTimer(){const el=document.getElementById('activeTimer');if(!el)return;el.textContent=Math.floor(lastSSEElapsed+(Date.now()-lastSSETime)/1000)+'s';}

function resetUpload(){
    document.getElementById('stepArea').classList.remove('show');
    uploadZone.classList.remove('disabled');
    newDocFile=null;refreshDocList();
}
function cancelUpload(){
    if(reviewTaskId)fetch(`/api/documents/review/${reviewTaskId}/cancel`,{method:'POST'});
    resetUpload();
}

// ============================================================
// 预审核结果
// ============================================================
function buildReviewPanel(){
const r=reviewResult;if(!r)return;
const items=r.inconsistencies||[];
document.getElementById('reviewInfo').textContent=items.length>0?`⚠️ 发现 ${items.length} 处矛盾（X = ${esc(newDocFile||'')}）`:'✅ 未发现矛盾，可入库';

// 按 point 去重合并（同一矛盾事项可能有多条记录）
const merged={};
items.forEach(c=>{
const key=c.point||'未知';
if(!merged[key])merged[key]={point:c.point,count:0,entries:[]};
merged[key].count++;
merged[key].entries.push(c);
});
const mergedItems=Object.values(merged);

let html='';
let idx=0;
mergedItems.forEach(m=>{
idx++;
const first=m.entries[0];
// X 侧（新文档）— 兼容扁平/嵌套两种格式
const aSays=(first.doc_a?.says||first.doc_a_says||'').slice(0,100);
// B 侧（已有文档）
const bFile=first.doc_b?.file||first.doc_b_file||'?';
const bSays=(first.doc_b?.says||first.doc_b_says||'').slice(0,100);
const bShort=bFile.length>16?bFile.slice(0,14)+'..':bFile;

html+=`<div class="conflict-item" onclick="selectConflict(${idx-1})">`;
html+=`<div class="title">#${idx} ${esc(m.point)} <span class="badge badge-primary">X</span> vs <span class="badge badge-warn">${esc(bShort)}</span>`;
if(m.count>1)html+=` <span style="color:var(--text3);font-size:10px;">（${m.count}处）</span>`;
html+=`</div>`;
html+=`<div class="desc"><span style="color:var(--primary);">X:</span> ${esc(aSays)}${aSays.length>=100?'…':''}`;
html+=`<br><span style="color:var(--warn);">${esc(bShort)}:</span> ${esc(bSays)}${bSays.length>=100?'…':''}`;
if(m.count>1)html+=`<br><span style="color:var(--text3);font-size:10px;">共 ${m.count} 处差异，点击展开详情</span>`;
html+=`</div></div>`;
});
document.getElementById('conflictList').innerHTML=html;
}

function selectConflict(idx){
document.querySelectorAll('.conflict-item').forEach((el,i)=>el.classList.toggle('selected',i===idx));
document.getElementById('conflictList').classList.add('collapsed');
const c=reviewResult.inconsistencies[idx];
const panel=document.getElementById('comparePanel');
panel.classList.add('show');

// 构建 tab 栏
const tabsEl=document.getElementById('cmpTabs');
tabsEl.innerHTML=`<button id="cmpTabA" class="active" onclick="switchCompare('a')">X 新文档</button>`;
tabsEl.innerHTML+=`<button id="cmpTabB" onclick="switchCompare('b')">B 已有文档</button>`;

// 默认显示 X 侧
switchCompare('a');
}

function switchCompare(side){
const items=document.querySelectorAll('.conflict-item');
const idx=Array.from(items).findIndex(el=>el.classList.contains('selected'));
const c=reviewResult.inconsistencies[idx<0?0:idx];
if(!c)return;
let loc='',says='';

if(side==='a'){
says=c.doc_a?.says||c.doc_a_says||'';loc=c.doc_a?.location||c.doc_a_location||'';
} else {
says=c.doc_b?.says||c.doc_b_says||'';loc=c.doc_b?.location||c.doc_b_location||'';
}

// 渲染对比内容
const body=document.getElementById('compareBody');
const imgUrl=`/api/documents/review/page?task_id=${reviewTaskId}&page=${extractPage(loc)||1}&highlight=${encodeURIComponent(says.slice(0,50))}`;
body.innerHTML=`<div><div class="highlight-text" style="margin-bottom:8px;">💬 ${esc(says||'')}</div><img src="${imgUrl}" style="max-width:100%;border-radius:4px;" onerror="this.outerHTML='<div style=\\'color:#aaa;padding:20px;\\'>页面渲染失败</div>'"></div>`;
}

function extractPage(loc){const m=(loc||'').match(/第(\d+)/);return m?parseInt(m[1]):1;}

function closeCompare(){
    document.getElementById('comparePanel').classList.remove('show');
    document.getElementById('conflictList').classList.remove('collapsed');
    document.querySelectorAll('.conflict-item').forEach(el=>el.classList.remove('selected'));
}

async function confirmIngest(){
    if(!reviewTaskId){alert('预审核任务不存在，请重新上传');return;}
    // 禁用按钮，显示进度
    const okBtn=document.querySelector('.review-actions .btn-ok');
    const noBtn=document.querySelector('.review-actions .btn-no');
    if(okBtn){okBtn.disabled=true;okBtn.textContent='⏳ 入库中...';}
    if(noBtn){noBtn.disabled=true;}
    // 在面板上显示进度提示
    const info=document.getElementById('reviewInfo');
    if(info){info.textContent='⏳ 正在入库（解析文档 + 计算向量 + 构建索引），预计 1-3 分钟，请勿离开...';}
    console.log('confirmIngest: confirming task',reviewTaskId);
    try{
        const res=await fetch(`/api/documents/review/${reviewTaskId}/confirm`,{method:'POST'});
        if(res.ok){
            const data=await res.json();
            console.log('confirmIngest: success',data);
            alert(`✅ ${data.message}\n文档: ${data.filename}\n段落: ${data.paragraphs}`);
            hideReviewTab();resetUpload();refreshDocList();
        }else{
            const e=await res.json();
            console.error('confirmIngest: error',e);
            alert(e.detail||'入库失败');
            // 恢复按钮
            if(okBtn){okBtn.disabled=false;okBtn.textContent='确认入库';}
            if(noBtn){noBtn.disabled=false;}
            if(info){info.textContent='⚠️ 入库失败，请重试';}
        }
    }catch(err){
        console.error('confirmIngest: fetch error',err);
        alert('入库请求失败: '+err.message);
        if(okBtn){okBtn.disabled=false;okBtn.textContent='确认入库';}
        if(noBtn){noBtn.disabled=false;}
    }
}
async function rejectIngest(){
    if(!reviewTaskId)return;
    await fetch(`/api/documents/review/${reviewTaskId}/reject`,{method:'POST'});
    hideReviewTab();resetUpload();
}

async function forceReReview(){
    // 强制刷新：删除预审核结果缓存，重新上传同一文件触发完整预审核
    if(!reviewTaskId||!newDocFile){
        alert('没有可刷新的预审核任务');
        return;
    }
    if(!confirm('确定强制重新执行预审核？\n\n这将忽略缓存，完整重跑所有步骤（可能需要 1-2 分钟）。'))return;

    try {
        // 调用后端清除缓存并重启预审核
        const res=await fetch(`/api/documents/review/${reviewTaskId}/rerun`,{method:'POST'});
        if(!res.ok){const e=await res.json();alert(e.detail||'操作失败');return;}

        // 清空当前结果，等待新结果
        reviewResult=null;
        document.getElementById('conflictList').innerHTML='<div style="padding:20px;text-align:center;color:var(--text3);">⏳ 正在重新执行预审核...</div>';
        document.getElementById('comparePanel').classList.remove('show');

        // 重新连接 SSE 等待新结果
        connectSSE(reviewTaskId);
    } catch(e) {
        alert('操作失败: '+e.message);
    }
}

// ============================================================
// 问答
// ============================================================
let qaCurrentSession='';  // 当前会话 ID

function toggleQaSidebar(){
    const sidebar=document.getElementById('qaSidebar');
    const btn=document.getElementById('qaSidebarToggle');
    sidebar.classList.toggle('collapsed');
    btn.textContent=sidebar.classList.contains('collapsed')?'▶':'◀';
}

function newQaSession(){
    qaCurrentSession='qa-'+Date.now()+'-'+Math.random().toString(36).slice(2,6);
    document.getElementById('qaMessages').innerHTML='<div class="msg bot"><p>👋 新对话已开始。请提问。</p></div>';
    // 重置后端会话
    fetch('/api/qa/reset?session_id='+qaCurrentSession,{method:'POST'});
    refreshQaSessionList();
}

async function refreshQaSessionList(){
    try{
        const res=await fetch('/api/qa/sessions?limit=30');
        const data=await res.json();
        const el=document.getElementById('qaSessionList');
        if(!data.sessions||!data.sessions.length){
            el.innerHTML='<div class="qa-sidebar-empty">暂无历史会话</div>';
            return;
        }
        el.innerHTML=data.sessions.map(s=>{
            const cls=s.session_id===qaCurrentSession?'qa-session-item active':'qa-session-item';
            return `<div class="${cls}" onclick="loadQaSession('${escA(s.session_id)}')">
                <div class="s-title">${esc(s.title||'（无标题）')}</div>
                <div class="s-meta"><span>${esc(s.updated_at||'')}</span><span>${s.message_count||0}条</span><span class="s-del" onclick="event.stopPropagation();deleteQaSession('${escA(s.session_id)}')">🗑️</span></div>
            </div>`;
        }).join('');
    }catch(e){console.error('加载会话列表失败:',e);}
}

async function loadQaSession(sessionId){
    qaCurrentSession=sessionId;
    try{
        const res=await fetch('/api/qa/sessions/'+sessionId);
        if(!res.ok){alert('加载会话失败');return;}
        const data=await res.json();
        const container=document.getElementById('qaMessages');
        container.innerHTML='';
        for(const msg of data.messages||[]){
            if(msg.role==='user'){
                appendMsg('user',esc(msg.content));
            }else{
                const cleanContent=stripOldSourceLines(msg.content||'');
                let html=renderMd(cleanContent);
                html=html.replace(
                    /\[(\d+)\](?![\w\s]*<\/a>)/g,
                    (m, p1) => `<a href="#ref-${p1}" class="ref-link" data-ref="${p1}" onclick="event.preventDefault();scrollToRef(${p1});return false;">[${p1}]</a>`
                );
                if(msg.sources&&msg.sources.length){
                    // 去重
                    const seen=new Map();
                    for(const s of msg.sources){
                        const key=s.source_file+'|'+(s.location||'');
                        if(!seen.has(key)) seen.set(key,s);
                    }
                    const uniqueSources=[...seen.values()].slice(0,8);
                    const fileIds={};let fileIdx=1;
                    let srcHtml=`<div class="sources"><div class="src-title">📎 来源（${uniqueSources.length} 条）</div>`;
                    for(const s of uniqueSources){
                        const id=s.idx?('B'+s.idx):'B'+(fileIdx++);
                        if(!s.idx&&!fileIds[s.source_file]) fileIds[s.source_file]=id;
                        const displayId=s.idx?('B'+s.idx):fileIds[s.source_file];
                        srcHtml+=`<div class="src-item" id="ref-${displayId.replace('B','')}" data-file="${escA(s.source_file||'')}" data-loc="${escA(s.location||'')}">`;
                        srcHtml+=`<div><span class="src-id">${displayId}</span><span class="src-file">${fileColoredHtml(s.source_file)}</span></div>`;
                        srcHtml+=`<div class="src-loc">${esc(s.location||'')}</div>`;
                        srcHtml+=`<div class="src-text">${esc((s.text||'').slice(0,120))}</div></div>`;
                    }
                    srcHtml+='</div>';
                    html+=srcHtml;
                }
                const el=document.createElement('div');el.className='msg bot';el.innerHTML=html;
                container.appendChild(el);
                // 绑定来源点击
                el.querySelectorAll('.src-item').forEach(item=>{
                    item.addEventListener('click',function(){
                        const srcData=(msg.sources||[]).find(s=>
                            s.source_file===this.dataset.file &&
                            s.location===this.dataset.loc
                        );
                        const hlText=srcData?srcData.text:'';
                        openQaPreview(this.dataset.file,this.dataset.loc,hlText);
                    });
                });
            }
        }
        container.scrollIntoView({behavior:'smooth'});
        refreshQaSessionList();
        // 通知后端恢复会话状态（重新加载会话历史到 ChatSession）
        // 后端会在下次 ask 时自动创建新 session，这里不强制恢复
    }catch(e){alert('加载会话失败: '+e.message);}
}

async function deleteQaSession(sessionId){
    if(!confirm('确定删除此会话？'))return;
    await fetch('/api/qa/sessions/'+sessionId,{method:'DELETE'});
    if(sessionId===qaCurrentSession){
        newQaSession();
    }
    refreshQaSessionList();
}

async function sendQuestion(){
    const input=document.getElementById('qaInput');
    const q=input.value.trim();if(!q)return;input.value='';
    const wel=document.getElementById('qaWelcome');
    if(wel)wel.remove();
    appendMsg('user',esc(q));
    appendMsg('bot','<em style="color:#aaa;">思考中...</em>');
    try{
        const res=await fetch('/api/qa/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question:q,session_id:qaCurrentSession})});
        const data=await res.json();
        const last=document.querySelector('.qa-messages .msg.bot:last-child');
        if(data.detail){if(last)last.innerHTML='<span style="color:var(--danger);">'+esc(data.detail)+'</span>';return;}
        // 后处理：将答案中 LLM 输出的 [1] [2] 编号转为可点击链接
        const cleanAnswer=stripOldSourceLines(data.answer||'');
        let answerHtml=renderMd(cleanAnswer);
        // 若 LLM 按引用指南使用了 [1] [2] 编号，转成可跳转到底部来源的链接
        answerHtml=answerHtml.replace(
            /\[(\d+)\](?![\w\s]*<\/a>)/g,
            (m, p1) => `<a href="#ref-${p1}" class="ref-link" data-ref="${p1}" onclick="event.preventDefault();scrollToRef(${p1});return false;">[${p1}]</a>`
        );
        // 旧格式兼容: （（...来源：...））
        answerHtml=answerHtml.replace(
            /（（资料中还[^\uff09]{0,80}?来源[:：][^\uff09]{0,80}?））/g,
            (m) => `<blockquote class="answer-note">${esc(m)}</blockquote>`
        );
        let html=answerHtml;
        let allSources=[];  // 提到外层作用域，供点击事件访问
        if(data.sources&&data.sources.length){
            // 去重：同一文件+同一位置只保留一条（取分数最高的）
            const seen=new Map();
            for(const s of data.sources){
                const key=s.source_file+'|'+(s.location||'');
                if(!seen.has(key)||(s.score||0)>seen.get(key).score){
                    seen.set(key,s);
                }
            }
            allSources=[...seen.values()].slice(0,8);
            const fileIds={};
            let fileIdx=1;
            let srcHtml=`<div class="sources"><div class="src-title">📎 来源（${allSources.length} 条）</div>`;
            for(const s of allSources){
                const id=s.idx?('B'+s.idx):'B'+(fileIdx++);
                if(!s.idx&&!fileIds[s.source_file]) fileIds[s.source_file]=id;
                const displayId=s.idx?('B'+s.idx):fileIds[s.source_file];
                srcHtml+=`<div class="src-item" id="ref-${displayId.replace('B','')}" data-file="${escA(s.source_file||'')}" data-loc="${escA(s.location||'')}">`;
                srcHtml+=`<div><span class="src-id">${displayId}</span><span class="src-file">${fileColoredHtml(s.source_file)}</span></div>`;
                srcHtml+=`<div class="src-loc">${esc(s.location||'')}</div>`;
                srcHtml+=`<div class="src-text">${esc((s.text||'').slice(0,120))}${(s.text||'').length>120?'…':''}</div>`;
                srcHtml+=`</div>`;
            }
            srcHtml+='</div>';
            html+=srcHtml;
        }
        if(last){
            last.innerHTML=html;
            // 绑定点击事件
            last.querySelectorAll('.src-item').forEach(item=>{
                item.addEventListener('click',function(){
                    const srcData=allSources.find(s=>
                        s.source_file===this.dataset.file &&
                        s.location===this.dataset.loc
                    );
                    const hlText=srcData?srcData.text:'';
                    openQaPreview(this.dataset.file,this.dataset.loc,hlText);
                });
            });
        }
    }catch(e){const last=document.querySelector('.qa-messages .msg.bot:last-child');if(last)last.innerHTML='<span style="color:var(--danger);">请求失败</span>';}
}
function appendMsg(role,html){
    const el=document.createElement('div');el.className='msg '+role;el.innerHTML=html;
    document.getElementById('qaMessages').appendChild(el);el.scrollIntoView({behavior:'smooth'});
}

// QA 预览面板 — 服务端高亮渲染
let qaCurrentFile='', qaCurrentPage=1, qaTotalPages=1, qaHighlightText='';

async function openQaPreview(file, location, highlightText){
    const panel=document.getElementById('qaPreview');
    panel.classList.add('show');
    document.getElementById('qaPreviewTitle').textContent=file;
    const container=document.getElementById('qaPdfContainer');
    const page=extractPage(location)||1;

    // 记录当前状态
    const isSameFile = qaCurrentFile===file;
    qaCurrentFile=file;
    qaCurrentPage=page;
    qaHighlightText=highlightText||'';

    // 清空容器，显示加载中
    container.innerHTML='<div style="color:#aaa;padding:20px;text-align:center;">加载中...</div>';

    try{
        // 获取文档总页数（先请求第一页，从响应中无法获取，用 PDF.js 轻量获取）
        if(!isSameFile || !qaTotalPages){
            const pdfUrl=`/api/documents/pdf?name=${encodeURIComponent(file)}`;
            const doc=await pdfjsLib.getDocument(pdfUrl).promise;
            qaTotalPages=doc.numPages;
            doc.destroy();
        }

        // 渲染页面图
        await renderQaPage(container, page, qaHighlightText);
    }catch(e){
        container.innerHTML='<div style="color:var(--danger);padding:20px;text-align:center;">文档加载失败：'+esc(e.message||String(e))+'</div>';
    }
}

async function renderQaPage(container, page, highlight){
    // 构建导航栏
    let navHtml=`<div class="qa-preview-nav">`;
    navHtml+=`<button onclick="qaGoPrev()" ${page<=1?'disabled':''}>← 上一页</button>`;
    navHtml+=`<span class="page-info">第 ${page} / ${qaTotalPages} 页</span>`;
    navHtml+=`<button onclick="qaGoNext()" ${page>=qaTotalPages?'disabled':''}>下一页 →</button>`;
    navHtml+=`</div>`;

    // 服务端渲染的页面图（带高亮）
    let imgUrl=`/api/documents/page?name=${encodeURIComponent(qaCurrentFile)}&page=${page}`;
    if(highlight) imgUrl+=`&highlight=${encodeURIComponent(highlight.slice(0,50))}`;

    container.innerHTML=navHtml;
    const img=document.createElement('img');
    img.src=imgUrl;
    img.style.cssText='max-width:100%;height:auto;border-radius:4px;box-shadow:0 2px 12px rgba(0,0,0,0.1);';
    img.onerror=function(){
        this.outerHTML='<div style="color:#aaa;padding:30px;text-align:center;background:#f9f9f9;border-radius:4px;">页面渲染失败</div>';
    };
    container.appendChild(img);
    container.scrollTop=0;
}

function qaGoPrev(){
    if(qaCurrentPage<=1)return;
    qaCurrentPage--;
    renderQaPage(document.getElementById('qaPdfContainer'), qaCurrentPage, qaHighlightText);
}

function qaGoNext(){
    if(qaCurrentPage>=qaTotalPages)return;
    qaCurrentPage++;
    renderQaPage(document.getElementById('qaPdfContainer'), qaCurrentPage, qaHighlightText);
}

function closeQaPreview(){
    document.getElementById('qaPreview').classList.remove('show');
    document.getElementById('qaPdfContainer').innerHTML='';
    qaCurrentFile='';qaCurrentPage=1;qaTotalPages=1;qaHighlightText='';
}

// ============================================================
// Debug
// ============================================================
let debugOn=false,logTimer=null,lastLogSig='';
function toggleDebug(){
debugOn=!debugOn;
document.getElementById('debugPanel').classList.toggle('show',debugOn);
document.getElementById('debugBtn').classList.toggle('active',debugOn);
document.querySelector('.main').style.height=debugOn?'calc(100vh - 42px - 180px)':'';
if(debugOn&&!logTimer){fetchLogs();logTimer=setInterval(fetchLogs,1500);}
if(!debugOn&&logTimer){clearInterval(logTimer);logTimer=null;}
}
async function fetchLogs(){
try{
const res=await fetch('/api/logs/tail?lines=200');
const data=await res.json();
const panel=document.getElementById('debugPanel');
// 用最后一行的内容做签名，判断是否有新日志
const sig=data.lines.length?data.lines[data.lines.length-1]:'';
if(sig!==lastLogSig){
lastLogSig=sig;
panel.innerHTML=data.lines.map(l=>`<div class="line">${esc(l)}</div>`).join('');
panel.scrollTop=panel.scrollHeight;
}
}catch(e){}
}

// ============================================================
// 初始化
// ============================================================
refreshDocList();
newQaSession();
refreshQaSessionList();
// 恢复活跃任务
(async function(){
    try{
        const res=await fetch('/api/documents/review/active');
        const d=await res.json();
        if(!d.task_id)return;
        newDocFile=d.filename;activeFileName=d.filename;reviewTaskId=d.task_id;newDocHash=d.file_hash||'';
        refreshDocList();
        if(d.status==='done'&&d.result){
            reviewResult=d.result;
            const n=d.result.inconsistencies?d.result.inconsistencies.length:0;
            document.getElementById('reviewBtn').textContent=n>0?`⚠️ 预审核完成，发现 ${n} 处矛盾 → 查看`:'✅ 预审核通过 → 确认入库';
            document.getElementById('reviewBtn').classList.add('show');
            buildReviewPanel();
        } else if(d.status==='pending'||d.status==='running'){
            uploadZone.classList.add('disabled');
            document.getElementById('stepArea').classList.add('show');
            document.getElementById('stepTitle').textContent='预审核: '+fmtDocName(d.filename||'',newDocHash);
            renderSteps(d);
            connectSSE(d.task_id);
        }
    }catch(e){}
})();

// ============================================================
// 系统设置面板
// ============================================================
let _settingsConfig=null;       // 打开时的配置快照
let _settingsProfiles={};       // llm_profiles 的本地可编辑副本
let _settingsSelectedProfile=''; // 当前选中的 profile 名

const LLM_PROFILE_FIELDS=[
  ['provider','provider'],['model','模型'],['base_url','base_url'],['api_key','api_key'],
  ['api_key_env','api_key_env'],['endpoint','endpoint'],['region','region'],
  ['max_tokens','max_tokens'],['timeout','timeout'],['max_retries','max_retries'],
  ['retry_backoff','retry_backoff'],['context_window','context_window'],['concurrency','concurrency'],
];

async function loadSettings(){
  document.getElementById('settingsStatus').textContent='加载中...';
  try{
    const res=await fetch('/api/config');
    _settingsConfig=await res.json();
    _settingsProfiles=JSON.parse(JSON.stringify(_settingsConfig.llm_profiles||{}));
    _settingsSelectedProfile=Object.keys(_settingsProfiles)[0]||'';
    renderSettingsForm();
    document.getElementById('settingsStatus').textContent='';
  }catch(e){
    document.getElementById('settingsStatus').textContent='加载失败: '+e.message;
  }
}

function switchSettingsTab(tab,btn){
  document.querySelectorAll('.settings-nav .sn').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  document.querySelectorAll('.sp').forEach(p=>p.classList.remove('active'));
  document.getElementById('sp-'+tab).classList.add('active');
}

function renderSettingsForm(){
  // LLM
  renderProfileSelect();
  renderLlmProfile();
  renderRouting();
  // Embedding
  const emb=_settingsConfig.embedding||{};
  document.getElementById('emb-model').value=emb.model||'';
  document.getElementById('emb-device').value=emb.device||'auto';
  document.getElementById('emb-dtype').value=emb.dtype||'';
  document.getElementById('emb-gpu_id').value=emb.gpu_id||0;
  // Retrieval
  const ret=_settingsConfig.retrieval||{};
  document.getElementById('ret-top_k').value=ret.top_k||5;
  document.getElementById('ret-threshold').value=ret.similarity_threshold||0.5;
  // Pre-review
  const pr=_settingsConfig.pre_review||{};
  document.getElementById('pr-threshold').value=pr.similarity_threshold||0.8;
  document.getElementById('pr-batch').value=pr.batch_size||0;
  // Conflict detection
  const cd=_settingsConfig.conflict_detection||{};
  document.getElementById('cd-min_score').value=cd.min_score||0.7;
  document.getElementById('cd-min_sim').value=cd.min_similarity||0.5;
  document.getElementById('cd-max_sim').value=cd.max_similarity||0.95;
  // Prompts
  const p=_settingsConfig.prompts||{};
  document.getElementById('prompt-system').value=p.system||'';
  document.getElementById('prompt-context').value=p.context_template||'';
  document.getElementById('prompt-conflict').value=p.conflict_warning||'';
  document.getElementById('judge-prompt_file').value=(_settingsConfig.judge||{}).prompt_file||'';
  // Chat
  document.getElementById('chat-max_history').value=(_settingsConfig.chat||{}).max_history||20;
}

function renderProfileSelect(){
  const sel=document.getElementById('llmProfileSelect');
  sel.innerHTML='';
  for(const name of Object.keys(_settingsProfiles)){
    const opt=document.createElement('option');
    opt.value=name; opt.textContent=name;
    if(name===_settingsSelectedProfile)opt.selected=true;
    sel.appendChild(opt);
  }
}

function renderLlmProfile(){
  const p=_settingsProfiles[_settingsSelectedProfile]||{};
  const container=document.getElementById('llmProfileFields');
  container.innerHTML='';
  for(const [key,label] of LLM_PROFILE_FIELDS){
    const row=document.createElement('div');
    row.className='setting-row';
    row.innerHTML=`<label>${label}</label><input id="lp-${key}" class="long" value="${esc(p[key]??'')}" ${key==='api_key'?'type="password"':''}>`;
    container.appendChild(row);
  }
}

function renderRouting(){
  const routing=_settingsConfig.llm_routing||{};
  const names=Object.keys(_settingsProfiles);
  for(const route of ['qa','pre_review','conflict_detection']){
    const sel=document.getElementById('route-'+route);
    sel.innerHTML='';
    for(const name of names){
      const opt=document.createElement('option');
      opt.value=name; opt.textContent=name;
      if(name===routing[route])opt.selected=true;
      sel.appendChild(opt);
    }
  }
}

function addLlmProfile(){
  const name=prompt('新 Profile 名称:');
  if(!name||name in _settingsProfiles){alert('名称为空或已存在');return;}
  _settingsProfiles[name]={provider:'openai',model:'',base_url:'',api_key:'',endpoint:'chat',max_tokens:2048,timeout:120,max_retries:3,retry_backoff:2.0,context_window:8192,concurrency:1};
  _settingsSelectedProfile=name;
  renderProfileSelect();
  renderLlmProfile();
}

function delLlmProfile(){
  if(!confirm(`删除 Profile "${_settingsSelectedProfile}"？`))return;
  delete _settingsProfiles[_settingsSelectedProfile];
  _settingsSelectedProfile=Object.keys(_settingsProfiles)[0]||'';
  renderProfileSelect();
  renderLlmProfile();
  renderRouting();
}

async function saveSettings(){
  // 收集当前 profile 的值
  if(_settingsSelectedProfile){
    const p=_settingsProfiles[_settingsSelectedProfile];
    for(const [key] of LLM_PROFILE_FIELDS){
      const el=document.getElementById('lp-'+key);
      if(el){
        const v=el.value.trim();
        if(['max_tokens','timeout','max_retries','context_window','concurrency'].includes(key))
          p[key]=parseInt(v)||0;
        else if(key==='retry_backoff')
          p[key]=parseFloat(v)||0;
        else
          p[key]=v;
      }
    }
  }
  // 构造更新
  const updates={
    embedding:{
      model:document.getElementById('emb-model').value.trim(),
      device:document.getElementById('emb-device').value,
      dtype:document.getElementById('emb-dtype').value,
      gpu_id:parseInt(document.getElementById('emb-gpu_id').value)||0,
    },
    retrieval:{
      top_k:parseInt(document.getElementById('ret-top_k').value)||5,
      similarity_threshold:parseFloat(document.getElementById('ret-threshold').value)||0.5,
    },
    pre_review:{
      similarity_threshold:parseFloat(document.getElementById('pr-threshold').value)||0.8,
      batch_size:parseInt(document.getElementById('pr-batch').value)||0,
    },
    conflict_detection:{
      min_score:parseFloat(document.getElementById('cd-min_score').value)||0.7,
      min_similarity:parseFloat(document.getElementById('cd-min_sim').value)||0.5,
      max_similarity:parseFloat(document.getElementById('cd-max_sim').value)||0.95,
    },
    prompts:{
      system:document.getElementById('prompt-system').value,
      context_template:document.getElementById('prompt-context').value,
      conflict_warning:document.getElementById('prompt-conflict').value,
    },
    judge:{prompt_file:document.getElementById('judge-prompt_file').value.trim()},
    chat:{max_history:parseInt(document.getElementById('chat-max_history').value)||20},
    llm_profiles:_settingsProfiles,
    llm_routing:{
      qa:document.getElementById('route-qa').value,
      pre_review:document.getElementById('route-pre_review').value,
      conflict_detection:document.getElementById('route-conflict_detection').value,
    },
  };
  const st=document.getElementById('settingsStatus');
  st.textContent='保存中...';
  try{
    const res=await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(updates)});
    if(!res.ok){const e=await res.json();st.textContent='保存失败: '+(e.detail||res.status);return;}
    st.textContent='✅ 已保存';
    _settingsConfig=null; // 下次进入页面时重新加载
  }catch(e){
    st.textContent='保存失败: '+e.message;
  }
}