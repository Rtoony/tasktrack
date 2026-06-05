import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
const CFG = window.TT_INTAKE;

const I = {
  general:'M4 5h7v7H4zM13 5h7v4h-7zM13 12h7v7h-7zM4 14h7v5H4z',
  project:'M4 7h16v12H4zM9 7V5h6v2',
  cad:'M5 19l4-12 4 12M6.5 15h5M14 7h5M14 11h5M14 15h4',
  training:'M3 8l9-4 9 4-9 4-9-4zM7 10.5V15c0 1.2 2.4 2.2 5 2.2s5-1 5-2.2v-4.5',
  suggestion:'M9 18h6M10 21h4M12 3a6 6 0 0 1 4 10.5c-.6.6-1 1.3-1 2.1H9c0-.8-.4-1.5-1-2.1A6 6 0 0 1 12 3z',
  problem:'M12 3l9 16H3zM12 10v4M12 17h.01',
  check:'M5 13l4 4L19 7',
  paperclip:'M21 11l-8.5 8.5a4 4 0 0 1-6-6L14 5.5a2.5 2.5 0 0 1 4 3L9.5 17a1 1 0 0 1-1.5-1.5L16 7',
};
const Stroke = ({d,size=22,w=1.75}) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth={w} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={d}/></svg>
);

const SKILL_AREAS = ['Surfaces','Corridors','Pipe Networks','Sheet Sets','LISP / Automation','Plan Production','Labels & Styles','Data Exchange','Dynamic Blocks','Templates','Other'];
const PHASES = ['100 - Survey','200 - Prelim Design','300 - Const Docs','400 - Bid Support','500 - Const Admin'];
const SUGGESTION_CATEGORIES = ['Standards','Workflow','Templates','Blocks','Onboarding','UI','Other'];
const PRIORITIES = ['Low','Medium','High'];
const SEVERITIES = ['Low','Medium','High','Critical'];

function normalizeOption(row){
  if(row && typeof row === 'object') return {value:row.value || row.label || '', label:row.label || row.value || ''};
  return {value:row || '', label:row || ''};
}
function fallbackOptions(values){
  return values.map(normalizeOption);
}
function optionMatches(current, option){
  const needle = String(current || '').toLowerCase();
  return [option.value, option.label].some(v => String(v || '').toLowerCase() === needle);
}
function useOptionSet(key, fallback){
  const [options, setOptions] = useState(() => fallbackOptions(fallback));
  useEffect(() => {
    let alive = true;
    const url = CFG.optionUrls && CFG.optionUrls[key];
    if(!url) return () => { alive = false; };
    (async () => {
      try{
        const res = await fetch(url, {headers:{'Accept':'application/json'}});
        if(!res.ok) return;
        const rows = await res.json();
        if(!alive || !Array.isArray(rows) || !rows.length) return;
        setOptions(rows.map(normalizeOption).filter(row => row.value));
      }catch(_){ /* keep fallback options */ }
    })();
    return () => { alive = false; };
  }, [key]);
  return options;
}
const TYPES = [
  { key:'general', icon:I.general, label:'General request', desc:"Not sure where it fits — we'll route it.", route:'Routed to the right team' },
  { key:'project_work', icon:I.project, label:'Project work', desc:'Billable work on a project number.', route:'Project work queue' },
  { key:'cad', icon:I.cad, label:'CAD / Drafting', desc:'Standards, automation, templates.', route:'CAD / Drafting team' },
  { key:'training', icon:I.training, label:'Training', desc:'A skill you or your team needs.', route:'Training coordinator' },
  { key:'suggestion', icon:I.suggestion, label:'Suggestion / Idea', desc:'An improvement to how we work.', route:'Suggestion box' },
  { key:'problem', icon:I.problem, label:'Report a problem', desc:'A recurring issue or capability gap.', route:'Reviewed confidentially' },
];

