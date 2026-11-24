import React, {useState, useEffect} from 'react';

// 训练向导只展示真实素材和任务状态；计划变化后必须重新确认训练。
export function Training({state, api, act, pending, queuePaused = false, Icon}) {
  const datasets = state.datasets || [];
  const [selected, choose] = useState('');
  const [createOpen, setCreateOpen] = useState(() => datasets.length === 0);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [filter, setFilter] = useState('all');
  const [report, setReport] = useState(null);
  const [confirmed, confirm] = useState(false);
  const [checkingPlan, setCheckingPlan] = useState(false);

  const ds = datasets.find(d => d.id === selected) || datasets[0];
  const busy = ds && state.jobs.some(j => j.project_id === ds.id && ['queued', 'running', 'cancelling'].includes(j.state));
  const locked = busy || queuePaused;
  const rows = ds?.rows || [];
  const included = rows.filter(r => !r.excluded).length;
  const reviewed = rows.filter(r => r.reviewed && !r.excluded).length;
  const pendingReview = rows.filter(r => !r.reviewed && !r.excluded).length;
  const excludedCount = rows.filter(r => r.excluded).length;
  const reviewPct = included > 0 ? Math.round((reviewed / included) * 100) : 0;
  const activeJob = ds ? state.jobs.find(j => j.project_id === ds.id) : null;

  // 计算当前素材所处的真实流水线阶段（1 导入 / 2 整理 / 3 校对 / 4 训练）
  const stage = !ds ? 1
    : !rows.length ? 2
    : pendingReview > 0 || included < 2 ? 3
    : 4;

  // 校对或参数变更使旧训练计划失效，防止使用过期修订号直接训练。
  const update = data => act(async () => {
    setReport(null);
    confirm(false);
    return api(`datasets/${ds.id}`, data);
  }, '已保存校对；原录音与原标注未改动。');

  const checkPlan = () => act(async () => {
    setCheckingPlan(true);
    try {
      const next = await api(`datasets/${ds.id}/plan`);
      setReport(next);
      confirm(false);
      return next;
    } finally {
      setCheckingPlan(false);
    }
  }, '已检查文件、依赖与显卡运算；尚未开始训练。');

  const problems = report?.problems || [];
  const tips = (report?.warnings || []).filter(w => !problems.length || !w.startsWith('训练前检查：'));

  const filteredRows = rows.map((r, i) => ({row: r, index: i})).filter(({row}) => {
    if (filter === 'pending') return !row.reviewed && !row.excluded;
    if (filter === 'reviewed') return row.reviewed && !row.excluded;
    if (filter === 'excluded') return row.excluded;
    return true;
  });

  const steps = [
    {num: 1, label: '导入录音'},
    {num: 2, label: '切分转写'},
    {num: 3, label: '试听校对'},
    {num: 4, label: '预检训练'}
  ];

  return <div className="page-stack">
    <section className="stepper-card">
      <div className="stepper">
        {steps.map(s => (
          <div key={s.num} className={'step-item ' + (stage > s.num ? 'done' : stage === s.num ? 'current' : '')}>
            <span className="step-num">{stage > s.num ? '✓' : s.num}</span>
            <span>{s.label}</span>
          </div>
        ))}
      </div>
      <div className="toolbar-tight">
        <button className={createOpen ? 'btn-ghost active' : 'btn-primary'} onClick={() => setCreateOpen(v => !v)}>
          {Icon && <Icon name="plus" size={15}/>}
          <span>{createOpen ? '收起新建' : '新建训练素材'}</span>
        </button>
      </div>
    </section>

    {(createOpen || !ds) && (
      <div className="create-stack">
        <RecordingImport
          api={api}
          act={act}
          pending={pending}
          choose={id => { choose(id); setCreateOpen(false); setReport(null); confirm(false); }}
        />
        <section className="card compact-card">
          <div className="card-head">
            <div>
              <h3>高级入口：导入已切分好的 WAV 与 .list</h3>
              <p className="muted">如果你已有整理好的中文标注清单，可以直接导入跳过自动切分。</p>
            </div>
            <button className="btn-sm" onClick={() => setAdvancedOpen(v => !v)}>
              {advancedOpen ? '收起' : '展开导入'}
            </button>
          </div>
          {advancedOpen && (
            <form style={{marginTop: '14px'}} onSubmit={e => {
              e.preventDefault();
              const f = new FormData(e.currentTarget);
              act(async () => {
                const d = await api('datasets', {...Object.fromEntries(f), authorized: f.has('authorized')});
                choose(d.id);
                setCreateOpen(false);
                setAdvancedOpen(false);
                return d;
              }, '素材已复制到独立项目，请试听校对。');
            }}>
              <div className="form-grid">
                <label className="field">
                  <span className="field-label">声线 / 素材名称</span>
                  <input name="name" required placeholder="我的讲解声线"/>
                </label>
                <label className="field">
                  <span className="field-label">标注文件 .list 的本机完整路径</span>
                  <input name="list_path" required placeholder="每行为 音频路径|说话人|ZH|文字"/>
                </label>
              </div>
              <label className="check"><input type="checkbox" name="authorized" required/>我拥有这些录音和声线的使用授权。</label>
              <button disabled={pending} className="btn-primary">导入并检查素材</button>
            </form>
          )}
        </section>
      </div>
    )}

    {ds && (
      <>
        <section className="studio-bar">
          <div className="studio-bar-left">
            <div className="project-select-wrap">
              <span className="micro-label">当前素材库</span>
              <select aria-label="选择训练素材" value={ds.id} onChange={e => { choose(e.target.value); setReport(null); confirm(false); setFilter('all'); }}>
                {datasets.map(d => <option key={d.id} value={d.id}>{d.name}</option>)}
              </select>
            </div>
            <div className="studio-chips">
              <span className="chip chip-emerald">已校对 {reviewed} / {included} 段</span>
              {ds.recordings && <span className="chip">{ds.recordings.length} 个原始录音</span>}
              <span className="chip">GPT {ds.params.gpt_epochs}轮 · SoVITS {ds.params.sovits_epochs}轮</span>
            </div>
          </div>
          <div className="studio-bar-actions">
            {ds.recordings && (
              <button className="btn-secondary" disabled={pending || locked || !ds.recordings.length} onClick={() => act(() => api(`datasets/${ds.id}/prepare`, {}), '整理任务已排队；进度见下方或任务中心。')}>
                {rows.length ? '重新切分转写（保留旧版）' : '开始自动切分与转写'}
              </button>
            )}
          </div>
        </section>

        {activeJob && (
          <div className={'taskstrip ' + activeJob.state} role="status">
            <div className="taskstrip-main">
              <span className={'badge ' + activeJob.state}>{activeJob.state === 'running' ? '进行中' : activeJob.state === 'done' ? '完成' : activeJob.state}</span>
              <span className="taskstrip-msg">{activeJob.message}</span>
              <span className="mono">{activeJob.completed} / {activeJob.total}</span>
            </div>
            <div className="taskstrip-actions">
              {['queued', 'running'].includes(activeJob.state) && (
                <button className="btn-sm" disabled={pending} onClick={() => act(() => api(`jobs/${activeJob.id}/cancel`, {}), '已请求取消，已完成片段会保留。')}>取消任务</button>
              )}
            </div>
          </div>
        )}

        {(ds.recordings?.length > 0 || ds.quality_reports?.length > 0) && (
          <section className="card compact-card">
            <div className="card-head">
              <div>
                <h3>原始录音与质量体检</h3>
                <p className="muted">原始录音永久留存不覆盖；切分与中文转写全部在本机 CPU 离线完成。</p>
              </div>
              {ds.recordings && <span className="chip">{ds.recordings.length} 份原音频</span>}
            </div>
            {ds.recordings?.length > 0 && (
              <div className="file-pills">
                {ds.recordings.map(r => <span className="file-pill" key={r.id}>{r.name}</span>)}
              </div>
            )}
            {!!ds.quality_reports?.length && (
              <details className="soft-details">
                <summary>查看音量与削波检查（{ds.quality_reports.length} 条，非主观音质评分）</summary>
                <div className="quality-list">
                  {ds.quality_reports.map(q => (
                    <div className="quality-item" key={q.recording_id}>
                      <div className="quality-top">
                        <strong>{q.name}</strong>
                        <span className="mono muted">{q.seconds}s · RMS {q.rms_dbfs} dBFS · 削波 {(q.clipped_ratio * 100).toFixed(2)}%</span>
                      </div>
                      <p className={q.warnings.length ? 'bad' : 'muted'}>
                        {q.warnings.join('；') || '未触发音量告警，仍需试听判断底噪与口音。'}
                      </p>
                    </div>
                  ))}
                </div>
              </details>
            )}
          </section>
        )}

        <section className="card">
          <div className="section-bar" style={{marginBottom: '14px'}}>
            <div>
              <h2>逐段试听与校对</h2>
              <p className="muted">听不清或有杂音的片段请排除；至少保留 2 段已校对素材，其中一段 3～10 秒作参考音频。</p>
            </div>
            <div className="review-meter">
              <span className="mono">{reviewPct}% 已确认</span>
              <div className="progress-track" style={{width: '120px'}}>
                <div className="progress-fill done" style={{width: `${reviewPct}%`}}/>
              </div>
            </div>
          </div>

          {rows.length > 0 && (
            <div className="filter-tabs" role="tablist">
              {[
                ['all', `全部 (${rows.length})`],
                ['pending', `待校对 (${pendingReview})`],
                ['reviewed', `已校对 (${reviewed})`],
                ['excluded', `已排除 (${excludedCount})`]
              ].map(([key, label]) => (
                <button key={key} role="tab" aria-selected={filter === key} className={filter === key ? 'tab-btn active' : 'tab-btn'} onClick={() => setFilter(key)}>
                  {label}
                </button>
              ))}
            </div>
          )}

          {rows.length === 0 ? (
            <div className="inline-empty">
              <p>还没有切分片段。点击上方“开始自动切分与转写”，完成后即可逐段试听校对。</p>
            </div>
          ) : filteredRows.length === 0 ? (
            <div className="inline-empty"><p>当前筛选条件下没有片段。</p></div>
          ) : (
            <div className="review-list">
              {filteredRows.map(({row, index}) => (
                <ReviewRow
                  key={row.id}
                  row={row}
                  index={index}
                  did={ds.id}
                  disabled={pending || locked}
                  save={data => update({row_id: row.id, ...data})}
                />
              ))}
            </div>
          )}
        </section>

        <section className="card preflight-card">
          <div className="card-head">
            <div>
              <h2>训练参数与启动前预检</h2>
              <p className="muted">先校验脚本、基础模型、运行环境依赖与 CUDA 运算，全部通过后再启动真实微调。</p>
            </div>
          </div>

          <TrainingParams key={ds.id} value={ds.params} disabled={pending || locked} save={params => update({params})}/>

          <div className="preflight-trigger">
            <button className="btn-secondary" disabled={pending || busy || checkingPlan} onClick={checkPlan}>
              {checkingPlan ? '正在检查依赖与显卡运算…' : '检查素材、依赖与空间'}
            </button>
            {checkingPlan && <span className="muted">首次加载训练依赖可能需要几十秒，请稍候…</span>}
          </div>

          {report && (
            <div className="preflight-result">
              <div className={'preflight-status ' + (report.ready ? 'ok' : 'blocked')}>
                <strong>{report.ready ? '✓ 检查通过，可以开始真实训练' : '! 检查未通过，已阻止训练入队'}</strong>
                <span className="mono">{report.count} 段 / {report.seconds}s · 未校对 {report.pending_review} 段 · 可用空间 {report.free_gb} GB</span>
              </div>

              {(problems.length > 0 || report.missing.length > 0) && (
                <div className="preflight-issues" role="alert">
                  <strong>阻断项（处理后重新点击检查）</strong>
                  <ul>
                    {problems.map(item => <li key={item}>{item}</li>)}
                    {report.missing.length > 0 && <li>缺少文件或配置：{report.missing.join('；')}</li>}
                  </ul>
                </div>
              )}

              <div className="preflight-tips">
                <p className="muted">{report.disk_note} {report.time_note}</p>
                {tips.map(w => <p key={w} className="muted">提示：{w}</p>)}
              </div>

              <div className="launch-box">
                <label className="check" style={{margin: 0}}>
                  <input type="checkbox" checked={confirmed} onChange={e => confirm(e.target.checked)}/>
                  我确认标注准确，允许本次训练占用显卡与磁盘空间；了解短素材不保证音质效果。
                </label>
                <button
                  className="btn-primary"
                  disabled={pending || locked || !report.ready || !confirmed || report.revision !== ds.revision}
                  onClick={() => act(() => api(`datasets/${ds.id}/train`, {confirmed: true, revision: report.revision}), '训练已排队。可在任务中心查看实际阶段或取消。')}
                >
                  确认并开始真实训练
                </button>
              </div>
            </div>
          )}
        </section>
      </>
    )}
  </div>;
}

