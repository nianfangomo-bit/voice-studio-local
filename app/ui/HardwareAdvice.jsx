import React, {useState} from 'react';

// 仅由用户点击触发硬件检测，候选信息不等同当前环境已通过实际运行验证。
export function HardwareAdvice({api, act, pending}) {
  const [report, setReport] = useState(null);

  // 只导出白名单检测数据，不包含会话、录音、数据库或完整系统日志。
  function exportReport() {
    const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], {type: 'application/json'}));
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = 'voice-hardware-report.json';
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  return <div>
    <h2>只读显卡与驱动检测</h2>
    <p className="muted">不改驱动、不下载模型。用于区分 RTX 20/30/40 系与 RTX 50 系对应的候选运行环境。</p>
    <div className="toolbar-tight" style={{marginTop: '12px'}}>
      <button className="btn-secondary" disabled={pending} onClick={() => act(async () => setReport(await api('hardware-advice')), '硬件检测完成；请注意候选包的验证范围。')}>
        检测显卡与驱动
      </button>
      {report && (
        <button className="btn-ghost" onClick={exportReport}>导出检测报告 JSON</button>
      )}
    </div>
    {report && (
      <div className="hw-report">
        <div className="hw-meta">
          <span>{report.system}</span>
          <span className="mono chip">规则 {report.rules}</span>
        </div>
        <p className="muted" style={{margin: '8px 0'}}>{report.note}</p>
        {report.gpus.map((g, i) => (
          <article className="hw-gpu" key={i}>
            <div className="hw-gpu-top">
              <strong>{g.name}</strong>
              <span className={'badge ' + (g.status === 'candidate_legacy' ? 'done' : 'paused')}>
                架构 {g.capability}
              </span>
            </div>
            <small className="mono muted">驱动 {g.driver} · 显存 {g.memory} MiB</small>
            <p>{g.message}</p>
            {g.url && (
              <a href={g.url} target="_blank" rel="noopener noreferrer">
                {g.status === 'driver_review' ? '查看官方驱动入口 ↗' : '查看官方候选包来源 ↗'}
              </a>
            )}
          </article>
        ))}
        <p className="muted" style={{fontSize: '12px', marginBottom: 0}}>报告只含系统、显卡和驱动信息；不会自动下载或切换环境。</p>
      </div>
    )}
  </div>;
}
