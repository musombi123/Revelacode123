import React, { useEffect, useMemo, useState } from 'react';

type Template = {
  id: string; domain: string; task: string; inputs: Record<string, string>;
  example_input: Record<string, unknown>; example_output: unknown; provenance: string;
};
const API = (import.meta as any).env?.VITE_LEGEND_API_BASE || 'http://localhost:8000';

export default function ProphecyDashboard() {
  const [health,setHealth]=useState<any>(null);
  const [templates,setTemplates]=useState<Template[]>([]);
  const [selected,setSelected]=useState('');
  const [inputs,setInputs]=useState('{}');
  const [task,setTask]=useState('');
  const [result,setResult]=useState<any>(null);
  const [adminKey,setAdminKey]=useState('');
  const [candidate,setCandidate]=useState('');
  const [chatPrompt,setChatPrompt]=useState('');
  const [copied,setCopied]=useState(false);
  const [teacherTask,setTeacherTask]=useState('');
  const [teacherSchema,setTeacherSchema]=useState('{"length":"number","width":"number"}');
  const [teacherTriggers,setTeacherTriggers]=useState('rectangle area, area of rectangle');
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const chosen=useMemo(()=>templates.find(x=>x.id===selected),[templates,selected]);

  async function call(path:string,init:RequestInit={},protectedCall=false) {
    const headers:Record<string,string>={'Content-Type':'application/json',...((init.headers as any)||{})};
    if(protectedCall && adminKey.trim()) headers['X-Legend-Admin-Key']=adminKey.trim();
    const response=await fetch(API+path,{...init,headers});
    const data=await response.json().catch(()=>({}));
    if(!response.ok) throw new Error(data.detail||('HTTP '+response.status));
    return data;
  }
  async function refresh() {
    setBusy(true); setError('');
    try {
      const [h,l]=await Promise.all([call('/v1/health'),call('/v1/templates')]);
      setHealth(h); setTemplates(l.templates||[]);
      if(!selected && l.templates?.length) {
        setSelected(l.templates[0].id);
        setInputs(JSON.stringify(l.templates[0].example_input,null,2));
      }
    } catch(e:any) { setError(e.message||'Legend API is not reachable'); }
    finally { setBusy(false); }
  }
  useEffect(()=>{refresh();},[]);
  useEffect(()=>{if(chosen)setInputs(JSON.stringify(chosen.example_input,null,2));},[selected]);

  async function composeNow() {
    setBusy(true); setError(''); setResult(null);
    try {
      setResult(await call('/v1/compose',{method:'POST',body:JSON.stringify({
        template_id:selected||undefined,task:task||undefined,inputs:JSON.parse(inputs)
      })}));
    } catch(e:any) { setError(e.message||'Composition failed'); }
    finally { setBusy(false); }
  }
  async function learnImport() {
    setBusy(true); setError('');
    try {
      const data=await call('/v1/learn/import',{method:'POST',body:JSON.stringify({template:JSON.parse(candidate)})},true);
      setResult(data); setCandidate(''); await refresh();
    } catch(e:any) { setError(e.message||'Import failed'); }
    finally { setBusy(false); }
  }
  async function learnTeacher() {
    setBusy(true); setError('');
    try {
      const data=await call('/v1/learn/teacher',{method:'POST',body:JSON.stringify({
        task:teacherTask,inputs:JSON.parse(teacherSchema),
        triggers:teacherTriggers.split(',').map(x=>x.trim()).filter(Boolean)
      })},true);
      setResult(data); await refresh();
    } catch(e:any) { setError(e.message||'Teacher proposal failed'); }
    finally { setBusy(false); }
  }
  function buildChatPrompt() {
    setError('');
    try {
      const schema=JSON.parse(teacherSchema);
      if (!schema || Array.isArray(schema) || typeof schema!=='object') throw new Error('Input schema must be a JSON object');
      const prompt = [
        'You are proposing one reusable procedure for REVELACODE LEGEND using T-Lang v1.',
        'Return ONLY one valid JSON object, with no Markdown fences and no explanation.',
        'Use exactly these top-level keys: version,id,domain,task,triggers,inputs,steps,result,tests,provenance.',
        'Set version to "tlang/1". Use only allowlisted operations from the T-Lang specification.',
        'Do not include Python, JavaScript, shell commands, SQL, network or file operations, loops, recursion, model weights, prompts, private data, credentials, or user data.',
        'Provide at least TWO independent executable test vectors with exact expected outputs. Include a boundary case.',
        'The procedure must do only the requested task. If it cannot be represented with the available operations, return a JSON object with {"error":"unsupported_by_tlang"} instead of inventing operations.',
        'Set provenance to {"kind":"teacher","teacher":"chatgpt"}.',
        '',
        'TASK: '+teacherTask.trim(),
        'INPUT SCHEMA: '+JSON.stringify(schema),
        'TRIGGER PHRASES: '+JSON.stringify(teacherTriggers.split(',').map(x=>x.trim()).filter(Boolean)),
        '',
        'The Home verifier will execute every test. Passing tests does not prove universal truth; do not overclaim.'
      ].join('\n');
      setChatPrompt(prompt);
      setCopied(false);
    } catch (e:any) { setError(e.message || 'Could not build ChatGPT request'); }
  }

  async function copyChatPrompt() {
    if (!chatPrompt) return;
    try {
      await navigator.clipboard.writeText(chatPrompt);
      setCopied(true);
    } catch {
      setError('Clipboard is unavailable. Select and copy the request text manually.');
    }
  }

  async function syncHome() {
    setBusy(true); setError('');
    try { setResult(await call('/v1/sync',{method:'POST'},true)); await refresh(); }
    catch(e:any) { setError(e.message||'Home sync failed'); }
    finally { setBusy(false); }
  }
  async function submitHome() {
    if(!selected)return;
    setBusy(true); setError('');
    try { setResult(await call('/v1/submit',{method:'POST',body:JSON.stringify({template_id:selected})},true)); }
    catch(e:any) { setError(e.message||'Home submission failed'); }
    finally { setBusy(false); }
  }

  return <main style={s.page}>
    <header style={s.header}>
      <div><div style={s.kicker}>REVELACODE RESEARCH / LEGEND V1</div>
        <h1 style={s.title}>Home + Children</h1>
        <p style={s.muted}>A bounded symbolic engine. Every answer must come from a verified procedure.</p></div>
      <button style={s.buttonAlt} onClick={refresh} disabled={busy}>Refresh</button>
    </header>
    <section style={s.metrics}>
      <div style={s.metric}><small>ENGINE</small><b>{health?.engine||'Connecting'}</b></div>
      <div style={s.metric}><small>VERIFIED TEMPLATES</small><b>{health?.templates??'—'}</b></div>
      <div style={s.metric}><small>TEACHER API</small><b>{health?(health.teacher_configured?'Configured':'Not configured'):'—'}</b></div>
      <div style={s.metric}><small>HOME REPOSITORY</small><b>{health?.home_repository||'—'}</b></div>
    </section>
    {error&&<div role="alert" style={s.error}>{error}</div>}
    <section style={s.panel}>
      <div style={s.kicker}>01 / COMPOSER</div><h2 style={s.h2}>Run a verified procedure</h2>
      <div style={s.grid}>
        <label style={s.label}>T-Lang procedure
          <select style={s.input} value={selected} onChange={e=>{setSelected(e.target.value);setTask('');}}>
            <option value="">Choose a procedure</option>
            {templates.map(t=><option key={t.id} value={t.id}>{t.id} — {t.task}</option>)}
          </select>
        </label>
        <label style={s.label}>Or describe a task
          <input style={s.input} value={task} onChange={e=>{setTask(e.target.value);setSelected('');}} placeholder="e.g. calculate percentage change"/>
        </label>
      </div>
      <label style={s.label}>Input values (JSON)
        <textarea style={s.code} rows={5} value={inputs} onChange={e=>setInputs(e.target.value)} spellCheck={false}/>
      </label>
      <button style={s.button} disabled={busy} onClick={composeNow}>{busy?'Working…':'Compose + verify'}</button>
      {result&&<div style={s.result}><b>Result</b><pre style={s.pre}>{JSON.stringify(result,null,2)}</pre></div>}
    </section>
    <section style={s.grid}>
      <div style={s.panel}>
        <div style={s.kicker}>02 / TEACHER API</div><h2 style={s.h2}>Learn a new procedure</h2>
        <p style={s.muted}>A configured OpenAI-compatible teacher proposes T-Lang. The verifier runs its test vectors before storage.</p>
        <label style={s.label}>Task<input style={s.input} value={teacherTask} onChange={e=>setTeacherTask(e.target.value)} placeholder="calculate rectangle area"/></label>
        <label style={s.label}>Input schema<textarea style={s.code} rows={3} value={teacherSchema} onChange={e=>setTeacherSchema(e.target.value)}/></label>
        <label style={s.label}>Triggers<input style={s.input} value={teacherTriggers} onChange={e=>setTeacherTriggers(e.target.value)}/></label>
        <button style={s.button} disabled={busy||!health?.teacher_configured||!teacherTask.trim()} onClick={learnTeacher}>Request + verify via teacher API</button>
        <button style={s.buttonAlt} disabled={busy||!teacherTask.trim()} onClick={buildChatPrompt}>Generate ChatGPT learning request</button>
        {!health?.teacher_configured&&<p style={s.note}>Teacher API is not configured. The ChatGPT request bridge below works without an API key.</p>}
      </div>
      <div style={s.panel}>
        <div style={s.kicker}>03 / CHATGPT BRIDGE</div><h2 style={s.h2}>Import T-Lang from ChatGPT</h2>
        <p style={s.muted}>Generate the request in the Teacher panel, paste it into ChatGPT, then paste the returned JSON object here. Only the validated T-Lang record is stored.</p>
        {chatPrompt&&<>
          <label style={s.label}>ChatGPT learning request<textarea style={s.code} rows={8} value={chatPrompt} readOnly spellCheck={false}/></label>
          <button style={s.buttonAlt} onClick={copyChatPrompt}>{copied?'Copied request':'Copy request for ChatGPT'}</button>
        </>}
        <label style={s.label}>Returned T-Lang JSON<textarea style={s.code} rows={10} value={candidate} onChange={e=>setCandidate(e.target.value)} placeholder='{"version":"tlang/1", ...}' spellCheck={false}/></label>
        <button style={s.buttonAlt} disabled={busy||!candidate.trim()} onClick={learnImport}>Verify + store</button>
      </div>
    </section>
    <section style={s.panel}>
      <div className="kicker" style={s.kicker}>04 / HOME REPOSITORY</div><h2 style={s.h2}>Sync and submit</h2>
      <p style={s.muted}>Home is the GitHub library. A child submits new T-Lang as a pull request; the trusted GitHub Action verifies and auto-merges eligible data-only templates.</p>
      <label style={s.label}>Admin control key (runtime only; never stored in browser storage)
        <input style={s.input} type="password" value={adminKey} onChange={e=>setAdminKey(e.target.value)} autoComplete="off" placeholder="LEGEND_ADMIN_API_KEY"/>
      </label>
      <div style={s.actions}><button style={s.buttonAlt} disabled={busy||!adminKey.trim()} onClick={syncHome}>Sync from Home</button>
        <button style={s.button} disabled={busy||!adminKey.trim()||!selected} onClick={submitHome}>Submit selected template</button></div>
    </section>
    <footer style={s.footer}>Unknown means unknown. T-Lang procedures are algorithms, not transferred model weights.</footer>
  </main>;
}