// 显示保守试训起点及范围限制；保存后必须重新检查训练计划。
function TrainingParams({value, disabled, save}) {
  const [gpt, setGpt] = useState(value.gpt_epochs);
  const [sovits, setSovits] = useState(value.sovits_epochs);
  const [batch, setBatch] = useState(value.batch_size);
  const dirty = Number(gpt) !== Number(value.gpt_epochs) || Number(sovits) !== Number(value.sovits_epochs) || Number(batch) !== Number(value.batch_size);

  return <form className="params-strip" onSubmit={e => { e.preventDefault(); save({gpt_epochs: gpt, sovits_epochs: sovits, batch_size: batch}); }}>
    <label className="param-box">
      <span>GPT 轮数 (1～20)</span>
      <input type="number" min="1" max="20" value={gpt} required disabled={disabled} onChange={e => setGpt(e.target.value)}/>
    </label>
    <label className="param-box">
      <span>SoVITS 轮数 (1～20)</span>
      <input type="number" min="1" max="20" value={sovits} required disabled={disabled} onChange={e => setSovits(e.target.value)}/>
    </label>
    <label className="param-box">
      <span>批大小 (1～4)</span>
      <input type="number" min="1" max="4" value={batch} required disabled={disabled} onChange={e => setBatch(e.target.value)}/>
    </label>
    <button className={dirty ? 'btn-primary' : 'btn-secondary'} disabled={disabled || !dirty}>
      {dirty ? '保存参数' : '参数已就绪'}
    </button>
  </form>;
}

