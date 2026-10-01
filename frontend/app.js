const state = {
  projects: [], project: null, settings: null, health: null,
  route: location.hash.slice(1) || 'dashboard', activeJob: null,
  upload: null, assetResults: [], assetFilter: 'all', library: [], selectedVersionId: null,
  compareIds: [], currentPlan: null,
};

const icons = {
  home: '<path d="M3 11.5 12 4l9 7.5"/><path d="M5.5 10v10h13V10M9 20v-6h6v6"/>',
  folder: '<path d="M3 6.5h6l2 2h10v10.5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M3 10h18"/>',
  clapper: '<path d="M4 8h16v12H4zM4 12h16M5 4h16l-1 4H4zM9 4 7 8m7-4-2 4m7-4-2 4"/>',
  timeline: '<path d="M4 6h16M4 12h10M4 18h16"/><circle cx="8" cy="6" r="2"/><circle cx="17" cy="12" r="2"/><circle cx="11" cy="18" r="2"/>',
  image: '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="m4 18 5-5 3 3 2-2 6 5"/>',
  history: '<path d="M4 6v5h5"/><path d="M5.5 16a8 8 0 1 0-.8-8.9L4 11"/><path d="M12 8v5l3 2"/>',
  sparkles: '<path d="m12 3 1.2 3.8L17 8l-3.8 1.2L12 13l-1.2-3.8L7 8l3.8-1.2zM19 14l.7 2.3L22 17l-2.3.7L19 20l-.7-2.3L16 17l2.3-.7zM5 14l.7 2.3L8 17l-2.3.7L5 20l-.7-2.3L2 17l2.3-.7z"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4V21a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14H2.8v-4H3a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1a1.7 1.7 0 0 0 1.9.3A1.7 1.7 0 0 0 10 3V2.8h4V3a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1z"/>',
  shield: '<path d="M12 3 5 6v5c0 4.6 2.8 8.3 7 10 4.2-1.7 7-5.4 7-10V6z"/><path d="m9 12 2 2 4-4"/>',
  plus: '<path d="M12 5v14M5 12h14"/>', chevron: '<path d="m8 10 4 4 4-4"/>',
  refresh: '<path d="M20 6v5h-5"/><path d="M18.5 16a8 8 0 1 1 .8-8.8L20 11"/>',
  menu: '<path d="M4 6h16M4 12h16M4 18h16"/>', x: '<path d="m6 6 12 12M18 6 6 18"/>',
  arrow: '<path d="M5 12h14M14 7l5 5-5 5"/>', play: '<path d="m9 7 8 5-8 5z"/>',
  upload: '<path d="M12 16V4m-5 5 5-5 5 5M4 20h16"/>',
  film: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M7 5v14M17 5v14M3 9h4m10 0h4M3 15h4m10 0h4"/>',
  check: '<path d="m5 12 4 4L19 6"/>', lock: '<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/>',
  grip: '<circle cx="9" cy="6" r="1"/><circle cx="15" cy="6" r="1"/><circle cx="9" cy="12" r="1"/><circle cx="15" cy="12" r="1"/><circle cx="9" cy="18" r="1"/><circle cx="15" cy="18" r="1"/>',
  edit: '<path d="M13.5 6.5 17.5 10.5M4 20l4.5-1 10-10a2.8 2.8 0 0 0-4-4l-10 10z"/>',
  trash: '<path d="M4 7h16M9 7V4h6v3m3 0-1 14H7L6 7m4 4v6m4-6v6"/>',
  brain: '<path d="M9.5 4.5A3 3 0 0 0 5 7a3 3 0 0 0 .5 5.5A3.2 3.2 0 0 0 9 18a3 3 0 0 0 3-3V7.5a3 3 0 0 0-2.5-3zM14.5 4.5A3 3 0 0 1 19 7a3 3 0 0 1-.5 5.5A3.2 3.2 0 0 1 15 18a3 3 0 0 1-3-3V7.5a3 3 0 0 1 2.5-3z"/>',
  download: '<path d="M12 4v12m-5-5 5 5 5-5M4 20h16"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m16 16 4 4"/>',
  music: '<path d="M9 18V6l10-2v12"/><circle cx="6" cy="18" r="3"/><circle cx="16" cy="16" r="3"/>',
  volume: '<path d="M5 9H2v6h3l5 4V5zM14 9a4 4 0 0 1 0 6m3-9a8 8 0 0 1 0 12"/>',
  file: '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v5h5"/>', eye: '<path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12z"/><circle cx="12" cy="12" r="2.5"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>', star: '<path d="m12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2-5.6-3-5.6 3 1.1-6.2L3 9.6l6.2-.9z"/>',
  alert: '<path d="M12 4 2.5 20h19z"/><path d="M12 9v5m0 3h.01"/>', info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10h.01"/>',
  external: '<path d="M14 4h6v6m0-6-9 9"/><path d="M18 13v7H4V6h7"/>',
  copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V4H4v12h4"/>',
};

function icon(name) { return `<svg aria-hidden="true" viewBox="0 0 24 24">${icons[name] || icons.file}</svg>`; }
function hydrateIcons(root = document) { root.querySelectorAll('[data-icon]').forEach(el => { el.innerHTML = icon(el.dataset.icon); }); }
function e(value = '') { return String(value).replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c])); }
function bytes(value = 0) { if (!value) return '0 B'; const units=['B','KB','MB','GB','TB']; const i=Math.min(Math.floor(Math.log(value)/Math.log(1024)),4); return `${(value/1024**i).toFixed(i?1:0)} ${units[i]}`; }
function duration(value = 0) { value=Math.max(0, Math.round(value)); const h=Math.floor(value/3600), m=Math.floor(value%3600/60), s=value%60; return h ? `${h}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}` : `${m}:${String(s).padStart(2,'0')}`; }
function date(value) { if (!value) return '—'; const d=new Date(value); return new Intl.DateTimeFormat(undefined,{month:'short',day:'numeric',year:d.getFullYear()!==new Date().getFullYear()?'numeric':undefined}).format(d); }
function timeAgo(value) { if (!value) return ''; const seconds=(Date.now()-new Date(value))/1000; if(seconds<60)return 'just now'; if(seconds<3600)return `${Math.floor(seconds/60)}m ago`; if(seconds<86400)return `${Math.floor(seconds/3600)}h ago`; return date(value); }
function statusLabel(workflow='upload') { return ({upload:'Building',ready:'Ready to edit',review:'Needs review',complete:'Approved'})[workflow] || workflow; }
function versionOf(id) { return state.project?.versions?.find(v => v.id === id); }
function selectedVersion() { const list=state.project?.versions || []; return versionOf(state.selectedVersionId) || list[list.length-1] || null; }

async function api(path, options = {}) {
  const init = {...options, headers: {...(options.headers || {})}};
  if (init.body && !(init.body instanceof FormData) && typeof init.body !== 'string') {
    init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(init.body);
  }
  const response = await fetch(path, init);
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try { const data=await response.json(); detail = Array.isArray(data.detail) ? data.detail.map(x=>x.msg).join(', ') : data.detail || detail; } catch (_) {}
    throw new Error(detail);
  }
  if (response.status === 204) return null;
  return response.json();
}

function toast(title, message = '', type = 'success') {
  const item=document.createElement('div'); item.className=`toast ${type}`;
  item.innerHTML=`<span>${icon(type==='error'?'alert':'check')}</span><div><strong>${e(title)}</strong><p>${e(message)}</p></div>`;
  document.querySelector('#toast-region').appendChild(item);
  setTimeout(()=>item.remove(), 5200);
}

