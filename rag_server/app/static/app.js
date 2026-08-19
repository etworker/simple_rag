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
function fmtDocName(name,hash,label){
  const h=hash?hash.slice(-8).toUpperCase():'';
  const base=name||'';
  return base+(label?` [${label}]`:'')+(h?` [${h}]`:'');
}
function fmtDocNameHtml(name,hash,label){return esc(fmtDocName(name,hash,label));}
function compareDocName(group){
  if(!group)return '';
  return fmtDocName(group.doc_filename||'',group.file_hash||'',group.label||'');
}
function tableChangeSummary(change){
  if(!change?.table_name&&!change?.cell_changes?.length)return '';
  const type=change.type==='added'?'新增':change.type==='removed'?'删除':'修改';
  const table=change.table_name||String(change.section||'').replace(/^表格[:：]?\s*/, '')||'未命名表格';
  const oldTable=_changeLocationForSide(change,'old');
  const newTable=_changeLocationForSide(change,'new');
  const tableLabel=change.type==='modified'&&oldTable&&newTable
    ? `${oldTable} → ${newTable}`
    : change.type==='removed'&&oldTable ? oldTable
    : change.type==='added'&&newTable ? newTable
    : table;
  const row=change.row_key?`；行标识：${change.row_key}`:'';
  const rowIndex=change.row_index?`；数据第 ${change.row_index} 行`:'';
  const cells=(change.cell_changes||[]).map(cell=>{
    const oldValue=String(cell.old_value||'');
    const newValue=String(cell.new_value||'');
    if(change.type==='added')return `${cell.column}=${newValue}`;
    if(change.type==='removed')return `${cell.column}=${oldValue}`;
    return `${cell.column}：${oldValue||'（空）'} → ${newValue||'（空）'}`;
  });
  const detail=cells.length?`；列变化：${cells.join('；')}`:'';
  if((change.new_text||'').startsWith('新增列:')||(change.old_text||'').startsWith('删除列:'))return `[表格${type}列] ${tableLabel}${change.new_text||change.old_text?`；${change.new_text||change.old_text}`:''}`;
  return `[表格${type}行] ${tableLabel}${rowIndex}${row}${detail}`;
}
function changeSummaryParts(change){
  if(!change?.table_name&&!change?.cell_changes?.length)return null;
  const type=change.type==='added'?'新增':change.type==='removed'?'删除':'修改';
  const table=change.table_name||String(change.section||'').replace(/^表格[:：]?\s*/, '')||'未命名表格';
  const oldTable=_changeLocationForSide(change,'old');
  const newTable=_changeLocationForSide(change,'new');
  const tableLabel=change.type==='modified'&&oldTable&&newTable
    ? `${oldTable} → ${newTable}`
    : change.type==='removed'&&oldTable ? oldTable
    : change.type==='added'&&newTable ? newTable
    : table;
  const parts=[{label:'类型',value:`表格${type}${(change.new_text||'').startsWith('新增列:')||(change.old_text||'').startsWith('删除列:')?'列':'行'}`},{label:'定位',value:tableLabel}];
  if(change.row_index)parts.push({label:'数据行',value:`第 ${change.row_index} 行`});
  if(change.row_key)parts.push({label:'行标识',value:String(change.row_key)});
  if((change.new_text||'').startsWith('新增列:')||(change.old_text||'').startsWith('删除列:')){
    parts.push({label:'列操作',value:String(change.new_text||change.old_text)});
  }else{
    (change.cell_changes||[]).forEach(cell=>{
      const oldValue=String(cell.old_value||'');
      const newValue=String(cell.new_value||'');
      const value=change.type==='added'
        ? `${cell.column||''} = ${newValue}`
        : change.type==='removed'
        ? `${cell.column||''} = ${oldValue}`
        : `${cell.column||''}：${oldValue||'（空）'} → ${newValue||'（空）'}`;
      parts.push({label:'列变化',value});
    });
  }
  return parts;
}
function changeSummaryHtml(change){
  const parts=changeSummaryParts(change);
  if(!parts)return `<div class="vc-summary-line"><span class="vc-summary-label">摘要：</span><span class="vc-summary-value">${esc(changeSummary(change))}</span></div>`;
  return parts.map(part=>`<div class="vc-summary-line"><span class="vc-summary-label">${esc(part.label)}：</span><span class="vc-summary-value">${esc(part.value)}</span></div>`).join('');
}
function changeSummary(change){
  const tableSummary=tableChangeSummary(change);
  if(tableSummary)return tableSummary;
  if(change?.summary)return change.summary;
  const oldText=String(change?.old_text||'').replace(/\s+/g,' ').trim();
  const newText=String(change?.new_text||'').replace(/\s+/g,' ').trim();
  if(change?.type==='added')return `[新增] ${newText.slice(0,160)}`;
  if(change?.type==='removed')return `[删除] ${oldText.slice(0,160)}`;
  if(oldText&&newText)return `[修改] ${oldText.slice(0,80)} → ${newText.slice(0,80)}`;
  return '[修改] 内容发生变化';
}

function _hasPageLocation(value){
  return /第\s*\d+(?:\s*[-–—至到]\s*\d+)?\s*页/.test(String(value||''));
}
function _changeLocationForSide(change,side){
  const direct=side==='old'?change?.old_location:change?.location;
  if(_hasPageLocation(direct))return String(direct);
  // table_name is retained as a legacy fallback for the old table side. The new
  // side must be supplied by the backend's direction-aware location field.
  if(side==='old'&&_hasPageLocation(change?.table_name))return String(change.table_name);
  return String(direct||'');
}
function _changePageForSide(change,side){
  const loc=_changeLocationForSide(change,side);
  const match=String(loc||'').match(/第\s*(\d+)\s*页/);
  return match?parseInt(match[1],10):1;
}
function tableChangeDetailsHtml(change){
  if(!change?.table_name&&!change?.cell_changes?.length)return '';
  const table=change.table_name||String(change.section||'').replace(/^表格[:：]?\s*/, '')||'未命名表格';
  const oldTableLocation=_changeLocationForSide(change,'old')||table;
  const newTableLocation=_hasPageLocation(change?.location)?String(change.location):'';
  const cells=change.cell_changes||[];
  let html='<div class="vc-table-details">';
  html+=`<div class="vc-table-context"><b>旧版表格：</b>${esc(oldTableLocation||table)}`;
  if(newTableLocation)html+=`　<b>新版表格：</b>${esc(newTableLocation)}`;
  if(change.row_index)html+=`　<b>数据行：</b>第 ${change.row_index} 行`;
  if(change.row_key)html+=`　<b>行标识：</b>${esc(change.row_key)}`;
  html+='</div>';
  if(cells.length){
    html+='<table class="vc-cell-change-table"><thead><tr><th>列</th><th>旧版值</th><th>新版值</th></tr></thead><tbody>';
    cells.forEach(cell=>{
      html+=`<tr><td>${esc(cell.column||'')}</td><td>${esc(cell.old_value??'')}</td><td>${esc(cell.new_value??'')}</td></tr>`;
    });
    html+='</tbody></table>';
  }
  html+='</div>';
  return html;
}
function compareGroupSummary(group,bTag){
  if(!group)return '等待比较';
  const isVersion=group.compare_type==='version_diff';
  const entries=isVersion
    ? (group.version_changes||[]).map(changeSummary)
    : (group.inconsistencies||[]).map(item=>normalizeConflictText(item.point||'内容差异',bTag));
  if(!isVersion){
    (group.suspects||[]).forEach(item=>entries.push(`疑似待复核：${normalizeConflictText(item.point||'内容差异',bTag)}`));
  }
  if(!entries.length){
    if(group.status==='running')return '本组比较中，结果尚未产生';
    if(group.status==='error')return group.error||'比较失败';
    return isVersion?'未发现实质性差异':'未发现矛盾';
  }
  const shown=entries.slice(0,2).join('；');
  const rest=entries.length>2?`；另有 ${entries.length-2} 项`:'';
  return shown+rest;
}
function compareGroupSummaryHtml(group,bTag){
  if(!group)return '<div class="vc-summary-line"><span class="vc-summary-value">等待比较</span></div>';
  if(group.compare_type==='version_diff'&&(group.version_changes||[]).length){
    const changes=group.version_changes||[];
    let html=changes.slice(0,2).map(changeSummaryHtml).join('');
    if(changes.length>2)html+=`<div class="vc-summary-line"><span class="vc-summary-value">另有 ${changes.length-2} 项差异</span></div>`;
    return html;
  }
  const summary=compareGroupSummary(group,bTag);
  return `<div class="vc-summary-line"><span class="vc-summary-label">摘要：</span><span class="vc-summary-value">${esc(summary)}</span></div>`;
}
function normalizeConflictText(text,bTag){
  let value=String(text||'');
  for(const [from,to] of [['文档 A','N'],['文档A','N'],['文档 a','N'],['文档a','N'],['A文档','N'],['文档 B',bTag],['文档B',bTag],['文档 b',bTag],['文档b',bTag],['B文档',bTag]]) value=value.split(from).join(to);
  return value;
}
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
let docMap={}, fileHashToName={}, docRefToId={}, docLabelMap={}, reviewResult=null, reviewTaskId=null, newDocFile=null, newDocHash='', newDocLabel='', activeFileName=null;
let currentDoc=null, currentPage=1, totalPages=1;
let reviewLayout='side-by-side';
let reviewPanelWidthPercent=32;
let reviewStackHeight=420;
let reviewPreviewVisible=true;
const REVIEW_PANEL_WIDTH_KEY='simple-rag.review-panel-width-percent';
const REVIEW_STACK_HEIGHT_KEY='simple-rag.review-stack-height';
const REVIEW_PREVIEW_VISIBLE_KEY='simple-rag.review-preview-visible';
let reviewPreviewViewToken=0;
let reviewLayoutReady=Promise.resolve();
const reviewPageInfoCache=new Map();
const REVIEW_PDF_CACHE_LIMIT=4;

// 预审核专用 N/B 预览状态。普通文档预览继续使用 currentDoc/kbPdfDoc，两个场景互不覆盖。
const reviewPanes={
    n:{side:'n',ref:null,refKey:'',kind:'',url:'',pdfDoc:null,loadingTask:null,page:1,totalPages:0,observer:null,loadToken:0,textLoaded:false},
    b:{side:'b',ref:null,refKey:'',kind:'',url:'',pdfDoc:null,loadingTask:null,page:1,totalPages:0,observer:null,loadToken:0,textLoaded:false},
};
const reviewPdfCache={n:new Map(),b:new Map()};
let reviewFocusedSide='n';

function reviewPaneRefKey(ref){
    if(!ref)return '';
    return [ref.kind||'',ref.taskId||'',ref.docId||'',ref.filename||''].join('|');
}
function reviewPaneElement(side,name){
    const suffix=side==='n'?'N':'B';
    return document.getElementById(`reviewPane${name||'Body'}${suffix}`);
}
function reviewPaneBody(side){return reviewPaneElement(side,'Body');}
function reviewPaneSetMessage(side,message,color='var(--text3)'){
    const body=reviewPaneBody(side);
    if(body)body.innerHTML=`<div class="review-pane-placeholder" style="color:${color};">${esc(message)}</div>`;
}
function disposeReviewPdfEntry(side,key,entry){
    const cache=reviewPdfCache[side];
    if(cache?.get(key)===entry)cache.delete(key);
    if(entry.loadingTask?.destroy)Promise.resolve(entry.loadingTask.destroy()).catch(()=>{});
    if(entry.pdfDoc?.destroy)Promise.resolve(entry.pdfDoc.destroy()).catch(()=>{});
    entry.loadingTask=null;entry.pdfDoc=null;
}
function evictReviewPdfCache(side,preserveKey=''){
    const cache=reviewPdfCache[side];
    if(!cache)return;
    const candidates=[...cache.entries()].filter(([key])=>key!==preserveKey).sort((a,b)=>(a[1].lastUsed||0)-(b[1].lastUsed||0));
    while(cache.size>REVIEW_PDF_CACHE_LIMIT&&candidates.length){const [key,entry]=candidates.shift();disposeReviewPdfEntry(side,key,entry);}
}
function getReviewPdfEntry(side,key,url){
    const cache=reviewPdfCache[side];
    let entry=cache.get(key);
    if(entry&&entry.url===url){entry.lastUsed=Date.now();return entry;}
    if(entry)disposeReviewPdfEntry(side,key,entry);
    const loadingTask=pdfjsLib.getDocument(url);
    entry={key,url,loadingTask,promise:null,pdfDoc:null,lastUsed:Date.now()};
    entry.promise=loadingTask.promise.then(pdfDoc=>{
        entry.pdfDoc=pdfDoc;entry.loadingTask=null;entry.lastUsed=Date.now();return pdfDoc;
    }).catch(error=>{
        if(cache.get(key)===entry)cache.delete(key);
        entry.loadingTask=null;entry.pdfDoc=null;
        throw error;
    });
    cache.set(key,entry);
    evictReviewPdfCache(side,key);
    return entry;
}
function disposeReviewPaneResources(state){
    // 切换条目或暂时关闭预览时不销毁 PDF；文档由 reviewPdfCache 统一复用和淘汰。
    if(!state)return;
    state.loadingTask=null;state.pdfDoc=null;
}
function reviewPaneUpdateNav(side){
    const state=reviewPanes[side];
    const input=reviewPaneElement(side,'Page');
    const total=reviewPaneElement(side,'Total');
    const pager=reviewPaneElement(side,'Pager');
    if(input)input.value=state.page||1;
    if(total)total.textContent=state.totalPages||'?';
    if(pager)pager.style.display=state.kind==='text'?'none':'';
}
function reviewPaneReset(side,message='选择一条审核结果后加载对比文档'){
    const state=reviewPanes[side];
    if(state.observer)state.observer.disconnect();
    disposeReviewPaneResources(state);
    state.ref=null;state.refKey='';state.kind='';state.url='';state.page=1;state.totalPages=0;state.textLoaded=false;state.loadToken++;
    const title=reviewPaneElement(side,'Title');
    if(title)title.textContent=side==='n'?'N 新文档':'B 对比文档';
    reviewPaneUpdateNav(side);
    reviewPaneSetMessage(side,message);
}
function resolveReviewStoredDocId(ref){
    const raw=String(ref||'');
    if(!raw)return '';
    if(docRefToId[raw])return docRefToId[raw];
    if(raw.includes('#'))return raw;
    if(fileHashToName[raw]&&docRefToId[raw])return docRefToId[raw];
    const base=splitFileHash(raw).name;
    if(docRefToId[base])return docRefToId[base];
    const match=Object.keys(docRefToId).find(key=>key===base||key.split('#')[0]===base);
    return match?docRefToId[match]:raw;
}
function reviewComparisonContext(kind){
    const panel=document.getElementById('versionComparePanel');
    if(kind==='version'){
        const gi=Number(panel?.dataset.groupIdx);
        const ci=Number(panel?.dataset.vcIdx);
        const group=reviewResult?.compare_groups?.[gi];
        return {kind,group,change:group?.version_changes?.[ci],bRef:group?.doc_id||''};
    }
    const gi=Number(window.__conflictGroupIdx),ci=Number(window.__conflictIdx),si=Number(window.__suspectIdx);
    const group=(Number.isInteger(gi)&&gi>=0)?reviewResult?.compare_groups?.[gi]:null;
    let item;
    if(group){
        item=Number.isInteger(si)&&si>=0?group.suspects?.[si]:group.inconsistencies?.[ci];
    }else{
        const selected=document.querySelector('.conflict-item.selected');
        const selectedIndex=selected?.dataset?.ci!=null?Number(selected.dataset.ci):ci;
        item=reviewResult?.inconsistencies?.[Number.isInteger(selectedIndex)&&selectedIndex>=0?selectedIndex:0];
    }
    return {kind:'conflict',group,item,bRef:item?.doc_b_id||item?.doc_b?.file||item?.doc_b_file||''};
}
function resolveReviewPreviewRef(side,context={}){
    if(side==='n'){
        if(!newDocFile||!reviewTaskId)return null;
        return {kind:'pending',taskId:reviewTaskId,filename:newDocFile,hash:newDocHash,label:newDocLabel};
    }
    const docId=resolveReviewStoredDocId(context.bRef||context.group?.doc_id||'');
    if(!docId)return null;
    const filename=String(docId).split('#')[0];
    return {kind:'stored',docId,filename,label:docLabelMap[docId]||'',hash:docId.includes('#')?docId.split('#').pop():''};
}
function reviewRefPage(side,context){
    if(context.change)return _changePageForSide(context.change,side==='n'?'new':'old');
    const item=context.item||{};
    const loc=side==='n'
        ? (item.doc_a?.location||item.doc_a_location||'')
        : (item.doc_b?.location||item.doc_b_location||'');
    return extractPage(loc)||1;
}
function reviewPaneTitle(side,ref,context){
    if(side==='n')return 'N '+fmtDocName(ref.filename,ref.hash,ref.label);
    const tag=docMap[ref.docId]||'B';
    const name=compareDocName(context.group)||fmtDocName(ref.filename,ref.hash,ref.label);
    return `${tag} ${name}`;
}
function reviewPaneUrl(ref){
    if(ref.kind==='pending')return `/api/documents/review/pdf?task_id=${encodeURIComponent(ref.taskId)}`;
    return `/api/documents/pdf?doc_id=${encodeURIComponent(ref.docId)}`;
}

