import React, {useEffect, useState, useRef} from 'react';
import {createRoot} from 'react-dom/client';
import {Training} from './Training';
import {HardwareAdvice} from './HardwareAdvice';
import {preferredVoiceId} from './voice-selection.mjs';

// 统一访问已认证的本机 API；有数据时提交 JSON，失败保留服务端中文提示。
async function api(path, data) {
  const res = await fetch('/api/' + path, data === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  const body = await res.json();
  if (!res.ok) throw Error(body.error || '操作失败');
  return body;
}
const statuses = {queued:'等待中', running:'处理中', cancelling:'停止中', cancelled:'已取消', done:'完成', failed:'失败', interrupted:'已中断'};
const busyStates = ['queued','running','cancelling'];
// 复用表单标签结构，保持输入项排版和点击标签聚焦的语义。
function Field({label,children}) {return <label className="field"><span>{label}</span>{children}</label>}
// 工作台入口：轮询真实任务状态，保留用户当前项目和页面选择。
function App() {
  const [state,setState]=useState(null), [page,setPage]=useState('配音工作台'), [pid,setPid]=useState('');
  const [error,setError]=useState(''), [notice,setNotice]=useState(''),[pending,setPending]=useState(false),[log,setLog]=useState('');
  const [checks,setChecks]=useState([]);
  const initialPage=useRef(false);
  // 仅首次加载决定引导页；后续状态刷新不应把用户带离当前页面。
  useEffect(()=>{if(state&&!initialPage.current){initialPage.current=true;if(!state.voices.length)setPage('训练工作台')}},[state]);
  // 拉取服务端快照，不从前端计时推测任务进度。
  const refresh=async()=>setState(await api('state'));
  // 交换一次性启动地址中的令牌后清除地址栏令牌；卸载时清理轮询定时器。
  useEffect(()=>{let mounted=true; (async()=>{
    const token=location.hash.slice(1);
    if(token){history.replaceState(null,'','/'); const r=await fetch('/connect',{method:'POST',headers:{'x-start-token':token}}); if(!r.ok) throw Error('启动凭据无效，请重新打开软件');}
    if(mounted) await refresh();
  })().catch(e=>setError(e.message)); const id=setInterval(()=>refresh().catch(e=>setError(e.message)),2000); return()=>{mounted=false;clearInterval(id)}},[]);
  // 包装异步按钮操作：锁定提交、展示错误，并在成功后刷新真实状态。
  const act=async(fn,message='已保存')=>{setPending(true);setError('');setNotice('');try{const result=await fn();await refresh();setNotice(message);return result}catch(e){setError(e.message)}finally{setPending(false)}};
  if(!state) return <main><h1>声线工作室</h1><p>{error || '正在读取本地项目…'}</p></main>;
  const project=state.projects.find(p=>p.id===pid)||state.projects[0];
  const working=project&&state.jobs.some(j=>j.project_id===project.id&&busyStates.includes(j.state));
  const nav=['配音工作台','训练工作台','我的声线','作品库'];
  const latestJob=project&&state.jobs.find(j=>j.project_id===project.id);
  // 只提交所选生成模式；实际缓存判断和排队由后端负责。
  const generate=(mode,segment_id)=>act(()=>api(`projects/${project.id}/generate`,{mode,segment_id}),'任务已提交；完成片段会自动保存。');
  return <div className="shell"><aside><div className="brand"><b>声线<span>工作室</span></b><small>VOICE STUDIO / LOCAL</small></div><nav>{nav.map(n=><button key={n} className={page===n?'selected':''} onClick={()=>setPage(n)}>{n==='配音工作台'?'开始配音':n}</button>)}<details className="navmore"><summary>更多工具</summary>{['训练工作台','任务中心','环境设置'].map(n=><button key={n} className={page===n?'selected':''} onClick={()=>setPage(n)}>{n}{n==='任务中心'&&` · ${state.jobs.filter(j=>busyStates.includes(j.state)).length}`}</button>)}</details></nav><div className="privacy"><i/>录音与文案留在本机<small>声线训练一次，后续直接使用。</small></div></aside><main><header><div><small>你的声音，从这里开始</small><h1>{page==='配音工作台'?'开始配音':page}</h1></div><span className="badge">本地保存</span></header>
    {error&&<div role="alert" className="error">{error}<button onClick={()=>setError('')}>关闭</button></div>}
    {notice&&<div className="notice">{notice}</div>}
    {page==='配音工作台'&&<><section className="intro"><h2>写好文案，剩下的交给声音。</h2><p>① 输入文案　→　② 试听与生成　→　③ 下载音频和字幕</p></section><details className="panel" open={!state.projects.length}><summary>＋ 输入新文案</summary><NewProject voices={state.voices} pending={pending} create={data=>act(async()=>{const p=await api('projects',data);setPid(p.id);return p},'文案已保存。先试听首句，满意后生成全文。')}/></details>
    {project&&<><div className="toolbar"><select aria-label="选择项目" value={project.id} onChange={e=>setPid(e.target.value)}>{state.projects.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select><span>{project.voice.name} · {project.segments.length} 句</span><button disabled={working||pending} onClick={()=>generate('preview')}>试听首句</button><button className="primary" disabled={working||pending} onClick={()=>generate('all')}>{working?'正在生成…':project.exports.length?'更新音频与字幕':'生成音频与字幕'}</button></div>
      {latestJob&&<div className="taskstrip" role="status"><span>{statuses[latestJob.state]} · {latestJob.message}（{latestJob.completed} / {latestJob.total}）</span><button onClick={()=>setPage('任务中心')}>查看任务</button></div>}
      <ExportResult project={project}/>
      <Options key={project.id} project={project} disabled={working||pending} save={data=>act(()=>api(`projects/${project.id}/options`,data))}/>
      <h2 className="sectiontitle">文案与逐句试听 <small>需要调整时，点“修改这一句”</small></h2>
      {project.segments.map((s,i)=><Segment key={s.id} segment={s} index={i} projectId={project.id} disabled={working||pending} save={data=>act(()=>api(`projects/${project.id}/segments/${s.id}`,data))} redo={()=>generate('segment',s.id)}/>)}
      {project.dirty&&<p className="muted">文案还未合成为最新成品。生成时只处理有变化的音频，并自动更新字幕时间；只修改字幕不会重新配音。</p>}
    </>}</>}
    {page==='我的声线'&&<><p className="lead">训练好的声线会长期保存在本机。之后创建配音项目直接选用，不必每次训练。</p><div className="cards">{state.voices.map(v=><article className="panel" key={v.id}><span className="avatar">声</span><h3>{v.name}</h3><p>{v.engine_version} · {v.status==='candidate'?'候选，待试听':v.status==='published'?'已发布，可重复使用':'已导入，可重复使用'}</p><small>请在配音工作台选择此声线，用新文案试听。</small>{v.status==='candidate'&&<button disabled={pending} onClick={()=>act(()=>api(`voices/${v.id}/publish`,{listened:true}),'声线已发布。以后可直接选用，无需重训。')}>我已试听新文案，确认发布</button>}</article>)}</div><VoiceForm pending={pending} submit={d=>act(()=>api('voices',d),'声线已导入，可创建配音项目试听。')}/></>}
    {page==='训练工作台'&&<Training state={state} api={api} act={act} pending={pending}/>}
    {page==='任务中心'&&<><p className="lead">配音按完成片段计数，训练按真实阶段计数。阶段计数不代表总耗时百分比。</p>{state.jobs.length===0&&<section className="panel">还没有任务。</section>}{state.jobs.map(j=><section className="panel job" key={j.id}><div><span className={'badge '+j.state}>{statuses[j.state]}</span><h3>{j.name||state.projects.find(p=>p.id===j.project_id)?.name||j.project_id}</h3><p>{j.message}</p><small>{j.completed} / {j.total} {j.kind==='training'?'阶段完成':'段通过验证'}</small></div><div>{busyStates.includes(j.state)&&<button disabled={pending||j.state==='cancelling'} onClick={()=>act(()=>api(`jobs/${j.id}/cancel`,{}),'已提交取消请求。')}>取消任务</button>}{['failed','cancelled','interrupted'].includes(j.state)&&j.kind!=='training'&&<button disabled={pending} onClick={()=>act(()=>api(`jobs/${j.id}/retry`,{}),'已重新排队。')}>重试</button>}<button onClick={()=>act(async()=>setLog((await api(`jobs/${j.id}/log`)).text),'已读取日志')}>查看日志</button></div></section>)}{log&&<pre className="panel">{log}</pre>}</>}
    {page==='作品库'&&<><p className="lead">成品是不可变版本。修改文案不会覆盖历史音频。当前提供句级 SRT，不包含逐字高亮或视频字幕烧录。</p>{state.projects.flatMap(p=>p.exports.map((e,i)=>{const base=`/api/projects/${p.id}/exports/${e.id}/`;return <section className="panel" key={e.id}><div className="toolbar"><h3>{p.name} · 第 {i+1} 版</h3><span>{new Date(e.created*1000).toLocaleString()}</span></div><audio controls src={base+'narration.wav'}/><div className="toolbar">{[['narration.wav','完整 WAV'],['subtitles.srt','字幕 SRT'],['timeline.json','剪辑时间线'],['delivery.zip','完整交付包 ZIP']].map(([f,t])=><a key={f} href={base+f} download={f}>{t} ↓</a>)}</div><small>解压交付包后，音频、字幕与时间线处于同一目录；尚未自动启动剪辑软件。</small></section>}))}{!state.projects.some(p=>p.exports.length)&&<section className="panel">还没有完整成品。生成全部片段后会自动合成。</section>}</>}
    {page==='环境设置'&&<><Settings current={state.settings} history={state.environment_history||[]} pending={pending} submit={d=>act(()=>api('settings',d),'环境路径已保存，新任务使用此配置；下次启动服务也会沿用。尚未证明训练兼容，没有安装或升级依赖。')}/><section className="panel"><button disabled={pending} onClick={()=>act(async()=>setChecks(await api('check')),'检测完成；基础检查通过不等于完整训练已验收。')}>检查环境与基础运算</button><p className="muted">会运行小型依赖和显卡运算检查，可能需要约一分钟；有任务时跳过运算，不安装或下载任何文件。</p>{checks.map((c,i)=><p key={i} className={c.ok?'good':'bad'}>{c.ok?'✓':'!'} {c.name}：{c.detail}</p>)}<a href="/api/diagnostics" download="voice-diagnostics.json">下载脱敏诊断摘要</a><p className="muted">摘要不含录音、文案、本机路径或原始日志。若需定位具体错误，请在任务中心查看日志，分享前检查私人信息。</p></section><details className="panel"><summary>可选：只读硬件检测</summary><HardwareAdvice api={api} act={act} pending={pending}/></details></>}
  </main></div>
}
// 新建项目表单：默认只选已发布声线，创建后由项目固定声线快照。
function NewProject({voices,pending,create}){
 const [name,N]=useState(''),[text,T]=useState(''),[voice,V]=useState(()=>preferredVoiceId(voices));
 const selected=voices.find(v=>v.id===voice);
 return <form onSubmit={e=>{e.preventDefault();if(selected)create({name,text,voice_id:selected.id})}}>
 <div className="grid"><Field label="项目名称"><input value={name} onChange={e=>N(e.target.value)} placeholder="例如：中位数讲解"/></Field>
 <Field label="选择声线"><select required value={selected?.id||''} onChange={e=>V(e.target.value)}><option value="" disabled>请选择声线（不会自动使用新导入版本）</option>{voices.map(v=><option key={v.id} value={v.id}>{v.name} · {v.status==='published'?'已发布':v.status==='candidate'?'候选，需试听':'导入版，需试听'}</option>)}</select></Field></div>
 <p className="muted">优先选择已发布声线；新增或刷新列表不会改变当前选择。已有项目保留创建时的声线。</p>
 {selected&&<p className={selected.status==='published'?'muted':'bad'}>本次参考录音原文：{selected.prompt_text}{selected.status!=='published'?'（尚非已发布版本，请先试听）':''}</p>}
 <Field label="原始文案"><textarea required rows="5" value={text} onChange={e=>T(e.target.value)} placeholder="粘贴你的中文文案。系统按标点分段，保留原文。"/></Field><button className="primary" disabled={pending||!selected}>保存并分段</button></form>
}
// 显示最近一次不可变成品；存在未合成修改时明确标注为旧版。
function ExportResult({project:p}) {
  const e=p.exports[p.exports.length-1];
  if(!e) return null;
  const base=`/api/projects/${p.id}/exports/${e.id}/`;
  return <section className={'panel result '+(p.dirty?'stale':'')}><div className="resultheading"><div><small>{p.dirty?'上一次成品 · 尚未包含当前修改':'音频与字幕已就绪'}</small><h2>试听一下，就可以导出了。</h2></div><span className="badge">第 {p.exports.length} 版</span></div><audio aria-label="完整配音试听" controls preload="metadata" src={base+'narration.wav'}/><div className="downloads"><a className="download primary" href={base+'narration.wav'} download="narration.wav">下载音频 WAV</a><a className="download" href={base+'subtitles.srt'} download="subtitles.srt">下载字幕 SRT</a><details><summary>更多导出</summary><div className="toolbar"><a href={base+'delivery.zip'} download="delivery.zip">音频、字幕与分段打包 ZIP</a><a href={base+'timeline.json'} download="timeline.json">剪辑时间线</a></div></details></div><p className="muted">SRT 是独立字幕文件，按每句实际音频时长生成。下载后可导入剪辑软件；不是逐字字幕，也不会自动把字幕烧录进视频。</p>{p.dirty&&<p className="bad">以上下载仍是旧版本。请先点击“更新音频与字幕”，再下载最新成品。</p>}</section>;
}
// 分开编辑朗读与字幕文本；未保存时禁止重做，旧音频仍可试听。
function Segment({segment:s,index,projectId,disabled,save,redo}){
  const [spoken,S]=useState(s.spoken),[display,D]=useState(s.display);
  const changed=spoken!==s.spoken||display!==s.display;
  return <section className="segment"><div className="sentence"><small>{String(index+1).padStart(2,'0')}</small><p>{s.display}</p><span className={'badge '+(s.current?'done':'')}>{s.current?'已生成':s.audio?'待更新':'未生成'}</span></div>
    {s.audio&&<audio aria-label={`第${index+1}句试听`} preload="metadata" controls src={`/api/projects/${projectId}/segments/${s.id}/audio?v=${s.audio.created}`}/>}
    <details className="sentenceedit"><summary>修改这一句{changed?' · 有未保存修改':''}</summary><div className="grid"><Field label="读出来的文字"><textarea aria-label={`第${index+1}段朗读`} disabled={disabled} value={spoken} onChange={e=>S(e.target.value)}/></Field><Field label="字幕上显示的文字"><textarea aria-label={`第${index+1}段字幕`} disabled={disabled} value={display} onChange={e=>D(e.target.value)}/></Field></div><button disabled={disabled||spoken===display} onClick={()=>D(spoken)}>将朗读文字填入字幕</button><p className="muted">两种文字不会自动互相覆盖。例如：朗读“q 的三次方”，字幕显示“q³”。普通文案可点击上方按钮同步，修改后先保存，再更新成品。</p><details><summary>查看原文与保留记录</summary><p>{s.original}</p><small>已保留 {s.history.length} 份历史音频。</small></details><div className="toolbar"><button disabled={disabled||!changed} onClick={()=>save({spoken,display})}>保存修改</button><button disabled={disabled||changed} onClick={redo}>只重做这一句</button></div></details></section>
}
// 编辑语速和停顿，不直接触发配音；后端校验范围并使相关成品失效。
function Options({project:p,disabled,save}){const [speed,S]=useState(p.speed),[gap,G]=useState(p.gap_ms);return <details className="options"><summary>语速与停顿设置</summary><form className="toolbar" onSubmit={e=>{e.preventDefault();save({speed,gap_ms:gap})}}><Field label="语速（0.6～1.5）"><input type="number" min="0.6" max="1.5" step="0.05" value={speed} onChange={e=>S(e.target.value)}/></Field><Field label="句间停顿（毫秒）"><input type="number" min="0" max="3000" value={gap} onChange={e=>G(e.target.value)}/></Field><button disabled={disabled}>保存参数</button></form></details>}
// 收集模型、参考录音和授权信息，要求用户明确确认可信来源。
function VoiceForm({pending,submit}){return <form className="panel" onSubmit={e=>{e.preventDefault();const f=new FormData(e.currentTarget);submit({...Object.fromEntries(f),authorized:f.has('authorized'),trusted:f.has('trusted')})}}><h2>导入已有声线</h2><div className="grid">{[['name','声线名称','我的讲解声线'],['gpt','GPT 权重（.ckpt）','粘贴本机完整路径'],['sovits','SoVITS 权重（.pth）','粘贴本机完整路径'],['reference','参考录音（3～10 秒 PCM WAV）','粘贴本机完整路径']].map(([name,label,placeholder])=><Field key={name} label={label}><input name={name} required placeholder={placeholder}/></Field>)}</div><Field label="参考录音原文（必须与声音一致）"><textarea name="prompt_text" required rows="2"/></Field><Field label="授权来源说明"><input name="authorization" required placeholder="例如：本人录制，本人拥有使用权"/></Field><label className="check"><input type="checkbox" name="authorized" required/>我有权使用此录音和声线。</label><label className="check"><input type="checkbox" name="trusted" required/>这些模型来自可信来源，我理解未知权重可能包含不安全内容。</label><button className="primary" disabled={pending}>导入声线</button></form>}
// 配置用户选择的本地引擎与旧工作流，保留原环境依赖。
function Settings({current,history=[],pending,submit}){
  const [choice,setChoice]=useState('current');
  const s=choice==='current'?current:(history.find(h=>h.id===choice)?.settings||current);
  return <section className="panel"><h2>选择已有环境</h2><p>训练、转写和配音共用所选配置；运行任务时不能切换。保存前检查依赖及基础显卡运算，失败保留原配置；通过仍不代表完整训练已验收。</p>
    <p>当前：{current.environment_name||current.engine||'尚未配置'}</p>
    <label className="field">填写来源<select value={choice} onChange={e=>setChoice(e.target.value)}><option value="current">当前配置</option>{history.map(h=><option key={h.id} value={h.id}>恢复历史：{h.settings.environment_name||h.settings.engine} · {new Date(h.created*1000).toLocaleString()}</option>)}</select></label>
    <form key={choice+'|'+(s.runtime||'')} onSubmit={e=>{e.preventDefault();const f=new FormData(e.currentTarget);submit({...Object.fromEntries(f),trusted:f.has('trusted')})}}>
      {[['environment_name','环境名称（便于区分）'],['engine','GPT-SoVITS v2Pro 引擎目录'],['runtime','Python 完整路径（留空使用引擎内 runtime/python.exe）'],['legacy','旧配音工作流目录'],['ffmpeg','FFmpeg 可执行文件（留空自动检测）']].map(([key,label])=><Field key={key} label={label}><input name={key} defaultValue={s[key]||''} required={['engine','legacy'].includes(key)}/></Field>)}
      <label className="check"><input type="checkbox" name="trusted" required/>我确认环境来自可信来源，允许运行；已有模型和录音不会被移动。</label>
      <button className="primary" disabled={pending}>验证环境并保存</button>
    </form><p className="muted">历史配置可重新确认后恢复。不会安装依赖或自动重启；当前服务进程保持原 Python，重新启动服务后才更换服务自身环境。</p></section>;
}
createRoot(document.getElementById('root')).render(<App/>);
