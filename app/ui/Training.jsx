import React,{useState,useEffect} from 'react';

export function Training({state,api,act,pending}) {
  const [selected,choose]=useState(''),[report,setReport]=useState(null),[confirmed,confirm]=useState(false);
  const datasets=state.datasets||[];
  const ds=datasets.find(d=>d.id===selected)||datasets[0];
  const busy=ds&&state.jobs.some(j=>j.project_id===ds.id&&['queued','running','cancelling'].includes(j.state));
  const reviewed=ds?.rows.filter(r=>r.reviewed&&!r.excluded).length||0;
  const included=ds?.rows.filter(r=>!r.excluded).length||0;
  const update=(data)=>act(async()=>{setReport(null);confirm(false);return api(`datasets/${ds.id}`,data)},'已保存校对；原录音与原标注未改动。');
  return <><section className="intro"><h2>训练一次，之后直接选用。</h2><p>① 选择录音 → ② 自动切分转写 → ③ 试听校对 → ④ 确认训练 → ⑤ 保存声线</p></section>
    <p className="lead">原始录音会另存保留；自动转写不等于正确标注。请先校对再训练，声线满意后可反复使用。</p>
    <RecordingImport api={api} act={act} pending={pending} choose={choose}/>
    <details className="panel"><summary>已有切分好的 WAV 和 .list？从这里导入</summary><form onSubmit={e=>{e.preventDefault();const f=new FormData(e.currentTarget);act(async()=>{const d=await api('datasets',{...Object.fromEntries(f),authorized:f.has('authorized')});choose(d.id);return d},'素材已复制到独立项目，请试听校对。')}}>
      <label className="field"><span>声线 / 素材名称</span><input name="name" required placeholder="我的讲解声线"/></label>
      <label className="field"><span>标注文件 .list 的本机完整路径</span><input name="list_path" required placeholder="每行为 音频路径|说话人|ZH|文字"/></label>
      <label className="check"><input type="checkbox" name="authorized" required/>我拥有这些录音和声线的使用授权。</label><button disabled={pending} className="primary">导入并检查素材</button>
    </form></details>
    {ds&&<><div className="toolbar"><select aria-label="选择训练素材" value={ds.id} onChange={e=>{choose(e.target.value);setReport(null);confirm(false)}}>{datasets.map(d=><option key={d.id} value={d.id}>{d.name}</option>)}</select><span>已确认 {reviewed} / {included} 段 · 原始录音保留</span></div>
      {ds.recordings&&<section className="panel"><h3>已导入 {ds.recordings.length} 个原始录音</h3><p>{ds.recordings.map(r=>r.name).join('、')}</p><p>切分与转写使用本机 CPU，完成后逐段校对。再次整理会保留旧版片段与校对记录，并生成新版本。</p><button disabled={pending||busy||!ds.recordings.length} onClick={()=>act(()=>api(`datasets/${ds.id}/prepare`,{}),'整理任务已排队；进度见下方或任务中心。')}>{ds.rows.length?'重新切分转写（保留旧版）':'开始自动切分与转写'}</button></section>}
      {state.jobs.filter(j=>j.project_id===ds.id).slice(0,1).map(j=><div className="taskstrip" role="status" key={j.id}>{j.message} · {j.completed} / {j.total}<button disabled={pending||!['queued','running'].includes(j.state)} onClick={()=>act(()=>api(`jobs/${j.id}/cancel`,{}),'已请求取消，已完成片段会保留。')}>取消任务</button></div>)}
      {!!ds.quality_reports?.length&&<details className="panel"><summary>录音检查结果（不是音质评分）</summary>{ds.quality_reports.map(q=><p key={q.recording_id}>{q.name} · {q.seconds} 秒 · RMS {q.rms_dbfs} dBFS · 接近满幅采样 {(q.clipped_ratio*100).toFixed(2)}%<br/>{q.warnings.join('；')||'未触发音量告警，仍需试听判断噪声、口音和内容。'}</p>)}</details>}
      <details className="panel"><summary>训练参数已预填 · 需要时再调整</summary><TrainingParams key={ds.id} value={ds.params} disabled={pending||busy} save={params=>update({params})}/></details>
      <h2>逐段试听与校对</h2><p className="muted">每段听完后核对文字。听不清或质量不合适的片段可排除；不要把自动转写当作已确认文本。名称带“自动核验试训”的数据集仅用于功能测试，机器核验不等于人工试听。</p>
      {ds.rows.map((r,i)=><ReviewRow key={r.id} row={r} index={i} did={ds.id} disabled={pending||busy} save={data=>update({row_id:r.id,...data})}/>)}
      <section className="panel"><h2>检查训练计划</h2><button disabled={pending||busy} onClick={()=>act(async()=>{setReport(await api(`datasets/${ds.id}/plan`));confirm(false)},'已检查文件与校对状态；尚未开始训练。')}>检查素材、参数与空间</button>
      {report&&<><p>{report.count} 段 / {report.seconds} 秒 · 尚未校对 {report.pending_review} 段</p><p>GPT {report.params.gpt_epochs} 轮 / SoVITS {report.params.sovits_epochs} 轮 / 批大小 {report.params.batch_size}</p><p>{report.disk_note} 当前可用 {report.free_gb} GB。</p><p>{report.time_note}</p>{report.warnings.map(w=><p key={w} className="bad">提示：{w}</p>)}{report.missing.length>0&&<p className="bad">缺少文件：{report.missing.join('；')}</p>}<label className="check"><input type="checkbox" checked={confirmed} onChange={e=>confirm(e.target.checked)}/>我确认标注准确，允许本次训练占用显卡和磁盘；了解短素材不保证效果。</label><button className="primary" disabled={pending||busy||!report.ready||!confirmed||report.revision!==ds.revision} onClick={()=>act(()=>api(`datasets/${ds.id}/train`,{confirmed:true,revision:report.revision}),'训练已排队。可在任务中心查看实际阶段或取消。')}>确认并开始真实训练</button></>}
      </section></>}
  </>
}
function TrainingParams({value,disabled,save}){return <form className="panel" onSubmit={e=>{e.preventDefault();save(Object.fromEntries(new FormData(e.currentTarget)))}}><h2>基础参数已预填</h2><p>针对短素材的保守试训起点，显卡编号固定为 0；训练轮数越多不一定越好。</p><div className="toolbar">{[['gpt_epochs','GPT 轮数',20],['sovits_epochs','SoVITS 轮数',20],['batch_size','批大小',4]].map(([key,label,max])=><label className="field" key={key}><span>{label}</span><input type="number" name={key} min="1" max={max} defaultValue={value[key]} required disabled={disabled}/></label>)}<button disabled={disabled}>保存参数</button></div><small>每次训练创建独立模型版本，不覆盖旧声线。后续配音直接加载已发布模型，无需重新训练。</small></form>}
function ReviewRow({row:r,index,did,disabled,save}) {
  const machine=r.review_method==='secondary_asr_agreement';
  const [text,T]=useState(r.text),[reviewed,R]=useState(r.reviewed&&!machine),[excluded,E]=useState(r.excluded);
  useEffect(()=>{T(r.text);R(r.reviewed&&!machine);E(r.excluded)},[r.id,r.text,r.reviewed,r.excluded]);
  return <section className="panel"><div className="toolbar"><h3>片段 {index+1} · {r.seconds} 秒</h3><span className="badge">{r.excluded?'已排除':machine?'机器交叉核验一致（未人工试听）':r.reviewed?'已校对':'待校对'}</span></div>
    <audio controls preload="none" src={`/api/datasets/${did}/audio/${r.id}`}/>
    {r.asr_error&&<p className="bad">{r.asr_error}</p>}
    {r.quality?.warnings.map((w,i)=><p className="bad" key={i}>{w}</p>)}
    {r.secondary_asr&&<p className="muted">第二次自动转写：{r.secondary_asr}</p>}
    <label className="field"><span>与录音逐字对应的文字</span><textarea aria-label={`训练片段${index+1}文字`} value={text} disabled={disabled} onChange={e=>{T(e.target.value);R(false)}} rows="2"/></label>
    <div className="toolbar"><label className="check"><input type="checkbox" checked={reviewed} disabled={disabled||excluded} onChange={e=>R(e.target.checked)}/>已人工试听，文字准确</label><label className="check"><input type="checkbox" checked={excluded} disabled={disabled} onChange={e=>E(e.target.checked)}/>排除此片段</label><button disabled={disabled} onClick={()=>save({text,reviewed,excluded})}>保存本段</button></div>
  </section>
}