async function loadReviewPane(side,ref,page=1,context={},viewToken=null){
    if(!isReviewPreviewViewCurrent(viewToken))return false;
    const state=reviewPanes[side];
    const paneToken=++state.loadToken;
    if(!ref){reviewPaneReset(side);return false;}
    const key=reviewPaneRefKey(ref);
    state.ref=ref;state.refKey=key;state.kind='';state.url='';state.textLoaded=false;
    const title=reviewPaneElement(side,'Title');
    if(title)title.textContent=reviewPaneTitle(side,ref,context);
    if(state.observer)state.observer.disconnect();
    state.observer=null;
    disposeReviewPaneResources(state);
    const body=reviewPaneBody(side);
    if(!body)return false;
    const ext=String(ref.filename||'').split('.').pop().toLowerCase();
    if(ext!=='pdf'){
        state.kind='text';state.totalPages=0;reviewPaneUpdateNav(side);
        body.innerHTML='<div class="review-pane-placeholder">加载文本内容...</div>';
        try{
            const query=ref.kind==='pending'
                ? `file=${encodeURIComponent(ref.filename)}&task_id=${encodeURIComponent(ref.taskId)}`
                : `name=${encodeURIComponent(ref.docId)}`;
            const response=await fetch(ref.kind==='pending'?`/api/documents/review/paragraphs?${query}`:`/api/documents/paragraphs?${query}`);
            const data=await response.json();
            if(paneToken!==state.loadToken||!isReviewPreviewViewCurrent(viewToken))return false;
            const paragraphs=data.paragraphs||[];
            if(!paragraphs.length){reviewPaneSetMessage(side,'该文档无段落内容');return false;}
            let html='<div style="width:100%;max-width:800px;padding:8px;">';
            paragraphs.forEach((p,i)=>{html+=`<div class="text-para" style="margin-bottom:10px;padding:8px 10px;background:var(--bg2);border-radius:5px;font-size:12px;line-height:1.6;"><span style="color:var(--primary);font-size:10px;margin-right:5px;">¶${i+1}</span>${esc(p.text||'')}${p.location?`<span style="color:var(--text3);font-size:10px;margin-left:6px;">${esc(p.location)}</span>`:''}</div>`;});
            html+='</div>';body.innerHTML=html;state.textLoaded=true;
        }catch(err){
            if(paneToken===state.loadToken&&isReviewPreviewViewCurrent(viewToken))reviewPaneSetMessage(side,`预览加载失败: ${err.message||err}`,'var(--danger)');
            return false;
        }
        return true;
    }
    state.kind='pdf';state.url=reviewPaneUrl(ref);reviewPaneUpdateNav(side);
    const entry=getReviewPdfEntry(side,key,state.url);
    body.innerHTML=entry.pdfDoc
        ? ''
        : '<div class="review-pane-placeholder">PDF 加载中...</div>';
    try{
        const pdfDoc=await entry.promise;
        if(paneToken!==state.loadToken||!isReviewPreviewViewCurrent(viewToken))return false;
        state.pdfDoc=pdfDoc;state.totalPages=pdfDoc.numPages;state.page=Math.max(1,Math.min(Number(page)||1,state.totalPages||1));
        buildPageSlots(body.id,state.totalPages);
        setupReviewFullRender(side,body,pdfDoc,paneToken,viewToken);
        reviewPaneUpdateNav(side);reviewScrollToPage(side,state.page);
        return true;
    }catch(err){
        if(paneToken!==state.loadToken||!isReviewPreviewViewCurrent(viewToken))return false;
        state.pdfDoc=null;state.totalPages=0;reviewPaneUpdateNav(side);
        console.warn('[review preview] PDF load failed', {side,url:state.url,ref,error:err});
        reviewPaneSetMessage(side,`PDF 加载失败: ${err.message||err}`,'var(--danger)');
        return false;
    }
}
function setupReviewFullRender(side,container,pdfDoc,paneToken,viewToken=null){
    const state=reviewPanes[side];
    if(state.observer)state.observer.disconnect();
    state.observer=null;
    const slots=[...container.querySelectorAll('.page-slot')];
    let nextIndex=0;
    const renderSlot=async slot=>{
        if(paneToken!==state.loadToken||!isReviewPreviewViewCurrent(viewToken))return;
        const pageNum=parseInt(slot.dataset.page,10);
        slot.dataset.rendered='loading';slot.onclick=null;
        try{
            await renderPageInSlot(pdfDoc,pageNum,slot);
            slot.dataset.rendered='1';
        }catch(err){
            if(paneToken!==state.loadToken||!isReviewPreviewViewCurrent(viewToken))return;
            slot.dataset.rendered='error';
            slot.innerHTML=`<button type="button" style="border:1px solid var(--border);background:var(--card);border-radius:4px;padding:8px 14px;color:var(--danger);cursor:pointer;">第 ${pageNum} 页加载失败，点击重试</button>`;
            slot.onclick=()=>{
                if(paneToken!==state.loadToken||!isReviewPreviewViewCurrent(viewToken))return;
                renderSlot(slot);
            };
            console.warn('[review preview] page render failed',{side,page:pageNum,error:err});
        }
    };
    const worker=async()=>{
        while(nextIndex<slots.length){
            const slot=slots[nextIndex++];
            await renderSlot(slot);
        }
    };
    // 完整渲染所有页面，但限制并发，避免两个 PDF 同时打开时瞬时占满内存。
    const workerCount=Math.min(2,slots.length);
    Promise.all(Array.from({length:workerCount},worker)).catch(error=>console.warn('[review preview] full render failed',{side,error}));
    container.onscroll=()=>{
        if(paneToken!==state.loadToken||!isReviewPreviewViewCurrent(viewToken))return;
        const visibleSlots=container.querySelectorAll('.page-slot');let visible=state.page||1;
        for(const slot of visibleSlots){if(slot.offsetTop<=container.scrollTop+100)visible=parseInt(slot.dataset.page,10);else break;}
        state.page=visible;reviewPaneUpdateNav(side);
    };
}
function reviewScrollToPage(side,page){
    const state=reviewPanes[side],body=reviewPaneBody(side);if(!body)return;
    state.page=Math.max(1,Math.min(Number(page)||1,state.totalPages||1));reviewPaneUpdateNav(side);
    const slot=body.querySelector(`.page-slot[data-page="${state.page}"]`);if(slot)slot.scrollIntoView({block:'start'});
}
function reviewGoToPage(side){const input=reviewPaneElement(side,'Page');reviewScrollToPage(side,parseInt(input?.value)||1);}
function reviewPrevPage(side){reviewScrollToPage(side,reviewPanes[side].page-1);}
function reviewNextPage(side){reviewScrollToPage(side,reviewPanes[side].page+1);}
function setReviewPreviewFocus(side){
    reviewFocusedSide=side==='b'?'b':'n';
    const right=document.getElementById('kbRight');if(right)right.dataset.reviewFocus=reviewFocusedSide;
    document.querySelectorAll('.review-focus-btn').forEach(btn=>btn.classList.toggle('active',btn.dataset.focusSide===reviewFocusedSide));
}
function applyReviewPreviewPaneMode(){
    const right=document.getElementById('kbRight');
    const groups=reviewResult?.compare_groups||[];
    const hasComparison=groups.length>0&&!reviewResult?.kb_empty;
    const mode=hasComparison?'dual':'single';
    if(right)right.dataset.reviewPaneMode=mode;
    if(!hasComparison)setReviewPreviewFocus('n');
    const hint=document.querySelector('.review-preview-hint');
    if(hint)hint.textContent=hasComparison?'N 新文档与 B 对比文档独立加载、翻页和滚动':'当前为首篇文档，无 B 对比文档';
    return hasComparison;
}
function initialReviewPreviewContext(){
    const groups=reviewResult?.compare_groups||[];
    const group=groups.find(g=>(g.version_changes||[]).length||(g.inconsistencies||[]).length||(g.suspects||[]).length||(g.minor_changes||[]).length);
    if(!group)return null;
    if(group.compare_type==='version_diff'&&(group.version_changes||[]).length){
        return {kind:'version',group,change:group.version_changes[0],bRef:group.doc_id||''};
    }
    const item=(group.inconsistencies||[])[0]||(group.suspects||[])[0];
    return {kind:'conflict',group,item,bRef:item?.doc_b_id||item?.doc_b?.file||item?.doc_b_file||group.doc_id||''};
}
function initializeReviewPreviews(){
    if(reviewPanes.n.ref&&reviewPanes.n.ref.taskId!==reviewTaskId)reviewPaneReset('n','等待当前 N 文档加载');
    if(reviewPanes.b.ref&&reviewPanes.b.refKey&&!reviewResult)reviewPaneReset('b');
    const viewToken=reviewPreviewViewToken;
    if(newDocFile&&reviewTaskId){
        const context=initialReviewPreviewContext();
        applyReviewPreviewPaneMode();
        if(!context)reviewPaneReset('b','无对比文档');
        const refs=context
            ? {n:resolveReviewPreviewRef('n',context),b:resolveReviewPreviewRef('b',context)}
            : {n:resolveReviewPreviewRef('n'),b:null};
        const pages=context
            ? {n:reviewRefPage('n',context)||1,b:reviewRefPage('b',context)||1}
            : {n:getDocPage(newDocFile),b:1};
        const loads=Object.entries(refs).map(([side,ref])=>{
            if(!ref){
                if(side==='b'&&context)reviewPaneSetMessage('b','B 对比文档信息缺失','var(--danger)');
                return Promise.resolve(false);
            }
            return loadReviewPane(side,ref,pages[side],context||{},viewToken).catch(err=>{
                console.warn('[review preview] initial pane load failed',{side,ref,error:err});
                return false;
            });
        });
        Promise.all(loads).catch(error=>console.warn('[review preview] initial pair load failed',error));
    }else reviewPaneReset('n','暂无待审核 N 文档');
    setReviewPreviewFocus(reviewFocusedSide);
}

function normalizeReviewLayout(layout){
    return ['side-by-side','stacked','single'].includes(layout)?layout:'side-by-side';
}

function applyReviewLayout(layout){
    const normalized=normalizeReviewLayout(layout);
    reviewLayout=normalized;
    const kbRight=document.getElementById('kbRight');
    if(kbRight)kbRight.dataset.reviewLayout=normalized;
    applyReviewPanelWidth(reviewPanelWidthPercent);
    applyReviewStackHeight(reviewStackHeight);
    setReviewPreviewVisible(reviewPreviewVisible,false);
    return normalized;
}

function clampReviewPanelWidthPercent(percent){
    const kbRight=document.getElementById('kbRight');
    const total=kbRight?.clientWidth||1000;
    const minLeft=240;
    const minPreview=420;
    const maxLeft=Math.max(minLeft,total-minPreview);
    const requested=Math.max(minLeft,Math.min(maxLeft,total*(Number(percent)||32)/100));
    return Math.max(20,Math.min(55,requested/total*100));
}
function applyReviewPanelWidth(percent){
    reviewPanelWidthPercent=clampReviewPanelWidthPercent(percent);
    const kbRight=document.getElementById('kbRight');
    if(kbRight)kbRight.style.setProperty('--review-panel-width',`${reviewPanelWidthPercent}%`);
    return reviewPanelWidthPercent;
}
function loadReviewPanelWidth(){
    let saved=32;
    try{saved=parseFloat(localStorage.getItem(REVIEW_PANEL_WIDTH_KEY))||32;}catch(e){}
    applyReviewPanelWidth(saved);
}
function saveReviewPanelWidth(){
    try{localStorage.setItem(REVIEW_PANEL_WIDTH_KEY,String(reviewPanelWidthPercent));}catch(e){}
}
function reviewStackBounds(){
    const kbRight=document.getElementById('kbRight');
    const total=kbRight?.clientHeight||800;
    const header=kbRight?.querySelector('.kb-tabs')?.offsetHeight||28;
    const minReview=260;
    const minPreview=240;
    return {min:minPreview,max:Math.max(minPreview,total-header-minReview)};
}
function clampReviewStackHeight(height){
    const {min,max}=reviewStackBounds();
    const total=document.getElementById('kbRight')?.clientHeight||800;
    const fallback=Math.round(Math.max(min,total*.48));
    return Math.round(Math.max(min,Math.min(max,Number(height)||fallback)));
}
function applyReviewStackHeight(height){
    reviewStackHeight=clampReviewStackHeight(height);
    const kbRight=document.getElementById('kbRight');
    if(kbRight)kbRight.style.setProperty('--review-stack-height',`${reviewStackHeight}px`);
    return reviewStackHeight;
}
function loadReviewStackHeight(){
    let saved=0;
    try{saved=parseFloat(localStorage.getItem(REVIEW_STACK_HEIGHT_KEY))||0;}catch(e){}
    applyReviewStackHeight(saved);
}
function saveReviewStackHeight(){
    try{localStorage.setItem(REVIEW_STACK_HEIGHT_KEY,String(reviewStackHeight));}catch(e){}
}
function setReviewPreviewVisible(visible,persist=true){
    reviewPreviewVisible=Boolean(visible);
    const kbRight=document.getElementById('kbRight');
    if(kbRight)kbRight.dataset.reviewPreview=reviewPreviewVisible?'open':'closed';
    const closeBtn=document.getElementById('reviewPreviewCloseBtn');
    const restoreBtn=document.getElementById('reviewPreviewRestoreBtn');
    if(closeBtn)closeBtn.style.display=reviewPreviewVisible?'':'none';
    if(restoreBtn)restoreBtn.style.display=reviewPreviewVisible?'none':'inline-block';
    if(persist){try{localStorage.setItem(REVIEW_PREVIEW_VISIBLE_KEY,reviewPreviewVisible?'1':'0');}catch(e){}}
}
function loadReviewPreviewVisibility(){
    try{reviewPreviewVisible=localStorage.getItem(REVIEW_PREVIEW_VISIBLE_KEY)!=='0';}catch(e){reviewPreviewVisible=true;}
    setReviewPreviewVisible(reviewPreviewVisible,false);
}
function initReviewResize(){
    const handle=document.getElementById('reviewResizeHandle');
    const stackHandle=document.getElementById('reviewStackResizeHandle');
    const kbRight=document.getElementById('kbRight');
    if(!kbRight)return;
    if(handle&&!handle.dataset.bound){
        handle.dataset.bound='1';
        let dragging=false;
        const finish=()=>{
            if(!dragging)return;
            dragging=false;handle.classList.remove('dragging');
            document.body.style.cursor='';document.body.style.userSelect='';
            saveReviewPanelWidth();
        };
        handle.addEventListener('pointerdown',event=>{
            if(reviewLayout!=='side-by-side'||reviewPreviewVisible===false)return;
            dragging=true;handle.classList.add('dragging');
            document.body.style.cursor='col-resize';document.body.style.userSelect='none';
            handle.setPointerCapture?.(event.pointerId);event.preventDefault();
        });
        handle.addEventListener('pointermove',event=>{
            if(!dragging)return;
            const rect=kbRight.getBoundingClientRect();
            const minLeft=240,minPreview=420;
            const maxLeft=Math.max(minLeft,rect.width-minPreview);
            const px=Math.max(minLeft,Math.min(maxLeft,event.clientX-rect.left));
            applyReviewPanelWidth(px/rect.width*100);
        });
        handle.addEventListener('pointerup',finish);
        handle.addEventListener('pointercancel',finish);
        handle.addEventListener('lostpointercapture',finish);
    }
    if(stackHandle&&!stackHandle.dataset.bound){
        stackHandle.dataset.bound='1';
        let dragging=false;
        const finish=()=>{
            if(!dragging)return;
            dragging=false;stackHandle.classList.remove('dragging');
            document.body.style.cursor='';document.body.style.userSelect='';
            saveReviewStackHeight();
        };
        stackHandle.addEventListener('pointerdown',event=>{
            if(reviewLayout!=='stacked'||reviewPreviewVisible===false)return;
            dragging=true;stackHandle.classList.add('dragging');
            document.body.style.cursor='row-resize';document.body.style.userSelect='none';
            stackHandle.setPointerCapture?.(event.pointerId);event.preventDefault();
        });
        stackHandle.addEventListener('pointermove',event=>{
            if(!dragging)return;
            const rect=kbRight.getBoundingClientRect();
            applyReviewStackHeight(rect.bottom-event.clientY);
        });
        stackHandle.addEventListener('pointerup',finish);
        stackHandle.addEventListener('pointercancel',finish);
        stackHandle.addEventListener('lostpointercapture',finish);
    }
    window.addEventListener('resize',()=>{
        applyReviewPanelWidth(reviewPanelWidthPercent);
        applyReviewStackHeight(reviewStackHeight);
    });
}

function nextReviewPreviewViewToken(){return ++reviewPreviewViewToken;}
function isReviewPreviewViewCurrent(token){return token==null||token===reviewPreviewViewToken;}
async function loadReviewLayoutFromServer(){
    try{
        const response=await fetch('/api/config');
        if(!response.ok)throw new Error(`config request failed: ${response.status}`);
        const config=await response.json();
        applyReviewLayout(config.review_layout);
    }catch(error){
        console.warn('加载预审核布局配置失败，使用默认布局:',error);
        applyReviewLayout(reviewLayout);
    }
}

