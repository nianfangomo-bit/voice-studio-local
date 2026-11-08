import React, {useState} from 'react';

// 仅由用户点击触发硬件检测，候选信息不等同当前环境已通过实际运行验证。
export function HardwareAdvice({api, act, pending}) {
  const [report,setReport]=useState(null);
  // 只导出白名单检测数据，不包含会话、录音、数据库或完整系统日志。
  function exportReport() {
    const url=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)],{type:'application/json'}));
    const anchor=document.createElement('a');anchor.href=url;anchor.download='voice-hardware-report.json';
    anchor.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  return <section className="panel"><h2>先检测，再选择环境包</h2>
    <p>不改驱动、不下载模型。已有环境能用，无需重复安装；检测只提供候选建议。</p>
    <button disabled={pending} onClick={()=>act(async()=>setReport(await api('hardware-advice')),'硬件检测完成；请注意候选包的验证范围。')}>检测显卡与驱动</button>
    {report&&<><p>{report.system} · 规则 {report.rules}</p><p>{report.note}</p>
      {report.gpus.map((g,i)=><article key={i}><h3>{g.name}</h3><p>驱动 {g.driver} · 架构 {g.capability} · 显存 {g.memory} MiB</p><p>{g.message}</p>
        {g.url&&<a href={g.url} target="_blank" rel="noopener noreferrer">{g.status==='driver_review'?'查看官方驱动入口':'查看官方候选包（不是自动安装）'}</a>}</article>)}
      <button onClick={exportReport}>导出检测报告，带到联网电脑</button>
      <p className="muted">报告只含系统、显卡和驱动信息。多显卡时不自动选择训练设备。现有离线包仍通过原安装器安装；候选 50 系包暂不自动切换。</p></>}
  </section>;
}
