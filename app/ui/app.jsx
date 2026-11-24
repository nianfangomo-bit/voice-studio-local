import React, {useEffect, useState, useRef} from 'react';
import {createRoot} from 'react-dom/client';
import {Training} from './Training';
import {HardwareAdvice} from './HardwareAdvice';
import {preferredVoiceId} from './voice-selection.mjs';

// 统一访问已认证的本机 API；有数据时提交 JSON，失败保留服务端中文提示。
async function api(path, data) {
  const res = await fetch('/api/' + path, data === undefined ? {} : {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(data)
  });
  const body = await res.json();
  if (!res.ok) throw Error(body.error || '操作失败');
  return body;
}

const statuses = {
  queued: '等待中',
  running: '处理中',
  cancelling: '停止中',
  cancelled: '已取消',
  done: '已完成',
  failed: '失败',
  interrupted: '已中断'
};
const busyStates = ['queued', 'running', 'cancelling'];

const PAGE_META = {
  '配音工作台': {title: '开始配音', sub: '输入文案、逐句微调重做，一键导出 WAV 音频与句级 SRT 字幕'},
  '训练工作台': {title: '训练工作台', sub: '导入本人授权录音，离线切分转写、逐段校对后微调专属声线'},
  '我的声线': {title: '我的声线', sub: '一次训练长期本机保存，后续创建配音项目直接选用'},
  '作品库': {title: '作品库', sub: '每次导出均为不可变独立版本，修改文案不会覆盖历史成品'},
  '任务中心': {title: '任务中心', sub: '查看真实阶段与片段进度，随时取消、重试或排查本机日志'},
  '环境设置': {title: '环境设置', sub: '管理本地 GPT-SoVITS 引擎路径、运算自检与脱敏诊断摘要'}
};

// 离线内联 SVG 图标集，不依赖任何外部字体或网络资源。
function Icon({name, size = 18}) {
  const props = {
    width: size, height: size, viewBox: '0 0 24 24', fill: 'none',
    stroke: 'currentColor', strokeWidth: 1.85, strokeLinecap: 'round', strokeLinejoin: 'round',
    'aria-hidden': 'true', className: 'ico'
  };
  switch (name) {
    case 'brand':
      return <svg {...props} strokeWidth="2.1"><path d="M4 12h2M8 7v10M12 4v16M16 7v10M20 12h-2"/></svg>;
    case 'mic':
      return <svg {...props}><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3M8.5 21h7"/></svg>;
    case 'sliders':
      return <svg {...props}><path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M2 14h4M10 8h4M18 16h4"/></svg>;
    case 'voice':
      return <svg {...props}><circle cx="12" cy="8" r="4"/><path d="M5 20a7 7 0 0 1 14 0"/></svg>;
    case 'folder':
      return <svg {...props}><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/></svg>;
    case 'activity':
      return <svg {...props}><path d="M3 12h4l2.5-6 4 12 2.5-6H21"/></svg>;
    case 'settings':
      return <svg {...props}><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h.08a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51h.08a1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v.08a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1Z"/></svg>;
    case 'plus':
      return <svg {...props}><path d="M12 5v14M5 12h14"/></svg>;
    case 'play':
      return <svg {...props}><polygon points="6 4 20 12 6 20 6 4"/></svg>;
    case 'download':
      return <svg {...props}><path d="M12 3v12M7 10l5 5 5-5M5 20h14"/></svg>;
    case 'edit':
      return <svg {...props}><path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>;
    case 'refresh':
      return <svg {...props}><path d="M21 12a9 9 0 1 1-2.64-6.36L21 8"/><path d="M21 3v5h-5"/></svg>;
    case 'check':
      return <svg {...props}><path d="M20 6 9 17l-5-5"/></svg>;
    case 'alert':
      return <svg {...props}><path d="M12 9v4M12 17h.01M10.3 3.9 1.8 18.5A2 2 0 0 0 3.5 21.5h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"/></svg>;
    case 'shield':
      return <svg {...props}><path d="M12 3 5 6v6c0 5 3.5 8 7 9 3.5-1 7-4 7-9V6l-7-3Z"/></svg>;
    case 'terminal':
      return <svg {...props}><path d="m4 17 6-5-6-5M12 19h8"/></svg>;
    default:
      return null;
  }
}

// 复用表单标签结构，保持输入项排版和点击标签聚焦的语义。
function Field({label, hint, children}) {
  return <label className="field">
    <span className="field-label">{label}{hint && <small>{hint}</small>}</span>
    {children}
  </label>;
}