const openCompareGroups=new Set();
let reviewEventSource=null;
let reviewReconcileTimer=null;
let reviewTerminalTaskId=null;
let reviewTabSwitchToken=0;

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
    docRefToId={};
    docLabelMap={};
    const el=document.getElementById('docList');
    let pendingHtml='', activeHtml='', inactiveHtml='';

    // 待审核文档（放上面）
    if(newDocFile){
        docMap[newDocFile]='N';
        if(newDocHash)fileHashToName[newDocHash]=newDocFile;
        const pendingName=fmtDocNameHtml(newDocFile,newDocHash,'');
        const pendingLabel=newDocLabel||'添加描述';
        const pendingLabelClass=newDocLabel?'doc-label-tag':'doc-label-tag doc-label-placeholder';
        const pendingTask=reviewTaskId||'';
        const cls=(currentDoc===newDocFile)?'doc-item pending active':'doc-item pending';
        const pendingStatus=reviewTaskId?'预审核进行中...':'等待审核';
        pendingHtml=`<div class="doc-list-title" style="color:var(--warn);">待审核</div><div class="${cls}" data-task-id="${escA(pendingTask)}" onclick="selectPendingDoc('${escA(newDocFile)}','${escA(pendingTask)}')" title="${escA(fmtDocName(newDocFile,newDocHash,newDocLabel))}"><span class="id" style="color:var(--warn);">N</span><div class="info"><div class="name">${pendingName} <span class="${pendingLabelClass}" onclick="event.stopPropagation();editPendingDocLabel('${escA(pendingTask)}')" title="点击修改补充描述" style="cursor:pointer;">${esc(pendingLabel)} ✎</span></div><div class="stats">${pendingStatus}</div></div></div>`;
    }

    const docs=(data.documents||[]);
    const activeDocs=docs.filter(d=>(d.status||'active')==='active');
    const inactiveDocs=docs.filter(d=>d.status==='inactive');
    const nameOwners={};
    const registerDoc=(d,id)=>{
        const docId=d.doc_id||d.filename;
        docMap[docId]=id;
        docRefToId[docId]=docId;
        if(d.file_hash){
            fileHashToName[d.file_hash]=d.filename;
            docRefToId[d.file_hash]=docId;
            docRefToId[d.file_hash+'.pdf']=docId;
        }
        docLabelMap[docId]=d.label||d.version||'';
        if(nameOwners[d.filename]){
            delete docMap[d.filename];
            delete docRefToId[d.filename];
        }else{
            nameOwners[d.filename]=docId;
            docMap[d.filename]=id;
            docRefToId[d.filename]=docId;
            docLabelMap[d.filename]=d.label||d.version||'';
        }
    };
    const renderDoc=(d,id,inactive)=>{
        registerDoc(d,id);
        const docId=d.doc_id||d.filename;
        const hashShort=d.file_hash?d.file_hash.slice(-8).toUpperCase():'';
        const cls=(currentDoc===docId)?`doc-item${inactive?' inactive':''} active`:`doc-item${inactive?' inactive':''}`;
        const pg=d.page_count||kbTotalPagesCache[docId]||'';
        const stats=[];
        if(pg)stats.push(pg+'页');
        if(d.char_count)stats.push(d.char_count+'字');
        if(d.paragraph_count)stats.push(d.paragraph_count+'段');
        if(d.table_count)stats.push(d.table_count+'表');
        if(d.added_at){
            const dt=new Date(d.added_at);
            const pad=n=>String(n).padStart(2,'0');
            stats.push((dt.getMonth()+1)+'/'+pad(dt.getDate())+' '+pad(dt.getHours())+':'+pad(dt.getMinutes()));
        }
        const label=d.label||d.version||'添加描述';
        const labelClass=d.label||d.version?'doc-label-tag':'doc-label-tag doc-label-placeholder';
        const verTag=`<span class="${labelClass}" onclick="event.stopPropagation();editDocLabel('${escA(docId)}')" title="点击修改补充描述" style="cursor:pointer;">${esc(label)} ✎</span>`;
        const displayName=hashShort?`${esc(d.filename)}<span style="color:var(--text3);font-size:10px;"> [${hashShort}]</span>${verTag}`:esc(d.filename)+verTag;
        const statusTag=inactive?'<span class="doc-status-tag">历史版本 · 不参与问答</span>':'<span class="doc-status-tag primary">当前版本</span>';
        const action=inactive
            ?`<button class="primary-doc-btn" onclick="event.stopPropagation();setPrimaryDoc('${escA(docId)}')">设为当前</button>`
            :`<span class="del-btn" onclick="event.stopPropagation();removeDoc('${escA(docId)}')">🗑️</span>`;
        return `<div class="${cls}" data-doc-id="${escA(docId)}" data-filename="${escA(d.filename)}" data-hash="${escA(d.file_hash||'')}" onclick="selectDoc('${escA(docId)}')" title="${escA(d.filename)} [${hashShort}]"><span class="id">${id}</span><div class="info"><div class="name">${displayName} ${statusTag}</div>${stats.length?'<div class="stats">'+stats.join(' · ')+'</div>':''}</div>${action}</div>`;
    };

    if(!activeDocs.length){
        activeHtml='<div class="doc-list-title">当前知识库</div><div style="color:#aaa;padding:12px;font-size:12px;">请上传第一份文档</div>';
    }else{
        activeHtml='<div class="doc-list-title">当前知识库</div>';
        activeDocs.forEach((d,i)=>{activeHtml+=renderDoc(d,'B'+(i+1),false);});
    }
    if(inactiveDocs.length){
        inactiveHtml='<div class="doc-list-title history-title">历史版本（'+inactiveDocs.length+'）</div>';
        inactiveDocs.forEach((d,i)=>{inactiveHtml+=renderDoc(d,'H'+(i+1),true);});
    }
    el.innerHTML=pendingHtml+activeHtml+inactiveHtml;
}