async function loadAll(projectId = state.project?.id) {
  const [projects, health, settings, library] = await Promise.all([api('/api/projects'), api('/api/health'), api('/api/settings'), api('/api/library')]);
  state.projects=projects; state.health=health; state.settings=settings; state.library=library;
  if (projectId) {
    try { state.project=await api(`/api/projects/${encodeURIComponent(projectId)}`); localStorage.setItem('cutwise-project', projectId); }
    catch (_) { state.project=null; localStorage.removeItem('cutwise-project'); }
  } else if (!state.project) {
    const saved=localStorage.getItem('cutwise-project');
    const choice=projects.find(p=>p.id===saved) || projects[0];
    if (choice) state.project=await api(`/api/projects/${encodeURIComponent(choice.id)}`);
  }
  updateChrome(); render();
}

function updateChrome() {
  document.querySelector('#active-project-name').textContent=state.project?.name || 'No project selected';
  document.querySelectorAll('.project-required').forEach(el=>el.classList.toggle('disabled', !state.project));
  const h=state.health, el=document.querySelector('#system-status');
  if (h) el.innerHTML=`<span class="status-dot ${h.ffmpeg&&h.ffprobe?'ok':''}"></span><div><strong>${h.ffmpeg&&h.ffprobe?'Local engine ready':'FFmpeg setup needed'}</strong><small>${h.ffmpeg?'FFmpeg detected':'Open Settings for help'}</small></div>`;
  const menu=document.querySelector('#project-menu');
  menu.innerHTML=state.projects.length ? state.projects.map(p=>`<button data-action="switch-project" data-id="${e(p.id)}"><span><strong>${e(p.name)}</strong><small>${p.clip_count||0} clips · ${p.version_count||0} versions</small></span></button>`).join('') : '<button data-action="new-project"><span><strong>Create your first project</strong><small>Start with a creative brief</small></span></button>';
}

function setRoute(route) {
  const allowed=['dashboard','projects','project','timeline','assets','history','ai-settings','settings'];
  route=allowed.includes(route)?route:'dashboard';
  if (['project','timeline','assets','history'].includes(route) && !state.project) route='projects';
  state.route=route;
  if(location.hash.slice(1)!==route) history.replaceState(null,'',`#${route}`);
  document.querySelectorAll('.view').forEach(v=>v.classList.toggle('active',v.dataset.view===route));
  document.querySelectorAll('.main-nav a').forEach(a=>a.classList.toggle('active',a.dataset.route===route));
  document.body.classList.remove('menu-open');
  render(); document.querySelector('#view-root').focus({preventScroll:true}); window.scrollTo({top:0,behavior:'smooth'});
}

function projectCard(p) {
  return `<article class="card project-card" data-action="open-project" data-id="${e(p.id)}">
    <div class="project-cover"><span class="status-badge ${e(p.workflow)}">${e(statusLabel(p.workflow))}</span><span class="reel"></span></div>
    <div class="project-info"><h3>${e(p.name)}</h3><p>${e(p.style || 'YouTube edit')}</p>
      <div class="project-meta"><span>${icon('film')} ${p.clip_count ?? p.clips?.length ?? 0} clips</span><span>${icon('history')} ${p.version_count ?? p.versions?.length ?? 0} versions</span><span>${icon('clock')} ${timeAgo(p.updated_at)}</span></div>
    </div></article>`;
}

function renderDashboard() {
  const root=document.querySelector('#view-dashboard');
  const projectCount=state.projects.length, clips=state.projects.reduce((n,p)=>n+(p.clip_count||p.clips?.length||0),0), versions=state.projects.reduce((n,p)=>n+(p.version_count||p.versions?.length||0),0), approved=state.projects.filter(p=>p.workflow==='complete').length;
  root.innerHTML=`
    <div class="card welcome-strip"><div class="welcome-content"><p class="eyebrow accent">LOCAL-FIRST AI EDITING</p><h1>Turn raw footage into an edit worth approving.</h1><p>Set the story order, let AI shape the pacing, then review every frame. Nothing becomes final until you say so.</p><button class="btn btn-primary btn-large" data-action="new-project">Start a new edit ${icon('arrow')}</button></div><div class="welcome-visual"><div class="visual-frame"><div class="visual-play"><span>${icon('play')}</span></div><div class="visual-timeline"><span></span><span></span><span></span><span></span><span></span><span></span></div></div></div></div>
    <div class="grid grid-4">
      ${statCard('Projects',projectCount,'Local workspaces','folder')}${statCard('Source clips',clips,'Order always stays locked','film')}${statCard('Rendered versions',versions,'No version overwritten','history')}${statCard('Human approved',approved,'Final decisions are yours','check')}
    </div>
    <div class="section-title"><h2>Recent projects</h2><button class="text-button" data-route-link="projects">View all projects →</button></div>
    ${state.projects.length?`<div class="grid grid-3">${state.projects.slice(0,6).map(projectCard).join('')}</div>`:`<div class="card empty-zone"><span class="empty-icon">${icon('clapper')}</span><h3>Your editing desk is clear</h3><p>Create a project, upload your clips in story order, and make your first human-approved AI edit.</p><button class="btn btn-primary" data-action="new-project">Create first project</button></div>`}`;
}
function statCard(label,value,note,ico) { return `<div class="card stat-card"><div class="stat-top"><span>${label}</span><span class="stat-icon">${icon(ico)}</span></div><div class="stat-value">${value}</div><div class="stat-note">${note}</div></div>`; }

function renderProjects() {
  document.querySelector('#view-projects').innerHTML=`<div class="page-head"><div><p class="eyebrow">LIBRARY</p><h1>Projects</h1><p>Every project keeps its clips, analysis, assets, versions, and approved final together.</p></div><div class="actions"><button class="btn btn-primary" data-action="new-project">${icon('plus')} New project</button></div></div>
  ${state.projects.length?`<div class="grid grid-3">${state.projects.map(projectCard).join('')}</div>`:`<div class="card empty-zone"><span class="empty-icon">${icon('folder')}</span><h3>No projects yet</h3><p>Your local project folders will appear here.</p><button class="btn btn-primary" data-action="new-project">Create project</button></div>`}`;
}