// 展示和保存单段人工校对，机器转写一致不等同于人工试听确认。
function ReviewRow({row: r, index, did, disabled, save}) {
  const machine = r.review_method === 'secondary_asr_agreement';
  const [text, T] = useState(r.text);
  const [reviewed, R] = useState(r.reviewed && !machine);
  const [excluded, E] = useState(r.excluded);

  // 素材修订同步到本地表单；改变文字时由输入回调取消旧的人工确认。
  useEffect(() => {
    T(r.text);
    R(r.reviewed && !machine);
    E(r.excluded);
  }, [r.id, r.text, r.reviewed, r.excluded]);

  const dirty = text !== r.text || reviewed !== (r.reviewed && !machine) || excluded !== r.excluded;
  const isRefCandidate = !r.excluded && r.seconds >= 3 && r.seconds <= 10;

  return <article className={'review-row ' + (excluded ? 'is-excluded' : reviewed ? 'is-done' : '')}>
    <div className="review-head">
      <div className="cue-meta">
        <span className="cue-num">{String(index + 1).padStart(2, '0')}</span>
        <span className="mono chip">{r.seconds}s</span>
        {isRefCandidate && <span className="chip chip-emerald">可作参考录音</span>}
        <span className={'badge ' + (excluded ? '' : reviewed ? 'done' : 'paused')}>
          {excluded ? '已排除' : machine ? '机器核验（待人工听）' : reviewed ? '已校对' : '待校对'}
        </span>
        {dirty && <span className="badge paused">未保存</span>}
      </div>
      <audio controls preload="none" src={`/api/datasets/${did}/audio/${r.id}`}/>
    </div>

    {r.asr_error && <p className="bad" style={{margin: '8px 0 0', fontSize: '13px'}}>{r.asr_error}</p>}
    {r.quality?.warnings?.map((w, i) => <p className="bad" key={i} style={{margin: '6px 0 0', fontSize: '12px'}}>注意：{w}</p>)}
    {r.secondary_asr && <p className="muted" style={{margin: '6px 0 0', fontSize: '12px'}}>第二次自动转写：{r.secondary_asr}</p>}

    <textarea
      aria-label={`训练片段${index + 1}文字`}
      value={text}
      disabled={disabled}
      onChange={e => { T(e.target.value); R(false); }}
      rows="2"
      placeholder="请输入与录音逐字一致的中文文本"
    />

    <div className="review-footer">
      <div className="toolbar-tight">
        <label className="check-pill">
          <input type="checkbox" checked={reviewed} disabled={disabled || excluded} onChange={e => R(e.target.checked)}/>
          <span>已人工试听，文字准确</span>
        </label>
        <label className="check-pill">
          <input type="checkbox" checked={excluded} disabled={disabled} onChange={e => E(e.target.checked)}/>
          <span>排除此片段</span>
        </label>
      </div>
      <button className={dirty ? 'btn-primary btn-sm' : 'btn-secondary btn-sm'} disabled={disabled} onClick={() => save({text, reviewed, excluded})}>
        {dirty ? '保存本段' : '保存'}
      </button>
    </div>
  </article>;
}