// 工作台入口：轮询真实任务状态，保留用户当前项目和页面选择。
function App() {
  const [state, setState] = useState(null);
  const [page, setPage] = useState('配音工作台');
  const [pid, setPid] = useState('');
  const [composerOpen, setComposerOpen] = useState(false);
  const [importVoiceOpen, setImportVoiceOpen] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [pending, setPending] = useState(false);
  const [log, setLog] = useState('');
  const [logJob, setLogJob] = useState('');
  const [checks, setChecks] = useState([]);
  const [checkingEnv, setCheckingEnv] = useState(false);
  const initialPage = useRef(false);

  // 仅首次加载决定引导页；后续状态刷新不应把用户带离当前页面。
  useEffect(() => {
    if (state && !initialPage.current) {
      initialPage.current = true;
      if (!state.voices.length) setPage('训练工作台');
      if (!state.projects.length) setComposerOpen(true);
    }
  }, [state]);

  // 拉取服务端快照，不从前端计时推测任务进度。
  const refresh = async () => setState(await api('state'));

  // 交换一次性启动地址中的令牌后清除地址栏令牌；卸载时清理轮询定时器。
  useEffect(() => {
    let mounted = true;
    (async () => {
      const token = location.hash.slice(1);
      if (token) {
        history.replaceState(null, '', '/');
        const r = await fetch('/connect', {method: 'POST', headers: {'x-start-token': token}});
        if (!r.ok) throw Error('启动凭据无效，请重新打开软件');
      }
      if (mounted) await refresh();
    })().catch(e => setError(e.message));
    const id = setInterval(() => refresh().catch(e => setError(e.message)), 2000);
    return () => { mounted = false; clearInterval(id); };
  }, []);

  // 包装异步按钮操作：锁定提交、展示错误，并在成功后刷新真实状态。
  const act = async (fn, message = '已保存') => {
    setPending(true);
    setError('');
    setNotice('');
    try {
      const result = await fn();
      await refresh();
      setNotice(message);
      return result;
    } catch (e) {
      setError(e.message);
    } finally {
      setPending(false);
    }
  };

  if (!state) {
    return <div className="boot-screen">
      <div className="boot-card">
        <div className="brand-mark"><Icon name="brand" size={24}/></div>
        <h1>声线工作室</h1>
        <p>{error || '正在连接本机离线工作台…'}</p>
      </div>
    </div>;
  }

  const project = state.projects.find(p => p.id === pid) || state.projects[0];
  const queuePaused = Boolean(state.queue_pause);
  const activeJobs = state.jobs.filter(j => busyStates.includes(j.state)).length;
  const working = project && state.jobs.some(j => j.project_id === project.id && busyStates.includes(j.state));
  const latestJob = project && state.jobs.find(j => j.project_id === project.id);
  const totalExports = state.projects.reduce((sum, p) => sum + p.exports.length, 0);
  const meta = PAGE_META[page] || {title: page, sub: ''};

  const primaryNav = [
    {id: '配音工作台', label: '开始配音', icon: 'mic', count: state.projects.length || null},
    {id: '训练工作台', label: '训练工作台', icon: 'sliders', count: state.datasets?.length || null},
    {id: '我的声线', label: '我的声线', icon: 'voice', count: state.voices.length || null},
    {id: '作品库', label: '作品库', icon: 'folder', count: totalExports || null}
  ];
  const systemNav = [
    {id: '任务中心', label: '任务中心', icon: 'activity', count: activeJobs, highlight: activeJobs > 0 || queuePaused},
    {id: '环境设置', label: '环境设置', icon: 'settings'}
  ];

  // 只提交所选生成模式；实际缓存判断和排队由后端负责。
  const generate = (mode, segment_id) => act(
    () => api(`projects/${project.id}/generate`, {mode, segment_id}),
    '任务已提交；完成片段会自动保存。'
  );

  const generatedCount = project ? project.segments.filter(s => s.current).length : 0;

  return <div className="shell">
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-mark"><Icon name="brand" size={20}/></div>
        <div>
          <b>声线<span>工作室</span></b>
          <small>VOICE STUDIO · LOCAL</small>
        </div>
      </div>

      <nav className="nav-group" aria-label="主导航">
        <span className="nav-caption">创作工作区</span>
        {primaryNav.map(item => (
          <button key={item.id} className={page === item.id ? 'nav-btn selected' : 'nav-btn'} onClick={() => setPage(item.id)}>
            <Icon name={item.icon} size={17}/>
            <span className="nav-text">{item.label}</span>
            {item.count !== null && <span className="nav-pill">{item.count}</span>}
          </button>
        ))}

        <span className="nav-caption nav-caption-sys">系统与监控</span>
        {systemNav.map(item => (
          <button key={item.id} className={page === item.id ? 'nav-btn selected' : 'nav-btn'} onClick={() => setPage(item.id)}>
            <Icon name={item.icon} size={17}/>
            <span className="nav-text">{item.label}</span>
            {item.id === '任务中心' && (
              <span className={'nav-pill' + (queuePaused ? ' warn' : activeJobs ? ' live' : '')}>
                {queuePaused ? '暂停' : activeJobs}
              </span>
            )}
          </button>
        ))}
      </nav>

      <div className="privacy-card">
        <div className="privacy-head"><Icon name="shield" size={15}/><span>离线隐私保护</span></div>
        <p>录音、声线与文案全部留在本机，不上传云端。训练一次即可反复配音。</p>
      </div>
    </aside>

    <main className="workspace">
      <header className="topbar">
        <div className="topbar-title">
          <h1>{meta.title}</h1>
          <p>{meta.sub}</p>
        </div>
        <div className="topbar-actions">
          {page === '配音工作台' && (
            <button className={composerOpen ? 'btn-ghost active' : 'btn-secondary'} onClick={() => setComposerOpen(v => !v)}>
              <Icon name="plus" size={16}/><span>{composerOpen ? '收起新建' : '新建文案'}</span>
            </button>
          )}
          <span className={'status-pill ' + (queuePaused ? 'paused' : activeJobs ? 'running' : 'ready')}>
            <i className="dot"/>
            {queuePaused ? '队列已暂停' : activeJobs ? `处理中 · ${activeJobs}` : '本机离线就绪'}
          </span>
        </div>
      </header>

      {state.queue_pause && (
        <div role="alert" className="pausebanner">
          <div className="pausebanner-icon"><Icon name="alert" size={20}/></div>
          <div className="pausebanner-body">
            <strong>队列已暂停（隔离保护中）</strong>
            <p>{state.queue_pause.message}</p>
          </div>
          <button onClick={() => setPage('任务中心')}>查看任务</button>
        </div>
      )}

      {error && (
        <div role="alert" className="banner banner-error">
          <Icon name="alert" size={18}/>
          <span className="banner-text">{error}</span>
          <button className="btn-inline" onClick={() => setError('')}>关闭</button>
        </div>
      )}

      {notice && (
        <div role="status" className="banner banner-notice">
          <Icon name="check" size={18}/>
          <span className="banner-text">{notice}</span>
          <button className="btn-inline" onClick={() => setNotice('')}>关闭</button>
        </div>
      )}

      {page === '配音工作台' && (
        <div className="page-stack">
          {(composerOpen || !state.projects.length) && (
            <section className="card composer-card">
              <div className="card-head">
                <div>
                  <h2>新建配音文案</h2>
                  <p className="muted">选择已训练的声线并粘贴文案，系统会自动按标点分段并保留原文。</p>
                </div>
                {state.projects.length > 0 && (
                  <button className="btn-ghost" onClick={() => setComposerOpen(false)}>收起</button>
                )}
              </div>
              <NewProject
                voices={state.voices}
                pending={pending}
                onGoTrain={() => setPage('训练工作台')}
                create={data => act(async () => {
                  const p = await api('projects', data);
                  setPid(p.id);
                  setComposerOpen(false);
                  return p;
                }, '文案已保存并分段。建议先点“试听首句”，满意后再生成全文。')}
              />
            </section>
          )}

          {project && (
            <>
              <section className="studio-bar">
                <div className="studio-bar-left">
                  <div className="project-select-wrap">
                    <span className="micro-label">当前项目</span>
                    <select aria-label="选择项目" value={project.id} onChange={e => setPid(e.target.value)}>
                      {state.projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
                    </select>
                  </div>
                  <div className="studio-chips">
                    <span className="chip chip-emerald"><Icon name="voice" size={14}/>{project.voice.name}</span>
                    <span className="chip">{generatedCount} / {project.segments.length} 句就绪</span>
                    <span className="chip">{project.speed}× 语速</span>
                  </div>
                </div>
                <div className="studio-bar-actions">
                  <button className="btn-secondary" disabled={working || pending || queuePaused} onClick={() => generate('preview')}>
                    <Icon name="play" size={15}/><span>试听首句</span>
                  </button>
                  <button className="btn-primary" disabled={working || pending || queuePaused} onClick={() => generate('all')}>
                    <Icon name="refresh" size={15}/>
                    <span>{working ? '正在生成…' : project.exports.length ? '更新音频与字幕' : '生成音频与字幕'}</span>
                  </button>
                </div>
              </section>

              {latestJob && (
                <div className={'taskstrip ' + latestJob.state} role="status">
                  <div className="taskstrip-main">
                    <span className={'badge ' + latestJob.state}>{statuses[latestJob.state]}</span>
                    <span className="taskstrip-msg">{latestJob.message}</span>
                    <span className="mono">{latestJob.completed} / {latestJob.total}</span>
                  </div>
                  {latestJob.total > 0 && (
                    <div className="progress-track">
                      <div className="progress-fill" style={{width: `${Math.min(100, Math.round((latestJob.completed / latestJob.total) * 100))}%`}}/>
                    </div>
                  )}
                  <div className="taskstrip-actions">
                    {busyStates.includes(latestJob.state) && (
                      <button className="btn-sm" disabled={pending || latestJob.state === 'cancelling'} onClick={() => act(() => api(`jobs/${latestJob.id}/cancel`, {}), '已提交取消请求。')}>取消</button>
                    )}
                    <button className="btn-sm" onClick={() => setPage('任务中心')}>任务详情</button>
                  </div>
                </div>
              )}

              <div className="workbench-grid">
                <div className="workbench-main">
                  <div className="section-bar">
                    <div>
                      <h2>逐句脚本与试听</h2>
                      <p className="muted">每句可独立试听、修改读音或字幕并单句重做，未改动的句子自动复用缓存。</p>
                    </div>
                    {project.dirty && <span className="badge paused">有未合成改动</span>}
                  </div>
                  <div className="cue-list">
                    {project.segments.map((s, i) => (
                      <Segment
                        key={s.id}
                        segment={s}
                        index={i}
                        projectId={project.id}
                        disabled={working || pending || queuePaused}
                        save={data => act(() => api(`projects/${project.id}/segments/${s.id}`, data), `第 ${i + 1} 句已保存。`)}
                        redo={() => generate('segment', s.id)}
                      />
                    ))}
                  </div>
                </div>

                <aside className="workbench-rail">
                  <ExportResult project={project} working={working} pending={pending} queuePaused={queuePaused} onUpdate={() => generate('all')}/>
                  <Options
                    key={project.id}
                    project={project}
                    disabled={working || pending || queuePaused}
                    save={data => act(() => api(`projects/${project.id}/options`, data), '语速与停顿已更新；重新生成成品后生效。')}
                  />
                  <section className="card voice-meta-card">
                    <h3>当前项目声线</h3>
                    <div className="voice-meta-row">
                      <span className="avatar-sm">{project.voice.name.slice(0, 1)}</span>
                      <div>
                        <strong>{project.voice.name}</strong>
                        <small>{project.voice.engine_version} · 创建项目时已锁定版本</small>
                      </div>
                    </div>
                    <p className="quote-box">参考录音原文：{project.voice.prompt_text}</p>
                  </section>
                </aside>
              </div>
            </>
          )}
        </div>
      )}

      {page === '训练工作台' && (
        <Training state={state} api={api} act={act} pending={pending} queuePaused={queuePaused} Icon={Icon}/>
      )}

      {page === '我的声线' && (
        <div className="page-stack">
          <div className="section-bar">
            <div>
              <h2>本机声线库（{state.voices.length}）</h2>
              <p className="muted">候选声线需先在“开始配音”用未见新文案试听，确认满意后再发布为正式声线。</p>
            </div>
            <div className="toolbar-tight">
              <button className="btn-secondary" onClick={() => setImportVoiceOpen(v => !v)}>
                <Icon name="plus" size={15}/><span>{importVoiceOpen ? '收起导入' : '导入已有权重'}</span>
              </button>
              <button className="btn-primary" onClick={() => setPage('训练工作台')}>去训练新声线</button>
            </div>
          </div>

          {state.voices.length === 0 ? (
            <section className="card empty-state">
              <div className="empty-icon"><Icon name="voice" size={28}/></div>
              <h3>还没有可用声线</h3>
              <p>前往“训练工作台”导入 2～5 分钟清晰中文录音，训练一次后就能长期离线使用。</p>
              <button className="btn-primary" onClick={() => setPage('训练工作台')}>开始创建我的声线</button>
            </section>
          ) : (
            <div className="voice-grid">
              {state.voices.map(v => (
                <article className="card voice-card" key={v.id}>
                  <div className="voice-card-top">
                    <span className="avatar">{v.name.slice(0, 1) || '声'}</span>
                    <span className={'badge ' + (v.status === 'published' ? 'done' : v.status === 'candidate' ? 'paused' : '')}>
                      {v.status === 'candidate' ? '候选 · 待试听' : v.status === 'published' ? '已发布' : '已导入'}
                    </span>
                  </div>
                  <h3>{v.name}</h3>
                  <small className="muted">{v.engine_version} · 本机离线保存</small>
                  <p className="quote-box">{v.prompt_text}</p>
                  <div className="voice-card-actions">
                    {v.status === 'candidate' ? (
                      <button className="btn-primary btn-block" disabled={pending} onClick={() => act(() => api(`voices/${v.id}/publish`, {listened: true}), '声线已发布。以后可直接选用，无需重训。')}>
                        我已试听新文案，确认发布
                      </button>
                    ) : (
                      <button className="btn-secondary btn-block" onClick={() => { setComposerOpen(true); setPage('配音工作台'); }}>
                        用此声线去配音
                      </button>
                    )}
                  </div>
                </article>
              ))}
            </div>
          )}

          {importVoiceOpen && (
            <VoiceForm pending={pending} submit={d => act(async () => {
              const v = await api('voices', d);
              setImportVoiceOpen(false);
              return v;
            }, '声线已导入，可创建配音项目试听。')}/>
          )}
        </div>
      )}

      {page === '作品库' && (
        <div className="page-stack">
          {totalExports === 0 ? (
            <section className="card empty-state">
              <div className="empty-icon"><Icon name="folder" size={28}/></div>
              <h3>还没有完整成品</h3>
              <p>在“开始配音”生成全部句子后，系统会自动拼接 32kHz WAV、生成句级 SRT 和交付 ZIP。</p>
              <button className="btn-primary" onClick={() => setPage('配音工作台')}>前往配音工作台</button>
            </section>
          ) : (
            <div className="archive-grid">
              {state.projects.flatMap(p => p.exports.map((e, i) => {
                const base = `/api/projects/${p.id}/exports/${e.id}/`;
                return (
                  <section className="card archive-card" key={e.id}>
                    <div className="archive-head">
                      <div>
                        <span className="badge done">第 {i + 1} 版</span>
                        <h3>{p.name}</h3>
                      </div>
                      <small className="mono muted">{new Date(e.created * 1000).toLocaleString()}</small>
                    </div>
                    <audio controls preload="none" src={base + 'narration.wav'}/>
                    <div className="download-grid">
                      <a className="dl-chip primary" href={base + 'narration.wav'} download="narration.wav"><Icon name="download" size={14}/>完整 WAV</a>
                      <a className="dl-chip" href={base + 'subtitles.srt'} download="subtitles.srt"><Icon name="download" size={14}/>字幕 SRT</a>
                      <a className="dl-chip" href={base + 'delivery.zip'} download="delivery.zip"><Icon name="download" size={14}/>交付包 ZIP</a>
                      <a className="dl-chip" href={base + 'timeline.json'} download="timeline.json"><Icon name="download" size={14}/>时间线 JSON</a>
                    </div>
                    <small className="muted">句级 SRT 按实际音频时长生成；解压交付包后音频、字幕与时间线位于同一目录。</small>
                  </section>
                );
              }))}
            </div>
          )}
        </div>
      )}

      {page === '任务中心' && (
        <div className="page-stack">
          {state.jobs.length === 0 ? (
            <section className="card empty-state">
              <div className="empty-icon"><Icon name="activity" size={28}/></div>
              <h3>当前没有任务记录</h3>
              <p>切分转写、声线训练与批量配音任务都会在这里实时显示真实阶段和日志。</p>
            </section>
          ) : (
            <div className="job-list">
              {state.jobs.map(j => {
                const pct = j.total > 0 ? Math.min(100, Math.round((j.completed / j.total) * 100)) : 0;
                const kindLabel = j.kind === 'training' ? '训练' : j.kind === 'preparation' ? '切分转写' : '配音';
                return (
                  <section className="card job-card" key={j.id}>
                    <div className="job-main">
                      <div className="job-title-row">
                        <span className={'badge ' + j.state}>{statuses[j.state]}</span>
                        <span className="chip">{kindLabel}</span>
                        <h3>{j.name || state.projects.find(p => p.id === j.project_id)?.name || j.project_id}</h3>
                      </div>
                      <p className="job-msg">{j.message}</p>
                      <div className="job-progress-row">
                        <div className="progress-track">
                          <div className={'progress-fill ' + j.state} style={{width: `${pct}%`}}/>
                        </div>
                        <span className="mono muted">{j.completed} / {j.total} {j.kind === 'training' ? '阶段' : '段'}</span>
                      </div>
                    </div>
                    <div className="job-actions">
                      {busyStates.includes(j.state) && (
                        <button className="btn-danger-ghost" disabled={pending || j.state === 'cancelling'} onClick={() => act(() => api(`jobs/${j.id}/cancel`, {}), '已提交取消请求。')}>
                          取消任务
                        </button>
                      )}
                      {['failed', 'cancelled', 'interrupted'].includes(j.state) && j.kind !== 'training' && (
                        <button className="btn-secondary" disabled={pending || queuePaused} onClick={() => act(() => api(`jobs/${j.id}/retry`, {}), '已重新排队。')}>
                          重试
                        </button>
                      )}
                      <button className="btn-ghost" onClick={() => act(async () => {
                        setLog((await api(`jobs/${j.id}/log`)).text);
                        setLogJob(j.name || j.id);
                      }, '已读取最新日志')}>
                        <Icon name="terminal" size={15}/><span>查看日志</span>
                      </button>
                    </div>
                  </section>
                );
              })}
            </div>
          )}

          {log && (
            <section className="log-viewer">
              <div className="log-viewer-head">
                <span><Icon name="terminal" size={15}/> 本机任务日志 · {logJob}</span>
                <button className="btn-sm" onClick={() => { setLog(''); setLogJob(''); }}>关闭日志</button>
              </div>
              <pre>{log}</pre>
            </section>
          )}
        </div>
      )}

      {page === '环境设置' && (
        <div className="settings-grid">
          <Settings
            current={state.settings}
            history={state.environment_history || []}
            pending={pending}
            submit={d => act(() => api('settings', d), '环境路径已保存，新任务使用此配置；下次启动服务也会沿用。')}
          />
          <div className="settings-side">
            <section className="card">
              <h2>运行环境自检与诊断</h2>
              <p className="muted">运行小型依赖与显卡矩阵/FFT 运算检测（首次约需几十秒）；有任务时跳过，不联网、不安装依赖。</p>
              <div className="toolbar-tight" style={{marginTop: '14px'}}>
                <button className="btn-primary" disabled={pending || checkingEnv} onClick={() => act(async () => {
                  setCheckingEnv(true);
                  try {
                    const res = await api('check');
                    setChecks(res);
                    return res;
                  } finally {
                    setCheckingEnv(false);
                  }
                }, '检测完成；基础检查通过不等于完整训练已验收。')}>
                  {checkingEnv ? '正在检测环境…' : '检查环境与基础运算'}
                </button>
                <a className="dl-chip" href="/api/diagnostics" download="voice-diagnostics.json">
                  <Icon name="download" size={14}/>下载脱敏诊断摘要
                </a>
              </div>
              {checks.length > 0 && (
                <ul className="check-list">
                  {checks.map((c, i) => (
                    <li key={i} className={c.ok ? 'ok' : 'bad'}>
                      <span className="check-dot">{c.ok ? '✓' : '!'}</span>
                      <div>
                        <strong>{c.name}</strong>
                        <p>{c.detail}</p>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
              <p className="muted" style={{marginTop: '12px', fontSize: '12px'}}>诊断摘要不含录音、文案、本机路径或原始日志；分享排障前仍请核对隐私信息。</p>
            </section>
            <section className="card">
              <HardwareAdvice api={api} act={act} pending={pending}/>
            </section>
          </div>
        </div>
      )}
    </main>
  </div>;
}

// 新建项目表单：默认只选已发布声线，创建后由项目固定声线快照。
function NewProject({voices, pending, create, onGoTrain}) {
  const [name, N] = useState('');
  const [text, T] = useState('');
  const [voice, V] = useState(() => preferredVoiceId(voices));
  const selected = voices.find(v => v.id === voice);

  if (!voices.length) {
    return <div className="inline-empty">
      <p>当前还没有可用声线，请先前往“训练工作台”训练或导入一条声线。</p>
      <button type="button" className="btn-primary" onClick={onGoTrain}>去训练声线</button>
    </div>;
  }

  return <form onSubmit={e => { e.preventDefault(); if (selected) create({name, text, voice_id: selected.id}); }}>
    <div className="form-grid">
      <Field label="项目名称" hint="留空自动取文案前 18 字">
        <input value={name} onChange={e => N(e.target.value)} placeholder="例如：中位数讲解 · 第一版"/>
      </Field>
      <Field label="选择声线" hint="优先已发布版本">
        <select required value={selected?.id || ''} onChange={e => V(e.target.value)}>
          <option value="" disabled>请选择声线（不会自动使用新导入版本）</option>
          {voices.map(v => (
            <option key={v.id} value={v.id}>
              {v.name} · {v.status === 'published' ? '已发布' : v.status === 'candidate' ? '候选，需试听' : '导入版，需试听'}
            </option>
          ))}
        </select>
      </Field>
    </div>
    {selected && (
      <p className={selected.status === 'published' ? 'quote-box' : 'quote-box warn'}>
        参考录音原文：{selected.prompt_text}{selected.status !== 'published' ? '（尚非已发布版本，请先用新文案试听确认）' : ''}
      </p>
    )}
    <Field label="配音文案" hint={`${text.trim().length} 字 · 按标点自动分句`}>
      <textarea required rows="5" value={text} onChange={e => T(e.target.value)} placeholder="粘贴你的中文文案。系统会自动按标点分段并保留原文，后续可以逐句修改读音或单独重做。"/>
    </Field>
    <div className="form-footer">
      <small className="muted">已有项目会锁定创建时的声线快照，不受后续重训影响。</small>
      <button className="btn-primary" disabled={pending || !selected}>保存并分段</button>
    </div>
  </form>;
}

// 显示最近一次不可变成品；存在未合成修改时明确标注为旧版。
function ExportResult({project: p, working, pending, queuePaused, onUpdate}) {
  const e = p.exports[p.exports.length - 1];
  if (!e) {
    return <section className="card export-card empty">
      <div className="card-head">
        <h3>成品与导出</h3>
        <span className="badge">尚未合成</span>
      </div>
      <p className="muted">点击顶部“生成音频与字幕”后，这里会自动提供完整 WAV 试听、句级 SRT 字幕与交付包下载。</p>
      <button className="btn-primary btn-block" disabled={working || pending || queuePaused} onClick={onUpdate}>
        {working ? '正在生成音频…' : '立即生成音频与字幕'}
      </button>
    </section>;
  }
  const base = `/api/projects/${p.id}/exports/${e.id}/`;
  return <section className={'card export-card ' + (p.dirty ? 'stale' : 'fresh')}>
    <div className="card-head">
      <div>
        <small className="eyebrow">{p.dirty ? '上一次成品 · 尚未包含新修改' : '音频与字幕已就绪'}</small>
        <h3>成品导出</h3>
      </div>
      <span className={'badge ' + (p.dirty ? 'paused' : 'done')}>第 {p.exports.length} 版</span>
    </div>
    <audio aria-label="完整配音试听" controls preload="metadata" src={base + 'narration.wav'}/>
    {p.dirty && (
      <div className="stale-callout">
        <span>文案或参数已更新，当前试听和下载仍是旧版。</span>
        <button className="btn-sm primary" disabled={working || pending || queuePaused} onClick={onUpdate}>更新成品</button>
      </div>
    )}
    <div className="download-stack">
      <a className="dl-chip primary" href={base + 'narration.wav'} download="narration.wav">
        <Icon name="download" size={15}/><span>下载完整 WAV</span>
      </a>
      <a className="dl-chip" href={base + 'subtitles.srt'} download="subtitles.srt">
        <Icon name="download" size={15}/><span>下载字幕 SRT</span>
      </a>
    </div>
    <div className="download-secondary">
      <a href={base + 'delivery.zip'} download="delivery.zip">交付包 ZIP</a>
      <span>·</span>
      <a href={base + 'timeline.json'} download="timeline.json">剪辑时间线 JSON</a>
    </div>
    <p className="muted" style={{fontSize: '12px', margin: '10px 0 0'}}>句级 SRT 按实际音频时长对齐，可直接导入剪辑软件；不自动烧录进视频。</p>
  </section>;
}

// 分开编辑朗读与字幕文本；未保存时禁止重做，旧音频仍可试听。
function Segment({segment: s, index, projectId, disabled, save, redo}) {
  const [spoken, S] = useState(s.spoken);
  const [display, D] = useState(s.display);
  const [editing, setEditing] = useState(false);

  useEffect(() => {
    S(s.spoken);
    D(s.display);
  }, [s.id, s.spoken, s.display]);

  const changed = spoken !== s.spoken || display !== s.display;
  const splitMode = s.spoken !== s.display;

  return <section className={'card cue-card ' + (s.current ? 'is-ready' : s.audio ? 'is-stale' : '')}>
    <div className="cue-head">
      <div className="cue-meta">
        <span className="cue-num">{String(index + 1).padStart(2, '0')}</span>
        <span className={'badge ' + (s.current ? 'done' : s.audio ? 'paused' : '')}>
          {s.current ? '已生成' : s.audio ? '待更新' : '未生成'}
        </span>
        {splitMode && <span className="chip">朗读/字幕分开</span>}
        {changed && <span className="badge paused">未保存</span>}
      </div>
      <div className="cue-actions">
        <button className="btn-sm" onClick={() => setEditing(v => !v)}>
          <Icon name="edit" size={14}/><span>{editing ? '收起' : '改词'}</span>
        </button>
        <button className="btn-sm" disabled={disabled || changed} title={changed ? '请先保存修改再重做' : '单独重新生成这一句'} onClick={redo}>
          <Icon name="refresh" size={14}/><span>重做这句</span>
        </button>
      </div>
    </div>

    <p className="cue-text">{s.display}</p>
    {splitMode && <p className="cue-spoken-note">实际朗读：{s.spoken}</p>}

    {s.audio && (
      <audio
        aria-label={`第${index + 1}句试听`}
        preload="metadata"
        controls
        src={`/api/projects/${projectId}/segments/${s.id}/audio?v=${s.audio.created}`}
      />
    )}

    {editing && (
      <div className="cue-editor">
        <div className="form-grid">
          <Field label="读出来的文字" hint="给 AI 朗读，可用同音字/展开写法">
            <textarea aria-label={`第${index + 1}段朗读`} disabled={disabled} value={spoken} onChange={e => S(e.target.value)} rows="3"/>
          </Field>
          <Field label="字幕显示的文字" hint="导出到 SRT，修改不影响音频缓存">
            <textarea aria-label={`第${index + 1}段字幕`} disabled={disabled} value={display} onChange={e => D(e.target.value)} rows="3"/>
          </Field>
        </div>
        <div className="cue-editor-bar">
          <div className="toolbar-tight">
            <button type="button" className="btn-sm" disabled={disabled || spoken === display} onClick={() => D(spoken)}>
              把朗读文字同步到字幕
            </button>
            <small className="muted">原文已保留 · 历史音频 {s.history.length} 份</small>
          </div>
          <div className="toolbar-tight">
            {changed && (
              <button type="button" className="btn-sm" onClick={() => { S(s.spoken); D(s.display); }}>还原</button>
            )}
            <button type="button" className="btn-primary btn-sm" disabled={disabled || !changed} onClick={async () => {
              const res = await save({spoken, display});
              if (res) setEditing(false);
            }}>保存修改</button>
          </div>
        </div>
      </div>
    )}
  </section>;
}

// 编辑语速和停顿，不直接触发配音；后端校验范围并使相关成品失效。
function Options({project: p, disabled, save}) {
  const [speed, S] = useState(p.speed);
  const [gap, G] = useState(p.gap_ms);
  const dirty = Number(speed) !== Number(p.speed) || Number(gap) !== Number(p.gap_ms);

  return <section className="card options-card">
    <div className="card-head">
      <h3>语速与停顿</h3>
      <span className="mono chip">{Number(speed).toFixed(2)}× · {gap}ms</span>
    </div>
    <form onSubmit={e => { e.preventDefault(); save({speed, gap_ms: gap}); }}>
      <label className="field">
        <span className="field-label">语速（0.6～1.5）<small className="mono">{Number(speed).toFixed(2)}×</small></span>
        <input className="range" type="range" min="0.6" max="1.5" step="0.05" value={speed} disabled={disabled} onChange={e => S(e.target.value)}/>
      </label>
      <Field label="句间停顿（毫秒）" hint="0～3000">
        <input type="number" min="0" max="3000" step="20" value={gap} disabled={disabled} onChange={e => G(e.target.value)}/>
      </Field>
      <button className={dirty ? 'btn-primary btn-block' : 'btn-secondary btn-block'} disabled={disabled || !dirty}>
        {dirty ? '保存参数修改' : '参数已同步'}
      </button>
    </form>
  </section>;
}

// 收集模型、参考录音和授权信息，要求用户明确确认可信来源。
function VoiceForm({pending, submit}) {
  return <form className="card" onSubmit={e => {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    submit({...Object.fromEntries(f), authorized: f.has('authorized'), trusted: f.has('trusted')});
  }}>
    <h2>导入已有声线权重</h2>
    <p className="muted">适用于已在本机训练或迁移过来的 GPT-SoVITS v2Pro 权重组合。</p>
    <div className="form-grid">
      {[
        ['name', '声线名称', '我的讲解声线'],
        ['gpt', 'GPT 权重（.ckpt）', '粘贴本机完整路径'],
        ['sovits', 'SoVITS 权重（.pth）', '粘贴本机完整路径'],
        ['reference', '参考录音（3～10 秒 PCM WAV）', '粘贴本机完整路径']
      ].map(([name, label, placeholder]) => (
        <Field key={name} label={label}><input name={name} required placeholder={placeholder}/></Field>
      ))}
    </div>
    <Field label="参考录音原文" hint="必须与参考录音逐字一致">
      <textarea name="prompt_text" required rows="2"/>
    </Field>
    <Field label="授权来源说明">
      <input name="authorization" required placeholder="例如：本人录制，本人拥有使用权"/>
    </Field>
    <label className="check"><input type="checkbox" name="authorized" required/>我有权使用此录音和声线。</label>
    <label className="check"><input type="checkbox" name="trusted" required/>这些模型来自可信来源，我理解未知权重可能包含不安全内容。</label>
    <button className="btn-primary" disabled={pending}>导入声线</button>
  </form>;
}

// 配置用户选择的本地引擎与旧工作流，保留原环境依赖。
function Settings({current, history = [], pending, submit}) {
  const [choice, setChoice] = useState('current');
  const s = choice === 'current' ? current : (history.find(h => h.id === choice)?.settings || current);

  return <section className="card">
    <div className="card-head">
      <div>
        <h2>本地运行环境配置</h2>
        <p className="muted">训练、转写与配音共用所选路径；运行任务期间锁定切换。保存前会校验依赖与显卡基础运算。</p>
      </div>
      <span className="chip">{current.environment_name || (current.engine ? '已配置' : '未配置')}</span>
    </div>

    {history.length > 0 && (
      <label className="field">
        <span className="field-label">配置来源</span>
        <select value={choice} onChange={e => setChoice(e.target.value)}>
          <option value="current">当前生效配置</option>
          {history.map(h => (
            <option key={h.id} value={h.id}>
              恢复历史：{h.settings.environment_name || h.settings.engine} · {new Date(h.created * 1000).toLocaleString()}
            </option>
          ))}
        </select>
      </label>
    )}

    <form key={choice + '|' + (s.runtime || '')} onSubmit={e => {
      e.preventDefault();
      const f = new FormData(e.currentTarget);
      submit({...Object.fromEntries(f), trusted: f.has('trusted')});
    }}>
      {[
        ['environment_name', '环境名称（便于区分）', false],
        ['engine', 'GPT-SoVITS v2Pro 引擎目录', true],
        ['runtime', 'Python 完整路径（留空使用引擎内 runtime/python.exe）', false],
        ['legacy', '旧配音工作流目录', true],
        ['ffmpeg', 'FFmpeg 可执行文件（留空自动检测）', false]
      ].map(([key, label, req]) => (
        <Field key={key} label={label}>
          <input name={key} defaultValue={s[key] || ''} required={req}/>
        </Field>
      ))}
      <label className="check">
        <input type="checkbox" name="trusted" required/>我确认环境来自可信来源，允许运行；已有模型和录音不会被移动。
      </label>
      <button className="btn-primary" disabled={pending}>验证环境并保存</button>
    </form>
    <p className="muted" style={{marginTop: '12px', fontSize: '12px'}}>历史配置可随时重新确认恢复。不会自动安装依赖或重启服务；重启软件后服务自身将沿用新环境。</p>
  </section>;
}

createRoot(document.getElementById('root')).render(<App/>);