function workflowHTML(project) {
  const stage=project.workflow==='upload'?(project.clips?.length?0:0):project.workflow==='ready'?1:project.workflow==='review'?2:3;
  const items=[['1','Add footage','Arrange clips'],['2','Analyze','Understand footage'],['3','Review edit','Rate & refine'],['4','Approve','Choose final']];
  return `<div class="workflow">${items.map((it,i)=>`<div class="workflow-step ${i<stage?'done':i===stage?'active':''}"><span class="number">${i<stage?icon('check'):it[0]}</span><span><strong>${it[1]}</strong><small>${it[2]}</small></span></div>`).join('')}</div>`;
}
function clipRow(clip,index,pid) {
  const m=clip.metadata||{};
  return `<div class="clip-row" draggable="true" data-clip-id="${e(clip.id)}"><span class="drag-handle" title="Drag to reorder">${icon('grip')}</span><button class="clip-thumb" data-action="preview-clip" data-id="${e(clip.id)}"><img src="/api/projects/${encodeURIComponent(pid)}/clips/${encodeURIComponent(clip.id)}/thumbnail" alt=""><span class="play-mini"><span>${icon('play')}</span></span></button><div class="clip-info"><strong><span class="muted">${String(index+1).padStart(2,'0')}.</span> ${e(clip.name)}</strong><small>${duration(m.duration)} · ${m.width||'?'}×${m.height||'?'} · ${m.fps||'?'} fps · ${bytes(clip.size)}</small><span class="analysis-state ${e(clip.analysis_status)}"><span></span>${clip.analysis_status==='complete'?'Analysis cached':'Waiting for analysis'}</span></div><div class="clip-actions"><button title="Rename" data-action="rename-clip" data-id="${e(clip.id)}">${icon('edit')}</button><button title="Remove" data-action="remove-clip" data-id="${e(clip.id)}">${icon('trash')}</button></div></div>`;
}
function progressHTML() {
  const j=state.activeJob, u=state.upload;
  if (!j && !u) return '';
  const value=Math.round((u||j).progress||0), stage=u?.stage||j?.stage||'Working', message=u?.message||j?.message||'';
  return `<div class="card progress-card"><div class="progress-top"><span>${icon(j?.kind==='render'?'clapper':'brain')}</span><div><h3>${e(stage)}</h3><p>${e(message)}</p></div><span class="progress-percent">${value}%</span></div><div class="progress-track"><div class="progress-bar" style="width:${value}%"></div></div><div class="progress-stages"><span>Analyze</span><span>Plan</span><span>Render</span><span>Self-review</span></div></div>`;
}
function renderProject() {
  const root=document.querySelector('#view-project'); if(!state.project){root.innerHTML='';return;}
  const p=state.project, clips=p.clips||[], complete=clips.length&&clips.every(c=>c.analysis_status==='complete');
  root.innerHTML=`<div class="page-head"><div><p class="eyebrow">CURRENT PROJECT / ${e(p.id)}</p><h1>${e(p.name)}</h1><p>${e(p.description || p.style || 'Human-directed AI edit')}</p></div><div class="actions"><button class="btn btn-ghost" data-route-link="assets">${icon('image')} Browse assets</button>${p.final_version?`<a class="btn" href="/api/projects/${encodeURIComponent(p.id)}/final">${icon('download')} Final MP4</a>`:''}</div></div>
    <div class="lock-banner"><span>${icon('lock')}</span><div><strong>AI editing cannot change the clip order.</strong><p>The sequence below is the narrative. AI may trim within each clip, but it can never move a later clip before an earlier one.</p></div></div>
    ${workflowHTML(p)}
    <div class="workspace-grid"><section class="card"><div class="card-head"><div><h2>Ordered source clips</h2><p>Drag to set your exact story order</p></div><span class="right status-badge ${complete?'ready':''}">${clips.length} clip${clips.length===1?'':'s'}</span></div>
      ${clips.length?`<div class="clip-list">${clips.map((c,i)=>clipRow(c,i,p.id)).join('')}</div>`:'<div class="empty-zone"><span class="empty-icon">'+icon('film')+'</span><h3>Add your raw footage</h3><p>Upload videos in any format FFmpeg supports, then drag them into story order.</p></div>'}
      <div class="drop-zone" id="clip-drop"><div><span>${icon('upload')}</span><strong>Drop video clips here or browse</strong><small>MP4, MOV, MKV, WebM and more · files stay local</small></div></div>
    </section>
    <aside class="card"><div class="card-head"><div><h2>Edit brief</h2><p>Human direction has highest priority</p></div><span class="right">${icon('sparkles')}</span></div><form class="card-body form-stack" id="render-form">
      <label>Project instructions<textarea name="instructions" rows="4" placeholder="Tell the editor what matters...">${e(p.instructions||'')}</textarea><div class="form-help">Example: Open on the surprise, keep the explanation, and make the ending feel earned.</div></label>
      <label>Editing style<input name="style" value="${e(p.style||'Clean, engaging YouTube edit')}"></label>
      <div class="analyze-callout"><span class="pulse-icon">${icon(complete?'check':'brain')}</span><div><strong>${complete?'Footage analysis is ready':'Analyze before editing'}</strong><small>${complete?'Metadata, frames, scenes and available transcript are cached.':'Extracts frames, scenes, audio, speech and on-screen text.'}</small></div><button class="btn btn-small ${complete?'btn-ghost':'btn-primary'}" type="button" data-action="analyze" ${!clips.length?'disabled':''}>${complete?'Re-analyze':'Analyze'}</button></div>
      <label class="switch-row"><input type="checkbox" name="use_ai" ${state.settings?.api_key_configured?'checked':''}><span class="switch"></span><span class="switch-copy"><strong>Use configured AI model</strong><small>${state.settings?.api_key_configured?'Structured plan with safe local fallback':'No key — local smart plan will be used'}</small></span></label>
      <label class="switch-row"><input type="checkbox" name="include_captions" checked><span class="switch"></span><span class="switch-copy"><strong>Generate transcript captions</strong><small>Burn into video when supported by FFmpeg</small></span></label>
      <label class="switch-row"><input type="checkbox" name="auto_retry"><span class="switch"></span><span class="switch-copy"><strong>Auto-retry only below 5/10</strong><small>Bounded at ${state.settings?.max_auto_retries??3} retries; off by default</small></span></label>
      <div class="brief-actions"><button class="btn btn-primary btn-large" type="submit" ${!complete||state.activeJob?'disabled':''}>${icon('sparkles')} Generate edit</button><button type="button" class="btn btn-ghost btn-large" data-route-link="ai-settings" title="AI settings">${icon('settings')}</button></div>
    </form></aside></div>
    ${progressHTML()}
    ${renderReview(selectedVersion())}`;
  setupClipDnD();
}

