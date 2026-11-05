// 按发布时间与标识稳定选择最早的已发布声线；新导入/候选版本不能自动抢占默认选择。
export function preferredVoiceId(voices) {
  const published = voices.filter(v => v.status === 'published');
  published.sort((a,b) => (a.published || a.created || 0) - (b.published || b.created || 0) || a.id.localeCompare(b.id));
  return published[0]?.id || '';
}