async function setPrimaryDoc(docId){
    if(!docId)return;
    if(!confirm('将此历史版本设为当前版本？原当前版本会保留为历史版本。'))return;
    const fd=new FormData();
    fd.append('doc_id',docId);
    try{
        const res=await fetch('/api/documents/primary',{method:'POST',body:fd});
        const data=await res.json();
        if(!res.ok){alert(data.detail||'切换当前版本失败');return;}
        if(currentDoc===docId)loadKbPreview(docId.split('#')[0]);
        refreshDocList();
    }catch(e){alert('切换当前版本失败: '+e.message);}
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
async function selectPendingDoc(filename, taskId=reviewTaskId){
    // 预审核模式下保持左侧结果和右侧文档预览并排；普通模式才切回文档预览 Tab。
    const reviewTabActive=document.getElementById('reviewPanel').classList.contains('active');
    const splitReview=document.getElementById('kbRight')?.classList.contains('review-mode');
    if(reviewTabActive&&splitReview){
        const ref={kind:'pending',taskId:taskId||reviewTaskId,filename,hash:newDocHash,label:newDocLabel};
        reviewFocusedSide='n';setReviewPreviewFocus('n');
        return loadReviewPane('n',ref,getDocPage(filename));
    }
    if(reviewTabActive&&!splitReview)switchKbTab('preview');
    // 清理 PDF 状态
    kbPdfDoc=null;kbPdfUrl=null;
    currentDoc=filename;
    refreshDocList();
    document.getElementById('previewEmpty').style.display='none';
    document.getElementById('previewContent').style.display='flex';
    const ext = filename.split('.').pop().toLowerCase();
    document.getElementById('previewTitle').textContent='N '+fmtDocName(filename,newDocHash,newDocLabel);
    if(ext === 'pdf'){
        if(!taskId){
            document.getElementById('kbPdfContainer').innerHTML=
                '<div style="padding:20px;color:var(--danger);text-align:center;">预审核任务不存在，请重新上传文档</div>';
            return;
        }
        const url=`/api/documents/review/pdf?task_id=${encodeURIComponent(taskId)}`;
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
        await loadPendingTextPreview(filename,taskId);
    }
}

// 待审核文档（尚未入库）的文本段落预览
async function loadPendingTextPreview(filename,taskId=reviewTaskId){
    const container=document.getElementById('kbPdfContainer');
    container.innerHTML='<div style="padding:16px;color:var(--text3);">加载中...</div>';
    const pager=document.getElementById('kbPager');
    if(pager)pager.style.display='none';
    try{
        const query=`file=${encodeURIComponent(filename)}${taskId?'&task_id='+encodeURIComponent(taskId):''}`;
        const resp=await fetch(`/api/documents/review/paragraphs?${query}`);
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
function promptLabel(message, initial){
    // 带输入框的确认框：返回 Promise<string|null>（null=取消，''=空描述）
    return new Promise(resolve=>{
        const overlay=document.getElementById('modalOverlay');
        const wrap=document.getElementById('modalInputWrap');
        const input=document.getElementById('modalInput');
        document.getElementById('modalMsg').textContent=message;
        wrap.style.display='';
        input.value=initial||'';
        overlay.classList.add('show');
        setTimeout(()=>input.focus(),50);
        const yesHandler=()=>{cleanup();resolve(input.value.trim());};
        const noHandler=()=>{cleanup();resolve(null);};
        const keyHandler=(e)=>{if(e.key==='Escape'){cleanup();resolve(null);}else if(e.key==='Enter'){cleanup();resolve(input.value.trim());}};
        const cleanup=()=>{
            overlay.classList.remove('show');
            wrap.style.display='none';
            document.getElementById('modalBtnYes').removeEventListener('click',yesHandler);
            document.getElementById('modalBtnNo').removeEventListener('click',noHandler);
            document.removeEventListener('keydown',keyHandler);
        };
        document.getElementById('modalBtnYes').addEventListener('click',yesHandler);
        document.getElementById('modalBtnNo').addEventListener('click',noHandler);
        document.addEventListener('keydown',keyHandler);
    });
}

async function editPendingDocLabel(taskId){
    taskId=taskId||reviewTaskId;
    if(!taskId)return;
    const val=await promptLabel('修改待审核文档补充描述（建议填版本号）：',newDocLabel);
    if(val===null)return;
    const fd=new FormData();
    fd.append('label',val);
    try{
        const r=await fetch(`/api/documents/review/${encodeURIComponent(taskId)}/label`,{method:'POST',body:fd});
        const d=await r.json();
        if(!r.ok){alert(d.detail||'更新失败');return;}
        if(taskId===reviewTaskId){
            newDocLabel=d.label||'';
            if(reviewResult)reviewResult.new_doc_label=newDocLabel;
            const title=document.getElementById('stepTitle');
            if(title&&newDocFile)title.textContent='预审核: '+fmtDocName(newDocFile,newDocHash,newDocLabel);
            if(reviewResult)buildReviewPanel();
        }
        refreshDocList();
    }catch(e){alert('更新失败: '+e.message);}
}

async function editDocLabel(docId){
    if(!docId)return;
    // 以唯一 doc_id 查找列表项；同名多版本不能再按文件名取描述。
    let curLabel='';
    const el = document.querySelector(`.doc-item[data-doc-id="${CSS.escape(docId)}"]`);
    if(el){
        const tag=el.querySelector('.doc-label-tag');
        if(tag) curLabel=tag.textContent.replace('✎','').trim();
    }
    const val=await promptLabel('修改文档补充描述（建议填版本号）：', curLabel);
    if(val===null)return;
    const fd=new FormData();
    fd.append('doc_id',docId);
    fd.append('label',val);
    try{
        const r=await fetch('/api/documents/label',{method:'POST',body:fd});
        const d=await r.json();
        if(!r.ok) alert(d.detail||'更新失败');
        else { refreshDocList(); }
    }catch(e){ alert('更新失败: '+e.message); }
}

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
    // 普通预审核 Tab 仍需确认切换；并排模式下直接在右侧预览选中的文档。
    const reviewTabActive=document.getElementById('reviewPanel').classList.contains('active');
    const splitReview=document.getElementById('kbRight')?.classList.contains('review-mode');
    if(reviewTabActive&&splitReview){
        const docId=resolveReviewStoredDocId(filename);
        const ref={kind:'stored',docId,filename:docId.split('#')[0],label:docLabelMap[docId]||'',hash:docId.includes('#')?docId.split('#').pop():''};
        reviewFocusedSide='b';setReviewPreviewFocus('b');
        return loadReviewPane('b',ref,1,{group:{doc_id:docId}});
    }
    if(reviewTabActive&&!splitReview){
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
    return loadKbPreview(basename);
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
    document.getElementById('previewTitle').textContent=(docMap[currentDoc]||'')+' '+fmtDocName(basename,currentDoc.includes('#')?currentDoc.split('#').pop():'',docLabelMap[currentDoc]||docLabelMap[basename]||'');
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
    const textDocHash=currentDoc&&currentDoc.includes('#')?currentDoc.split('#').pop():'';
    document.getElementById('previewTitle').textContent=(docMap[currentDoc]||docMap[filename]||'')+' '+fmtDocName(filename,textDocHash,docLabelMap[currentDoc]||docLabelMap[filename]||'');
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
let reviewViewState=null;

function rememberVersionCompare(){
    const panel=document.getElementById('versionComparePanel');
    if(!panel||!panel.classList.contains('show'))return;
    reviewViewState={
        gi:Number(panel.dataset.groupIdx),
        ci:Number(panel.dataset.vcIdx),
        side:panel.querySelector('.vc-quick-tab.active')?.dataset.side||'new',
        showAll:panel.dataset.showAll==='1',
    };
    panel.classList.remove('show');
}

function restoreVersionCompare(){
    if(!reviewViewState||!reviewResult)return;
    const state=reviewViewState;
    const g=reviewResult.compare_groups?.[state.gi];
    if(!g?.version_changes?.[state.ci])return;
    showVersionDiffForGroup(state.gi,state.ci).then(()=>{
        const panel=document.getElementById('versionComparePanel');
        if(!panel)return;
        panel.dataset.showAll=state.showAll?'1':'0';
        switchVcTab(state.side);
    });
}

async function switchKbTab(tab){
    const switchToken=++reviewTabSwitchToken;
    const switchTaskId=reviewTaskId;
    if(tab==='review'){
        await reviewLayoutReady;
        if(switchToken!==reviewTabSwitchToken||switchTaskId!==reviewTaskId)return;
        try{await refreshDocList();}catch(error){console.warn('[review] 刷新文档映射失败，继续显示审核结果:',error);}
        if(switchToken!==reviewTabSwitchToken||switchTaskId!==reviewTaskId)return;
    }
    const kbRight=document.getElementById('kbRight');
    const previewPanel=document.getElementById('previewPanel');
    const previewEmpty=document.getElementById('previewEmpty');
    const previewContent=document.getElementById('previewContent');
    const reviewPreviewContent=document.getElementById('reviewPreviewContent');
    const reviewPanel=document.getElementById('reviewPanel');
    document.querySelectorAll('.kb-tabs .kb-tab').forEach(b=>b.classList.remove('active'));
    if(tab==='preview'){
        nextReviewPreviewViewToken();
        rememberVersionCompare();
        kbRight?.classList.remove('review-mode');
        document.querySelector('.kb-tabs .kb-tab:first-child').classList.add('active');
        previewPanel.classList.add('active');
        reviewPanel.classList.remove('active');
        if(reviewPreviewContent)reviewPreviewContent.style.display='none';
        if(previewContent)previewContent.style.display=currentDoc?'flex':'none';
        if(previewEmpty)previewEmpty.style.display=currentDoc?'none':'flex';
    } else {
        applyReviewLayout(reviewLayout);
        applyReviewPreviewPaneMode();
        if(reviewResult)buildReviewPanel();
        kbRight?.classList.add('review-mode');
        document.getElementById('reviewTabBtn').classList.add('active');
        previewPanel.classList.add('active');
        reviewPanel.classList.add('active');
        if(previewEmpty)previewEmpty.style.display='none';
        if(previewContent)previewContent.style.display='none';
        if(reviewPreviewContent)reviewPreviewContent.style.display='flex';
        initializeReviewPreviews();
        restoreVersionCompare();
    }
}

function updateReviewTab(){
    const reviewTabBtn=document.getElementById('reviewTabBtn');
    if(!reviewTabBtn)return;
    reviewTabBtn.style.display='';
    const groups=reviewResult&&reviewResult.compare_groups?reviewResult.compare_groups:[];
    let n=0,vc=0,suspect=0;
    groups.forEach(g=>{
        if(g.compare_type==='version_diff') vc+=(g.version_changes||[]).length;
        else { n+=(g.inconsistencies||[]).length; suspect+=(g.suspects||[]).length; }
    });
    let label='预审核';
    if(reviewResult?.phase==='error'||reviewResult?.incomplete){
        label=`预审核失败 (${reviewResult.n_error_groups||groups.filter(g=>g.status==='error').length||1}组)`;
    }else{
        const parts=[];
        if(n>0)parts.push(`${n}矛盾`);
        if(suspect>0)parts.push(`${suspect}疑似待复核`);
        if(vc>0)parts.push(`${vc}变更`);
        if(parts.length)label=`预审核 (${parts.join('+')})`;
    }
    reviewTabBtn.textContent=`⚠️ ${label}`;
}

function showReviewTab(){
    updateReviewTab();
    const kbRight=document.getElementById('kbRight');
    const reviewPanel=document.getElementById('reviewPanel');
    kbRight?.classList.add('review-mode');
    reviewPanel?.classList.add('active');
    switchKbTab('review');
}

function updatePartialReviewButton(){
    const reviewBtn=document.getElementById('reviewBtn');
    const groups=reviewResult?.compare_groups||[];
    if(!reviewBtn||!groups.length)return;
    let conflictCount=0,versionCount=0,suspectCount=0;
    groups.forEach(g=>{
        if(g.compare_type==='version_diff') versionCount+=(g.version_changes||[]).length;
        else { conflictCount+=(g.inconsistencies||[]).length; suspectCount+=(g.suspects||[]).length; }
    });
    let text='';
    if(conflictCount>0)text+=`⚠️ ${conflictCount} 处内容矛盾`;
    if(suspectCount>0)text+=`${text?'，':''}🔎 ${suspectCount} 处疑似待人工复核`;
    if(versionCount>0)text+=`${text?'，':''}📝 ${versionCount} 处版本变更`;
    if(!text)text='🔎 已产生部分比对结果';
    reviewBtn.textContent=text+' → 查看详情';
    reviewBtn.classList.add('show');
    updateReviewTab();
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

function renderUploadTransferProgress(pct){
    const known=Number.isFinite(Number(pct));
    const value=known?Math.max(0,Math.min(100,Math.round(Number(pct)))):0;
    const pctText=known?`<span class="step-pct">${value}%</span>`:'';
    const fillClass=known?'':' indeterminate';
    const title=known?`上传文件 ${value}%`:'正在上传文件（进度暂不可计算）';
    const items=document.getElementById('stepItems');
    if(items)items.innerHTML=`<div class="step-item upload-transfer-item"><div class="dot active">↑</div><span class="step-label">${title}</span>${pctText}<div class="step-bar" title="${title}"><div class="step-bar-fill${fillClass}" style="width:${value}%"></div></div></div>`;
}

function uploadWithProgress(formData){
    return new Promise((resolve,reject)=>{
        const xhr=new XMLHttpRequest();
        renderUploadTransferProgress(null);
        xhr.upload.addEventListener('progress',event=>{
            renderUploadTransferProgress(event.lengthComputable&&event.total>0?(event.loaded/event.total)*100:null);
        });
        xhr.onload=()=>{
            let data={};
            try{data=JSON.parse(xhr.responseText||'{}');}catch(e){data={detail:xhr.responseText||'上传响应格式错误'};}
            if(xhr.status>=200&&xhr.status<300)renderUploadTransferProgress(100);
            resolve({ok:xhr.status>=200&&xhr.status<300,status:xhr.status,data});
        };
        xhr.onerror=()=>reject(new Error('网络连接失败'));
        xhr.onabort=()=>reject(new Error('上传已中止'));
        xhr.open('POST','/api/documents/upload');
        xhr.send(formData);
    });
}

async function handleUpload(file){
if(!file)return;
// 上传前弹窗输入补充描述（用户可取消 → 空描述）
const labelVal=await promptLabel('为文档「'+file.name+'」添加补充描述（可手工填写版本号；系统不会自动识别版本号；可留空）：','');
window.__uploadLabel=labelVal===null?'':labelVal;
newDocLabel=window.__uploadLabel;
// 清理上一轮预审核结果，避免在 SSE 首个事件到达前仍显示旧结果
stopReviewEventSource();
reviewResult=null;reviewTaskId=null;reviewTerminalTaskId=null;reviewViewState=null;openCompareGroups.clear();
const oldVersionPanel=document.getElementById('versionComparePanel');
if(oldVersionPanel){oldVersionPanel.classList.remove('show');oldVersionPanel.replaceChildren();}
const oldBtn=document.getElementById('reviewBtn');if(oldBtn){oldBtn.classList.remove('show');oldBtn.textContent='';}
const oldPanel=document.getElementById('reviewPanel');if(oldPanel)oldPanel.classList.remove('active');
// 新一轮审核开始前，结果操作按钮全部保持禁用，直到收到最终结果。
const reviewOkBtn=document.getElementById('reviewConfirmBtn');
const reviewNoBtn=document.getElementById('reviewRejectBtn');
const reviewExportBtn=document.getElementById('reviewExportBtn');
if(reviewOkBtn){reviewOkBtn.disabled=true;reviewOkBtn.textContent='等待审核完成';}
if(reviewNoBtn){reviewNoBtn.disabled=true;reviewNoBtn.textContent='取消入库';}
if(reviewExportBtn)reviewExportBtn.disabled=true;
newDocFile=file.name;newDocHash='';activeFileName=file.name;
uploadZone.classList.add('disabled');
document.getElementById('stepArea').classList.add('show');
document.getElementById('stepTitle').textContent='上传: '+file.name;
document.getElementById('stepItems').innerHTML='';
renderUploadTransferProgress(null);
const fd=new FormData();fd.append('file',file);fd.append('label',window.__uploadLabel||'');
try{
const uploadResponse=await uploadWithProgress(fd);
if(!uploadResponse.ok){alert(uploadResponse.data.detail||'上传失败');resetUpload();return;}
const data=uploadResponse.data;
// 同名文档：保留旧版本并进入版本审核，最终确认时再选择当前版本
if(data.needs_choice){
    const proceed=await customConfirm(
        `检测到疑似同一文档的已有版本「${data.filename}」（${data.existing.label||'未标注版本'}）。\n\n将保留旧版本，并在预审核完成后让你选择：\n· 是：继续预审核并保留两个版本\n· 否：取消本次上传`
    );
    if(!proceed){resetUpload();return;}
    const fd2=new FormData();
    fd2.append('file',file);
    fd2.append('choice','coexist');
    fd2.append('label',window.__uploadLabel||'');
    const uploadResponse2=await uploadWithProgress(fd2);
    if(!uploadResponse2.ok){alert(uploadResponse2.data.detail||'上传失败');resetUpload();return;}
    const data2=uploadResponse2.data;
    reviewTaskId=data2.task_id;
    newDocHash=data2.file_hash||'';
    document.getElementById('stepTitle').textContent='预审核: '+fmtDocName(data2.filename,newDocHash,newDocLabel);
    refreshDocList();
    connectSSE(data2.task_id);
    return;
}
reviewTaskId=data.task_id;
newDocHash=data.file_hash||'';
document.getElementById('stepTitle').textContent='预审核: '+fmtDocName(data.filename,newDocHash,newDocLabel);
refreshDocList();
connectSSE(data.task_id);
}catch(e){alert('上传失败: '+e.message);resetUpload();}
}

function showTerminalReviewResult(d){
    reviewTerminalTaskId=reviewTaskId;
    const isError=d.status==='error';
    const result=d.result&&typeof d.result==='object'
        ? d.result
        : {phase:isError?'error':'done',compare_groups:[],message:d.current_step||'预审核未完成'};
    if(isError){
        result.phase='error';
        result.incomplete=true;
        result.message=result.message||d.current_step||result.error||'预审核失败，请重新上传';
        result.n_error_groups=result.n_error_groups||0;
    }
    reviewResult=result;
    if(d.new_doc_label!==undefined) reviewResult.new_doc_label=d.new_doc_label;
    if(d.result?.new_doc_label!==undefined) reviewResult.new_doc_label=d.result.new_doc_label;
    if(d.old_version_filepath) reviewResult.old_version_filepath=d.old_version_filepath;
    if(d.old_doc_filename) reviewResult.old_doc_filename=d.old_doc_filename;
    document.getElementById('stepArea').classList.remove('show');

    const groups=reviewResult.compare_groups||[];
    let n=0,vc=0,suspect=0;
    groups.forEach(g=>{
        if(g.compare_type==='version_diff') vc+=(g.version_changes||[]).length;
        else { n+=(g.inconsistencies||[]).length; suspect+=(g.suspects||[]).length; }
    });
    const reviewBtn=document.getElementById('reviewBtn');
    if(isError){
        const errorCount=reviewResult.n_error_groups||groups.filter(g=>g.status==='error').length||1;
        reviewBtn.textContent=`❌ 预审核失败：${errorCount} 组比较失败 → 查看详情`;
        uploadZone.classList.remove('disabled');
    }else{
        let btnText='';
        if(n>0) btnText+=`⚠️ ${n} 处内容矛盾`;
        if(suspect>0) btnText+=`${btnText?'，':''}🔎 ${suspect} 处疑似待人工复核`;
        if(vc>0) btnText+=`${n>0||suspect>0?'，':''}📝 ${vc} 处版本变更`;
        if(!btnText) btnText='✅ 未发现异常 → 确认入库';
        else btnText+=' → 查看详情';
        reviewBtn.textContent=btnText;
    }
    reviewBtn.classList.add('show');
    buildReviewPanel();
    showReviewTab();
}

function clearReviewReconcileTimer(){
    if(reviewReconcileTimer){clearInterval(reviewReconcileTimer);reviewReconcileTimer=null;}
}
function stopReviewEventSource(){
    clearReviewReconcileTimer();
    if(reviewEventSource){reviewEventSource.close();reviewEventSource=null;}
}
async function reconcileReviewTask(taskId,es){
    if(taskId!==reviewTaskId||reviewEventSource!==es||reviewTerminalTaskId===taskId)return;
    try{
        const res=await fetch('/api/documents/review/active',{cache:'no-store'});
        const d=await res.json();
        if(taskId!==reviewTaskId||reviewEventSource!==es||reviewTerminalTaskId===taskId)return;
        if(d.task_id!==taskId||!d.result)return;
        if(d.status==='done'||d.status==='error'){
            clearReviewReconcileTimer();
            es.close();
            if(reviewEventSource===es)reviewEventSource=null;
            showTerminalReviewResult(d);
        }
    }catch(error){
        if(taskId===reviewTaskId&&reviewEventSource===es)console.warn('[review] 状态校准失败:',error);
    }
}
function connectSSE(taskId){
    stopReviewEventSource();
    const es=new EventSource(`/api/documents/review/${taskId}/progress`);
    reviewEventSource=es;
    reviewReconcileTimer=setInterval(()=>reconcileReviewTask(taskId,es),1000);
    es.onmessage=function(event){
        // 旧任务、旧连接或已进入终态的事件不能覆盖当前结果。
        if(taskId!==reviewTaskId||reviewEventSource!==es||reviewTerminalTaskId===taskId)return;
        const d=JSON.parse(event.data);
        renderSteps(d);
        // 增量结果推送：已有比较组时允许用户随时打开右侧预审核面板。
        if((d.status==='running'||d.status==='paused')&&d.result&&d.result.phase&&d.result.phase!=='done'){
            reviewResult={...d.result,status:d.status};
            if(d.new_doc_label!==undefined) newDocLabel=d.new_doc_label||'';
            if(d.result?.new_doc_label!==undefined) reviewResult.new_doc_label=d.result.new_doc_label;
            const compareStarted=d.result.phase==='comparing' || (d.result.compare_groups||[]).length>0;
            if(compareStarted){
                if(d.old_version_filepath) reviewResult.old_version_filepath=d.old_version_filepath;
                if(d.old_doc_filename) reviewResult.old_doc_filename=d.old_doc_filename;
                updatePartialReviewButton();
                buildReviewPanel();
            }
        }
        if(d.status==='done'||d.status==='error'||d.status==='cancelled'){
            clearReviewReconcileTimer();
            es.close();
            if(reviewEventSource===es)reviewEventSource=null;
            if((d.status==='done'||d.status==='error')&&d.result){
                showTerminalReviewResult(d);
            }else{
                resetUpload();
            }
        }
    };
    es.onerror=function(){
        if(reviewEventSource===es&&taskId===reviewTaskId)console.warn('[review] SSE connection error',{taskId});
    };
}


let stepTimer=null,lastSSEElapsed=0,lastSSETime=Date.now();
function renderSteps(d){
    const all=d.all_steps||[],comp=d.completed_steps||[];
    if(!all.length){
        document.getElementById('stepItems').innerHTML=`<div class="step-item"><div class="dot active">⏳</div><span class="step-label">${esc(d.current_step||'处理中...')}</span><div class="step-bar" title="进度暂不可计算"><div class="step-bar-fill indeterminate" style="width:40%"></div></div></div>`;
        return;
    }
    const byId=new Map(comp.map(s=>[s.id,s]));
    const doneCount=all.filter(s=>{
        const state=byId.get(s.id);
        return state&&(state.status==='done'||state.elapsed!=null);
    }).length;
    const overallPct=Number.isFinite(Number(d.progress))?Math.max(0,Math.min(100,Math.round(Number(d.progress)))):null;
    document.getElementById('stepTitle').textContent=`预审核: ${fmtDocName(activeFileName||'',newDocHash,newDocLabel)} (${doneCount}/${all.length})${overallPct!==null?` · ${overallPct}%`:''}`;
    lastSSEElapsed=d.current_elapsed||0;
    lastSSETime=Date.now();
    let html='';
    for(const s of all){
        const cs=byId.get(s.id);
        const status=cs?.status||((cs?.elapsed!=null)?'done':(cs?'active':'pending'));
        const isDone=status==='done', isAct=status==='active';
        const cls=isDone?'done':(isAct?'active':'pending');
        const icon=isDone?'✓':(isAct?'▸':'·');
        const details=cs?.details&&typeof cs.details==='object'?cs.details:null;
        const dynamicLabel=(s.id==='loading'||s.id==='embedding')&&cs?.message?cs.message:s.label;
        let time='';
        if(isDone&&cs) time=`<span class="time">${Math.round(cs.elapsed||0)}s</span>`;
        else if(isAct) time=`<span class="time" id="activeTimer">${Math.floor(d.current_elapsed||0)}s</span>`;
        const rawPct=details?.stage_pct??cs?.pct??d.progress;
        const pctKnown=isAct&&rawPct!==null&&rawPct!==undefined&&Number.isFinite(Number(rawPct));
        const pct=pctKnown?Math.max(0,Math.min(100,Math.round(Number(rawPct)))):0;
        const pctText=pctKnown?`<span class="step-pct">${pct}%</span>`:'';
        const fillClass=!pctKnown?' indeterminate':'';
        const barTitle=pctKnown?`${pct}%`:'该阶段进度暂不可计算';
        const bar=isAct?`<div class="step-bar" title="${barTitle}"><div class="step-bar-fill${fillClass}" style="width:${pct}%"></div></div>`:'';
        html+=`<div class="step-item"><div class="dot ${cls}">${icon}</div><span class="step-label">${esc(dynamicLabel)}</span>${pctText}${time}${bar}</div>`;
    }
    document.getElementById('stepItems').innerHTML=html;
    if(!stepTimer&&doneCount<all.length)stepTimer=setInterval(tickTimer,1000);
    if(doneCount>=all.length){clearInterval(stepTimer);stepTimer=null;}
}
function tickTimer(){const el=document.getElementById('activeTimer');if(!el)return;el.textContent=Math.floor(lastSSEElapsed+(Date.now()-lastSSETime)/1000)+'s';}

function resetUpload(){
    stopReviewEventSource();
    document.getElementById('stepArea').classList.remove('show');
    uploadZone.classList.remove('disabled');
    // 清理当前审核上下文，避免上一轮终态按钮残留到下一轮或重新上传流程。
    reviewResult=null;
    reviewTaskId=null;
    reviewTerminalTaskId=null;
    reviewViewState=null;
    openCompareGroups.clear();
    const fileInput=document.getElementById('fileInput');
    if(fileInput)fileInput.value='';
    const exportBtn=document.getElementById('reviewExportBtn');
    const rerunBtn=document.getElementById('reviewRerunBtn');
    const okBtn=document.getElementById('reviewConfirmBtn');
    const noBtn=document.getElementById('reviewRejectBtn');
    if(exportBtn)exportBtn.disabled=true;
    if(rerunBtn){rerunBtn.disabled=true;rerunBtn.style.display='none';}
    if(okBtn){okBtn.disabled=true;okBtn.textContent='等待审核完成';}
    if(noBtn){noBtn.disabled=true;noBtn.textContent='取消入库';}
    newDocFile=null;refreshDocList();
}
function startReupload(){
    // 错误任务无法重跑或用户希望从头执行时，直接打开本地文件选择器。
    hideReviewTab();
    resetUpload();
    const fileInput=document.getElementById('fileInput');
    if(fileInput){
        fileInput.value='';
        fileInput.click();
    }
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
    if(!confirm('确定要停止本次文档检测吗？已完成的对比结果会保留，未完成的将丢弃。'))return;
    fetch(`/api/documents/review/${reviewTaskId}/cancel`,{method:'POST'}).then(r=>r.json()).then(d=>{
        if(reviewResult)reviewResult.status='cancelled';
        buildReviewPanel();
    });
}

function updateReviewActionButtons(){
    const exportBtn=document.getElementById('reviewExportBtn');
    const rerunBtn=document.getElementById('reviewRerunBtn');
    const okBtn=document.getElementById('reviewConfirmBtn');
    const noBtn=document.getElementById('reviewRejectBtn');
    const r=reviewResult||{};
    const phase=r.phase||'';
    const isError=phase==='error'||r.incomplete===true;
    const isTerminal=phase==='done'||isError;
    const canExport=Boolean(reviewTaskId&&isTerminal);
    const canConfirm=Boolean(reviewTaskId&&phase==='done'&&!r.incomplete&&!r.cancelled);
    const canReject=Boolean(reviewTaskId&&isTerminal&&!r.cancelled);
    if(exportBtn)exportBtn.disabled=!canExport;
    if(rerunBtn){
        rerunBtn.style.display=isError&&reviewTaskId?'inline-block':'none';
        rerunBtn.disabled=!isError||!reviewTaskId;
    }
    if(okBtn){
        okBtn.disabled=!canConfirm;
        okBtn.textContent=canConfirm?'确认入库':isError?'无法确认入库':'等待审核完成';
    }
    if(noBtn)noBtn.disabled=!canReject;
}

function buildReviewPanel(){
const r=reviewResult;if(!r)return;
applyReviewPreviewPaneMode();
const groups=r.compare_groups||[];
const currentGroupKeys=new Set(groups.map((_,gi)=>String(gi)));
for(const key of openCompareGroups){if(!currentGroupKeys.has(key))openCompareGroups.delete(key);}
const hasOpenReviewGroup=groups.some((g,gi)=>openCompareGroups.has(String(gi))&&((g.version_changes||[]).length||(g.inconsistencies||[]).length||(g.suspects||[]).length));
const phase = r.phase || 'done';
const isError = phase==='error' || r.incomplete===true;
const isCancelled = phase==='cancelled' || r.cancelled===true;
const isTerminal = phase==='done' || isError || isCancelled;
const compareTotal = r.compare_total ?? groups.length;
const compareDone = r.compare_done ?? groups.filter(g=>g.status==='done').length;
const currentGroup=r.current_group&&typeof r.current_group==='object'?r.current_group:null;
const currentGroupKind=currentGroup?.compare_type==='version_diff'?'版本差异':'跨文档矛盾检测';
const currentGroupName=currentGroup?fmtDocName(currentGroup.doc_filename||'',currentGroup.file_hash||'',currentGroup.label||''):'';
const batchTotal=Number(currentGroup?.batch_total||0);
const batchDone=Math.min(batchTotal,Math.max(0,Number(currentGroup?.batch_done||0)));
const newDocName = fmtDocName(newDocFile || r.new_filename || '',newDocHash,r.new_doc_label||newDocLabel);
const statusTag=r.status==='paused'?'<span style="color:var(--warn);font-size:10px;">已暂停</span>':'';
updateReviewActionButtons();

// ====== 控制按钮（进行中：暂停/续跑/取消） ======
let controlsHtml='';
if(!isTerminal){
    const paused = r.status==='paused';
    controlsHtml='<div class="review-controls">';
    controlsHtml+=paused
        ? `<button onclick="resumeReview()" class="review-control-btn resume">▶ 续跑</button>`
        : `<button onclick="pauseReview()" class="review-control-btn pause">⏸ 暂停</button>`;
    controlsHtml+=`<button onclick="cancelReviewWithConfirm()" class="review-control-btn cancel">⏹ 停止</button>`;
    controlsHtml+='</div>';
}

// ====== 进行中：进度条 + 动态 message ======
let progressHtml='';
if(!isTerminal){
    const pct = compareTotal>0 ? Math.round(compareDone/compareTotal*100) : 0;
    const progressKnown=Number.isFinite(Number(r.progress));
    const safePct=progressKnown?Math.max(0,Math.min(100,Math.round(Number(r.progress)))):pct;
    const progressFillClass=progressKnown?'':' indeterminate';
    const progressColor = r.phase==='scoring' ? 'var(--primary)' : '#722ed1';
    const icon = r.phase==='scoring' ? '🔍' : r.phase==='comparing' ? '🔁' : '🧠';
    const label = r.phase==='scoring' ? '评估相似度' : r.phase==='comparing' ? '逐文档比较' : '处理中';
    const currentGroupHtml=currentGroup
        ? `<div class="judging-current-group" title="${esc(r.message||currentGroupName)}"><span class="judging-group-index">第 ${currentGroup.index}/${currentGroup.total} 组</span><span class="judging-group-kind">${esc(currentGroupKind)}</span><span class="judging-group-name">${esc(currentGroupName)}</span></div>`
        : '';
    const batchLabel=currentGroup&&currentGroup.compare_type!=='version_diff'
        ? (batchTotal>0?`LLM batch ${batchDone}/${batchTotal}`:'LLM batch 准备中')
        : '当前组处理中';
    const percentLabel=progressKnown?`${safePct}%`:'进度计算中';
    const progressMetaHtml=`<div class="judging-progress-meta"><span>${batchLabel}</span><span>${percentLabel}</span></div>`;
    progressHtml=`<div class="judging-banner">`;
    progressHtml+=`<div class="judging-title"><span class="judging-title-main"><span class="pulse-dot" style="background:${progressColor};"></span><b>${icon} ${label}</b> <span class="judging-count">${compareDone}/${compareTotal} 组</span> ${statusTag}</span></div>`;
    progressHtml+=currentGroupHtml+progressMetaHtml;
    progressHtml+=`<div class="judging-progress-row"><div class="judging-progress-track"><div class="judging-progress-fill${progressFillClass}" style="width:${safePct}%;background:${progressColor};"></div></div>${controlsHtml}</div>`;
    progressHtml+=`</div>`;
} else if(isError){
    const errorCount=r.n_error_groups||groups.filter(g=>g.status==='error').length||1;
    progressHtml=`<div class="review-error-banner"><div class="review-error-title">❌ 预审核未完成：${errorCount} 组比较失败</div><div class="review-error-message">${esc(r.message||'存在库中文件缺失或比较异常，不能判定为安全。')}<br><span style="color:var(--text3);">如果重跑失败，可重新选择原文档从头执行。</span></div><div class="review-error-actions"><button class="review-control-btn resume" onclick="forceReReview()">↻ 重新执行预审核</button><button class="review-control-btn cancel" onclick="startReupload()">📄 重新上传文档</button></div></div>`;
} else if(groups.length===0){
    if(r.kb_empty){ progressHtml='<div style="padding:20px;text-align:center;color:var(--text3);">📭 首篇文档，无对比对象，可入库</div>'; }
    else { progressHtml='<div style="padding:20px;text-align:center;color:var(--text3);">✅ 预审核通过，未发现矛盾或版本差异</div>'; }
}

// ====== 比较结果摘要：按文档纵向列出，并可跳转到对应比较组 ======
let compareSummaryHtml='';
if(groups.length){
    compareSummaryHtml='<div class="compare-summary"><div class="compare-summary-title">比较结果总览 <span class="summary-hint">点击文档可展开对应比较组</span></div><div class="compare-summary-list">';
    groups.forEach((g,gi)=>{
        const isVersion=g.compare_type==='version_diff';
        const count=isVersion?(g.version_changes||[]).length:(g.inconsistencies||[]).length;
        const suspectCount=isVersion?0:(g.suspects||[]).length;
        const minorCount=(g.minor_changes||[]).length;
        const label=docMap[g.doc_id]||'B—';
        const kind=isVersion?'版本差异':'跨文档矛盾检测';
        let detail;
        if(g.status==='error') detail='比较失败';
        else if(isVersion) detail=`${count} 处实质性变更${minorCount?`，${minorCount} 处细微差异`:''}`;
        else {
            detail=`${count} 处矛盾`;
            if(suspectCount) detail+=`，${suspectCount} 处疑似待复核`;
        }
        const summaryHtml=compareGroupSummaryHtml(g,label);
        compareSummaryHtml+=`<button type="button" class="compare-summary-card" data-gi="${gi}" onclick="jumpToCompareGroup(${gi})">`;
        compareSummaryHtml+=`<span class="src-id">${esc(label)}</span><span class="summary-doc">${esc(compareDocName(g))}</span>`;
        compareSummaryHtml+=`<span class="summary-kind">${kind} · ${detail} · 相似度 ${g.similarity!==undefined?(g.similarity*100).toFixed(0)+'%':'—'}</span>`;
        compareSummaryHtml+=`<span class="summary-detail">${summaryHtml}</span></button>`;
    });
    compareSummaryHtml+='</div></div>';
}

// ====== 分组 fold（compare_groups） ======
let groupsHtml='';
if(groups.length>0){
    groupsHtml='<div class="compare-groups">';
    groups.forEach((g,gi)=>{
        const changes = g.version_changes||[];
        const minor = g.minor_changes||[];
        const incons = g.inconsistencies||[];
        const suspects = g.suspects||[];
        const isVersion = g.compare_type==='version_diff';
        const typeBadge = g.status==='error'
            ? `<span class="badge badge-warn">❌ 无法比较</span>`
            : isVersion
            ? `<span class="badge badge-primary">📝 版本差异 ${changes.length}</span>`
            : `<span class="badge badge-warn">⚠️ 跨文档矛盾检测 ${incons.length}</span>${suspects.length?` <span class="badge" style="color:var(--warn);border-color:var(--warn);">🔎 疑似 ${suspects.length}</span>`:''}`;
        const gHash = g.file_hash ? '#'+g.file_hash.slice(-8).toUpperCase() : '';
        const simTag = g.similarity!==undefined ? `<span style="color:var(--text3);font-size:10px;">相似度 ${(g.similarity*100).toFixed(0)}%</span>` : '';
        const statusIcon = g.status==='done' ? '✅' : g.status==='error' ? '❌' : '⏳';
        const groupHasItems=changes.length>0||incons.length>0||suspects.length>0;
        const groupOpen=openCompareGroups.has(String(gi))||(!hasOpenReviewGroup&&groupHasItems);
        groupsHtml+=`<div class="compare-group" data-gi="${gi}">`;
        const bTag=docMap[g.doc_id]||'B—';
        groupsHtml+=`<div class="compare-group-title" onclick="toggleCompareGroup(${gi})" aria-expanded="${groupOpen}">`;
        groupsHtml+=`<span class="cg-caret" style="display:inline-block;transition:transform .2s;">▶</span>`;
        groupsHtml+=`<span class="src-id">${bTag}</span>`;
        groupsHtml+=`<span class="src-file">${esc(compareDocName(g))}</span>`;
        groupsHtml+=` ${typeBadge} ${simTag} <span style="color:var(--text3);font-size:10px;">${statusIcon}</span>`;
        groupsHtml+=`</div>`;
        groupsHtml+=`<div class="compare-group-body" style="display:${groupOpen?'block':'none'};">`;
        if(g.error){ groupsHtml+=`<div style="padding:8px;color:var(--danger);font-size:11px;">${esc(g.error)}</div>`; }
        if(isVersion){
            if(changes.length===0 && minor.length===0 && g.status==='done'){
                groupsHtml+='<div style="padding:8px;color:var(--text3);font-size:11px;">无实质性差异</div>';
            }
            changes.forEach((c,ci)=>{
                const icon=c.type==='modified'?'✏️':c.type==='added'?'➕':'➖';
                const tl=c.type==='modified'?'修改':c.type==='added'?'新增':'删除';
                const bTag=docMap[g.doc_id]||'B—';
                const newLoc=c.location||'';
                const oldLoc=c.old_location||'';
                const locText=c.type==='added'?`N ${newLoc}`:c.type==='removed'?`${bTag} ${oldLoc}`:`N ${newLoc} · ${bTag} ${oldLoc}`;
                groupsHtml+=`<div class="version-diff-item" onclick="showGroupVersionDiff(${gi},${ci})" data-gi="${gi}" data-ci="${ci}">`;
                groupsHtml+=`<div class="vd-header"><span class="vd-type">${icon} ${tl}</span><span class="vd-loc">${esc(locText)}</span><span class="vd-summary">${esc(changeSummary(c))}</span></div>`;
                groupsHtml+=`</div>`;
            });
        } else {
            if(incons.length===0 && suspects.length===0 && g.status==='done'){
                groupsHtml+='<div style="padding:8px;color:var(--text3);font-size:11px;">未发现矛盾</div>';
            }
            incons.forEach((inc,ci)=>{
                const bTag=docMap[g.doc_id]||'B—';
                const point=normalizeConflictText(inc.point||'内容差异',bTag);
                const nSays=normalizeConflictText(inc.doc_a_says||'',bTag);
                const bSays=normalizeConflictText(inc.doc_b_says||'',bTag);
                groupsHtml+=`<div class="conflict-item" data-gi="${gi}" data-ci="${ci}" style="cursor:pointer;" onclick="showGroupConflict(${gi},${ci})">`;
                groupsHtml+=`<div class="title">${esc(point)}</div>`;
                groupsHtml+=`<div class="desc"><span style="color:var(--primary);">N:</span> ${esc(nSays.slice(0,100))}`;
                groupsHtml+=`<br><span style="color:var(--warn);">${esc(bTag)}:</span> ${esc(bSays.slice(0,100))}</div>`;
                groupsHtml+=`</div>`;
            });
            suspects.forEach((inc,si)=>{
                const bTag=docMap[g.doc_id]||'B—';
                const point=normalizeConflictText(inc.point||'内容差异',bTag);
                const nSays=normalizeConflictText(inc.doc_a_says||'',bTag);
                const bSays=normalizeConflictText(inc.doc_b_says||'',bTag);
                groupsHtml+=`<div class="suspect-item" data-gi="${gi}" data-si="${si}" style="cursor:pointer;border-left:3px solid var(--warn);background:rgba(250,173,20,.08);" onclick="showGroupSuspect(${gi},${si})">`;
                groupsHtml+=`<div class="title" style="color:var(--warn);">🔎 疑似待人工复核：${esc(point)}</div>`;
                groupsHtml+=`<div class="desc"><span style="color:var(--primary);">N:</span> ${esc(nSays.slice(0,100))}`;
                groupsHtml+=`<br><span style="color:var(--warn);">${esc(bTag)}:</span> ${esc(bSays.slice(0,100))}</div>`;
                groupsHtml+=`</div>`;
            });
        }
        groupsHtml+=`<div class="compare-group-back"><button type="button" onclick="backToCompareSummary(${gi},event)">↑ 返回总览并收起本组</button></div>`;
        groupsHtml+=`</div></div>`;
    });
    groupsHtml+='</div>';
}

// info 汇总
let infoText='';
if(!isTerminal){
    // 当前组、batch 和总体百分比已在上方进度横幅展示，这里只保留简短状态，避免重复。
    infoText=r.status==='paused'?'⏸ 已暂停':'预审核进行中';
} else {
    const nIssue = r.n_issue_groups || 0;
    const nSuspects = r.n_suspects || groups.reduce((sum,g)=>sum+(g.suspects||[]).length,0);
    if(isError){ infoText=`❌ 预审核未完成：${r.n_error_groups||groups.filter(g=>g.status==='error').length||1} 组比较失败`; }
    else if(r.cancelled){ infoText='⏹ 已中断'; }
    else if(nIssue>0){ infoText=`⚠️ 共 ${nIssue} 组存在差异/矛盾（${r.n_version_groups||0} 组版本差异 + ${r.n_conflict_groups||0} 组跨文档矛盾检测）`; }
    else if(nSuspects>0){ infoText=`🔎 无确定性矛盾，但有 ${nSuspects} 处疑似项需人工复核`; }
    else if(r.kb_empty){ infoText='📭 首篇文档，无对比对象，可入库'; }
    else { infoText='✅ 未发现内容矛盾，可安全入库'; }
    infoText+=`　（新文档: ${esc(newDocName)}）`;
}
document.getElementById('reviewInfo').textContent=infoText;
const conflictList=document.getElementById('conflictList');
if(conflictList&&!document.getElementById('comparePanel')?.classList.contains('show')&&!document.getElementById('versionComparePanel')?.classList.contains('show'))conflictList.classList.remove('collapsed');
if(conflictList)conflictList.innerHTML=progressHtml+compareSummaryHtml+groupsHtml;
}

function setCompareGroupOpen(gi,open){
    const key=String(gi);
    if(open)openCompareGroups.add(key);else openCompareGroups.delete(key);
    const el=document.querySelector(`.compare-group[data-gi="${gi}"]`);
    if(!el)return;
    const body=el.querySelector('.compare-group-body');
    const caret=el.querySelector('.cg-caret');
    const title=el.querySelector('.compare-group-title');
    if(body)body.style.display=open?'block':'none';
    if(caret)caret.style.transform=open?'rotate(90deg)':'';
    if(title)title.setAttribute('aria-expanded',String(open));
}

function jumpToCompareGroup(gi){
    setCompareGroupOpen(gi,true);
    const el=document.querySelector(`.compare-group[data-gi="${gi}"]`);
    if(!el)return;
    el.scrollIntoView({behavior:'smooth',block:'start'});
    el.classList.add('compare-group-jump');
    setTimeout(()=>el.classList.remove('compare-group-jump'),1200);
}

function backToCompareSummary(gi,event){
    if(event)event.stopPropagation();
    setCompareGroupOpen(gi,false);
    const summary=document.querySelector(`.compare-summary-card[data-gi="${gi}"]`);
    if(summary)summary.scrollIntoView({behavior:'smooth',block:'start'});
}

function toggleCompareGroup(gi){
    const el=document.querySelector(`.compare-group[data-gi="${gi}"]`);
    if(!el)return;
    const body=el.querySelector('.compare-group-body');
    setCompareGroupOpen(gi,!body||body.style.display==='none');
}

function showGroupVersionDiff(gi,ci){
    const g=reviewResult?.compare_groups?.[gi];
    const vc=g?.version_changes?.[ci];
    if(!vc)return;
    // 复用现有版本差异详情面板（用一个全局指向当前组）
    window.__groupIdx=gi;
    document.querySelectorAll('.version-diff-item').forEach(el=>el.classList.toggle('selected', Number(el.dataset.gi)===gi && Number(el.dataset.ci)===ci));
    showVersionDiffForGroup(gi,ci);
}

async function openVersionDiffSide(gi,ci,side,event){
    if(event)event.stopPropagation();
    await showVersionDiffForGroup(gi,ci);
    switchVcTab(side);
}

async function showVersionDiffForGroup(gi,ci){
    const g=reviewResult?.compare_groups?.[gi];
    const vc=g?.version_changes?.[ci];
    if(!vc)return;
    window.__groupIdx=gi;
    const panel=document.getElementById('versionComparePanel');
    if(!panel)return;
    const previewToken=nextReviewPreviewViewToken();
    const viewToken=`${Date.now()}-${gi}-${ci}`;
    panel.dataset.viewToken=viewToken;
    panel.dataset.previewToken=String(previewToken);
    panel.classList.add('show');
    panel.dataset.vcIdx=ci;
    panel.dataset.groupDocId=g.doc_id||'';
    panel.dataset.groupIdx=gi;
    panel.dataset.showAll='0';
    const typeIcon=vc.type==='modified'?'✏️':vc.type==='added'?'➕':'➖';
    const typeLabel=vc.type==='modified'?'修改':vc.type==='added'?'新增':'删除';
    const newDiffPage=_changePageForSide(vc,'new');
    const oldDiffPage=_changePageForSide(vc,'old');
    const diffPage=newDiffPage;
    panel.dataset.newDiffPage=newDiffPage;
    panel.dataset.oldDiffPage=oldDiffPage;
    reviewViewState={gi:Number(gi),ci:Number(ci),side:'new',showAll:false,newDiffPage,oldDiffPage};
    let html='';
    html+=`<div style="display:flex;justify-content:space-between;align-items:center;padding:10px 14px;border-bottom:1px solid var(--border);background:var(--card);flex-shrink:0;">`;
    html+=`<span style="font-weight:600;">${typeIcon} ${typeLabel} · ${esc(compareDocName(g))} 差异 #${ci+1}</span>`;
    html+=`<button onclick="closeVersionCompare()" style="background:none;border:1px solid var(--border);border-radius:4px;padding:3px 12px;cursor:pointer;font-size:11px;">✕ 返回列表</button></div>`;
    // sticky: 差异定位 + 摘要 + 文字差异 + 页面切换
    html+=`<div class="vc-sticky-header">`;
    const oldLoc=parseLoc(_changeLocationForSide(vc,'old'));
    const newLoc=parseLoc(_changeLocationForSide(vc,'new'));
    html+=`<div class="vc-loc-bar">`;
    const bnTag=docMap[g.doc_id]||'B—';
    html+=`<div class="vc-loc-side vc-loc-old"><span class="vc-loc-tag">${bnTag} 对比文档</span><span class="vc-loc-page">第 ${oldLoc.page||oldDiffPage} 页</span><span class="vc-loc-section" title="${escA(oldLoc.raw||'')}">${esc(oldLoc.raw||'—')}</span></div>`;
    html+=`<div class="vc-loc-arrow">→</div>`;
    const newName=fmtDocName(newDocFile||reviewResult?.new_filename||'新文档',newDocHash,reviewResult?.new_doc_label||newDocLabel);
    html+=`<div class="vc-loc-side vc-loc-new"><span class="vc-loc-tag">N 新文档</span><span class="vc-loc-page">第 ${newLoc.page||newDiffPage} 页</span><span class="vc-loc-section" title="${escA(newName + ' ' + (newLoc.raw||''))}">${esc(newName)}${newLoc.raw?(' · '+esc(newLoc.raw)):''}</span></div>`;
    html+=`</div>`;
    html+=`<div class="vc-summary-bar">💡 ${changeSummaryHtml(vc)}</div>`;
    html+=tableChangeDetailsHtml(vc);
    html+=`<div class="vc-text-diff">`;
    html+=`<div style="font-size:11px;font-weight:600;color:var(--text3);margin-bottom:8px;">📝 文字差异对比</div>`;
    const oldText=vc.old_text||'';
    const newText=vc.new_text||'';
    if(vc.type==='added'){
        html+=`<div class="vc-diff-row vc-diff-new-row"><span class="vc-diff-tag vc-diff-jump" title="打开新版预览" onclick="openVersionDiffSide(${gi},${ci},'new',event)">N</span><span class="vc-diff-text">${esc(newText)}</span></div>`;
        html+=`<div class="vc-diff-empty">（旧版无此内容 — 新增段落）</div>`;
    } else if(vc.type==='removed'){
        html+=`<div class="vc-diff-row vc-diff-old-row"><span class="vc-diff-tag vc-diff-jump" title="打开对比文档预览" onclick="openVersionDiffSide(${gi},${ci},'old',event)">${bnTag}</span><span class="vc-diff-text">${esc(oldText)}</span></div>`;
        html+=`<div class="vc-diff-empty">（新版已删除此段落）</div>`;
    } else {
        html+=`<div class="vc-diff-row vc-diff-old-row"><span class="vc-diff-tag vc-diff-jump" title="打开对比文档预览" onclick="openVersionDiffSide(${gi},${ci},'old',event)">${bnTag}</span><span class="vc-diff-text">${diffMark(newText,oldText,'b')}</span></div>`;
        html+=`<div class="vc-diff-row vc-diff-new-row"><span class="vc-diff-tag vc-diff-jump" title="打开新版预览" onclick="openVersionDiffSide(${gi},${ci},'new',event)">N</span><span class="vc-diff-text">${diffMark(newText,oldText,'a')}</span></div>`;
    }
    html+=`</div>`;
    // 页面切换
    html+=`<div style="padding:8px 14px;background:var(--card);border-top:1px solid var(--border);display:flex;align-items:center;gap:8px;">`;
    html+=`<span style="font-size:11px;font-weight:600;color:var(--text3);">📄 页面定位：N 第 ${newLoc.page||newDiffPage} 页 / ${bnTag} 第 ${oldLoc.page||oldDiffPage} 页</span>`;
    html+=`<div style="margin-left:auto;display:flex;gap:6px;">`;
    html+=`<button class="vc-quick-tab active" data-side="new" onclick="switchVcTab('new')" style="padding:4px 14px;border:1px solid var(--border);border-radius:4px;background:var(--primary);color:#fff;cursor:pointer;font-size:11px;">N 新版</button>`;
    html+=`<button class="vc-quick-tab" data-side="old" onclick="switchVcTab('old')" style="padding:4px 14px;border:1px solid var(--border);border-radius:4px;background:transparent;cursor:pointer;font-size:11px;color:var(--text3);">${bnTag} 对比文档</button>`;
    html+=`</div></div>`;
    html+=`</div>`;
    html+=`<div class="vc-scroll-area" id="vcPagesContainer" data-diff-page="${diffPage}"><div style="padding:20px;text-align:center;color:var(--text3);">正在加载文档预览...</div></div>`;
    html+=`<div class="vc-bottom-actions"><button class="vc-back-btn" onclick="closeVersionCompare()">⟵ 返回差异列表</button></div>`;
    panel.innerHTML=html;
    // 获取新旧页数（B 侧用组 doc_id）
    const gid=panel.dataset.groupDocId||'';
    const q=gid?`&doc_id=${encodeURIComponent(gid)}`:' ';
    const [newInfo, oldInfo]=await Promise.all([
        fetch('/api/documents/review/info?task_id='+reviewTaskId).then(r=>r.json()),
        fetch('/api/documents/review/old/info?task_id='+reviewTaskId+q.trim()).then(r=>r.json()),
    ]);
    if(panel.dataset.viewToken!==viewToken || !panel.classList.contains('show'))return;
    panel.dataset.newPages=newInfo.page_count||0;
    panel.dataset.oldPages=oldInfo.page_count||0;
    panel.dataset.newDiffPage=newDiffPage;
    panel.dataset.oldDiffPage=oldDiffPage;
    panel.dataset.diffPage=newDiffPage;
    await renderVcPages('new',previewToken);
}

async function showVersionDiff(idx){
    const vc = reviewResult?.version_changes?.[idx];
    if(!vc)return;
    document.querySelectorAll('.version-diff-item').forEach((el,i)=>el.classList.toggle('selected',i===idx));

    const panel = document.getElementById('versionComparePanel');
    if(!panel)return;
    const previewToken=nextReviewPreviewViewToken();
    const viewToken = `${Date.now()}-${idx}`;
    panel.dataset.viewToken = viewToken;
    panel.dataset.previewToken=String(previewToken);
    panel.classList.add('show');
    panel.dataset.vcIdx = idx;
    panel.dataset.groupIdx = 0;
    panel.dataset.showAll = '0';
    const legacyGroup=reviewResult?.compare_groups?.[0];
    const legacyBTag=legacyGroup? (docMap[legacyGroup.doc_id]||'B1') : 'B';
    const newDiffPage=_changePageForSide(vc,'new');
    const oldDiffPage=_changePageForSide(vc,'old');
    const diffPage=newDiffPage;
    panel.dataset.newDiffPage=newDiffPage;
    panel.dataset.oldDiffPage=oldDiffPage;
    reviewViewState={gi:0,ci:Number(idx),side:'new',showAll:false,newDiffPage,oldDiffPage};

    const typeIcon = vc.type==='modified'?'✏️':vc.type==='added'?'➕':'➖';
    const typeLabel = vc.type==='modified'?'修改':vc.type==='added'?'新增':'删除';

    // 顶部标题栏（固定不滚动）
    let html = '';
    html += `<div style="display:flex;justify-content:space-between;align-items:center;padding:10px 14px;border-bottom:1px solid var(--border);background:var(--card);flex-shrink:0;">`;
    html += `<span style="font-weight:600;">${typeIcon} ${typeLabel} · ${esc(fmtDocName(newDocFile||reviewResult?.new_filename||'新文档',newDocHash,reviewResult?.new_doc_label||newDocLabel))} 差异 #${idx+1}</span>`;
    html += `<button onclick="closeVersionCompare()" style="background:none;border:1px solid var(--border);border-radius:4px;padding:3px 12px;cursor:pointer;font-size:11px;">✕ 返回列表</button>`;
    html += `</div>`;

    // ★ sticky 区域：差异定位 + 摘要 + 文字差异 + 页面切换（滚动时固定在顶部）
    html += `<div class="vc-sticky-header">`;
    // 差异定位
    const oldLoc = parseLoc(_changeLocationForSide(vc,'old'));
    const newLoc = parseLoc(_changeLocationForSide(vc,'new'));
    html += `<div class="vc-loc-bar">`;
    html += `<div class="vc-loc-side vc-loc-old"><span class="vc-loc-tag">${legacyBTag} 对比文档</span><span class="vc-loc-page">第 ${oldLoc.page||oldDiffPage} 页</span><span class="vc-loc-section">${esc(oldLoc.raw||'—')}</span></div>`;
    html += `<div class="vc-loc-arrow">→</div>`;
    html += `<div class="vc-loc-side vc-loc-new"><span class="vc-loc-tag">N 新文档</span><span class="vc-loc-page">第 ${newLoc.page||newDiffPage} 页</span><span class="vc-loc-section">${esc(newLoc.raw||'—')}</span></div>`;
    html += `</div>`;
    // LLM 摘要
    html += `<div class="vc-summary-bar">💡 ${changeSummaryHtml(vc)}</div>`;
    html += tableChangeDetailsHtml(vc);
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
    html += `<span style="font-size:11px;font-weight:600;color:var(--text3);">📄 页面定位：N 第 ${newLoc.page||newDiffPage} 页 / ${legacyBTag} 第 ${oldLoc.page||oldDiffPage} 页</span>`;
    html += `<div style="margin-left:auto;display:flex;gap:6px;">`;
    html += `<button class="vc-quick-tab active" data-side="new" onclick="switchVcTab('new')" style="padding:4px 14px;border:1px solid var(--border);border-radius:4px;background:var(--primary);color:#fff;cursor:pointer;font-size:11px;">N 新版</button>`;
    html += `<button class="vc-quick-tab" data-side="old" onclick="switchVcTab('old')" style="padding:4px 14px;border:1px solid var(--border);border-radius:4px;background:transparent;cursor:pointer;font-size:11px;color:var(--text3);">${legacyBTag} 对比文档</button>`;
    html += `</div></div>`;
    html += `</div>`; // 关闭 vc-sticky-header

    // 滚动区域：PDF 页面预览（可上下滚动看完整文档）
    html += `<div class="vc-scroll-area" id="vcPagesContainer" data-diff-page="${diffPage}"><div style="padding:20px;text-align:center;color:var(--text3);">正在加载文档预览...</div></div>`;
    html += `<div class="vc-bottom-actions"><button class="vc-back-btn" onclick="closeVersionCompare()">⟵ 返回差异列表</button></div>`;

    panel.innerHTML = html;

    // 获取页数并渲染
    const [newInfo, oldInfo] = await Promise.all([
        fetch(`/api/documents/review/info?task_id=${reviewTaskId}`).then(r=>r.json()),
        fetch(`/api/documents/review/old/info?task_id=${reviewTaskId}`).then(r=>r.json()),
    ]);
    if(panel.dataset.viewToken!==viewToken || !panel.classList.contains('show'))return;
    panel.dataset.newPages = newInfo.page_count||0;
    panel.dataset.oldPages = oldInfo.page_count||0;
    panel.dataset.newDiffPage=newDiffPage;
    panel.dataset.oldDiffPage=oldDiffPage;
    panel.dataset.diffPage=newDiffPage;

    await renderVcPages('new',previewToken);
}

// 解析 location 字符串 → {page, section}
function parseLoc(loc){
    if(!loc)return {page:1,section:'',raw:''};
    const page=(loc.match(/第\s*(\d+)\s*页/)||[])[1];
    const sec=(loc.match(/§([\d.]+)/)||[])[1];
    return {page:page?parseInt(page):1,section:sec?`§${sec}`:'',raw:loc};
}

// 将差异页滚动到预览容器可视区域。图片懒加载会改变前置页面高度，
// 因此在首帧、短延迟和目标图片加载后各尝试一次。
function focusVcPage(container,target){
    if(!container||!target)return;
    const focus=()=>{
        if(!target.isConnected)return;
        container.scrollTo({top:Math.max(0,target.offsetTop-4),behavior:'auto'});
    };
    requestAnimationFrame(()=>{focus();setTimeout(focus,80);setTimeout(focus,350);});
}

// 渲染指定侧（old/new）的 PDF 预览，默认显示差异页前后各 3 页
async function renderVcPages(side,viewToken=Number(document.getElementById('versionComparePanel')?.dataset.previewToken)||reviewPreviewViewToken){
    const panel = document.getElementById('versionComparePanel');
    if(!panel)return false;
    const container = document.getElementById('vcPagesContainer');
    if(!container)return false;
    const pages = parseInt(panel.dataset[side==='new'?'newPages':'oldPages'])||0;
    const sidePageKey=side==='new'?'newDiffPage':'oldDiffPage';
    const rawDiffPage=parseInt(panel.dataset[sidePageKey]||panel.dataset.diffPage)||1;
    const diffPage=Math.max(1,Math.min(pages||rawDiffPage,rawDiffPage));
    // 版本差异的页面预览统一使用右侧固定预览，审核面板只保留差异详情。
    container.innerHTML=`<div style="padding:12px 14px;color:var(--text3);font-size:11px;text-align:center;">页面预览已移至右侧固定文档预览：${side==='new'?'N 新文档':'对比文档'}，第 ${diffPage} 页</div>`;
    return showReviewComparePreview(side,diffPage,'version',viewToken);

    if(!pages){container.innerHTML='<div style="padding:20px;text-align:center;color:#aaa;">文档不可用</div>';return;}

    // 检测是否显示全部页面还是只显示差异页
    const showAll = panel.dataset.showAll === '1';
    const previewRadius = 3;
    const startPage = Math.max(1,diffPage-previewRadius);
    const endPage = Math.max(startPage,Math.min(pages,diffPage+previewRadius));
    const pagesToShow = showAll ? Array.from({length:pages},(_,i)=>i+1) : Array.from({length:Math.max(1,endPage-startPage+1)},(_,i)=>startPage+i);

    let html = '';
    // 标题
    html += `<div style="padding:6px 14px;font-size:11px;color:var(--text3);background:#fafafa;border-bottom:1px solid #eee;display:flex;justify-content:space-between;align-items:center;">`;
    const group=reviewResult?.compare_groups?.[parseInt(panel.dataset.groupIdx)||0];
    const sideName=side==='new'
        ? fmtDocName(newDocFile||reviewResult?.new_filename||'新文档',newDocHash,reviewResult?.new_doc_label||newDocLabel)
        : compareDocName(group);
    html += `<span>${side==='new'?'🆕 N 新文档':'📜 '+(docMap[group?.doc_id]||'B—')+' 对比文档'}：${esc(sideName)} — 第 ${diffPage} 页${showAll?`（共 ${pages} 页）`:`（前后各 ${previewRadius} 页）`}</span>`;
    html += `<button onclick="toggleVcShowAll()" style="background:none;border:1px solid var(--border);border-radius:3px;padding:2px 10px;cursor:pointer;font-size:10px;">${showAll?'收起':'查看全部'}</button>`;
    html += `</div>`;

    for(const p of pagesToShow){
        const gid = document.getElementById('versionComparePanel') ? document.getElementById('versionComparePanel').dataset.groupDocId || '' : '';
        const gq = gid ? '&doc_id=' + encodeURIComponent(gid) : '';
        // 页面图高亮：new 侧用新版差异文本，old 侧用旧版差异文本（与 QA 预览一致，截断 50 字符）
        const vcIdx = parseInt(panel.dataset.vcIdx)||0;
        const groups = (reviewResult && reviewResult.compare_groups) || [];
        let vcs = (groups[parseInt(panel.dataset.groupIdx)||0] && groups[parseInt(panel.dataset.groupIdx)||0].version_changes) || [];
        if(!vcs.length && reviewResult && Array.isArray(reviewResult.version_changes)) vcs = reviewResult.version_changes;
        const vc = vcs[vcIdx] || null;
        const hlText = vc ? diffQueryText(vc.new_text||'',vc.old_text||'',side==='new'?'a':'b') : '';
        const hl = hlText ? '&highlight=' + encodeURIComponent(hlText.slice(0,50)) : '';
        const imgUrl = side==='new'
            ? `/api/documents/review/page?task_id=${reviewTaskId}&page=${p}${hl}`
            : `/api/documents/review/old/page?task_id=${reviewTaskId}&page=${p}${gq}${hl}`;
        html += `<div class="vc-page-wrap${p===diffPage?' vc-page-active':''}" data-page="${p}">`;
        if(pagesToShow.length>1) html += `<div class="vc-page-num">第 ${p} 页</div>`;
        html += `<img src="${imgUrl}" loading="lazy" style="width:100%;display:block;" onload="if(this.parentElement.classList.contains('vc-page-active'))focusVcPage(document.getElementById('vcPagesContainer'),this.parentElement)" onerror="this.parentElement.innerHTML='<div style=\'padding:20px;text-align:center;color:#aaa;\'>第${p}页加载失败</div>'"></div>`;
    }
    container.innerHTML = html;

    const target = container.querySelector('.vc-page-wrap.vc-page-active');
    focusVcPage(container,target);
}

function toggleVcShowAll(){
    const panel = document.getElementById('versionComparePanel');
    if(!panel)return;
    panel.dataset.showAll = panel.dataset.showAll==='1'?'0':'1';
    if(reviewViewState)reviewViewState.showAll=panel.dataset.showAll==='1';
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
    if(reviewViewState)reviewViewState={...reviewViewState,side};
    const previewToken=nextReviewPreviewViewToken();
    panel.dataset.previewToken=String(previewToken);
    return renderVcPages(side,previewToken);
}

// 从 location 描述中提取页码；支持“第 N 页”、页码范围和段落回退。
function extractPage(loc){
    if(!loc)return 1;
    const page=(String(loc).match(/第\s*(\d+)/)||[])[1];
    if(page)return parseInt(page);
    const paragraph=(String(loc).match(/段落#(\d+)/)||[])[1];
    return paragraph?Math.max(1,Math.ceil(parseInt(paragraph)/2)):1;
}
function extractPageFromLoc(loc){return extractPage(loc);}

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

function showGroupConflict(gi,ci){
    const g=reviewResult?.compare_groups?.[gi];
    const c=g?.inconsistencies?.[ci];
    if(!c)return;
    window.__conflictGroupIdx=gi;
    window.__conflictIdx=ci;
    window.__suspectIdx=-1;
    document.querySelectorAll('.conflict-item,.suspect-item').forEach(el=>el.classList.toggle('selected',Number(el.dataset.gi)===gi&&Number(el.dataset.ci)===ci));
    document.getElementById('conflictList').classList.add('collapsed');
    const panel=document.getElementById('comparePanel');
    panel.classList.add('show');
    const bRef=c.doc_b_id||c.doc_b?.file||c.doc_b_file||'';
    const bTag=docMap[bRef]||'B—';
    const bFile=compareDocName(g)||resolveFileName(bRef)||'?';
    const tabsEl=document.getElementById('cmpTabs');
    tabsEl.innerHTML=`<button id="cmpTabA" class="active" onclick="compareShowTab('a')">N 新文档</button>`;
    tabsEl.innerHTML+=`<button id="cmpTabB" onclick="compareShowTab('b')">${esc(bTag)} ${esc(bFile)} 已有文档</button>`;
    tabsEl.innerHTML+=`<span class="compare-close" onclick="closeCompare()" title="返回列表" style="margin-left:auto;cursor:pointer;color:var(--text3);font-size:12px;display:flex;align-items:center;gap:3px;"><span style="font-size:14px;">⟵</span> 返回列表</span>`;
    compareShowTab('a');
}

function showGroupSuspect(gi,si){
    const g=reviewResult?.compare_groups?.[gi];
    const c=g?.suspects?.[si];
    if(!c)return;
    window.__conflictGroupIdx=gi;
    window.__conflictIdx=-1;
    window.__suspectIdx=si;
    document.querySelectorAll('.conflict-item,.suspect-item').forEach(el=>el.classList.toggle('selected',Number(el.dataset.gi)===gi&&Number(el.dataset.si)===si));
    document.getElementById('conflictList').classList.add('collapsed');
    const panel=document.getElementById('comparePanel');
    panel.classList.add('show');
    const bRef=c.doc_b_id||c.doc_b?.file||c.doc_b_file||'';
    const bTag=docMap[bRef]||'B—';
    const bFile=compareDocName(g)||resolveFileName(bRef)||'?';
    const tabsEl=document.getElementById('cmpTabs');
    tabsEl.innerHTML=`<button id="cmpTabA" class="active" onclick="compareShowTab('a')">N 新文档</button>`;
    tabsEl.innerHTML+=`<button id="cmpTabB" onclick="compareShowTab('b')">${esc(bTag)} ${esc(bFile)} 已有文档</button>`;
    tabsEl.innerHTML+=`<span class="compare-close" onclick="closeCompare()" title="返回列表" style="margin-left:auto;cursor:pointer;color:var(--text3);font-size:12px;display:flex;align-items:center;gap:3px;"><span style="font-size:14px;">⟵</span> 返回列表</span>`;
    compareShowTab('a');
}

function selectConflict(idx){
window.__conflictGroupIdx=-1;
window.__conflictIdx=idx;
window.__suspectIdx=-1;
document.querySelectorAll('.conflict-item').forEach((el,i)=>el.classList.toggle('selected',i===idx));
document.getElementById('conflictList').classList.add('collapsed');
const c=reviewResult.inconsistencies[idx];
const panel=document.getElementById('comparePanel');
panel.classList.add('show');

// 构建 tab 栏（保留关闭按钮）
// A 侧固定为 N（新文档），B 侧用已有文档的简短名（B1/B2）
const bRef=c.doc_b_id||c.doc_b?.file||c.doc_b_file||'';
const bTag=docMap[bRef]||'B—';
const bFile=resolveFileName(bRef) || '?';
const tabsEl=document.getElementById('cmpTabs');
tabsEl.innerHTML=`<button id="cmpTabA" class="active" onclick="compareShowTab('a')">N 新文档</button>`;
tabsEl.innerHTML+=`<button id="cmpTabB" onclick="compareShowTab('b')">${esc(bTag)} ${esc(bFile)} 已有文档</button>`;
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

function diffQueryText(textA,textB,side){
    if(!textA||!textB)return (side==='a'?textA:textB).slice(0,120);
    let p=0;const minLen=Math.min(textA.length,textB.length);
    while(p<minLen&&textA[p]===textB[p])p++;
    let s=0;
    while(s<minLen-p&&textA[textA.length-1-s]===textB[textB.length-1-s])s++;
    const source=side==='a'?textA:textB;
    const mid=source.slice(p,source.length-s);
    return (mid||source).slice(0,120);
}

async function showReviewComparePreview(side,page,kind,viewToken=reviewPreviewViewToken){
    if(!isReviewPreviewViewCurrent(viewToken))return false;
    const target=side==='a'||side==='new'?'n':'b';
    const context=reviewComparisonContext(kind==='version'?'version':'conflict');
    // 差异点击时同时启动 N/B，单侧失败不应阻塞另一侧显示。
    setReviewPreviewFocus(target);
    const refs={
        n:resolveReviewPreviewRef('n',context),
        b:resolveReviewPreviewRef('b',context),
    };
    const pages={
        n:target==='n'?(Number(page)||reviewRefPage('n',context)||1):(reviewRefPage('n',context)||1),
        b:target==='b'?(Number(page)||reviewRefPage('b',context)||1):(reviewRefPage('b',context)||1),
    };
    const loads=Object.entries(refs).map(([pane,ref])=>{
        if(!ref){
            reviewPaneSetMessage(pane,pane==='n'?'N 文档信息缺失':'B 对比文档信息缺失','var(--danger)');
            console.warn('[review preview] missing pane ref',{pane,kind,context});
            return Promise.resolve(false);
        }
        return loadReviewPane(pane,ref,pages[pane],context,viewToken).catch(err=>{
            console.warn('[review preview] pane load rejected',{pane,ref,error:err});
            return false;
        });
    });
    const results=await Promise.all(loads);
    return isReviewPreviewViewCurrent(viewToken)&&results.every(Boolean);
}

async function compareShowTab(side){
const viewToken=nextReviewPreviewViewToken();
const items=document.querySelectorAll('.conflict-item,.suspect-item');
const gi=Number(window.__conflictGroupIdx),ci=Number(window.__conflictIdx),si=Number(window.__suspectIdx);
const idx=Array.from(items).findIndex(el=>el.classList.contains('selected'));
const group=(Number.isInteger(gi)&&gi>=0)?reviewResult?.compare_groups?.[gi]:null;
const c=group
    ? (Number.isInteger(si)&&si>=0 ? group.suspects?.[si] : group.inconsistencies?.[ci])
    : reviewResult.inconsistencies[idx<0?0:idx];
if(!c)return;

// 更新 tab 按钮高亮
const tabs=document.querySelectorAll('#cmpTabs button');
if(tabs[0])tabs[0].classList.toggle('active', side==='a');
if(tabs[1])tabs[1].classList.toggle('active', side==='b');

const body=document.getElementById('compareBody');
const bTag=group?(docMap[group.doc_id]||'B—'):'B';
const aSays=normalizeConflictText(c.doc_a?.says||c.doc_a_says||'',bTag);
const bSays=normalizeConflictText(c.doc_b?.says||c.doc_b_says||'',bTag);
const aLoc=c.doc_a?.location||c.doc_a_location||'';
const bLoc=c.doc_b?.location||c.doc_b_location||'';
const loc = side==='a'?aLoc:bLoc;
const page = extractPage(loc)||1;

// 文字差异高亮（sticky 固定在顶部）
    const textDiffHtml = `<div class="diff-compare">`+
    `<div class="diff-row"><span class="diff-label" onclick="compareShowTab('a')">N</span><span class="diff-text">${diffMark(aSays,bSays,'a')}</span></div>`+
    `<div class="diff-row"><span class="diff-label" onclick="compareShowTab('b')">${esc(bTag)}</span><span class="diff-text">${diffMark(aSays,bSays,'b')}</span></div>`+
    `</div>`;

let imgBase,infoUrl;
const hlText=diffQueryText(aSays,bSays,side==='a'?'a':'b');
const hl=hlText?'&highlight='+encodeURIComponent(hlText):'';
if(side==='a'){
    // N 侧 → 新文档（待审核），使用 review/page API
    imgBase=`/api/documents/review/page?task_id=${encodeURIComponent(reviewTaskId)}`;
    infoUrl=`/api/documents/review/info?task_id=${encodeURIComponent(reviewTaskId)}`;
} else {
    // B 侧 → 已有文档（在知识库中），使用 documents/page API
    const bRef=c.doc_b_id||c.doc_b?.file||c.doc_b_file||'';
    const bFile=resolveFileName(bRef)||'';
    if(!bFile){
        body.innerHTML=`<div style="color:#aaa;padding:20px;text-align:center;">${textDiffHtml}<br>⚠️ 旧文档信息缺失，无法定位预览</div>`;
        return;
    }
    const fullName=docRefToId[bRef]||docRefToId[bFile]||Object.keys(docMap).find(k=>docMap[k]===bFile&&k.includes('#'))||bFile;
    const encodedName=encodeURIComponent(fullName);
    imgBase=`/api/documents/page?name=${encodedName}`;
    infoUrl=`/api/documents/info?name=${encodedName}`;
}

const compareToken=`${viewToken}-${side}-${gi}-${ci}`;
body.dataset.compareToken=compareToken;
// 构建可滚动的预览区域：文字差异固定，PDF 页面连续排列
const navBar=`<div style="padding:6px 10px;background:var(--card);border-bottom:1px solid var(--border);display:flex;align-items:center;gap:8px;font-size:11px;color:var(--text3);">
    <span id="cmpPageLabel">📄 当前第 ${page} 页</span>
    <div style="margin-left:auto;display:flex;gap:4px;align-items:center;">
        <button onclick="cmpPrevPage()" style="padding:2px 10px;border:1px solid var(--border);border-radius:3px;background:none;cursor:pointer;font-size:11px;">◀</button>
        <input type="number" id="cmpPageInput" value="${page}" min="1" style="width:40px;text-align:center;font-size:11px;" onchange="cmpGoToPage()" onkeydown="if(event.key==='Enter')cmpGoToPage()">
        <button onclick="cmpNextPage()" style="padding:2px 10px;border:1px solid var(--border);border-radius:3px;background:none;cursor:pointer;font-size:11px;">▶</button>
        <button id="cmpShowAllBtn" onclick="toggleCmpShowAll()" style="margin-left:6px;padding:2px 10px;border:1px solid var(--border);border-radius:3px;background:none;cursor:pointer;font-size:11px;">收起连续预览</button>
    </div></div>`;
body.innerHTML=`<div style="display:flex;flex-direction:column;width:100%;max-width:700px;gap:6px;">${textDiffHtml}${navBar}<div id="cmpPdfArea" style="flex:1;overflow-y:auto;"><div style="padding:20px;text-align:center;color:#aaa;">正在加载差异页附近预览...</div></div></div>`;
body.dataset.page=page;
body.dataset.pageCount='0';
body.dataset.imgBase=imgBase;
body.dataset.highlight=hl;
body.dataset.showAll='0';
await showReviewComparePreview(side,page,'conflict',viewToken);
if(!isReviewPreviewViewCurrent(viewToken)||body.dataset.compareToken!==compareToken)return;
return;
    /* hidden legacy image preview disabled; right-side N/B panes are authoritative
    let infoPromise=reviewPageInfoCache.get(infoUrl);
    if(!infoPromise){
        infoPromise=fetch(infoUrl).then(res=>{
            if(!res.ok)throw new Error(`info request failed: ${res.status}`);
            return res.json();
        });
        reviewPageInfoCache.set(infoUrl,infoPromise);
    }
    const info=await infoPromise;
    if(body.dataset.compareToken!==compareToken)return;
    body.dataset.pageCount=String(info.page_count||1);
}catch(err){
    if(body.dataset.compareToken!==compareToken)return;
    reviewPageInfoCache.delete(infoUrl);
    body.dataset.pageCount='1';
}
renderCmpPages();
    */
}

function renderCmpPages(){
    const body=document.getElementById('compareBody');
    const area=document.getElementById('cmpPdfArea');
    if(!body||!area)return;
    const count=Math.max(1,parseInt(body.dataset.pageCount||'1'));
    const current=Math.min(count,Math.max(1,parseInt(body.dataset.page||'1')));
    const showAll=body.dataset.showAll==='1';
    const pages=showAll?Array.from({length:count},(_,i)=>i+1):Array.from({length:Math.min(count,7)},(_,i)=>Math.max(1,Math.min(count,current-3))+i).filter(p=>p<=count);
    const base=body.dataset.imgBase||'';
    const separator=base.includes('?')?'&':'?';
    area.innerHTML=pages.map(p=>{
        // 只让当前差异页生成带高亮的 PNG；邻近页复用普通页面缓存。
        const pageHighlight=p===current?(body.dataset.highlight||''):'';
        return `<div class="cmp-page-wrap${p===current?' cmp-page-active':''}" data-page="${p}"><div class="cmp-page-num">第 ${p} 页</div><img src="${base}${separator}page=${p}${pageHighlight}" loading="lazy" style="width:100%;border-radius:4px;display:block;" onload="if(this.parentElement.classList.contains('cmp-page-active')){const a=document.getElementById('cmpPdfArea');a.scrollTo({top:Math.max(0,this.parentElement.offsetTop-4),behavior:'auto'});}" onerror="this.outerHTML='<div style=\'padding:20px;text-align:center;color:#aaa;\'>第${p}页加载失败</div>'"></div>`;
    }).join('');
    const label=document.getElementById('cmpPageLabel');
    if(label)label.textContent=showAll?`📄 当前第 ${current} 页（共 ${count} 页）`:`📄 第 ${current} 页`;
    const input=document.getElementById('cmpPageInput');
    if(input)input.value=current;
    const toggle=document.getElementById('cmpShowAllBtn');
    if(toggle)toggle.textContent=showAll?'收起连续预览':'查看全部页面';
    if(showAll){
        const active=area.querySelector('.cmp-page-active');
        if(active)area.scrollTo({top:Math.max(0,active.offsetTop-4),behavior:'auto'});
        requestAnimationFrame(()=>{
            const activeNow=area.querySelector('.cmp-page-active');
            if(activeNow)area.scrollTo({top:Math.max(0,activeNow.offsetTop-4),behavior:'auto'});
            setTimeout(()=>{const delayed=area.querySelector('.cmp-page-active');if(delayed)area.scrollTo({top:Math.max(0,delayed.offsetTop-4),behavior:'auto'});},300);
        });
    }
}

function toggleCmpShowAll(){
    const body=document.getElementById('compareBody');
    if(!body)return;
    body.dataset.showAll=body.dataset.showAll==='1'?'0':'1';
    renderCmpPages();
}

// 内容矛盾预览翻页
function cmpPrevPage(){const body=document.getElementById('compareBody');if(!body)return;const p=Math.max(1,parseInt(body.dataset.page||'1')-1);cmpSetPage(p);}
function cmpNextPage(){const body=document.getElementById('compareBody');if(!body)return;const count=parseInt(body.dataset.pageCount||'0');const p=parseInt(body.dataset.page||'1')+1;cmpSetPage(count?Math.min(count,p):p);}
function cmpGoToPage(){const inp=document.getElementById('cmpPageInput');if(!inp)return;cmpSetPage(Math.max(1,parseInt(inp.value)||1));}
function cmpSetPage(p){
    const body=document.getElementById('compareBody');if(!body)return;
    const count=parseInt(body.dataset.pageCount||'0');
    body.dataset.page=count?Math.min(count,Math.max(1,p)):Math.max(1,p);
    renderCmpPages();
}

function closeCompare(){
    nextReviewPreviewViewToken();
    document.getElementById('comparePanel').classList.remove('show');
    document.getElementById('conflictList').classList.remove('collapsed');
    document.querySelectorAll('.conflict-item').forEach(el=>el.classList.remove('selected'));
}
function closeVersionCompare(){
    nextReviewPreviewViewToken();
    const panel=document.getElementById('versionComparePanel');
    if(panel){
        panel.classList.remove('show');
        panel.replaceChildren();
        delete panel.dataset.vcIdx;
        delete panel.dataset.groupIdx;
        delete panel.dataset.groupDocId;
        delete panel.dataset.viewToken;
    }
    reviewViewState=null;
    document.querySelectorAll('.version-diff-item').forEach(el=>el.classList.remove('selected'));
    switchKbTab('review');
}

function exportReviewReport(){
    const phase=reviewResult?.phase||'';
    if(!reviewTaskId){alert('预审核任务不存在，请重新上传文档');return;}
    if(phase!=='done'&&phase!=='error'){
        alert('逐文档比对尚未完成，暂不能导出报告。');
        return;
    }
    const link=document.createElement('a');
    link.href=`/api/documents/review/${encodeURIComponent(reviewTaskId)}/report.html`;
    link.download=`review-report-${reviewTaskId}.html`;
    document.body.appendChild(link);
    link.click();
    link.remove();
}

async function confirmIngest(){
    if(reviewResult?.phase!=='done'||reviewResult?.incomplete||reviewResult?.cancelled){
        alert('逐文档比对尚未完成，不能确认入库；请等待审核结束。');
        return;
    }
    if(!reviewTaskId){alert('预审核任务不存在，请重新上传');return;}
    let confirmMode='new_primary';
    if(reviewResult?.existing_primary_doc_id){
        const useNew=await customConfirm(
            '预审核已完成。是否将上传的新版本设为当前版本？\n\n· 是：新版本参与问答，旧版本保留为历史版本\n· 否：保留旧版本参与问答，新版本保存为历史版本'
        );
        confirmMode=useNew?'new_primary':'keep_current';
    }
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
        const confirmForm=new FormData();
        confirmForm.append('mode',confirmMode);
        const res=await fetch(`/api/documents/review/${reviewTaskId}/confirm`,{method:'POST',body:confirmForm});
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
    const phase=reviewResult?.phase||'';
    if(!reviewTaskId)return;
    if(phase!=='done'&&phase!=='error'){
        alert('逐文档比对尚未完成，暂不能取消入库。');
        return;
    }
    const noBtn=document.getElementById('reviewRejectBtn');
    if(noBtn)noBtn.disabled=true;
    try{
        const res=await fetch(`/api/documents/review/${reviewTaskId}/reject`,{method:'POST'});
        if(!res.ok){const e=await res.json();throw new Error(e.detail||'取消入库失败');}
        hideReviewTab();resetUpload();
    }catch(err){
        if(noBtn)noBtn.disabled=false;
        alert(err.message);
    }
}

async function forceReReview(){
    // 优先复用服务器保留的上传文件；文件丢失时由错误页提供重新上传兜底。
    if(!reviewTaskId||!newDocFile){
        alert('没有可重跑的预审核任务，将打开重新上传。');
        startReupload();
        return;
    }
    if(!confirm('确定强制重新执行预审核？\n\n这将忽略缓存，完整重跑所有步骤（可能需要 1-2 分钟）。'))return;

    try {
        const res=await fetch(`/api/documents/review/${reviewTaskId}/rerun`,{method:'POST'});
        if(!res.ok){
            const e=await res.json().catch(()=>({}));
            const detail=e.detail||'重跑操作失败';
            alert(detail+'\n\n你仍可以选择“重新上传文档”从头执行。');
            if(res.status===400 && /文件|上传|不存在|重新上传/.test(detail))startReupload();
            return;
        }

        // 清空当前结果，等待新结果
        reviewResult=null;
        openCompareGroups.clear();
        updateReviewActionButtons();
        document.getElementById('conflictList').innerHTML='<div style="padding:20px;text-align:center;color:var(--text3);">⏳ 正在重新执行预审核...</div>';
        document.getElementById('comparePanel').classList.remove('show');

        // 重新连接 SSE 等待新结果
        connectSSE(reviewTaskId);
    } catch(e) {
        alert('重跑请求失败：'+e.message+'\n\n你仍可以选择“重新上传文档”从头执行。');
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

async function clearQaHistory(){
    if(!confirm('确定清空全部问答历史？此操作不可恢复。'))return;
    try{
        const res=await fetch('/api/qa/sessions',{method:'DELETE'});
        const data=await res.json().catch(()=>({}));
        if(!res.ok)throw new Error(data.detail||'清空历史失败');
        newQaSession();
        await refreshQaSessionList();
    }catch(e){
        alert(e.message||'清空历史失败');
    }
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
                // idx 是单条回答内的引用身份。即使多个证据来自同一文档和位置，
                // 也必须全部保留，否则正文 [n] 会失去对应的来源项。
                const allSources=(msg.sources||[]).map((s,i)=>({...s,idx:s.idx||i+1}));
                const sourceIdxs=new Set(allSources.map(s=>String(s.idx)));
                const cleanContent=stripOldSourceLines(msg.content||'');
                let html=renderMd(cleanContent);
                html=html.replace(
                    /\[(\d+)\](?![\w\s]*<\/a>)/g,
                    (m, p1) => sourceIdxs.has(String(p1))
                        ? `<a href="#ref-${p1}" class="ref-link" data-ref="${p1}" onclick="event.preventDefault();scrollToRef(${p1});return false;">[${p1}]</a>`
                        : m
                );
                if(allSources.length){
                    const uniqueSources=allSources;
                    const fileIds={};let fileIdx=1;
                    let srcHtml=`<div class="sources"><div class="src-title">📎 来源（${uniqueSources.length} 条）</div>`;
                    for(const s of uniqueSources){
                        const id=s.idx?('B'+s.idx):'B'+(fileIdx++);
                        if(!s.idx&&!fileIds[s.source_file]) fileIds[s.source_file]=id;
                        const displayId=s.idx?('B'+s.idx):fileIds[s.source_file];
                        srcHtml+=`<div class="src-item" id="ref-${displayId.replace('B','')}" data-idx="${escA(String(s.idx))}" data-file="${escA(s.source_file||'')}" data-loc="${escA(s.location||'')}">`;
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
                        const srcData=allSources.find(s=>String(s.idx)===this.dataset.idx);
                        const hlText=srcData?srcData.text:'';
                        openQaPreview(this.dataset.file,this.dataset.loc,hlText,this.dataset.title);
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
        let allSources=[];  // 提到外层作用域，供点击事件访问
        let sourceIdxs=new Set();  // 仅有真实来源的 [n] 才渲染为可点击链接
        let idxToDoc={};    // 引用编号 idx → 文档级编号（B1/B2...），正文 [n] 后附 Bn
        let docIds={}, docOrder=[];  // 文档级编号映射（外层声明，legend 渲染共用）
        if(data.sources&&data.sources.length){
            // idx 是 LLM 上下文和 sources 共同使用的引用身份。不能按文档+位置
            // 去重：同一表格的整表块和多个行级块可能共享 location，但分别对应 [n]。
            allSources=data.sources.map((s,i)=>({...s,idx:s.idx||i+1}));
            sourceIdxs=new Set(allSources.map(s=>String(s.idx)));
            // hash 文件名（底层存储名，如 fcc13f4b....pdf）→ 友好文件名
            const friendlyName=(did)=>{
                const m=(did||'').match(/^([0-9a-fA-F]{64})\.pdf(#.*)?$/);
                if(m&&fileHashToName[m[1].toLowerCase()])return fileHashToName[m[1].toLowerCase()];
                return did||'';
            };
            // 文档级编号：优先用知识库列表的 docMap（B1/B2 与列表一致），
            // 未在列表中的文档按出现顺序补 B3/B4...
            for(const s of allSources){
                const did=s.doc_id||s.source_file||'';
                if(did&&!docIds[did]){
                    docIds[did]=docMap[did]||docMap[friendlyName(did)]||('B'+(docOrder.length+1));
                    docOrder.push(did);
                }
            }
            for(const s of allSources){
                idxToDoc[s.idx]=docIds[s.doc_id||s.source_file||'']||'';
            }
        }
        // 若 LLM 按引用指南使用了 [1] [2] 编号，转成可跳转到底部来源的链接，
        // 并在编号后附文档编号（如 [1]B1），一眼看清来源文档
        answerHtml=answerHtml.replace(
            /\[(\d+)\](?![\w\s]*<\/a>)/g,
            (m, p1) => {
                if(!sourceIdxs.has(String(p1)))return m;
                const bn=idxToDoc[p1]||'';
                const docTag=bn?('<span class="ref-doc">'+bn+'</span>'):'';
                return '<a href="#ref-'+p1+'" class="ref-link" data-ref="'+p1+'" onclick="event.preventDefault();scrollToRef('+p1+');return false;">['+p1+']'+docTag+'</a>';
            }
        );
        // 旧格式兼容: （（...来源：...））
        answerHtml=answerHtml.replace(
            /（（资料中还[^\uff09]{0,80}?来源[:：][^\uff09]{0,80}?））/g,
            (m) => `<blockquote class="answer-note">${esc(m)}</blockquote>`
        );
        let html=answerHtml;
        if(data.sources&&data.sources.length){
            // （来源归一化与文档编号构建已前移到 answerHtml 渲染之前）
            // 底部 legend：按文档分组，块标题=Bn+文件名[hash]+tag，块内列出 [idx] 引用项
            let srcHtml=`<div class="sources"><div class="src-title">📎 引用来源</div>`;
            for(const did of docOrder){
                const bn=docIds[did];
                const docSources=allSources.filter(x=>(x.doc_id||x.source_file||'')===did);
                // 文件名展示：doc_id 形如 '友好名.pdf#29A17952'；
                // 旧数据可能为磁盘哈希文件名（如 fcc13f4b....pdf），反查 fileHashToName 得友好名
                const parts=did.split('#');
                let displayFile=parts[0]||did;
                const mf=displayFile.match(/^([0-9a-fA-F]{64})\.pdf$/);
                if(mf&&fileHashToName[mf[1].toLowerCase()])displayFile=fileHashToName[mf[1].toLowerCase()];
                const hash8=parts[1]||(mf?mf[1].slice(-8).toUpperCase():'');
                const firstSrc=docSources[0];
                if(firstSrc&&firstSrc.filename)displayFile=firstSrc.filename;
                srcHtml+=`<div class="src-doc-block"><div class="src-doc-title"><span class="src-id">${bn}</span><span class="src-file">${esc(displayFile)}</span>`;
                if(hash8)srcHtml+=` <span class="src-file-hash">#${esc(hash8)}</span>`;
                if(firstSrc&&firstSrc.label)srcHtml+=` <span class="doc-label-tag">${esc(firstSrc.label)}</span>`;
                if(firstSrc&&firstSrc.is_primary)srcHtml+=` <span class="doc-status-tag primary">当前版本</span>`;
                srcHtml+=`</div>`;
                for(const s of docSources){
                    srcHtml+=`<div class="src-item" id="ref-${s.idx}" data-idx="${escA(String(s.idx))}" data-file="${escA(s.source_file||'')}" data-loc="${escA(s.location||'')}" data-title="${escA(s.filename||did)}">`;
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
                    const srcData=allSources.find(s=>String(s.idx)===this.dataset.idx);
                    const hlText=srcData?srcData.text:'';
                    openQaPreview(this.dataset.file,this.dataset.loc,hlText,this.dataset.title);
                });
            });
        }
        await refreshQaSessionList();
    }catch(e){console.error('QA render error:', e);const last=document.querySelector('.qa-messages .msg.bot:last-child');if(last)last.innerHTML='<span style="color:var(--danger);">请求失败</span>';}
}
function appendMsg(role,html){
    const el=document.createElement('div');el.className='msg '+role;el.innerHTML=html;
    document.getElementById('qaMessages').appendChild(el);el.scrollIntoView({behavior:'smooth'});
}

// QA 预览面板 — 服务端高亮渲染
let qaCurrentFile='', qaCurrentPage=1, qaTotalPages=1, qaHighlightText='';

async function openQaPreview(file, location, highlightText, displayTitle=''){
    const panel=document.getElementById('qaPreview');
    panel.classList.add('show');
    document.getElementById('qaPreviewTitle').textContent=displayTitle||file.split('#')[0];
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
loadReviewPanelWidth();
loadReviewStackHeight();
loadReviewPreviewVisibility();
initReviewResize();
reviewLayoutReady=loadReviewLayoutFromServer();
refreshDocList();
newQaSession();
refreshQaSessionList();
// 恢复活跃任务
(async function(){
    try{
        const res=await fetch('/api/documents/review/active',{cache:'no-store'});
        const d=await res.json();
        if(!d.task_id)return;
        newDocFile=d.filename;activeFileName=d.filename;reviewTaskId=d.task_id;newDocHash=d.file_hash||'';newDocLabel=d.label||d.result?.new_doc_label||'';
        refreshDocList();
        if((d.status==='done'||d.status==='error')&&d.result){
            showTerminalReviewResult(d);
        } else if(d.status==='pending'||d.status==='running'||d.status==='paused'){
            uploadZone.classList.add('disabled');
            document.getElementById('stepArea').classList.add('show');
            document.getElementById('stepTitle').textContent='预审核: '+fmtDocName(d.filename||'',newDocHash,newDocLabel);
            if(d.result&&typeof d.result==='object'){
                reviewResult={...d.result,status:d.status};
                if(d.old_version_filepath) reviewResult.old_version_filepath=d.old_version_filepath;
                if(d.old_doc_filename) reviewResult.old_doc_filename=d.old_doc_filename;
                const compareStarted=d.result.phase==='comparing' || (d.result.compare_groups||[]).length>0;
                if(compareStarted){
                    updatePartialReviewButton();
                    buildReviewPanel();
                }
            }
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
  applyReviewLayout(_settingsConfig.review_layout);
  document.getElementById('review-layout').value=reviewLayout;
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
  const judge=_settingsConfig.judge||{};
  document.getElementById('judge-prompt_template').value=judge.prompt_template||'';
  document.getElementById('judge-prompt_file').value=judge.prompt_file||'';
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
  const layout=normalizeReviewLayout(document.getElementById('review-layout').value);
  // 构造更新
  const updates={
    review_layout:layout,
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
    judge:{
      prompt_template:document.getElementById('judge-prompt_template').value,
      prompt_file:document.getElementById('judge-prompt_file').value.trim(),
    },
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
    const data=await res.json();
    const notes=[data.message||'配置已更新'];
    if(data.reset_knowledge_base_required){
      notes.push('Embedding 配置已变化；重启后请重置知识库并重新入库。');
    }
    applyReviewLayout(layout);
    notes.push('预审核布局已立即应用。');
    st.textContent=(data.restart_required?'⚠️ ':'✅ ')+notes.join(' ');
    _settingsConfig=null; // 下次进入页面时重新加载
  }catch(e){
    st.textContent='保存失败: '+e.message;
  }
}