function renderReview(v) {
  if (!v || !state.project) return `<div class="card empty-zone" style="margin-top:16px"><span class="empty-icon">${icon('play')}</span><h3>No render yet</h3><p>Analyze your clips, generate a validated edit plan, and the rendered version will appear here for your approval.</p></div>`;
  const p=state.project, r=v.review||{}, score=Number(r.score||0), metrics=[['Hook',r.hook],['Pacing',r.pacing],['Clarity',r.clarity],['Flow',r.narrative_flow],['Entertainment',r.entertainment],['Visual variety',r.visual_variety],['Audio',r.audio],['Ending',r.ending]];
  const accepted=v.status==='accepted';
  return `<div class="review-grid"><section class="card"><div class="card-head"><div><h2>Rendered preview</h2><p>Watch the complete version before deciding</p></div><span class="right status-badge ${e(v.status)}">${e(v.status?.replaceAll('_',' ')||'Awaiting approval')}</span></div><div class="card-body"><div class="video-player-shell"><video controls preload="metadata" src="/api/projects/${encodeURIComponent(p.id)}/versions/${encodeURIComponent(v.id)}/media"></video><span class="version-pill">${e(v.id.toUpperCase())} · ${duration(v.duration)}</span></div></div><div class="video-footer"><div><strong>${e(p.name)} — ${e(v.id.toUpperCase())}</strong><small>Created ${date(v.created_at)} · ${e(v.ai_model||'local plan')}</small></div><div class="actions"><a class="btn btn-small" href="/api/projects/${encodeURIComponent(p.id)}/versions/${encodeURIComponent(v.id)}/media?download=true">${icon('download')} MP4</a><button class="btn btn-small" data-action="view-plan" data-id="${e(v.id)}">${icon('timeline')} Plan</button></div></div></section>
  <aside class="card"><div class="card-head"><div><h2>AI Self-Review</h2><p>Advisory only — your opinion wins</p></div></div><div class="score-hero"><div class="score-ring" style="--score-angle:${Math.max(0,score)*36}deg"><span>${score.toFixed(1)}<small>/10</small></span></div><div class="score-copy"><h3>${score>=8?'Strong edit':score>=5?'Ready for your review':'Another pass recommended'}</h3><p>The model evaluated pacing and craft. It cannot predict what you will enjoy.</p></div></div>
  ${score<5?'<div class="low-score"><strong>AI recommends another editing pass.</strong> You can still review or accept this version anyway.</div>':''}
  <div class="score-metrics">${metrics.map(([k,val])=>`<div class="metric-line"><span>${k}</span><strong>${Number(val||0).toFixed(1)}</strong><div class="mini-track"><span style="width:${Number(val||0)*10}%"></span></div></div>`).join('')}</div>
  ${r.problems?.length?`<div class="review-notes"><strong>REVIEW NOTES</strong><ul>${r.problems.map(x=>`<li>${e(x)}</li>`).join('')}</ul></div>`:''}
  ${v.warnings?.length?`<div class="review-notes"><strong>PIPELINE NOTES</strong><ul>${v.warnings.map(x=>`<li>${e(x)}</li>`).join('')}</ul></div>`:''}
  <div class="approval-actions">${accepted?`<button class="btn btn-primary wide" disabled>${icon('check')} Human approved final</button>`:`<button class="btn btn-primary" data-action="accept-version" data-id="${e(v.id)}">${icon('check')} ${score<5?'Accept anyway':'Accept'}</button><button class="btn btn-danger" data-action="feedback" data-id="${e(v.id)}">${icon('refresh')} Reject & regenerate</button><button class="btn btn-ghost" data-action="feedback" data-id="${e(v.id)}">${icon('edit')} Give feedback / rate</button><button class="btn btn-ghost" data-action="keep-editing">${icon('settings')} Keep editing</button>${score<5?`<button class="btn wide" data-action="auto-retry" data-id="${e(v.id)}">${icon('sparkles')} Automatically create another version</button>`:''}`}</div></aside></div>`;
}

function renderTimeline() {
  const root=document.querySelector('#view-timeline'); if(!state.project)return;
  const p=state.project, v=selectedVersion(), plan=state.currentPlan;
  const segments=plan?.timeline || (p.clips||[]).map(c=>({clip_id:c.id,start:0,end:c.metadata?.duration||10,effects:[]}));
  const clipMap=Object.fromEntries((p.clips||[]).map(c=>[c.id,c])); const total=segments.reduce((n,s)=>n+(s.end-s.start),0)||1;
  root.innerHTML=`<div class="page-head"><div><p class="eyebrow">WORKSPACE / LOCKED ORDER</p><h1>Timeline</h1><p>${v?`${v.id.toUpperCase()} editing plan · ${segments.length} cuts · ${duration(total)}`:'Source sequence preview — generate a version to inspect the precise plan.'}</p></div><div class="actions">${v?`<button class="btn" data-action="load-plan" data-id="${e(v.id)}">${icon('refresh')} Load ${e(v.id.toUpperCase())} plan</button>`:''}</div></div>
  <div class="lock-banner"><span>${icon('lock')}</span><div><strong>Source order is enforced by backend validation.</strong><p>Even malformed AI output is rejected before FFmpeg receives a plan.</p></div></div>
  <section class="card"><div class="card-head"><div><h2>${v?'Validated editing plan':'Source timeline'}</h2><p>Visual overview — source trim details remain in the JSON plan</p></div><span class="right status-badge ready">${segments.length} segments</span></div><div class="timeline-ruler"><div class="track-labels"><div>VIDEO</div><div>AUDIO</div><div>CAPTIONS</div><div>EFFECTS</div></div><div class="tracks"><div class="time-marks">${[0,1,2,3,4,5,6,7].map(i=>`<span>${duration(total*i/7)}</span>`).join('')}</div>
    <div class="track">${segments.map(s=>`<div class="timeline-clip" style="width:${Math.max(80,(s.end-s.start)/total*670)}px"><strong>${e(clipMap[s.clip_id]?.name||s.clip_id)}</strong><br>${s.start.toFixed(1)}–${s.end.toFixed(1)}s</div>`).join('')}</div>
    <div class="track">${segments.map(s=>`<div class="timeline-clip timeline-audio" style="width:${Math.max(80,(s.end-s.start)/total*670)}px"></div>`).join('')}</div>
    <div class="track">${(plan?.captions||[]).slice(0,30).map(c=>`<div class="timeline-clip" style="width:${Math.max(65,(c.end-c.start)/total*670)}px">${e(c.text)}</div>`).join('')||'<span class="muted">No caption plan loaded</span>'}</div>
    <div class="track">${[...segments.flatMap(s=>s.effects||[]).map(f=>({label:f.type})),...(plan?.visual_overlays||[]).map(v=>({label:`${v.mode} asset`,start:v.start,end:v.end}))].map(f=>`<div class="timeline-clip" style="width:${f.end?Math.max(80,(f.end-f.start)/total*670):95}px">${e(f.label)}</div>`).join('')||'<span class="muted">Restrained edit — no unnecessary effects</span>'}</div></div></div></section>
  ${plan?`<section class="card" style="margin-top:16px"><div class="card-head"><h3>Editor rationale</h3></div><div class="card-body muted">${e(plan.rationale||'No rationale provided.')}</div></section>`:''}`;
}

