// ============================================================
// PDF.js 初始化
// ============================================================
if(typeof pdfjsLib!=='undefined'){pdfjsLib.GlobalWorkerOptions.workerSrc='https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js';}

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
let docMap={}, fileHashToName={}, reviewResult=null, reviewTaskId=null, newDocFile=null, newDocHash='', activeFileName=null;
let currentDoc=null, currentPage=1, totalPages=1;

// 自定义确认对话框（是/否），返回 Promise<boolean>
function customConfirm(message){
    return new Promise(resolve=>{
        const overlay=document.getElementById('modalOverlay');
        document.getElementById('modalMsg').textContent=message;
        overlay.classList.add('show');
        const yesHandler=()=>{cleanup();resolve(true);};
        const noHandler=()=>{cleanup();resolve(false);};
        const keyHandler=(e)=>{if(e.key==='Escape'){cleanup();resolve(false);}};
        const cleanup=()=>{
            overlay.classList.remove('show');
            document.getElementById('modalBtnYes').removeEventListener('click',yesHandler);
            document.getElementById('modalBtnNo').removeEventListener('click',noHandler);
            document.removeEventListener('keydown',keyHandler);
        };
        document.getElementById('modalBtnYes').addEventListener('click',yesHandler);
        document.getElementById('modalBtnNo').addEventListener('click',noHandler);
        document.addEventListener('keydown',keyHandler);
    });
}