const s:Record<string,React.CSSProperties>={
  page:{maxWidth:1120,margin:'0 auto',padding:'28px 20px 56px',color:'#eaf0fa',fontFamily:'Inter,system-ui,sans-serif'},
  header:{display:'flex',justifyContent:'space-between',alignItems:'center',gap:18,flexWrap:'wrap',marginBottom:22},
  kicker:{color:'#82aaf0',fontSize:11,fontWeight:800,letterSpacing:'0.14em'},
  title:{fontSize:'clamp(30px,5vw,48px)',letterSpacing:'-.04em',margin:'8px 0'},
  h2:{fontSize:21,margin:'7px 0 14px'},
  muted:{color:'#9eabc0',fontSize:13,lineHeight:1.65},
  metrics:{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(180px,1fr))',gap:10},
  metric:{background:'#111a2a',border:'1px solid #293953',borderRadius:14,padding:15,display:'grid',gap:8},
  panel:{background:'#111a2a',border:'1px solid #293953',borderRadius:17,padding:20,marginTop:14,minWidth:0},
  grid:{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(min(100%,390px),1fr))',gap:14},
  label:{display:'grid',gap:8,margin:'12px 0',fontSize:13,color:'#c0cce0'},
  input:{boxSizing:'border-box',width:'100%',background:'#0a1220',color:'#eff4ff',border:'1px solid #384a66',borderRadius:9,padding:'12px',minHeight:44,font:'inherit'},
  code:{boxSizing:'border-box',width:'100%',background:'#080e18',color:'#c6e6d2',border:'1px solid #384a66',borderRadius:9,padding:12,fontFamily:'ui-monospace,monospace',fontSize:12,lineHeight:1.55},
  button:{background:'#2563eb',color:'#fff',border:0,borderRadius:9,padding:'12px 16px',fontWeight:750,cursor:'pointer'},
  buttonAlt:{background:'#1c2c46',color:'#edf3ff',border:'1px solid #415675',borderRadius:9,padding:'11px 15px',fontWeight:700,cursor:'pointer'},
  actions:{display:'flex',gap:10,flexWrap:'wrap',marginTop:12},
  error:{background:'#3a1921',border:'1px solid #8d3544',color:'#ffd9df',padding:12,borderRadius:9,marginTop:14},
  note:{color:'#d5bd7e',fontSize:12},
  result:{background:'#091220',border:'1px solid #3c506d',borderRadius:10,padding:14,marginTop:15,overflow:'hidden'},
  pre:{whiteSpace:'pre-wrap',wordBreak:'break-word',fontSize:12,lineHeight:1.6,color:'#c9d9ed',maxHeight:350,overflow:'auto'},
  footer:{padding:22,textAlign:'center',fontSize:12,color:'#7888a0'}
};