function assetPreview(asset) {
  const p=state.project, url=`/api/projects/${encodeURIComponent(p.id)}/assets/${encodeURIComponent(asset.id)}/media`, category=asset.category;
  if(category==='images') return `<img loading="lazy" src="${url}" alt="">`;
  if(category==='music'||category==='sound_effects') return `<audio controls preload="none" src="${url}"></audio>`;
  return icon(category==='video'?'film':'file');
}
function libraryPreview(asset) {
  const url=`/api/library/${encodeURIComponent(asset.id)}/media`, category=asset.category;
  if(category==='images') return `<img loading="lazy" src="${url}" alt="">`;
  if(category==='music'||category==='sound_effects') return `<audio controls preload="none" src="${url}"></audio>`;
  return icon(category==='video'?'film':'file');
}
function renderAssets() {
  const root=document.querySelector('#view-assets'); if(!state.project)return; const p=state.project;
  const filtered=(p.assets||[]).filter(a=>state.assetFilter==='all'||a.category===state.assetFilter);
  root.innerHTML=`<div class="page-head"><div><p class="eyebrow">PROJECT ASSET LIBRARY</p><h1>Assets</h1><p>Every source, license, location, and version usage stays traceable.</p></div><div class="actions"><button class="btn btn-primary" data-action="upload-asset">${icon('upload')} Upload asset</button></div></div>
  <div class="asset-toolbar"><div class="search-field">${icon('search')}<input id="asset-search-input" placeholder="Search reusable assets on Wikimedia Commons..."></div><select id="asset-search-type" style="width:120px;margin:0"><option value="image">Images</option><option value="audio">Audio</option><option value="video">Video</option></select><button class="btn" data-action="search-assets">Search web</button></div>
  <div class="category-tabs"><button class="${state.assetFilter==='all'?'active':''}" data-action="filter-assets" data-filter="all">All</button>${['music','images','sound_effects','video','other'].map(x=>`<button class="${state.assetFilter===x?'active':''}" data-action="filter-assets" data-filter="${x}">${x.replace('_',' ')}</button>`).join('')}</div>
  <div class="section-title"><h2>Project assets <span class="muted">(${filtered.length})</span></h2></div>
  ${filtered.length?`<div class="asset-grid">${filtered.map(a=>`<article class="card asset-card"><div class="asset-preview">${assetPreview(a)}<span class="license-tag">${e(a.license||'License unknown')}</span></div><div class="asset-info"><strong>${e(a.filename)}</strong><p>${e(a.source_name||'User upload')} · ${bytes(a.size)}<br>Used in: ${a.used_in?.length?a.used_in.map(e).join(', '):'not used yet'}</p><div class="row"><a class="btn btn-small" href="/api/projects/${encodeURIComponent(p.id)}/assets/${encodeURIComponent(a.id)}/media?download=true">${icon('download')} File</a><button class="btn btn-small" data-action="keep-library" data-id="${e(a.id)}" ${a.library_id?'disabled':''}>${icon(a.library_id?'check':'copy')} ${a.library_id?'Saved':'Reuse'}</button><button class="btn btn-small btn-danger" data-action="remove-asset" data-id="${e(a.id)}">${icon('trash')}</button></div></div></article>`).join('')}</div>`:`<div class="card empty-zone"><span class="empty-icon">${icon('image')}</span><h3>No assets in this category</h3><p>Upload your own music, images, and sound effects, or search reuse-friendly Wikimedia Commons media.</p></div>`}
  ${state.library.length?`<div class="search-results"><div class="section-title"><h2>Reusable library <span class="muted">(${state.library.length})</span></h2><span class="muted">Available to every project</span></div><div class="asset-grid">${state.library.map(a=>`<article class="card asset-card"><div class="asset-preview">${libraryPreview(a)}<span class="license-tag">${e(a.license||'License unknown')}</span></div><div class="asset-info"><strong>${e(a.filename)}</strong><p>${e(a.source_name||'Reusable asset')} · ${bytes(a.size)}</p><div class="row"><button class="btn btn-small btn-primary" data-action="add-library" data-id="${e(a.id)}">${icon('plus')} Add to project</button></div></div></article>`).join('')}</div></div>`:''}
  ${state.assetResults.length?`<div class="search-results"><div class="section-title"><h2>Licensed search results</h2><span class="muted">Review the source terms before use</span></div><div class="asset-grid">${state.assetResults.map((a,i)=>`<article class="card asset-card"><div class="asset-preview">${a.preview_url?`<img loading="lazy" src="${e(a.preview_url)}" alt="">`:icon('file')}<span class="license-tag">${e(a.license)}</span></div><div class="asset-info"><strong>${e(a.title)}</strong><p>${e(a.source_name)} · ${e(a.artist||'Creator listed at source')}</p><div class="row"><a class="btn btn-small" href="${e(a.source_url)}" target="_blank" rel="noopener">${icon('external')} Source</a><button class="btn btn-small btn-primary" data-action="download-asset" data-index="${i}">${icon('download')} Add</button></div></div></article>`).join('')}</div></div>`:''}`;
}

function renderHistory() {
  const root=document.querySelector('#view-history'); if(!state.project)return; const p=state.project, versions=[...(p.versions||[])].reverse();
  state.compareIds=state.compareIds.filter(id=>p.versions?.some(v=>v.id===id)).slice(-2);
  const compared=state.compareIds.map(id=>versionOf(id)).filter(Boolean);
  const comparison=compared.length?`<section class="card compare-card"><div class="card-head"><div><h2>Version comparison</h2><p>${compared.length===1?'Choose one more version for side-by-side playback':'Play both versions and compare the edit decisions yourself'}</p></div><button class="btn btn-small right" data-action="clear-compare">Clear</button></div><div class="compare-grid">${compared.map(v=>`<div class="compare-pane"><div class="video-player-shell"><video controls preload="metadata" src="/api/projects/${encodeURIComponent(p.id)}/versions/${encodeURIComponent(v.id)}/media"></video><span class="version-pill">${e(v.id.toUpperCase())}</span></div><div class="compare-meta"><strong>${e(v.id.toUpperCase())}</strong><span>AI ${Number(v.review?.score||0).toFixed(1)}/10</span><span>Human ${v.human_rating?`${v.human_rating}/10`:'not rated'}</span><button class="btn btn-small" data-action="open-version" data-id="${e(v.id)}">Full review</button></div></div>`).join('')}${compared.length===1?`<div class="compare-placeholder">${icon('plus')}<strong>Select another version below</strong><span>The comparison never changes either saved file.</span></div>`:''}</div></section>`:'';
  root.innerHTML=`<div class="page-head"><div><p class="eyebrow">IMMUTABLE VERSIONS</p><h1>Render history</h1><p>Compare, rate, restore, or approve any previous version. Older files are never overwritten.</p></div><div class="actions"><button class="btn btn-primary" data-route-link="project">${icon('sparkles')} New editing pass</button></div></div>${comparison}
  <section class="card"><div class="card-head"><h2>${versions.length} rendered version${versions.length===1?'':'s'}</h2><span class="right status-badge ${p.final_version?'complete':''}">${p.final_version?`${p.final_version.toUpperCase()} final`:'No final selected'}</span></div>${versions.length?`<div class="version-list">${versions.map(v=>`<div class="version-row"><button class="version-thumb" data-action="open-version" data-id="${e(v.id)}">${icon('play')}</button><div class="version-main"><strong>${e(v.id.toUpperCase())} ${v.id===p.final_version?'<span class="accent">· FINAL</span>':''}</strong><small>${date(v.created_at)} · ${duration(v.duration)} · ${e(v.ai_model)}</small></div><div class="version-cell"><small>AI SELF-REVIEW</small><span class="version-score">${Number(v.review?.score||0).toFixed(1)}</span></div><div class="version-cell"><small>HUMAN RATING</small><strong>${v.human_rating?`${v.human_rating}/10`:'Not rated'}</strong></div><div class="version-cell hide-medium"><small>STATUS</small><span class="status-badge ${e(v.status)}">${e(v.status?.replaceAll('_',' '))}</span></div><div class="version-actions"><button class="btn btn-small ${state.compareIds.includes(v.id)?'btn-primary':''}" data-action="toggle-compare" data-id="${e(v.id)}">${icon('copy')} ${state.compareIds.includes(v.id)?'Selected':'Compare'}</button><button class="btn btn-small" data-action="open-version" data-id="${e(v.id)}">Review</button>${v.id!==p.final_version?`<button class="btn btn-small" data-action="accept-version" data-id="${e(v.id)}">Select final</button>`:''}</div></div>`).join('')}</div>`:`<div class="empty-zone"><span class="empty-icon">${icon('history')}</span><h3>No versions yet</h3><p>Each editing pass creates a new numbered directory and appears here.</p><button class="btn btn-primary" data-route-link="project">Go to editor</button></div>`}</section>`;
}