function RecordingImport({api,act,pending,choose}) {
  const [files,setFiles]=useState([]),[status,setStatus]=useState('');
  return <details className="panel" open><summary>＋ 用自己的录音创建声线</summary><form onSubmit={e=>{
    e.preventDefault();const form=new FormData(e.currentTarget);
    act(async()=>{
      if(!files.length) throw Error('请先选择录音文件');
      if(files.length>30||files.some(f=>f.size>512*1024*1024)) throw Error('最多 30 个录音，单文件不超过 512 MB');
      const ds=await api('recording-datasets',{name:form.get('name'),authorization:form.get('authorization'),authorized:form.has('authorized')});
      choose(ds.id);
      try {
        for(let i=0;i<files.length;i++) {
          setStatus(`正在保存 ${i+1} / ${files.length}：${files[i].name}`);
          const response=await fetch(`/api/datasets/${ds.id}/recordings?filename=${encodeURIComponent(files[i].name)}`,{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:files[i]});
          const body=await response.json();if(!response.ok) throw Error(body.error||'录音导入失败');
        }
      } finally {setStatus('');}
      return ds;
    },'录音已保存在本机。点击“开始自动切分与转写”继续。');
  }}>
    <label className="field"><span>声线名称</span><input name="name" required maxLength="100" placeholder="我的讲解声音" disabled={pending}/></label>
    <label className="field"><span>选择原始录音（可多选）</span><input type="file" accept=".wav,.mp3,.m4a,.flac,.ogg" multiple required disabled={pending} onChange={e=>setFiles(Array.from(e.target.files))}/></label>
    <p className="muted">支持 WAV、MP3、M4A、FLAC、OGG；每个文件不超过 30 分钟 / 512 MB。用清晰、无背景音乐、只有本人说话的录音；不承诺任意素材都能训练出优质声音。</p>
    <label className="field"><span>授权来源</span><input name="authorization" required placeholder="例如：本人录制并授权用于声线训练" disabled={pending}/></label>
    <label className="check"><input type="checkbox" name="authorized" required disabled={pending}/>我拥有录音和声线使用授权。</label>
    <button className="primary" disabled={pending}>保存录音，进入整理</button><p role="status">{status}</p>
  </form></details>
}