/* ── project autocomplete — now hits the real endpoint (debounced) ── */
function ProjectInput({ value, onChange, error }){
  const [open, setOpen] = useState(false);
  const [hi, setHi] = useState(0);
  const [matches, setMatches] = useState([]);
  const ref = useRef();
  useEffect(()=>{ const h=e=>{ if(ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown',h); return ()=>document.removeEventListener('mousedown',h); },[]);
  useEffect(()=>{
    const q = (value||'').trim();
    if(!q){ setMatches([]); return; }
    const t = setTimeout(async ()=>{
      try{
        const res = await fetch(CFG.projectSearchUrl+'?q='+encodeURIComponent(q), {headers:{'Accept':'application/json'}});
        if(res.ok){ const data = await res.json(); setMatches((Array.isArray(data)?data:data.items||[]).slice(0,8)); }
      }catch(_){ /* offline / no endpoint yet — autocomplete just stays empty */ }
    }, 180);
    return ()=>clearTimeout(t);
  }, [value]);
  const pick = p => { onChange(p.project_number); setOpen(false); };
  return (
    <div className="ac" ref={ref}>
      <input className={"input mono"+(error?' bad':'')} value={value} placeholder="Type a number or project name…"
        onFocus={()=>setOpen(true)} onChange={e=>{onChange(e.target.value);setOpen(true);setHi(0);}}
        onKeyDown={e=>{ if(e.key==='ArrowDown'){e.preventDefault();setHi(h=>Math.min(h+1,matches.length-1));}
          if(e.key==='ArrowUp'){e.preventDefault();setHi(h=>Math.max(h-1,0));}
          if(e.key==='Enter'&&open&&matches[hi]){e.preventDefault();pick(matches[hi]);} }}/>
      {open && matches.length>0 && (
        <div className="ac-menu">
          {matches.map((p,i)=>(
            <div key={p.project_number} className={"ac-item"+(i===hi?' hi':'')} onMouseEnter={()=>setHi(i)} onClick={()=>pick(p)}>
              <span className="pn">{p.project_number}</span><span className="nm">{p.name}</span><span className="cl">{p.client}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function FileDrop({ files, setFiles }){
  const inp = useRef();
  const add = list => { const arr=[...files]; for(const f of list){ arr.push(f); } setFiles(arr.slice(0,8)); };
  const fmt = b => b<1024?b+' B':b<1048576?(b/1024).toFixed(0)+' KB':(b/1048576).toFixed(1)+' MB';
  return (
    <div>
      <div className="drop" onClick={()=>inp.current.click()}
        onDragOver={e=>e.preventDefault()} onDrop={e=>{e.preventDefault(); add(e.dataTransfer.files);}}>
        <b>Choose files</b> or drag them here · DWG, DXF, PDF, PNG, JPG, XLSX · 50 MB each
      </div>
      <input ref={inp} type="file" multiple style={{display:'none'}} onChange={e=>add(e.target.files)}/>
      {files.length>0 && (
        <div className="files">
          {files.map((f,i)=>(
            <div key={i} className="filechip">
              <span style={{color:'var(--br-navy)'}}><Stroke d={I.paperclip} size={15} w={1.6}/></span>
              <span className="nm">{f.name}</span>
              <span className="sz">{fmt(f.size)}</span>
              <span className="x" onClick={()=>setFiles(files.filter((_,j)=>j!==i))}>&times;</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function Field({ label, req, hint, error, children }){
  return (
    <div className="field">
      {label && <label className="label">{label}{req && <span className="req"> *</span>}{hint && <span className="hint">{hint}</span>}</label>}
      {children}
      {error && <div className="err-msg">{error}</div>}
    </div>
  );
}

const TODAY = new Date().toISOString().slice(0,10);

function IntakeForm(){
  const params = new URLSearchParams(location.search);
  const initial = TYPES.find(t=>t.key===params.get('type')) ? params.get('type') : 'general';
  const [type, setType] = useState(initial);
  const [f, setF] = useState({ project: params.get('project')||'', software:'Either', severity:'Medium', priority:'Medium', who:'Just me' });
  const [files, setFiles] = useState([]);
  const [errors, setErrors] = useState({});
  const [done, setDone] = useState(null);
  const [busy, setBusy] = useState(false);
  const [banner, setBanner] = useState('');
  const set = (k,v) => setF(s=>({...s,[k]:v}));
  const cfg = TYPES.find(t=>t.key===type);
  const openIntake = !CFG.user.signedIn;
  const cadSkillAreas = useOptionSet('cadSkillArea', SKILL_AREAS);
  const trainingSkillAreas = useOptionSet('trainingSkillArea', SKILL_AREAS);
  const billingPhases = useOptionSet('projectBillingPhase', PHASES);
  const suggestionCategories = useOptionSet('suggestionCategory', SUGGESTION_CATEGORIES);
  const taskPriorities = useOptionSet('taskPriority', PRIORITIES);
  const incidentSeverities = useOptionSet('incidentSeverity', SEVERITIES);
  const problemAreas = [
    ...cadSkillAreas.filter(o => !['other','workflow','software'].includes(String(o.value).toLowerCase())),
    {value:'Workflow', label:'Workflow'},
    {value:'Software', label:'Software'},
    {value:'Other', label:'Other'},
  ];

  const reset = ()=>{ setType('general'); setF({project:'',software:'Either',severity:'Medium',priority:'Medium',who:'Just me'});
    setFiles([]); setErrors({}); setDone(null); setBanner(''); window.scrollTo(0,0); };

  async function uploadFiles(inboxId){
    for(const file of files){
      const fd = new FormData(); fd.append('file', file);
      try{ await fetch(CFG.attachUrlBase + inboxId, { method:'POST', headers:{'X-CSRF-Token':CFG.csrfToken}, body:fd }); }
      catch(_){ /* surfaced to triage as "attachment failed"; non-fatal to the submit */ }
    }
  }

  async function submit(){
    const e = {};
    const need = (k,msg)=>{ if(!f[k] || !String(f[k]).trim()) e[k]=msg; };
    if(openIntake) need('submitter_name','Tell us who to follow up with.');
    if(type==='general'){ need('summary','Tell us what you need.'); need('details','Add a little detail.'); }
    if(type==='project_work'){ need('project','A project number is required for project work.'); need('summary','Describe the task.'); }
    if(type==='cad'){ need('summary','What should CAD build or fix?'); }
    if(type==='training'){ need('topic','What skill or topic?'); need('goals','What should they be able to do after?'); }
    if(type==='suggestion'){ need('title','Give your idea a title.'); need('body','Describe your idea.'); }
    if(type==='problem'){ need('details','Describe what happened.'); }
    setErrors(e);
    if(Object.keys(e).length){ const first=document.querySelector('.bad'); if(first) first.scrollIntoView({block:'center'}); return; }

    const summary = f.summary||f.title||f.topic||(f.details||'').slice(0,80)||'(see details)';
    const payload = {
      type, fields: {...f}, priority: f.priority||'Medium',
      severity: type==='problem' ? (f.severity||'Medium') : null,
      desired_by: f.desiredBy||'',
      submitter_name: openIntake ? (f.submitter_name||'') : '',
      submitter_email: openIntake ? (f.submitter_email||'') : '',
    };
    setBusy(true); setBanner('');
    try{
      const res = await fetch(CFG.submitUrl, { method:'POST',
        headers:{'Content-Type':'application/json','X-CSRF-Token':CFG.csrfToken,'Accept':'application/json'},
        body: JSON.stringify(payload) });
      if(!res.ok){ const t = await res.text().catch(()=> ''); throw new Error(t || ('HTTP '+res.status)); }
      const data = await res.json();           // { ref, inbox_id }
      if(files.length && data.inbox_id) await uploadFiles(data.inbox_id);
      setDone({ ref:data.ref, type, cfg, summary, project:f.project,
        priority: type==='problem'?f.severity:f.priority, files:files.length });
      window.scrollTo(0,0);
    }catch(err){ setBanner('Could not submit your request — '+err.message+'. Please try again or contact CAD admin.'); }
    finally{ setBusy(false); }
  }

  if(done){
    return (
      <div className="card done-card">
        <div className="checkwrap"><Stroke d={I.check} size={30} w={2.2}/></div>
        <div className="eyebrow">Request received</div>
        <h1 style={{marginTop:4}}>Thanks — it's in the queue</h1>
        <p className="sub" style={{margin:'6px 0 0'}}>Sent to TaskTrack intake. Someone will pick it up and confirm the details.</p>
        <div className="refbox"><span className="lab">Reference</span><span className="num">{done.ref}</span></div>
        <div className="summary">
          <div className="r"><span className="k">Type</span><span className="v">{done.cfg.label}</span></div>
          <div className="r"><span className="k">Summary</span><span className="v">{done.summary}</span></div>
          {done.project && <div className="r"><span className="k">Project</span><span className="v mono">{done.project}</span></div>}
          <div className="r"><span className="k">{done.type==='problem'?'Severity':'Priority'}</span><span className="v">{done.priority}</span></div>
          {done.files>0 && <div className="r"><span className="k">Attachments</span><span className="v">{done.files} file{done.files>1?'s':''}</span></div>}
          <div className="r"><span className="k">Routed to</span><span className="v">{done.cfg.route}</span></div>
        </div>
        <div className="next">
          <div className="sect-h" style={{marginBottom:0}}>What happens next</div>
          <ol>
            <li>It lands in the TaskTrack intake inbox, flagged for review.</li>
            <li>A reviewer confirms the details and creates a tracked record.</li>
            <li>You'll get an email at pickup, and again when it's done. Quote <span className="mono">{done.ref}</span> if you follow up.</li>
          </ol>
        </div>
        <div style={{display:'flex',gap:12,justifyContent:'center'}}>
          <button className="btn btn-primary" onClick={reset}>Submit another request</button>
        </div>
      </div>
    );
  }

  return (
    <div className="card">
      {banner && <div style={{padding:'16px 22px 0'}}><div className="banner">{banner}</div></div>}

      {openIntake && (
        <div className="sect">
          <div className="sect-h">Your details</div>
          <div className="row">
            <Field label="Your name" req error={errors.submitter_name}>
              <input className={"input"+(errors.submitter_name?' bad':'')} value={f.submitter_name||''} onChange={e=>set('submitter_name',e.target.value)} placeholder="First Last"/>
            </Field>
            <Field label="Email" hint="for follow-up">
              <input className="input" type="email" value={f.submitter_email||''} onChange={e=>set('submitter_email',e.target.value)} placeholder="you@brelje-race.com"/>
            </Field>
          </div>
        </div>
      )}

      <div className="sect">
        <div className="sect-h">What kind of request is this?</div>
        <div className="typegrid">
          {TYPES.map(t=>(
            <button key={t.key} className={"typecard"+(type===t.key?' on':'')} onClick={()=>{setType(t.key);setErrors({});}}>
              <span className="ic"><Stroke d={t.icon}/></span>
              <span className="t">{t.label}</span>
              <span className="d">{t.desc}</span>
            </button>
          ))}
        </div>
      </div>

      <div className="sect">
        <div className="sect-h">{cfg.label} details</div>

        {type==='general' && <>
          <Field label="What do you need?" req error={errors.summary}>
            <input className={"input"+(errors.summary?' bad':'')} value={f.summary||''} onChange={e=>set('summary',e.target.value)} placeholder="One line — e.g. “Vicinity map exhibit for a submittal”"/>
          </Field>
          <Field label="Details" req hint="dates, context, links — whatever helps" error={errors.details}>
            <textarea className={"textarea"+(errors.details?' bad':'')} value={f.details||''} onChange={e=>set('details',e.target.value)}/>
          </Field>
          <Field label="Related project" hint="optional"><ProjectInput value={f.project||''} onChange={v=>set('project',v)}/></Field>
        </>}

        {type==='project_work' && <>
          <Field label="Project number" req error={errors.project}><ProjectInput value={f.project||''} onChange={v=>set('project',v)} error={errors.project}/></Field>
          <Field label="Task summary" req error={errors.summary}>
            <input className={"input"+(errors.summary?' bad':'')} value={f.summary||''} onChange={e=>set('summary',e.target.value)} placeholder="e.g. “Reservoir site grading exhibit”"/>
          </Field>
          <div className="row">
            <Field label="Billing phase" hint="optional">
              <select className="select" value={f.phase||''} onChange={e=>set('phase',e.target.value)}>
                <option value="">— Select phase —</option>{billingPhases.map(p=><option key={p.value} value={p.value}>{p.label}</option>)}
              </select>
            </Field>
            <Field label="Time required" hint="30-min increments">
              <input className="input mono" type="number" min="0" step="30" value={f.time_required_minutes||''} onChange={e=>set('time_required_minutes',e.target.value)} placeholder="30"/>
            </Field>
          </div>
          <Field label="Scheduled completion" hint="optional">
            <input className="input mono" type="datetime-local" step="900" value={f.scheduled_completion_at||''} onChange={e=>set('scheduled_completion_at',e.target.value)}/>
          </Field>
          <Field label="Details"><textarea className="textarea" value={f.details||''} onChange={e=>set('details',e.target.value)} placeholder="Deliverable, scope, anything the engineer should know."/></Field>
        </>}

        {type==='cad' && <>
          <Field label="What should CAD build or fix?" req error={errors.summary}>
            <input className={"input"+(errors.summary?' bad':'')} value={f.summary||''} onChange={e=>set('summary',e.target.value)} placeholder="e.g. “Dynamic block for curb ramp detail”"/>
          </Field>
          <div className="row">
            <Field label="Skill area">
              <select className="select" value={f.skill||''} onChange={e=>set('skill',e.target.value)}>
                <option value="">— Select —</option>{cadSkillAreas.map(s=><option key={s.value} value={s.value}>{s.label}</option>)}
              </select>
            </Field>
            <Field label="Software">
              <div className="seg">{['AutoCAD','Civil 3D','Either'].map(s=>(<button key={s} className={f.software===s?'on':''} onClick={()=>set('software',s)}>{s}</button>))}</div>
            </Field>
          </div>
          <Field label="Related project" hint="optional"><ProjectInput value={f.project||''} onChange={v=>set('project',v)}/></Field>
          <Field label="Details"><textarea className="textarea" value={f.details||''} onChange={e=>set('details',e.target.value)} placeholder="What's happening now, what you'd like instead, example files."/></Field>
        </>}

        {type==='training' && <>
          <Field label="Skill or topic" req error={errors.topic}>
            <select className={"select"+(errors.topic?' bad':'')} value={f.topic||''} onChange={e=>set('topic',e.target.value)}>
              <option value="">— Select training topic —</option>{trainingSkillAreas.map(s=><option key={s.value} value={s.value}>{s.label}</option>)}
            </select>
          </Field>
          <Field label="Who's it for?">
            <div className="seg">{['Just me','My team','Someone specific'].map(s=>(<button key={s} className={f.who===s?'on':''} onClick={()=>set('who',s)}>{s}</button>))}</div>
          </Field>
          {f.who==='Someone specific' && <Field label="Who?"><input className="input" value={f.trainees||''} onChange={e=>set('trainees',e.target.value)} placeholder="Names"/></Field>}
          <Field label="What should they be able to do after?" req error={errors.goals}>
            <textarea className={"textarea"+(errors.goals?' bad':'')} value={f.goals||''} onChange={e=>set('goals',e.target.value)}/>
          </Field>
        </>}

        {type==='suggestion' && <>
          <Field label="Title" req error={errors.title}>
            <input className={"input"+(errors.title?' bad':'')} value={f.title||''} onChange={e=>set('title',e.target.value)} placeholder="Short and punchy"/>
          </Field>
          <Field label="Category">
            <select className="select" value={f.category||''} onChange={e=>set('category',e.target.value)}>
              <option value="">— Select —</option>{suggestionCategories.map(c=><option key={c.value} value={c.value}>{c.label}</option>)}
            </select>
          </Field>
          <Field label="Your idea" req error={errors.body}>
            <textarea className={"textarea"+(errors.body?' bad':'')} value={f.body||''} onChange={e=>set('body',e.target.value)} placeholder="What would you change, and why does it help?"/>
          </Field>
          <Field label="Related project" hint="optional"><ProjectInput value={f.project||''} onChange={v=>set('project',v)}/></Field>
        </>}

        {type==='problem' && <>
          <Field label="What happened?" req error={errors.details}>
            <textarea className={"textarea"+(errors.details?' bad':'')} value={f.details||''} onChange={e=>set('details',e.target.value)} placeholder="Describe the issue — recurring problems are the most useful to flag."/>
          </Field>
          <div className="row">
            <Field label="Area">
              <select className="select" value={f.skill||''} onChange={e=>set('skill',e.target.value)}>
                <option value="">— Select —</option>{problemAreas.map(s=><option key={s.value} value={s.value}>{s.label}</option>)}
              </select>
            </Field>
            <Field label="Severity">
              <div className="seg sev">{incidentSeverities.map(s=>(<button key={s.value} data-sev={s.label} className={optionMatches(f.severity,s)?'on':''} onClick={()=>set('severity',s.value)}>{s.label}</button>))}</div>
            </Field>
          </div>
          <Field label="People or files involved" hint="optional"><input className="input" value={f.involved||''} onChange={e=>set('involved',e.target.value)}/></Field>
          <Field label="Related project" hint="optional"><ProjectInput value={f.project||''} onChange={v=>set('project',v)}/></Field>
        </>}
      </div>

      <div className="sect">
        <div className="sect-h">Timing &amp; attachments</div>
        {type!=='problem' && type!=='suggestion' && (
          <div className="row">
            <Field label="Urgency">
              <div className="seg">{taskPriorities.map(s=>(<button key={s.value} className={optionMatches(f.priority,s)?'on':''} onClick={()=>set('priority',s.value)}>{s.label}</button>))}</div>
            </Field>
            <Field label="Needed by" hint="optional">
              <input className="input mono" type="date" min={TODAY} value={f.desiredBy||''} onChange={e=>set('desiredBy',e.target.value)}/>
            </Field>
          </div>
        )}
        <Field label="Attachments" hint="optional"><FileDrop files={files} setFiles={setFiles}/></Field>
      </div>

      <div className="foot">
        <button className="btn btn-primary" disabled={busy} onClick={submit}>{busy?'Submitting…':'Submit request'}</button>
        <span className="routehint"><span className="dot"></span>{cfg.route}</span>
      </div>
    </div>
  );
}

function Page(){
  const u = CFG.user;
  return (
    <div>
      <div className="hdr">
        <img src={CFG.logoUrl} alt="Brelje & Race Consulting Engineers"
          onError={e=>{e.target.style.display='none'; e.target.nextSibling.style.display='block';}}/>
        <span className="logo-fallback" style={{display:'none'}}>Brelje &amp; Race</span>
        {u.signedIn && (
          <div className="sso">
            <div className="who"><b>{u.name}</b><br/><span>signed in</span></div>
            <div className="ava">{u.initials}</div>
          </div>
        )}
      </div>
      <div className="accent"></div>
      <div className="wrap">
        <div className="eyebrow">Brelje &amp; Race · Internal</div>
        <h1>Submit a request</h1>
        <p className="sub">Anything you need from the team — project work, CAD, training, an idea, or a problem to flag. It goes straight to our TaskTrack intake queue.</p>
        <IntakeForm/>
        <p className="formnote">
          {u.signedIn
            ? "You're signed in, so we already know who's asking — no need to add your name. "
            : ""}
          Sensitive reports (problems &amp; capability concerns) are reviewed confidentially by a principal.</p>
      </div>
      <div className="pagefoot">Brelje &amp; Race Consulting Engineers — Request intake — 2026</div>
    </div>
  );
}
createRoot(document.getElementById('root')).render(<Page/>);