// ============================================================
// 侧边栏文档列表
// ============================================================
async function refreshDocList(){
    const res=await fetch('/api/documents/list');
    const data=await res.json();
    docMap={};
    fileHashToName={};
    const el=document.getElementById('docList');
    let pendingHtml='', ingestedHtml='';
// 待审核文档（放上面）
if(newDocFile&&!docMap[newDocFile]){
docMap[newDocFile]='N';
if(newDocHash)fileHashToName[newDocHash]=newDocFile;
const hashShort=newDocHash?newDocHash.slice(-8).toUpperCase():'';
const pendingName=hashShort?`${esc(newDocFile)} <span style="color:var(--text3);font-size:10px;">[${hashShort}]</span>`:esc(newDocFile);
const cls=(currentDoc===newDocFile)?'doc-item pending active':'doc-item pending';
const pendingStatus=reviewTaskId?'预审核进行中...':'等待审核';
pendingHtml=`<div class="doc-list-title" style="color:var(--warn);">待审核</div><div class="${cls}" onclick="selectPendingDoc('${escA(newDocFile)}')" title="${escA(newDocFile)}"><span class="id" style="color:var(--warn);">N</span><div class="info"><div class="name">${pendingName}</div><div class="stats">${pendingStatus}</div></div></div>`;
}
    // 已入库文档
    if(!data.documents||!data.documents.length){
        ingestedHtml='<div class="doc-list-title">已入库文档</div><div style="color:#aaa;padding:12px;font-size:12px;">请上传第一份文档</div>';
    } else {
        ingestedHtml='<div class="doc-list-title">已入库文档</div>';
data.documents.forEach((d,i)=>{const id='B'+(i+1);const hashShort=d.file_hash?d.file_hash.slice(-8).toUpperCase():'';const docId=d.doc_id||d.filename;docMap[docId]=id;docMap[d.filename]=id;if(d.file_hash)fileHashToName[d.file_hash]=d.filename;const cls=(currentDoc===docId||currentDoc===d.filename)?'doc-item active':'doc-item';const pg=d.page_count||kbTotalPagesCache[d.filename]||'';const stats=[];if(pg)stats.push(pg+'页');if(d.char_count)stats.push(d.char_count+'字');if(d.paragraph_count)stats.push(d.paragraph_count+'段');if(d.table_count)stats.push(d.table_count+'表');if(d.added_at){const dt=new Date(d.added_at);const pad=n=>String(n).padStart(2,'0');stats.push((dt.getMonth()+1)+'/'+pad(dt.getDate())+' '+pad(dt.getHours())+':'+pad(dt.getMinutes()));}const verTag=(d.label||d.version)?`<span class="doc-label-tag">${esc(d.label||d.version)}</span>`:'';const displayName=hashShort?`${esc(d.filename)}<span style="color:var(--text3);font-size:10px;"> [${hashShort}]</span>${verTag}`:esc(d.filename);ingestedHtml+=`<div class="${cls}" data-filename="${escA(d.filename)}" data-hash="${escA(d.file_hash||'')}" onclick="selectDoc('${escA(docId)}')" title="${escA(d.filename)} [${hashShort}]"><span class="id">${id}</span><div class="info"><div class="name">${displayName}</div>${stats.length?'<div class="stats">'+stats.join(' · ')+'</div>':''}</div><span class="del-btn" onclick="event.stopPropagation();removeDoc('${escA(docId)}')">🗑️</span></div>`;});
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
// 预览待审核文档（支持 PDF / docx 等）
async function selectPendingDoc(filename){
    // 如果正在预审核 tab，弹窗确认
    const reviewTabActive=document.getElementById('reviewPanel').classList.contains('active');
    if(reviewTabActive){
        if(!confirm('当前正在查看预审核结果，确定切换到文档预览？'))return;
        switchKbTab('preview');
    }
    // 清理 PDF 状态
    kbPdfDoc=null;kbPdfUrl=null;
    currentDoc=filename;
    refreshDocList();
    document.getElementById('previewEmpty').style.display='none';
    document.getElementById('previewContent').style.display='flex';
    const ext = filename.split('.').pop().toLowerCase();
    document.getElementById('previewTitle').textContent='X '+filename;
    if(ext === 'pdf'){
        if(!reviewTaskId){
            document.getElementById('kbPdfContainer').innerHTML=
                '<div style="padding:20px;color:var(--danger);text-align:center;">预审核任务不存在，请重新上传文档</div>';
            return;
        }
        const url=`/api/documents/review/pdf?task_id=${reviewTaskId}`;
        try{
            kbPdfDoc=await pdfjsLib.getDocument(url).promise;
        }catch(e){
            document.getElementById('kbPdfContainer').innerHTML=
                `<div style="padding:20px;color:var(--danger);text-align:center;">PDF 加载失败: ${esc(e.message||String(e))}</div>`;
            return;
        }
        totalPages=kbPdfDoc.numPages;
        currentPage=getDocPage(filename);
        document.getElementById('kbPageInput').value=currentPage;
        document.getElementById('kbTotalPages').textContent=totalPages;
        buildPageSlots('kbPdfContainer', totalPages);
        setupLazyRender('kbPdfContainer', kbPdfDoc);
        scrollToPage('kbPdfContainer', currentPage);
    } else {
        // 非 PDF：用文本段落预览
        await loadPendingTextPreview(filename);
    }
}

// 待审核文档（尚未入库）的文本段落预览
async function loadPendingTextPreview(filename){
    const container=document.getElementById('kbPdfContainer');
    container.innerHTML='<div style="padding:16px;color:var(--text3);">加载中...</div>';
    const pager=document.getElementById('kbPager');
    if(pager)pager.style.display='none';
    try{
        const resp=await fetch(`/api/documents/review/paragraphs?file=${encodeURIComponent(filename)}`);
        const data=await resp.json();
        if(!data.paragraphs||!data.paragraphs.length){
            container.innerHTML='<div style="padding:16px;color:var(--text3);">该文档无段落内容</div>';
            return;
        }
        let html='<div style="padding:16px;max-width:800px;">';
        data.paragraphs.forEach((p,i)=>{
            const loc=p.location?`<span style="color:var(--text3);font-size:11px;margin-left:8px;">${esc(p.location)}</span>`:'';
            html+=`<div class="text-para" id="pp-${i}" style="margin-bottom:12px;padding:8px 12px;background:var(--bg2);border-radius:6px;font-size:13px;line-height:1.6;cursor:default;"><span style="color:var(--primary);font-size:11px;margin-right:6px;">¶${i+1}</span>${esc(p.text)}${loc}${p.page?`<span style="float:right;color:var(--text3);font-size:10px;">第${p.page}页</span>`:''}</div>`;
        });
        html+='</div>';
        container.innerHTML=html;
        container.scrollTop=0;
    }catch(e){
        container.innerHTML=`<div style="padding:16px;color:var(--danger);">预览加载失败: ${esc(e.message)}</div>`;
    }
}
// 点击段落滚动到可视区域（用于版本变更跳转）
function scrollParaIntoView(el){
    if(!el)return;
    el.scrollIntoView({behavior:'smooth',block:'center'});
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
    // filename 可能是 doc_id（含 #hash）或纯文件名
    const basename = filename.split('#')[0];
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
        if(input)saveDocPage(currentDoc, parseInt(input.value)||1);
    }
    // 清理 PDF 状态（切换文档时强制重新加载，无论哪种预览类型）
    kbPdfDoc=null;kbPdfUrl=null;
    currentDoc=filename;
    currentPage=getDocPage(filename);
    refreshDocList();
    loadKbPreview(basename);
}

// PDF.js 渲染核心
let kbPdfDoc=null, kbPdfUrl=null;

async function loadKbPreview(basename){
    if(!currentDoc)return;
    // 优先使用传入的 basename，否则从 currentDoc 提取
    if(!basename)basename = currentDoc.split('#')[0];
    const ext = basename.split('.').pop().toLowerCase();
    if(ext !== 'pdf'){
        loadTextPreview(basename);
        return;
    }
    document.getElementById('previewEmpty').style.display='none';
    document.getElementById('previewContent').style.display='flex';
    document.getElementById('previewTitle').textContent=(docMap[currentDoc]||'')+' '+basename;
    // 确保翻页控件可见（从文本预览切回 PDF 时）
    const pager=document.getElementById('kbPager');
    if(pager)pager.style.display='';
    // 用完整 doc_id（含 #hash）定位，避免同名文档预览到同一个文件
    const url=`/api/documents/pdf?name=${encodeURIComponent(currentDoc)}`;
    // 如果换了文档才重新加载 PDF（否则只渲染页面）
    if(kbPdfUrl!==url){
        kbPdfUrl=url;
        document.getElementById('kbPdfContainer').innerHTML='<div style="padding:20px;color:var(--text3);text-align:center;">PDF 加载中...</div>';
        try{
            kbPdfDoc=await pdfjsLib.getDocument(url).promise;
        }catch(e){
            document.getElementById('kbPdfContainer').innerHTML=
                `<div style="padding:20px;color:var(--danger);text-align:center;">PDF 加载失败: ${esc(e.message||String(e))}<br><span style="font-size:11px;">${esc(url)}</span></div>`;
            return;
        }
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
        const resp=await fetch(`/api/documents/paragraphs?name=${encodeURIComponent(currentDoc)}`);
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
const groups=reviewResult&&reviewResult.compare_groups?reviewResult.compare_groups:[];
let n=0, vc=0;
groups.forEach(g=>{ if(g.compare_type==='version_diff') vc+=(g.version_changes||[]).length; else n+=(g.inconsistencies||[]).length; });
let label='预审核';
if(n>0||vc>0) label=`预审核 (${n}矛盾+${vc}变更)`;
document.getElementById('reviewTabBtn').textContent=`⚠️ ${label}`;
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
// 清理上一轮预审核结果，避免在 SSE 首个事件到达前仍显示旧结果
reviewResult=null;reviewTaskId=null;
const oldBtn=document.getElementById('reviewBtn');if(oldBtn){oldBtn.classList.remove('show');oldBtn.textContent='';}
const oldPanel=document.getElementById('reviewPanel');if(oldPanel)oldPanel.classList.remove('active');
newDocFile=file.name;newDocHash='';activeFileName=file.name;
uploadZone.classList.add('disabled');
document.getElementById('stepArea').classList.add('show');
document.getElementById('stepTitle').textContent='上传: '+file.name;
document.getElementById('stepItems').innerHTML='<div class="step-item"><div class="dot active">⏳</div><span>上传中...</span></div>';
const fd=new FormData();fd.append('file',file);fd.append('label',(document.getElementById('docLabelInput')?.value||'').trim());
try{
const res=await fetch('/api/documents/upload',{method:'POST',body:fd});
if(!res.ok){const e=await res.json();alert(e.detail||'上传失败');resetUpload();return;}
const data=await res.json();
// 同名文档：需要用户选择覆盖/预审核（自定义是/否对话框）
if(data.needs_choice){
    const overwrite=await customConfirm(
        `知识库已有同名文档「${data.filename}」（${data.existing.paragraph_count}段）。\n是否直接覆盖？\n\n· 是：删除旧文档，替换为新文档。\n· 否：走预审核流程（版本差异审核）。`
    );
    const choice = overwrite ? 'overwrite' : 'coexist';
    // 重新上传，带 choice 参数
    const fd2=new FormData();fd2.append('file',file);fd2.append('choice',choice);fd2.append('label',(document.getElementById('docLabelInput')?.value||'').trim());
    const res2=await fetch('/api/documents/upload',{method:'POST',body:fd2});
    if(!res2.ok){const e=await res2.json();alert(e.detail||'上传失败');resetUpload();return;}
    const data2=await res2.json();
    reviewTaskId=data2.task_id;
    newDocHash=data2.file_hash||'';
    document.getElementById('stepTitle').textContent='预审核: '+fmtDocName(data2.filename,newDocHash);
    refreshDocList();
    connectSSE(data2.task_id);
    return;
}
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
        // ★ 增量结果推送：运行中 + phase≠done 时，实时更新预审核面板
        if(d.status==='running'&&d.result&&d.result.phase&&d.result.phase!=='done'){
            reviewResult=d.result;
            if(d.old_version_filepath) reviewResult.old_version_filepath = d.old_version_filepath;
            if(d.old_doc_filename) reviewResult.old_doc_filename = d.old_doc_filename;
            // 用 buildReviewPanel 渲染，保留点击等交互能力
            buildReviewPanel();
            // 确保 reviewPanel 可见
            const area=document.getElementById('reviewPanel');
            if(area) area.classList.add('active');
            const preview=document.getElementById('previewPanel');
            if(preview) preview.classList.remove('active');
        }
        if(d.status==='done'||d.status==='error'||d.status==='cancelled'){
            es.close();
            if(d.status==='done'&&d.result){
                reviewResult=d.result;
                if(d.old_version_filepath) reviewResult.old_version_filepath = d.old_version_filepath;
                if(d.old_doc_filename) reviewResult.old_doc_filename = d.old_doc_filename;
                document.getElementById('stepArea').classList.remove('show');
                const groups=d.result.compare_groups||[];
                let n=0, vc=0;
                groups.forEach(g=>{ if(g.compare_type==='version_diff') vc+=(g.version_changes||[]).length; else n+=(g.inconsistencies||[]).length; });
                let btnText='';
                if(n>0) btnText+=`⚠️ ${n} 处内容矛盾`;
                if(vc>0) btnText+=`${n>0?'，':''}📝 ${vc} 处版本变更`;
                if(!btnText) btnText='✅ 未发现异常 → 确认入库';
                else btnText+=' → 查看详情';
                document.getElementById('reviewBtn').textContent=btnText;
                document.getElementById('reviewBtn').classList.add('show');
                buildReviewPanel();
                if(vc>0||(!n&&!vc)) showReviewTab();
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
        const s=all[i],cs=comp.find(function(x){return x.id===s.id;});
        const hasEl=cs&&cs.elapsed!=null,isIn=compIds.indexOf(s.id)>=0;
        const isDone=isIn&&hasEl,isAct=isIn&&!hasEl;
        const cls=isDone?'done':(isAct?'active':'pending');
        const icon=isDone?'\u2713':(isAct?'\u25B8':'\u00B7');
        let time='';
        if(isDone&&cs){time='<span class="time">'+Math.round(cs.elapsed)+'s</span>';}
        else if(isAct){time='<span class="time" id="activeTimer">'+Math.floor(d.current_elapsed||0)+'s</span>';}
        // 进度条：done 显示满条；active 显示流动动画（docling 无进度回调，
        // pct 只有阶段起止点，用 indeterminate 动画表达'进行中'，避免静止误导）
        let bar='';
        if(isDone){
            bar='<div class="step-bar"><div class="step-bar-fill" style="width:100%"></div></div>';
        }else if(isAct){
            bar='<div class="step-bar"><div class="step-bar-fill indeterminate"></div></div>';
        }
        html+='<div class="step-item"><div class="dot '+cls+'">'+icon+'</div><span>'+esc(s.label)+'</span>'+time+bar+'</div>';
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
// 预审核结果面板（支持增量实时渲染）
// ============================================================
function pauseReview(){
    if(!reviewTaskId)return;
    fetch(`/api/documents/review/${reviewTaskId}/pause`,{method:'POST'}).then(r=>r.json()).then(d=>{
        if(reviewResult)reviewResult.status='paused';
        buildReviewPanel();
    });
}
function resumeReview(){
    if(!reviewTaskId)return;
    fetch(`/api/documents/review/${reviewTaskId}/resume`,{method:'POST'}).then(r=>r.json()).then(d=>{
        if(reviewResult)reviewResult.status='running';
        buildReviewPanel();
    });
}
function cancelReviewWithConfirm(){
    if(!reviewTaskId)return;
    if(!confirm('确定要中断本次文档检测吗？已完成的对比结果会保留，未完成的将丢弃。'))return;
    fetch(`/api/documents/review/${reviewTaskId}/cancel`,{method:'POST'}).then(r=>r.json()).then(d=>{
        if(reviewResult)reviewResult.status='cancelled';
        buildReviewPanel();
    });
}

function buildReviewPanel(){
const r=reviewResult;if(!r)return;
const groups=r.compare_groups||[];
const phase = r.phase || 'done';
const compareTotal = r.compare_total || groups.length;
const compareDone = r.compare_done || (phase==='done'?groups.length:0);
const newDocName = newDocFile || r.new_filename || '';

// ====== 进行中：进度条 + 动态 message ======
let progressHtml='';
if(phase!=='done'){
    const pct = compareTotal>0 ? Math.round(compareDone/compareTotal*100) : 0;
    const progressColor = r.phase==='scoring' ? 'var(--primary)' : '#722ed1';
    const icon = r.phase==='scoring' ? '🔍' : r.phase==='comparing' ? '🔁' : '🧠';
    const label = r.phase==='scoring' ? '评估相似度' : r.phase==='comparing' ? '逐文档比较中' : '处理中';
    const statusTag = r.status==='paused' ? '<span style="color:var(--warn);font-weight:700;">⏸ 已暂停</span>' : '';
    progressHtml=`<div class="judging-banner" style="padding:8px 10px;margin-bottom:8px;border-radius:4px;font-size:12px;background:rgba(114,46,209,0.06);">`;
    progressHtml+=`<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;"><span class="pulse-dot" style="width:8px;height:8px;background:${progressColor};border-radius:50%;animation:pulse 1s infinite;display:inline-block;"></span><b>${icon} ${label}</b> <span style="color:var(--text3);">${compareDone}/${compareTotal} 组</span> ${statusTag}</div>`;
    progressHtml+=`<div style="height:6px;background:#eee;border-radius:3px;overflow:hidden;"><div style="height:100%;width:${pct}%;background:${progressColor};transition:width .4s;"></div></div>`;
    progressHtml+=`<div style="margin-top:4px;color:var(--text3);font-size:11px;">${esc(r.message||'处理中...')}</div>`;
    progressHtml+=`</div>`;
} else if(groups.length===0){
    if(r.kb_empty){ progressHtml='<div style="padding:20px;text-align:center;color:var(--text3);">📭 首篇文档，无对比对象，可入库</div>'; }
    else { progressHtml='<div style="padding:20px;text-align:center;color:var(--text3);">✅ 预审核通过，未发现矛盾或版本差异</div>'; }
}

// ====== 控制按钮（进行中：暂停/续跑/取消） ======
let controlsHtml='';
if(phase!=='done'){
    const paused = r.status==='paused';
    controlsHtml='<div class="review-controls" style="margin-bottom:8px;display:flex;gap:6px;">';
    controlsHtml+=paused
        ? `<button onclick="resumeReview()" style="padding:4px 12px;border-radius:4px;border:1px solid var(--primary);background:var(--primary);color:#fff;font-size:11px;cursor:pointer;">▶ 续跑</button>`
        : `<button onclick="pauseReview()" style="padding:4px 12px;border-radius:4px;border:1px solid var(--warn);background:none;color:var(--warn);font-size:11px;cursor:pointer;">⏸ 暂停</button>`;
    controlsHtml+=`<button onclick="cancelReviewWithConfirm()" style="padding:4px 12px;border-radius:4px;border:1px solid var(--danger);background:none;color:var(--danger);font-size:11px;cursor:pointer;">✕ 中断</button>`;
    controlsHtml+='</div>';
}

// ====== 分组 fold（compare_groups） ======
let groupsHtml='';
if(groups.length>0){
    groupsHtml='<div class="compare-groups">';
    groups.forEach((g,gi)=>{
        const changes = g.version_changes||[];
        const minor = g.minor_changes||[];
        const incons = g.inconsistencies||[];
        const isVersion = g.compare_type==='version_diff';
        const typeBadge = isVersion
            ? `<span class="badge badge-primary">📝 版本差异 ${changes.length}</span>`
            : `<span class="badge badge-warn">⚠️ 内容检查 ${incons.length}</span>`;
        const gHash = g.file_hash ? '#'+g.file_hash.slice(-8).toUpperCase() : '';
        const simTag = g.similarity!==undefined ? `<span style="color:var(--text3);font-size:10px;">相似度 ${(g.similarity*100).toFixed(0)}%</span>` : '';
        const statusIcon = g.status==='done' ? '✅' : g.status==='error' ? '❌' : '⏳';
        const itemCount = isVersion ? changes.length : incons.length;
        groupsHtml+=`<div class="compare-group" data-gi="${gi}">`;
        groupsHtml+=`<div class="compare-group-title" onclick="toggleCompareGroup(${gi})">`;
        groupsHtml+=`<span class="cg-caret" style="display:inline-block;transition:transform .2s;">▶</span>`;
        groupsHtml+=`<span class="src-id">${docMap[g.doc_id]||('B'+(gi+1))}</span>`;
        groupsHtml+=`<span class="src-file">${esc(g.doc_filename||'')}</span>`;
        groupsHtml+=gHash?` <span class="src-file-hash">${esc(gHash)}</span>`:'';
        groupsHtml+=g.label?` <span class="doc-label-tag">${esc(g.label)}</span>`:'';
        groupsHtml+=` ${typeBadge} ${simTag} <span style="color:var(--text3);font-size:10px;">${statusIcon}</span>`;
        groupsHtml+=`</div>`;
        groupsHtml+=`<div class="compare-group-body" style="display:none;">`;
        if(g.error){ groupsHtml+=`<div style="padding:8px;color:var(--danger);font-size:11px;">${esc(g.error)}</div>`; }
        if(isVersion){
            if(changes.length===0 && minor.length===0 && g.status==='done'){
                groupsHtml+='<div style="padding:8px;color:var(--text3);font-size:11px;">无实质性差异</div>';
            }
            changes.forEach((c,ci)=>{
                const icon=c.type==='modified'?'✏️':c.type==='added'?'➕':'➖';
                const tl=c.type==='modified'?'修改':c.type==='added'?'新增':'删除';
                groupsHtml+=`<div class="version-diff-item" onclick="showGroupVersionDiff(${gi},${ci})" data-gi="${gi}" data-ci="${ci}">`;
                groupsHtml+=`<div class="vd-header"><span class="vd-type">${icon} ${tl}</span><span class="vd-loc">${esc(c.location||'')}</span><span class="vd-summary">${esc(c.summary||'')}</span></div>`;
                groupsHtml+=`</div>`;
            });
        } else {
            if(incons.length===0 && g.status==='done'){
                groupsHtml+='<div style="padding:8px;color:var(--text3);font-size:11px;">未发现矛盾</div>';
            }
            incons.forEach((inc,ci)=>{
                groupsHtml+=`<div class="conflict-item" style="cursor:pointer;" onclick="showGroupConflict(${gi},${ci})">`;
                groupsHtml+=`<div class="title">${esc(inc.point||'内容差异')}</div>`;
                groupsHtml+=`<div class="desc"><span style="color:var(--primary);">新:</span> ${esc((inc.doc_a_says||'').slice(0,100))}`;
                groupsHtml+=`<br><span style="color:var(--warn);">旧:</span> ${esc((inc.doc_b_says||'').slice(0,100))}</div>`;
                groupsHtml+=`</div>`;
            });
        }
        groupsHtml+=`</div></div>`;
    });
    groupsHtml+='</div>';
}

// info 汇总
let infoText='';
if(phase!=='done'){
    infoText = r.message || '处理中...';
    infoText+=`　（新文档: ${esc(newDocName)}）`;
} else {
    const nIssue = r.n_issue_groups || 0;
    if(r.cancelled){ infoText='⏹ 已中断'; }
    else if(nIssue>0){ infoText=`⚠️ 共 ${nIssue} 组存在差异/矛盾（${r.n_version_groups||0} 组版本差异 + ${r.n_conflict_groups||0} 组内容检查）`; }
    else if(r.kb_empty){ infoText='📭 首篇文档，无对比对象，可入库'; }
    else { infoText='✅ 未发现内容矛盾，可安全入库'; }
    infoText+=`　（新文档: ${esc(newDocName)}）`;
}
document.getElementById('reviewInfo').textContent=infoText;
document.getElementById('conflictList').innerHTML=progressHtml+controlsHtml+groupsHtml;
}

function toggleCompareGroup(gi){
    const el=document.querySelectorAll('.compare-group')[gi];
    if(!el)return;
    const body=el.querySelector('.compare-group-body');
    const caret=el.querySelector('.cg-caret');
    const open=body.style.display!=='none';
    body.style.display=open?'none':'block';
    caret.style.transform=open?'':'rotate(90deg)';
}

function showGroupVersionDiff(gi,ci){
    const g=reviewResult?.compare_groups?.[gi];
    const vc=g?.version_changes?.[ci];
    if(!vc)return;
    // 复用现有版本差异详情面板（用一个全局指向当前组）
    window.__groupIdx=gi;
    document.querySelectorAll('.version-diff-item').forEach((el,i)=>el.classList.toggle('selected', i===ci && Number(el.dataset.gi)===gi));
    showVersionDiffForGroup(gi,ci);
}

async function showVersionDiffForGroup(gi,ci){
    const g=reviewResult?.compare_groups?.[gi];
    const vc=g?.version_changes?.[ci];
    if(!vc)return;
    window.__groupIdx=gi;
    const panel=document.getElementById('versionComparePanel');
    if(!panel)return;
    panel.classList.add('show');
    panel.dataset.vcIdx=ci;
    panel.dataset.groupDocId=g.doc_id||'';
    const typeIcon=vc.type==='modified'?'✏️':vc.type==='added'?'➕':'➖';
    const typeLabel=vc.type==='modified'?'修改':vc.type==='added'?'新增':'删除';
    const diffPage=extractPageFromLoc(vc.location||vc.old_location)||1;
    let html='';
    html+=`<div style="display:flex;justify-content:space-between;align-items:center;padding:10px 14px;border-bottom:1px solid var(--border);background:var(--card);flex-shrink:0;">`;
    html+=`<span style="font-weight:600;">${typeIcon} ${typeLabel} · ${esc(g.doc_filename||'')} 差异 #${ci+1}</span>`;
    html+=`<button onclick="closeVersionCompare()" style="background:none;border:1px solid var(--border);border-radius:4px;padding:3px 12px;cursor:pointer;font-size:11px;">✕ 返回列表</button></div>`;
    // sticky: 差异定位 + 摘要 + 文字差异 + 页面切换
    html+=`<div class="vc-sticky-header">`;
    const oldLoc=parseLoc(vc.old_location||'');
    const newLoc=parseLoc(vc.location||'');
    html+=`<div class="vc-loc-bar">`;
    html+=`<div class="vc-loc-side vc-loc-old"><span class="vc-loc-tag">B ${esc(g.doc_filename||'').slice(0,12)}</span><span class="vc-loc-page">第 ${oldLoc.page||diffPage} 页</span><span class="vc-loc-section">${esc(oldLoc.section||'—')}</span></div>`;
    html+=`<div class="vc-loc-arrow">→</div>`;
    html+=`<div class="vc-loc-side vc-loc-new"><span class="vc-loc-tag">N 新</span><span class="vc-loc-page">第 ${newLoc.page||diffPage} 页</span><span class="vc-loc-section">${esc(newLoc.section||'—')}</span></div>`;
    html+=`</div>`;
    if(vc.summary) html+=`<div class="vc-summary-bar">💡 ${esc(vc.summary)}</div>`;
    html+=`<div class="vc-text-diff">`;
    html+=`<div style="font-size:11px;font-weight:600;color:var(--text3);margin-bottom:8px;">📝 文字差异对比</div>`;
    const oldText=vc.old_text||'';
    const newText=vc.new_text||'';
    if(vc.type==='added'){
        html+=`<div class="vc-diff-row vc-diff-new-row"><span class="vc-diff-tag">新</span><span class="vc-diff-text">${esc(newText)}</span></div>`;
        html+=`<div class="vc-diff-empty">（旧版无此内容 — 新增段落）</div>`;
    } else if(vc.type==='removed'){
        html+=`<div class="vc-diff-row vc-diff-old-row"><span class="vc-diff-tag">旧</span><span class="vc-diff-text">${esc(oldText)}</span></div>`;
        html+=`<div class="vc-diff-empty">（新版已删除此段落）</div>`;
    } else {
        html+=`<div class="vc-diff-row vc-diff-old-row"><span class="vc-diff-tag">旧</span><span class="vc-diff-text">${diffMark(newText,oldText,'b')}</span></div>`;
        html+=`<div class="vc-diff-row vc-diff-new-row"><span class="vc-diff-tag">新</span><span class="vc-diff-text">${diffMark(newText,oldText,'a')}</span></div>`;
    }
    html+=`</div>`;
    // 页面切换
    html+=`<div style="padding:8px 14px;background:var(--card);border-top:1px solid var(--border);display:flex;align-items:center;gap:8px;">`;
    html+=`<span style="font-size:11px;font-weight:600;color:var(--text3);">📄 页面定位：第 ${diffPage} 页</span>`;
    html+=`<div style="margin-left:auto;display:flex;gap:6px;">`;
    html+=`<button class="vc-quick-tab active" data-side="new" onclick="switchVcTab('new')" style="padding:4px 14px;border:1px solid var(--border);border-radius:4px;background:var(--primary);color:#fff;cursor:pointer;font-size:11px;">N 新版</button>`;
    html+=`<button class="vc-quick-tab" data-side="old" onclick="switchVcTab('old')" style="padding:4px 14px;border:1px solid var(--border);border-radius:4px;background:transparent;cursor:pointer;font-size:11px;color:var(--text3);">B 对比文档</button>`;
    html+=`</div></div>`;
    html+=`</div>`;
    html+=`<div class="vc-scroll-area" id="vcPagesContainer" data-diff-page="${diffPage}"><div style="padding:20px;text-align:center;color:var(--text3);">正在加载文档预览...</div></div>`;
    panel.innerHTML=html;
    // 获取新旧页数（B 侧用组 doc_id）
    const gid=panel.dataset.groupDocId||'';
    const q=gid?`&doc_id=${encodeURIComponent(gid)}`:' ';
    const [newInfo, oldInfo]=await Promise.all([
        fetch('/api/documents/review/info?task_id='+reviewTaskId).then(r=>r.json()),
        fetch('/api/documents/review/old/info?task_id='+reviewTaskId+q.trim()).then(r=>r.json()),
    ]);
    panel.dataset.newPages=newInfo.page_count||0;
    panel.dataset.oldPages=oldInfo.page_count||0;
    panel.dataset.diffPage=diffPage;
    renderVcPages('new');
}

async function showVersionDiff(idx){
    const vc = reviewResult?.version_changes?.[idx];
    if(!vc)return;
    document.querySelectorAll('.version-diff-item').forEach((el,i)=>el.classList.toggle('selected',i===idx));

    const panel = document.getElementById('versionComparePanel');
    if(!panel)return;
    panel.classList.add('show');
    panel.dataset.vcIdx = idx;

    const typeIcon = vc.type==='modified'?'✏️':vc.type==='added'?'➕':'➖';
    const typeLabel = vc.type==='modified'?'修改':vc.type==='added'?'新增':'删除';
    const diffPage = extractPageFromLoc(vc.location||vc.old_location)||1;

    // 顶部标题栏（固定不滚动）
    let html = '';
    html += `<div style="display:flex;justify-content:space-between;align-items:center;padding:10px 14px;border-bottom:1px solid var(--border);background:var(--card);flex-shrink:0;">`;
    html += `<span style="font-weight:600;">${typeIcon} ${typeLabel} · 差异 #${idx+1}</span>`;
    html += `<button onclick="closeVersionCompare()" style="background:none;border:1px solid var(--border);border-radius:4px;padding:3px 12px;cursor:pointer;font-size:11px;">✕ 返回列表</button>`;
    html += `</div>`;

    // ★ sticky 区域：差异定位 + 摘要 + 文字差异 + 页面切换（滚动时固定在顶部）
    html += `<div class="vc-sticky-header">`;
    // 差异定位
    const oldLoc = parseLoc(vc.old_location||'');
    const newLoc = parseLoc(vc.location||'');
    html += `<div class="vc-loc-bar">`;
    html += `<div class="vc-loc-side vc-loc-old"><span class="vc-loc-tag">旧 B1</span><span class="vc-loc-page">第 ${oldLoc.page||diffPage} 页</span><span class="vc-loc-section">${esc(oldLoc.section||'—')}</span></div>`;
    html += `<div class="vc-loc-arrow">→</div>`;
    html += `<div class="vc-loc-side vc-loc-new"><span class="vc-loc-tag">N 新</span><span class="vc-loc-page">第 ${newLoc.page||diffPage} 页</span><span class="vc-loc-section">${esc(newLoc.section||'—')}</span></div>`;
    html += `</div>`;
    // LLM 摘要
    if(vc.summary) html += `<div class="vc-summary-bar">💡 ${esc(vc.summary)}</div>`;
    // 文字差异高亮（双栏 diff）
    html += `<div class="vc-text-diff">`;
    html += `<div style="font-size:11px;font-weight:600;color:var(--text3);margin-bottom:8px;">📝 文字差异对比</div>`;
    const oldText = vc.old_text||'';
    const newText = vc.new_text||'';
    if(vc.type==='added'){
        html += `<div class="vc-diff-row vc-diff-new-row"><span class="vc-diff-tag">新</span><span class="vc-diff-text">${diffMark(newText, oldText, 'a')}</span></div>`;
        html += `<div class="vc-diff-empty">（旧版无此内容 — 新增段落）</div>`;
    } else if(vc.type==='removed'){
        html += `<div class="vc-diff-row vc-diff-old-row"><span class="vc-diff-tag">旧</span><span class="vc-diff-text">${diffMark(newText, oldText, 'b')}</span></div>`;
        html += `<div class="vc-diff-empty">（新版已删除此段落）</div>`;
    } else {
        html += `<div class="vc-diff-row vc-diff-old-row"><span class="vc-diff-tag">旧</span><span class="vc-diff-text">${diffMark(newText, oldText, 'b')}</span></div>`;
        html += `<div class="vc-diff-row vc-diff-new-row"><span class="vc-diff-tag">新</span><span class="vc-diff-text">${diffMark(newText, oldText, 'a')}</span></div>`;
    }
    html += `</div>`;
    // 页面切换按钮
    html += `<div style="padding:8px 14px;background:var(--card);border-top:1px solid var(--border);display:flex;align-items:center;gap:8px;">`;
    html += `<span style="font-size:11px;font-weight:600;color:var(--text3);">📄 页面定位：第 ${diffPage} 页</span>`;
    html += `<div style="margin-left:auto;display:flex;gap:6px;">`;
    html += `<button class="vc-quick-tab active" data-side="new" onclick="switchVcTab('new')" style="padding:4px 14px;border:1px solid var(--border);border-radius:4px;background:var(--primary);color:#fff;cursor:pointer;font-size:11px;">N 新版</button>`;
    html += `<button class="vc-quick-tab" data-side="old" onclick="switchVcTab('old')" style="padding:4px 14px;border:1px solid var(--border);border-radius:4px;background:transparent;cursor:pointer;font-size:11px;color:var(--text3);">B 旧版</button>`;
    html += `</div></div>`;
    html += `</div>`; // 关闭 vc-sticky-header

    // 滚动区域：PDF 页面预览（可上下滚动看完整文档）
    html += `<div class="vc-scroll-area" id="vcPagesContainer" data-diff-page="${diffPage}"><div style="padding:20px;text-align:center;color:var(--text3);">正在加载文档预览...</div></div>`;

    panel.innerHTML = html;

    // 获取页数并渲染
    const [newInfo, oldInfo] = await Promise.all([
        fetch(`/api/documents/review/info?task_id=${reviewTaskId}`).then(r=>r.json()),
        fetch(`/api/documents/review/old/info?task_id=${reviewTaskId}`).then(r=>r.json()),
    ]);
    panel.dataset.newPages = newInfo.page_count||0;
    panel.dataset.oldPages = oldInfo.page_count||0;
    panel.dataset.diffPage = diffPage;

    renderVcPages('new');
}

// 解析 location 字符串 → {page, section}
function parseLoc(loc){
    if(!loc)return {page:1,section:''};
    const page=(loc.match(/第(\d+)页/)||[])[1];
    const sec=(loc.match(/§([\d.]+)/)||[])[1];
    return {page:page?parseInt(page):1,section:sec?`§${sec}`:''};
}

// 渲染指定侧（old/new）的 PDF 预览，智能展示差异页优先
function renderVcPages(side){
    const panel = document.getElementById('versionComparePanel');
    if(!panel)return;
    const container = document.getElementById('vcPagesContainer');
    if(!container)return;
    const pages = parseInt(panel.dataset[side==='new'?'newPages':'oldPages'])||0;
    const diffPage = parseInt(panel.dataset.diffPage)||1;

    if(!pages){container.innerHTML='<div style="padding:20px;text-align:center;color:#aaa;">文档不可用</div>';return;}

    // 检测是否显示全部页面还是只显示差异页
    const showAll = panel.dataset.showAll === '1';
    const pagesToShow = showAll ? Array.from({length:pages},(_,i)=>i+1) : [diffPage];

    let html = '';
    // 标题
    html += `<div style="padding:6px 14px;font-size:11px;color:var(--text3);background:#fafafa;border-bottom:1px solid #eee;display:flex;justify-content:space-between;align-items:center;">`;
    html += `<span>${side==='new'?'🆕 N 新文档':'📜 B 旧版文档'} — 第 ${diffPage} 页${showAll?`（共 ${pages} 页）`:''}</span>`;
    html += `<button onclick="toggleVcShowAll()" style="background:none;border:1px solid var(--border);border-radius:3px;padding:2px 10px;cursor:pointer;font-size:10px;">${showAll?'收起':'查看全部'}</button>`;
    html += `</div>`;

    for(const p of pagesToShow){
        const gid = document.getElementById('versionComparePanel') ? document.getElementById('versionComparePanel').dataset.groupDocId || '' : '';
        const gq = gid ? '&doc_id=' + encodeURIComponent(gid) : '';
        const imgUrl = side==='new'
            ? `/api/documents/review/page?task_id=${reviewTaskId}&page=${p}`
            : `/api/documents/review/old/page?task_id=${reviewTaskId}&page=${p}${gq}`;
        html += `<div class="vc-page-wrap${p===diffPage?' vc-page-active':''}" data-page="${p}">`;
        if(pagesToShow.length>1) html += `<div class="vc-page-num">第 ${p} 页</div>`;
        html += `<img src="${imgUrl}" loading="lazy" style="width:100%;display:block;" onerror="this.parentElement.innerHTML='<div style=\'padding:20px;text-align:center;color:#aaa;\'>第${p}页加载失败</div>'"></div>`;
    }
    container.innerHTML = html;

    // 滚动到差异所在页
    requestAnimationFrame(()=>{
        const target = container.querySelector('.vc-page-wrap.vc-page-active');
        if(target) target.scrollIntoView({behavior:'instant', block:'start'});
    });
}

function toggleVcShowAll(){
    const panel = document.getElementById('versionComparePanel');
    if(!panel)return;
    panel.dataset.showAll = panel.dataset.showAll==='1'?'0':'1';
    const activeSide = panel.querySelector('.vc-quick-tab.active')?.dataset.side||'new';
    renderVcPages(activeSide);
}

// 切换旧版/新版 tab
function switchVcTab(side){
    const panel = document.getElementById('versionComparePanel');
    if(!panel)return;
    panel.querySelectorAll('.vc-quick-tab').forEach(btn=>{
        const active = btn.dataset.side===side;
        btn.classList.toggle('active', active);
        btn.style.background = active?'var(--primary)':'transparent';
        btn.style.color = active?'#fff':'var(--text3)';
    });
    panel.dataset.showAll = '0';
    renderVcPages(side);
}

// 从 location 描述中提取段落号并映射到页码
function extractPageFromLoc(loc){
    if(!loc)return 1;
    const m2 = loc.match(/第(\d+)页/);
    if(m2)return parseInt(m2[1]);
    const m = loc.match(/段落#(\d+)/);
    if(m)return Math.max(1, Math.ceil(parseInt(m[1]) / 2));
    return 1;
}

function extractPage(loc){return extractPageFromLoc(loc);}

// 在 B 项列表中查找匹配文件名（支持按 data-filename 精确匹配或按 title 前缀匹配）
function findDocInList(filename){
    if(!filename)return null;
    // 优先按 data-filename 精确匹配
    let el = document.querySelector(`.doc-item[data-filename="${CSS.escape(filename)}"]`);
    if(el)return {el, name:filename};
    // 回退：按 title 属性前缀匹配
    const items = document.querySelectorAll('.doc-item');
    for(const item of items){
        const title = item.getAttribute('title') || '';
        if(title.startsWith(filename)){return {el:item, name:filename};}
    }
    return null;
}

// 解析 source_file（可能是 SHA256 哈希或路径）→ 返回简短文档定位符（X / B1 / B2 ...）
function resolveFileName(fileRef){
    if(!fileRef)return '';
    // docMap lookup first: filename → short name (X, B1, B2)
    if(docMap[fileRef])return docMap[fileRef];
    // 已是 SHA256 → 反查 fileHashToName → docMap
    if(fileHashToName[fileRef]){
        const fn=fileHashToName[fileRef];
        return docMap[fn]||docMap[fileRef]||fn;
    }
    // 模糊匹配 hash 前缀
    const matchKey=Object.keys(fileHashToName).find(k=>k&&k.startsWith(fileRef));
    if(matchKey){
        const fn=fileHashToName[matchKey];
        return docMap[fn]||docMap[matchKey]||fn;
    }
    // 已是可读名（含中文/扩展名）→ 反查 docMap 返回简短名
    if(/[\u4e00-\u9fff]/.test(fileRef)||/\.\w{2,4}$/.test(fileRef)){
        const found=Object.entries(docMap).find(([k])=>k===fileRef);
        if(found)return found[1];
        return fileRef;
    }
    return fileRef.replace(/\.[^.]+$/,'').slice(0,14);
}

function selectConflict(idx){
document.querySelectorAll('.conflict-item').forEach((el,i)=>el.classList.toggle('selected',i===idx));
document.getElementById('conflictList').classList.add('collapsed');
const c=reviewResult.inconsistencies[idx];
const panel=document.getElementById('comparePanel');
panel.classList.add('show');

// 构建 tab 栏（保留关闭按钮）
// A 侧固定为 N（新文档），B 侧用已有文档的简短名（B1/B2）
const bFile=resolveFileName(c.doc_b?.file||c.doc_b_file||'') || '?';
const tabsEl=document.getElementById('cmpTabs');
tabsEl.innerHTML=`<button id="cmpTabA" class="active" onclick="compareShowTab('a')">N 新文档</button>`;
tabsEl.innerHTML+=`<button id="cmpTabB" onclick="compareShowTab('b')">${esc(bFile)} 已有文档</button>`;
tabsEl.innerHTML+=`<span class="compare-close" onclick="closeCompare()" title="返回列表" style="margin-left:auto;cursor:pointer;color:var(--text3);font-size:12px;display:flex;align-items:center;gap:3px;"><span style="font-size:14px;">⟵</span> 返回列表</span>`;

// 默认显示 N 侧（新文档）
compareShowTab('a');
}

// 文本差异高亮：找公共前后缀，中间不同的部分用 <mark> 包裹
function diffMark(textA, textB, side){
    if(!textA||!textB)return esc(side==='a'?textA:textB);
    // 公共前缀
    let p=0; const minLen=Math.min(textA.length,textB.length);
    while(p<minLen&&textA[p]===textB[p])p++;
    // 公共后缀
    let s=0;
    while(s<minLen-p&&textA[textA.length-1-s]===textB[textB.length-1-s])s++;
    const pre=textA.slice(0,p);
    const suf=textA.slice(textA.length-s);
    const midA=textA.slice(p,textA.length-s);
    const midB=textB.slice(p,textB.length-s);
    const mid = side==='a' ? midA : midB;
    const other = side==='a' ? midB : midA;
    // 如果差异太大（<0.7 相似度可能），直接返回原文
    if(!mid&&!other) return esc(pre+suf);
    if(mid===other) return esc(textA);
    return esc(pre)+`<mark class="diff-mark">${esc(mid)}</mark>`+esc(suf);
}

function compareShowTab(side){
const items=document.querySelectorAll('.conflict-item');
const idx=Array.from(items).findIndex(el=>el.classList.contains('selected'));
const c=reviewResult.inconsistencies[idx<0?0:idx];
if(!c)return;

// 更新 tab 按钮高亮
const tabs=document.querySelectorAll('#cmpTabs button');
if(tabs[0])tabs[0].classList.toggle('active', side==='a');
if(tabs[1])tabs[1].classList.toggle('active', side==='b');

const body=document.getElementById('compareBody');
const aSays=c.doc_a?.says||c.doc_a_says||'';
const bSays=c.doc_b?.says||c.doc_b_says||'';
const aLoc=c.doc_a?.location||c.doc_a_location||'';
const bLoc=c.doc_b?.location||c.doc_b_location||'';
const loc = side==='a'?aLoc:bLoc;
const page = extractPage(loc)||1;

// 文字差异高亮（sticky 固定在顶部）
const textDiffHtml = `<div class="diff-compare">`+
    `<div class="diff-row"><span class="diff-label">N</span><span class="diff-text">${diffMark(aSays,bSays,'a')}</span></div>`+
    `<div class="diff-row"><span class="diff-label">B</span><span class="diff-text">${diffMark(aSays,bSays,'b')}</span></div>`+
    `</div>`;

// 页面导航条
const navBar = `<div style="padding:6px 10px;background:var(--card);border-bottom:1px solid var(--border);display:flex;align-items:center;gap:8px;font-size:11px;color:var(--text3);">
    <span>📄 第 ${page} 页</span>
    <div style="margin-left:auto;display:flex;gap:4px;">
        <button onclick="cmpPrevPage()" style="padding:2px 10px;border:1px solid var(--border);border-radius:3px;background:none;cursor:pointer;font-size:11px;">◀</button>
        <input type="number" id="cmpPageInput" value="${page}" min="1" style="width:40px;text-align:center;font-size:11px;" onchange="cmpGoToPage()" onkeydown="if(event.key==='Enter')cmpGoToPage()">
        <button onclick="cmpNextPage()" style="padding:2px 10px;border:1px solid var(--border);border-radius:3px;background:none;cursor:pointer;font-size:11px;">▶</button>
    </div></div>`;

let imgUrl;
if(side==='a'){
    // N 侧 → 新文档（待审核），使用 review/page API
    imgUrl = `/api/documents/review/page?task_id=${reviewTaskId}&page=`;
} else {
    // B 侧 → 已有文档（在知识库中），使用 documents/page API
    const bFile = resolveFileName(c.doc_b?.file||c.doc_b_file||'') || '';
    if(bFile){
        const fullName = Object.keys(docMap).find(k=>docMap[k]===bFile) || bFile;
        imgUrl = `/api/documents/page?name=${encodeURIComponent(fullName)}&page=`;
    } else {
        body.innerHTML=`<div style="color:#aaa;padding:20px;text-align:center;">${textDiffHtml}<br>⚠️ 旧文档信息缺失，无法定位预览</div>`;
        return;
    }
}

// 构建可滚动的预览区域：sticky 文字差异 + 导航条 + PDF 页面
body.innerHTML=`<div style="display:flex;flex-direction:column;width:100%;max-width:700px;gap:6px;">${textDiffHtml}${navBar}<div id="cmpPdfArea" style="flex:1;overflow-y:auto;"><img src="${imgUrl}${page}" style="width:100%;border-radius:4px;" onerror="this.outerHTML='<div style=\\'padding:20px;text-align:center;color:#aaa;\\'>页面渲染失败</div>'"></div></div>`;
// 保存当前页和 URL 前缀供翻页使用
body.dataset.page=page;
body.dataset.imgBase=imgUrl;
}

// 内容矛盾预览翻页
function cmpPrevPage(){const body=document.getElementById('compareBody');if(!body)return;const p=Math.max(1,parseInt(body.dataset.page||'1')-1);cmpSetPage(p);}
function cmpNextPage(){const body=document.getElementById('compareBody');if(!body)return;const p=parseInt(body.dataset.page||'1')+1;cmpSetPage(p);}
function cmpGoToPage(){const inp=document.getElementById('cmpPageInput');if(!inp)return;cmpSetPage(Math.max(1,parseInt(inp.value)||1));}
function cmpSetPage(p){
    const body=document.getElementById('compareBody');if(!body)return;
    const base=body.dataset.imgBase||'';if(!base)return;
    body.dataset.page=p;
    const inp=document.getElementById('cmpPageInput');if(inp)inp.value=p;
    const nav=document.querySelector('#compareBody > div > div[style*="padding:6px"] > span');if(nav)nav.textContent=`📄 第 ${p} 页`;
    const area=document.getElementById('cmpPdfArea');if(!area)return;
    area.innerHTML=`<img src="${base}${p}" style="width:100%;border-radius:4px;" onerror="this.outerHTML='<div style=\\'padding:20px;text-align:center;color:#aaa;\\'>第${p}页加载失败</div>'">`;
}

function extractPage(loc){const m=(loc||'').match(/第(\d+)/);return m?parseInt(m[1]):1;}

function closeCompare(){
    document.getElementById('comparePanel').classList.remove('show');
    document.getElementById('conflictList').classList.remove('collapsed');
    document.querySelectorAll('.conflict-item').forEach(el=>el.classList.remove('selected'));
}
function closeVersionCompare(){
    const panel=document.getElementById('versionComparePanel');
    if(panel)panel.classList.remove('show');
    document.querySelectorAll('.version-diff-item').forEach(el=>el.classList.remove('selected'));
    switchKbTab('review');
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
    if(info){info.textContent='⏳ 正在入库（解析/向量复用预审核缓存，首次稍慢），请稍候...';}
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
            // 去重：同一文档+同一位置只保留一条（取分数最高的）
            const seen=new Map();
            for(const s of data.sources){
                const key=(s.doc_id||s.source_file)+'|'+(s.location||'');
                if(!seen.has(key)||(s.score||0)>seen.get(key).score){
                    seen.set(key,s);
                }
            }
            allSources=[...seen.values()];
            // 文档级编号：优先用知识库列表的 docMap（B1/B2 与列表一致），
            // 未在列表中的文档按出现顺序补 B3/B4...
            const docIds={};
            const docOrder=[];
            for(const s of allSources){
                const did=s.doc_id||s.source_file||'';
                if(did&&!docIds[did]){
                    docIds[did]=docMap[did]||('B'+(docOrder.length+1));
                    docOrder.push(did);
                }
            }
            // 底部 legend：按文档分组，块标题=Bn+文件名[hash]+tag，块内列出 [idx] 引用项
            let srcHtml=`<div class="sources"><div class="src-title">📎 引用来源</div>`;
            for(const did of docOrder){
                const bn=docIds[did];
                const docSources=allSources.filter(x=>(x.doc_id||x.source_file||'')===did);
                // doc_id 形如 'xxx.pdf#29A17952'：文件名 + 短 hash 显示
                const parts=did.split('#');
                const displayFile=parts[0]||did;
                const hash8=parts[1]||'';
                srcHtml+=`<div class="src-doc-block"><div class="src-doc-title"><span class="src-id">${bn}</span><span class="src-file">${esc(displayFile)}</span>`;
                if(hash8)srcHtml+=` <span class="src-file-hash">#${esc(hash8)}</span>`;
                const firstSrc=docSources[0];
                if(firstSrc&&firstSrc.label)srcHtml+=` <span class="doc-label-tag">${esc(firstSrc.label)}</span>`;
                srcHtml+=`</div>`;
                for(const s of docSources){
                    srcHtml+=`<div class="src-item" id="ref-${s.idx}" data-file="${escA(s.source_file||'')}" data-loc="${escA(s.location||'')}">`;
                    srcHtml+=`<div><span class="src-ref">[${s.idx}]</span><span class="src-loc">${esc(s.location||'')}</span></div>`;
                    srcHtml+=`<div class="src-text">${esc((s.text||'').slice(0,120))}${(s.text||'').length>120?'…':''}</div>`;
                    srcHtml+=`</div>`;
                }
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
            if(d.old_version_filepath) reviewResult.old_version_filepath = d.old_version_filepath;
            if(d.old_doc_filename) reviewResult.old_doc_filename = d.old_doc_filename;
            const groups=d.result.compare_groups||[];
            let n=0, vc=0;
            groups.forEach(g=>{ if(g.compare_type==='version_diff') vc+=(g.version_changes||[]).length; else n+=(g.inconsistencies||[]).length; });
            let btnText='';
            if(n>0) btnText+=`⚠️ ${n} 处内容矛盾`;
            if(vc>0) btnText+=(`${n>0?'，':''}📝 ${vc} 处版本变更`);
            if(!btnText) btnText='✅ 未发现异常';
            btnText+=' → 查看详情';
            document.getElementById('reviewBtn').textContent=btnText;
            document.getElementById('reviewBtn').classList.add('show');
            buildReviewPanel();
            showReviewTab();  // 自动切到预审核 tab 展示结果
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
let _settingsSelectedProfile=''; // 当前选中的 LLM 名

// 每个 LLM 独立的字段组（分组 + 中文标签，选择名字即展示该 LLM 对应的完整配置）
const LLM_PROFILE_GROUPS=[
  {
    title:'连接',
    fields:[
      ['provider','服务商'],
      ['base_url','Base URL'],
      ['api_key','API Key'],
      ['api_key_env','API Key 环境变量'],
      ['region','区域(region)'],
      ['endpoint','接口(endpoint)'],
    ],
  },
  {
    title:'模型',
    fields:[
      ['model','模型(model)'],
      ['max_tokens','max_tokens'],
      ['context_window','上下文窗口'],
    ],
  },
  {
    title:'重试与并发',
    fields:[
      ['timeout','超时(秒)'],
      ['max_retries','最大重试'],
      ['retry_backoff','重试间隔'],
      ['concurrency','并发数'],
    ],
  },
];
// 扁平 key 列表，供保存逻辑遍历
const LLM_PROFILE_FIELDS=LLM_PROFILE_GROUPS.flatMap(g=>g.fields);
// 数字型字段
const LLM_NUM_FIELDS=['max_tokens','timeout','max_retries','context_window','concurrency'];
// 新建 LLM 的常用模板（预填，选模板名即得一组完整参数）
const LLM_PROFILE_TEMPLATES={
  'OpenAI 兼容':{provider:'openai',model:'',base_url:'',api_key:'',api_key_env:'',endpoint:'chat',region:'',max_tokens:2048,timeout:120,max_retries:3,retry_backoff:2.0,context_window:8192,concurrency:1},
  'Bedrock (Glm flash)':{provider:'bedrock',model:'zai.glm-4.7-flash',region:'us-east-1',api_key_env:'AWS_BEARER_TOKEN_BEDROCK',endpoint:'',max_tokens:2048,timeout:120,max_retries:3,retry_backoff:2.0,context_window:8192,concurrency:1},
  'Bedrock (Kimi thinking)':{provider:'bedrock',model:'moonshot.kimi-k2-thinking',region:'us-east-1',api_key_env:'AWS_BEARER_TOKEN_BEDROCK',endpoint:'',max_tokens:4096,timeout:180,max_retries:2,retry_backoff:3.0,context_window:128000,concurrency:1},
};

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
  renderProfileOverview();
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
  document.getElementById('pr-parse_backend').value=pr.parse_backend||'auto';
  document.getElementById('pr-docling_device').value=pr.docling_device||'auto';
  document.getElementById('pr-docling_batch').value=pr.docling_batch_size||0;
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

// 切换 LLM：更新选中名并刷新该 LLM 的字段（修复切换不生效的 bug）
function selectLlmProfile(name){
  _settingsSelectedProfile=name;
  renderProfileSelect();
  renderLlmProfile();
}

// LLM 路由页的 LLM 概览列表：每个 LLM 一行（名字 + 服务商 + 模型），点击进入管理
function renderProfileOverview(){
  const container=document.getElementById('profileOverviewList');
  if(!container)return;
  const names=Object.keys(_settingsProfiles);
  if(names.length===0){
    container.innerHTML='<div class="hint">暂无 LLM，请先「+ 新增 LLM」或到「LLM 管理」添加。</div>';
    return;
  }
  // 表格风格：表头 + 数据行，行间分隔线，与整个设置页风格统一
  const list=document.createElement('div');
  list.className='profile-overview';
  list.innerHTML=
    `<div class="po-head">`+
      `<span class="po-col po-name">LLM</span>`+
      `<span class="po-col po-provider">服务商</span>`+
      `<span class="po-col po-model">模型</span>`+
      `<span class="po-col po-action"></span>`+
    `</div>`;
  for(const name of names){
    const p=_settingsProfiles[name]||{};
    const row=document.createElement('div');
    row.className='po-row';
    row.dataset.name=name;
    row.innerHTML=
      `<span class="po-col po-name">${esc(name)}</span>`+
      `<span class="po-col po-provider">${esc(p.provider||'—')}</span>`+
      `<span class="po-col po-model">${esc(p.model||'—')}</span>`+
      `<span class="po-col po-action"><button class="mini-btn">管理</button></span>`;
    // 点击整行进入该 LLM 的管理子页
    row.addEventListener('click',()=>{
      _settingsSelectedProfile=name;
      renderProfileSelect();
      renderLlmProfile();
      switchSettingsTab('profiles',document.querySelector('.sn:nth-child(2)'));
    });
    list.appendChild(row);
  }
  container.appendChild(list);
}

function renderLlmProfile(){
  const p=_settingsProfiles[_settingsSelectedProfile]||{};
  const container=document.getElementById('llmProfileFields');
  container.innerHTML='';
  for(const group of LLM_PROFILE_GROUPS){
    const sec=document.createElement('div');
    sec.className='setting-section-title';
    sec.textContent=group.title;
    container.appendChild(sec);
    for(const [key,label] of group.fields){
      const row=document.createElement('div');
      row.className='setting-row';
      row.innerHTML=`<label>${label}</label><input id="lp-${key}" class="long" value="${esc(p[key]??'')}" ${key==='api_key'?'type="password"':''}>`;
      container.appendChild(row);
    }
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
  // 从常用模板中选择，选模板名即预填一组完整参数；也可自定义
  const templateNames=Object.keys(LLM_PROFILE_TEMPLATES);
  const template=prompt('选择模板（可选）：\n'+templateNames.join('\n'));
  let base={};
  if(template&&template in LLM_PROFILE_TEMPLATES){
    base=JSON.parse(JSON.stringify(LLM_PROFILE_TEMPLATES[template]));
  }else if(template&&template!==''){
    alert('模板名无效，使用空模板');base={provider:'openai',model:'',base_url:'',api_key:'',endpoint:'chat',max_tokens:2048,timeout:120,max_retries:3,retry_backoff:2.0,context_window:8192,concurrency:1};
  }else{
    base={provider:'openai',model:'',base_url:'',api_key:'',endpoint:'chat',max_tokens:2048,timeout:120,max_retries:3,retry_backoff:2.0,context_window:8192,concurrency:1};
  }
  const name=prompt('新 LLM 名称:');
  if(!name||name in _settingsProfiles){alert('名称为空或已存在');return;}
  _settingsProfiles[name]=base;
  _settingsSelectedProfile=name;
  renderProfileSelect();
  renderLlmProfile();
  renderRouting();
  renderProfileOverview();
}

function delLlmProfile(){
  if(!confirm(`删除 LLM "${_settingsSelectedProfile}"？`))return;
  delete _settingsProfiles[_settingsSelectedProfile];
  _settingsSelectedProfile=Object.keys(_settingsProfiles)[0]||'';
  renderProfileSelect();
  renderLlmProfile();
  renderRouting();
  renderProfileOverview();
}

async function saveSettings(){
  // 收集当前 profile 的值
  if(_settingsSelectedProfile){
    const p=_settingsProfiles[_settingsSelectedProfile];
    for(const [key] of LLM_PROFILE_FIELDS){
      const el=document.getElementById('lp-'+key);
      if(el){
        const v=el.value.trim();
        if(LLM_NUM_FIELDS.includes(key))
          p[key]=parseInt(v)||0;
        else if(key==='retry_backoff')
          p[key]=parseFloat(v)||0;
        else
          p[key]=v;
      }
    }
  }
  // 检测 embedding 模型是否变更：切换模型会改变向量空间/维度，已入库文档与向量缓存全部失效，
  // 必须重置知识库并重建向量缓存，否则检索会维度不匹配或结果失真。
  const embOld=(_settingsConfig&&_settingsConfig.embedding)||{};
  const embNewModel=document.getElementById('emb-model').value.trim();
  if(embOld.model&&embNewModel&&embOld.model!==embNewModel){
    const ok=await customConfirm(
      '⚠️ 检测到 embedding 模型变更：\n\n'+
      '当前：'+embOld.model+'\n'+
      '新值：'+embNewModel+'\n\n'+
      '切换 embedding 模型会使已入库文档的向量全部失效（向量维度/分布改变），'+
      '必须重置知识库（删除所有文档并重建向量缓存），否则检索会报错或结果失真。\n\n'+
      '是否继续保存？（保存后请手动"重置知识库"再重新上传文档）'
    );
    if(!ok) return; // 用户取消，不保存
  }
  // 构造更新
  const updates={
    embedding:{
      model:embNewModel,
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
      parse_backend:document.getElementById('pr-parse_backend').value||'auto',
      docling_device:document.getElementById('pr-docling_device').value||'auto',
      docling_batch_size:parseInt(document.getElementById('pr-docling_batch').value)||0,
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