function settingsNav(active) { return `<nav class="settings-nav"><a href="#ai-settings" class="${active==='ai'?'active':''}">AI provider</a><a href="#settings" class="${active==='local'?'active':''}">Local processing</a><a href="#settings" class="${active==='output'?'active':''}">Output & retries</a><a href="#settings">Storage & privacy</a></nav>`; }
function renderAISettings() {
  const root=document.querySelector('#view-ai-settings'), s=state.settings||{};
  root.innerHTML=`<div class="page-head"><div><p class="eyebrow">CONFIGURATION</p><h1>AI Settings</h1><p>Use any OpenAI-compatible provider without changing the editing engine.</p></div></div><div class="settings-layout">${settingsNav('ai')}<form class="card settings-panel" id="ai-settings-form"><h2>Model provider</h2><p>Secrets are never returned to the browser after saving and are excluded from Git.</p><div class="settings-section"><div class="form-row"><label>Provider<select name="provider"><option value="openai-compatible" ${s.provider==='openai-compatible'?'selected':''}>OpenAI-compatible</option><option value="openai" ${s.provider==='openai'?'selected':''}>OpenAI</option><option value="local" ${s.provider==='local'?'selected':''}>Local compatible server</option></select></label><label>API base URL<input name="api_base_url" value="${e(s.api_base_url||'')}" required></label></div><label class="secret-wrap">API key<input name="api_key" type="password" autocomplete="new-password" placeholder="${s.api_key_configured?'Configured — leave blank to keep current key':'sk-...'}"><button type="button" data-action="toggle-secret">${icon('eye')}</button></label><div class="notice" style="margin-top:10px">For best results, use a model that supports structured JSON and image input. A safe local editing plan remains available without a key.</div></div><div class="settings-section"><h3>Models</h3><div class="form-row"><label>Editing model<input name="model" value="${e(s.model||'')}"></label><label>Vision / review model<input name="vision_model" value="${e(s.vision_model||'')}"></label></div><label>Speech-to-text model<input name="stt_model" value="${e(s.stt_model||'whisper-1')}"></label></div><div class="settings-section"><h3>Generation controls</h3><div class="form-row"><label>Temperature<input name="temperature" type="number" min="0" max="2" step=".05" value="${s.temperature??.35}"></label><label>Maximum tokens<input name="max_tokens" type="number" min="256" value="${s.max_tokens||6000}"></label></div></div><button class="btn btn-primary" type="submit">${icon('check')} Save AI settings</button></form></div>`;
}
function renderSettings() {
  const root=document.querySelector('#view-settings'), s=state.settings||{}, h=state.health||{};
  root.innerHTML=`<div class="page-head"><div><p class="eyebrow">LOCAL WORKSPACE</p><h1>Settings</h1><p>Control deterministic analysis, output quality, captions, and bounded retries.</p></div></div><div class="settings-layout">${settingsNav('local')}<form class="card settings-panel" id="local-settings-form"><h2>Local processing</h2><p>Video bytes stay on this machine. Only structured context and selected low-resolution frames go to your configured model.</p><div class="settings-section"><h3>Capabilities</h3><div class="capability-list">${capability('FFmpeg rendering',h.ffmpeg&&h.ffprobe,h.ffmpeg?'Detected':'Install FFmpeg + FFprobe and restart','clapper')}${capability('Local Whisper',h.local_whisper,h.local_whisper?'faster-whisper detected':'Optional: pip install faster-whisper','volume')}${capability('OCR',h.ocr,h.ocr?'pytesseract detected':'Optional: install Tesseract + pytesseract','image')}</div></div><div class="settings-section"><h3>Analysis</h3><div class="form-row"><label>Frame interval (seconds)<input name="frame_interval" type="number" min="2" max="120" value="${s.frame_interval||12}"></label><label>Local Whisper model<input name="whisper_model" value="${e(s.whisper_model||'small')}"></label></div><label class="switch-row"><input name="use_local_whisper" type="checkbox" ${s.use_local_whisper?'checked':''}><span class="switch"></span><span class="switch-copy"><strong>Prefer local transcription</strong><small>Falls back gracefully if optional dependency is unavailable</small></span></label><label class="switch-row" style="margin-top:10px"><input name="enable_ocr" type="checkbox" ${s.enable_ocr?'checked':''}><span class="switch"></span><span class="switch-copy"><strong>Run OCR on representative frames</strong><small>Detect useful on-screen text without scanning every frame</small></span></label><label class="switch-row" style="margin-top:10px"><input name="enable_vision_analysis" type="checkbox" ${s.enable_vision_analysis?'checked':''}><span class="switch"></span><span class="switch-copy"><strong>Use AI vision during footage analysis</strong><small>Sends at most 8 low-resolution representative frames per clip when a key is configured</small></span></label></div><div class="settings-section"><h3>YouTube output</h3><div class="form-row"><label>Width<input name="output_width" type="number" value="${s.output_width||1920}"></label><label>Height<input name="output_height" type="number" value="${s.output_height||1080}"></label></div><div class="form-row"><label>Frame rate<input name="output_fps" type="number" min="12" max="120" value="${s.output_fps||30}"></label><label>Maximum auto-retries<input name="max_auto_retries" type="number" min="0" max="10" value="${s.max_auto_retries??3}"></label></div><label class="switch-row"><input name="burn_captions" type="checkbox" ${s.burn_captions?'checked':''}><span class="switch"></span><span class="switch-copy"><strong>Burn planned captions into output</strong><small>Also keeps an ASS caption sidecar with each version</small></span></label></div><div class="notice">Free disk space: <strong>${bytes(h.free_disk_bytes)}</strong>. Source assets and rendered versions are never silently deleted.</div><button class="btn btn-primary" type="submit" style="margin-top:18px">${icon('check')} Save local settings</button></form></div>`;
}
function capability(name,ok,note,ico){return `<div class="capability ${ok?'ok':''}"><span>${icon(ok?'check':ico)}</span><div><strong>${name}</strong><small>${e(note)}</small></div><em>${ok?'Ready':'Optional'}</em></div>`;}

function render() {
  renderDashboard(); renderProjects(); renderProject(); renderTimeline(); renderAssets(); renderHistory(); renderAISettings(); renderSettings();
  document.querySelectorAll('.view').forEach(v=>v.classList.toggle('active',v.dataset.view===state.route));
  hydrateIcons();
}

function openProjectModal(){document.querySelector('#project-modal').classList.remove('hidden');setTimeout(()=>document.querySelector('#project-form input[name=name]').focus(),10);}
function closeProjectModal(){document.querySelector('#project-modal').classList.add('hidden');}
function openFeedback(id){const v=versionOf(id), modal=document.querySelector('#feedback-modal'), f=document.querySelector('#feedback-form');f.version_id.value=id;f.rating.value=v?.human_rating||8;f.feedback.value=v?.user_feedback||'';document.querySelector('#rating-value').textContent=`${f.rating.value}/10`;modal.classList.remove('hidden');setTimeout(()=>f.feedback.focus(),10);}

async function createProject(form){const data=Object.fromEntries(new FormData(form));const p=await api('/api/projects',{method:'POST',body:data});closeProjectModal();form.reset();await loadAll(p.id);setRoute('project');toast('Project created','Add clips in your exact narrative order.');}
async function switchProject(id){state.currentPlan=null;state.selectedVersionId=null;state.compareIds=[];await loadAll(id);setRoute('project');document.querySelector('#project-menu').classList.add('hidden');}

function uploadVideo(file,index,total){return new Promise((resolve,reject)=>{const xhr=new XMLHttpRequest(), form=new FormData();form.append('file',file);xhr.open('POST',`/api/projects/${encodeURIComponent(state.project.id)}/clips`);xhr.upload.onprogress=event=>{if(event.lengthComputable){const fileP=event.loaded/event.total;state.upload={stage:'Uploading footage',progress:Math.round(((index+fileP)/total)*100),message:`${file.name} · ${index+1} of ${total}`};renderProject();hydrateIcons();}};xhr.onload=()=>xhr.status>=200&&xhr.status<300?resolve(JSON.parse(xhr.responseText)):reject(new Error(parseXhrError(xhr)));xhr.onerror=()=>reject(new Error('Network error during upload'));xhr.send(form);});}
function parseXhrError(xhr){try{return JSON.parse(xhr.responseText).detail||`Upload failed (${xhr.status})`;}catch(_){return `Upload failed (${xhr.status})`;}}
async function uploadClips(files){if(!state.project||!files.length)return;const videos=[...files];try{for(let i=0;i<videos.length;i++)await uploadVideo(videos[i],i,videos.length);state.upload=null;await loadAll(state.project.id);toast('Footage added',`${videos.length} clip${videos.length===1?'':'s'} uploaded. Drag to confirm story order.`);}catch(err){state.upload=null;await loadAll(state.project.id);toast('Upload failed',err.message,'error');}}