// 顺序上传用户选择的录音并显示实际文件进度；失败保留已成功上传项。
function RecordingImport({api, act, pending, choose}) {
  const [files, setFiles] = useState([]);
  const [status, setStatus] = useState('');

  return <section className="card composer-card">
    <h2>用自己的录音创建新声线</h2>
    <p className="muted">建议准备 2～10 分钟清晰人声（无背景音乐、无多人重叠），支持 WAV、MP3、M4A、FLAC、OGG，单条不超过 30 分钟 / 512 MB。</p>
    <form onSubmit={e => {
      e.preventDefault();
      const form = new FormData(e.currentTarget);
      act(async () => {
        if (!files.length) throw Error('请先选择录音文件');
        if (files.length > 30 || files.some(f => f.size > 512 * 1024 * 1024)) throw Error('最多 30 个录音，单文件不超过 512 MB');
        const ds = await api('recording-datasets', {
          name: form.get('name'),
          authorization: form.get('authorization'),
          authorized: form.has('authorized')
        });
        choose(ds.id);
        try {
          for (let i = 0; i < files.length; i++) {
            setStatus(`正在保存 ${i + 1} / ${files.length}：${files[i].name}`);
            const response = await fetch(`/api/datasets/${ds.id}/recordings?filename=${encodeURIComponent(files[i].name)}`, {
              method: 'POST',
              headers: {'Content-Type': 'application/octet-stream'},
              body: files[i]
            });
            const body = await response.json();
            if (!response.ok) throw Error(body.error || '录音导入失败');
          }
        } finally {
          setStatus('');
        }
        return ds;
      }, '录音已保存在本机。点击“开始自动切分与转写”继续。');
    }}>
      <div className="form-grid">
        <label className="field">
          <span className="field-label">声线名称</span>
          <input name="name" required maxLength="100" placeholder="例如：我的讲解声线" disabled={pending}/>
        </label>
        <label className="field">
          <span className="field-label">授权来源</span>
          <input name="authorization" required placeholder="例如：本人录制并授权用于声线训练" disabled={pending}/>
        </label>
      </div>
      <label className="dropzone">
        <input type="file" accept=".wav,.mp3,.m4a,.flac,.ogg" multiple required disabled={pending} onChange={e => setFiles(Array.from(e.target.files))}/>
        <strong>{files.length ? `已选择 ${files.length} 个录音文件` : '点击选择原始录音文件（可多选）'}</strong>
        <span>{files.length ? files.map(f => f.name).join(' · ') : '支持 WAV / MP3 / M4A / FLAC / OGG · 原始文件将安全复制留在本机'}</span>
      </label>
      <label className="check">
        <input type="checkbox" name="authorized" required disabled={pending}/>我拥有这些录音和声线的使用授权。
      </label>
      <div className="form-footer">
        <span role="status" className="muted">{status || '导入完成后将进入离线切分与中文转写。'}</span>
        <button className="btn-primary" disabled={pending}>保存录音，进入整理</button>
      </div>
    </form>
  </section>;
}