async function analyze(){const force=Boolean(state.project?.clips?.length&&state.project.clips.every(c=>c.analysis_status==='complete'));try{const j=await api(`/api/projects/${encodeURIComponent(state.project.id)}/analyze${force?'?force=true':''}`,{method:'POST'});state.activeJob=j;render();pollJob(j.id);}catch(err){toast('Could not start analysis',err.message,'error');}}
async function saveBrief(form){const data=new FormData(form);await api(`/api/projects/${encodeURIComponent(state.project.id)}`,{method:'PATCH',body:{instructions:data.get('instructions'),style:data.get('style')}});}
async function startRender(feedback='') {const form=document.querySelector('#render-form');if(!form)return;const fd=new FormData(form);try{await saveBrief(form);const body={instructions:fd.get('instructions')||'',feedback,use_ai:fd.has('use_ai'),include_captions:fd.has('include_captions'),auto_retry:fd.has('auto_retry')};const j=await api(`/api/projects/${encodeURIComponent(state.project.id)}/render`,{method:'POST',body});state.activeJob=j;render();pollJob(j.id);}catch(err){toast('Could not start render',err.message,'error');}}
async function pollJob(id){let failures=0;const loop=async()=>{try{const j=await api(`/api/jobs/${id}`);state.activeJob=j;render();if(j.status==='completed'){state.activeJob=null;await loadAll(state.project.id);state.selectedVersionId=j.result?.latest_version||null;setRoute('project');toast(j.kind==='analysis'?'Analysis complete':'New version ready',j.kind==='analysis'?(j.result.warnings?.length?j.result.warnings.join('\n'):'Structured footage data is cached.'):`${j.result.latest_version.toUpperCase()} scored ${j.result.score}/10 in advisory self-review.`);return;}if(j.status==='failed'){state.activeJob=null;render();toast(`${j.kind} failed`,j.error||j.message,'error');return;}setTimeout(loop,900);}catch(err){if(++failures<4)setTimeout(loop,1500);else{state.activeJob=null;toast('Lost job connection',err.message,'error');}}};loop();}

async function reorderClips(ids){try{await api(`/api/projects/${encodeURIComponent(state.project.id)}/clips/order`,{method:'PUT',body:{clip_ids:ids}});await loadAll(state.project.id);toast('Story order saved','The AI is now locked to this sequence.');}catch(err){toast('Could not save order',err.message,'error');}}
function setupClipDnD(){let dragged=null;document.querySelectorAll('.clip-row').forEach(row=>{row.addEventListener('dragstart',()=>{dragged=row;row.classList.add('dragging');});row.addEventListener('dragend',()=>{row.classList.remove('dragging');document.querySelectorAll('.clip-row').forEach(x=>x.classList.remove('drag-over'));const ids=[...document.querySelectorAll('.clip-row')].map(x=>x.dataset.clipId);if(ids.join(',')!==(state.project.clips||[]).map(c=>c.id).join(','))reorderClips(ids);});row.addEventListener('dragover',ev=>{ev.preventDefault();if(row!==dragged){row.classList.add('drag-over');const rect=row.getBoundingClientRect();row.parentNode.insertBefore(dragged,ev.clientY<rect.top+rect.height/2?row:row.nextSibling);}});row.addEventListener('dragleave',()=>row.classList.remove('drag-over'));});const drop=document.querySelector('#clip-drop');if(drop){['dragenter','dragover'].forEach(x=>drop.addEventListener(x,ev=>{ev.preventDefault();drop.classList.add('over');}));['dragleave','drop'].forEach(x=>drop.addEventListener(x,()=>drop.classList.remove('over')));drop.addEventListener('drop',ev=>{ev.preventDefault();uploadClips(ev.dataTransfer.files);});}}

async function acceptVersion(id){try{await api(`/api/projects/${encodeURIComponent(state.project.id)}/versions/${encodeURIComponent(id)}/accept`,{method:'POST'});await loadAll(state.project.id);state.selectedVersionId=id;setRoute(state.route);toast('Final approved','Your human-approved MP4 is preserved in the final folder.');}catch(err){toast('Could not approve version',err.message,'error');}}
async function feedbackSubmit(form){const fd=new FormData(form), id=fd.get('version_id'), rating=Number(fd.get('rating')), feedback=fd.get('feedback');try{await api(`/api/projects/${encodeURIComponent(state.project.id)}/versions/${encodeURIComponent(id)}/reject`,{method:'POST',body:{rating,feedback}});document.querySelector('#feedback-modal').classList.add('hidden');await loadAll(state.project.id);setRoute('project');await startRender(feedback);}catch(err){toast('Could not save feedback',err.message,'error');}}
async function loadPlan(id, route=true){try{state.currentPlan=await api(`/api/projects/${encodeURIComponent(state.project.id)}/versions/${encodeURIComponent(id)}/plan`);state.selectedVersionId=id;if(route)setRoute('timeline');else render();}catch(err){toast('Could not load plan',err.message,'error');}}

async function searchAssets(){const q=document.querySelector('#asset-search-input')?.value.trim(), type=document.querySelector('#asset-search-type')?.value;if(!q)return toast('Enter a search','Search for an image, music, or footage.','error');try{toast('Searching licensed media','Querying Wikimedia Commons...');state.assetResults=await api(`/api/assets/search?q=${encodeURIComponent(q)}&media_type=${encodeURIComponent(type)}`);renderAssets();hydrateIcons();if(!state.assetResults.length)toast('No licensed results','Try a broader search.');}catch(err){toast('Search failed',err.message,'error');}}
async function downloadAsset(index){const a=state.assetResults[index];if(!a)return;const mime=a.mime||'',category=mime.startsWith('image/')?'images':mime.startsWith('audio/')?'music':mime.startsWith('video/')?'video':'other';try{await api(`/api/projects/${encodeURIComponent(state.project.id)}/assets/download`,{method:'POST',body:{url:a.download_url,filename:a.title,category,source_url:a.source_url,source_name:a.source_name,license:[a.license,a.license_url].filter(Boolean).join(' · '),usage_notes:[a.artist,a.attribution].filter(Boolean).join(' · ')}});await loadAll(state.project.id);setRoute('assets');toast('Asset added',`${a.title} and its license metadata were saved.`);}catch(err){toast('Download failed',err.message,'error');}}
async function uploadAssets(files){for(const file of files){const form=new FormData();form.append('file',file);const category=file.type.startsWith('image/')?'images':file.type.startsWith('audio/')?'music':file.type.startsWith('video/')?'video':'other';form.append('category',category);form.append('source_name','User upload');form.append('license','User supplied — confirm you have usage rights');try{await api(`/api/projects/${encodeURIComponent(state.project.id)}/assets`,{method:'POST',body:form});}catch(err){toast(`Could not add ${file.name}`,err.message,'error');}}await loadAll(state.project.id);setRoute('assets');toast('Assets uploaded','Files remain in the project asset library.');}
async function keepInLibrary(id){await api(`/api/projects/${encodeURIComponent(state.project.id)}/assets/${encodeURIComponent(id)}/library`,{method:'POST'});await loadAll(state.project.id);setRoute('assets');toast('Saved for future projects','A licensed copy is now available in the reusable library.');}
async function addFromLibrary(id){await api(`/api/projects/${encodeURIComponent(state.project.id)}/assets/from-library/${encodeURIComponent(id)}`,{method:'POST'});await loadAll(state.project.id);setRoute('assets');toast('Added from reusable library','The asset and its license metadata were copied into this project.');}

function mergedSettings(form,type){const fd=new FormData(form), s={...state.settings};if(type==='ai'){Object.assign(s,{provider:fd.get('provider'),api_base_url:fd.get('api_base_url'),api_key:fd.get('api_key')||undefined,model:fd.get('model'),vision_model:fd.get('vision_model'),stt_model:fd.get('stt_model'),temperature:Number(fd.get('temperature')),max_tokens:Number(fd.get('max_tokens'))});}else{Object.assign(s,{frame_interval:Number(fd.get('frame_interval')),whisper_model:fd.get('whisper_model'),use_local_whisper:fd.has('use_local_whisper'),enable_ocr:fd.has('enable_ocr'),enable_vision_analysis:fd.has('enable_vision_analysis'),output_width:Number(fd.get('output_width')),output_height:Number(fd.get('output_height')),output_fps:Number(fd.get('output_fps')),max_auto_retries:Number(fd.get('max_auto_retries')),burn_captions:fd.has('burn_captions')});}delete s.api_key_configured;return s;}
async function saveSettings(form,type){try{state.settings=await api('/api/settings',{method:'PUT',body:mergedSettings(form,type)});render();toast('Settings saved',type==='ai'?'Provider configuration updated.':'Local pipeline settings updated.');}catch(err){toast('Could not save settings',err.message,'error');}}

// Global events
document.addEventListener('click', async event => {
  const routeLink=event.target.closest('[data-route-link]'); if(routeLink){event.preventDefault();setRoute(routeLink.dataset.routeLink);return;}
  const target=event.target.closest('[data-action]'); if(!target)return; const action=target.dataset.action, id=target.dataset.id;
  try {
    if(action==='new-project')openProjectModal();
    else if(action==='close-modal')closeProjectModal();
    else if(action==='close-feedback')document.querySelector('#feedback-modal').classList.add('hidden');
    else if(action==='switch-project'||action==='open-project')await switchProject(id);
    else if(action==='preview-clip')window.open(`/api/projects/${encodeURIComponent(state.project.id)}/clips/${encodeURIComponent(id)}/media`,'_blank','noopener');
    else if(action==='rename-clip'){const clip=state.project.clips.find(c=>c.id===id),name=prompt('Rename clip',clip.name);if(name?.trim()){await api(`/api/projects/${encodeURIComponent(state.project.id)}/clips/${encodeURIComponent(id)}`,{method:'PATCH',body:{name:name.trim()}});await loadAll(state.project.id);}}
    else if(action==='remove-clip'){if(confirm('Remove this clip from the timeline? Its file will be preserved in the project trash folder.')){await api(`/api/projects/${encodeURIComponent(state.project.id)}/clips/${encodeURIComponent(id)}`,{method:'DELETE'});await loadAll(state.project.id);toast('Clip removed','Original bytes were preserved in project trash.');}}
    else if(action==='analyze')analyze();
    else if(action==='accept-version')acceptVersion(id);
    else if(action==='feedback')openFeedback(id);
    else if(action==='keep-editing'){const field=document.querySelector('#render-form textarea[name=instructions]');field?.scrollIntoView({behavior:'smooth',block:'center'});field?.focus();}
    else if(action==='auto-retry'){const v=versionOf(id);const changes=v?.review?.suggested_changes?.join('; ')||'Create a stronger pass based on the self-review.';startRender(`AI Self-Review requested: ${changes}`);}
    else if(action==='view-plan'||action==='load-plan')loadPlan(id,true);
    else if(action==='open-version'){state.selectedVersionId=id;setRoute('project');}
    else if(action==='toggle-compare'){state.compareIds=state.compareIds.includes(id)?state.compareIds.filter(item=>item!==id):[...state.compareIds,id].slice(-2);renderHistory();}
    else if(action==='clear-compare'){state.compareIds=[];renderHistory();}
    else if(action==='filter-assets'){state.assetFilter=target.dataset.filter;renderAssets();hydrateIcons();}
    else if(action==='search-assets')searchAssets();
    else if(action==='download-asset')downloadAsset(Number(target.dataset.index));
    else if(action==='upload-asset')document.querySelector('#asset-file-input').click();
    else if(action==='keep-library')await keepInLibrary(id);
    else if(action==='add-library')await addFromLibrary(id);
    else if(action==='remove-asset'){if(confirm('Remove this unused asset? Its file will be moved to project trash.')){await api(`/api/projects/${encodeURIComponent(state.project.id)}/assets/${encodeURIComponent(id)}`,{method:'DELETE'});await loadAll(state.project.id);setRoute('assets');}}
    else if(action==='toggle-secret'){const input=target.parentElement.querySelector('input');input.type=input.type==='password'?'text':'password';}
  } catch(err) { toast('Action failed',err.message,'error'); }
});

document.addEventListener('submit', event => { event.preventDefault(); if(event.target.id==='project-form')createProject(event.target).catch(err=>toast('Could not create project',err.message,'error')); else if(event.target.id==='render-form')startRender(); else if(event.target.id==='feedback-form')feedbackSubmit(event.target); else if(event.target.id==='ai-settings-form')saveSettings(event.target,'ai'); else if(event.target.id==='local-settings-form')saveSettings(event.target,'local'); });
document.querySelector('#feedback-form input[name=rating]').addEventListener('input',ev=>document.querySelector('#rating-value').textContent=`${ev.target.value}/10`);
document.querySelector('#project-switcher').addEventListener('click',()=>document.querySelector('#project-menu').classList.toggle('hidden'));
document.querySelector('#refresh-button').addEventListener('click',()=>loadAll(state.project?.id).catch(err=>toast('Refresh failed',err.message,'error')));
document.querySelector('#mobile-menu').addEventListener('click',()=>document.body.classList.toggle('menu-open'));
document.querySelector('#clip-file-input').addEventListener('change',ev=>{uploadClips(ev.target.files);ev.target.value='';});
document.querySelector('#asset-file-input').addEventListener('change',ev=>{uploadAssets(ev.target.files);ev.target.value='';});
document.addEventListener('click',ev=>{if(ev.target.closest('#clip-drop'))document.querySelector('#clip-file-input').click();});
window.addEventListener('hashchange',()=>setRoute(location.hash.slice(1)));
window.addEventListener('keydown',ev=>{if(ev.key==='Escape'){closeProjectModal();document.querySelector('#feedback-modal').classList.add('hidden');document.querySelector('#project-menu').classList.add('hidden');}});

hydrateIcons();
loadAll().then(()=>setRoute(location.hash.slice(1)||'dashboard')).catch(err=>{toast('Could not start the app',err.message,'error');document.querySelector('#view-dashboard').classList.add('active');document.querySelector('#view-dashboard').innerHTML=`<div class="card empty-zone"><span class="empty-icon">${icon('alert')}</span><h3>Backend connection failed</h3><p>${e(err.message)}</p></div>`;});
