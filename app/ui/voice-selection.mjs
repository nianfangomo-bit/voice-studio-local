// Imported/test voices must never displace a published voice implicitly.
export function preferredVoiceId(voices) {
  const published = voices.filter(v => v.status === 'published');
  published.sort((a,b) => (a.published || a.created || 0) - (b.published || b.created || 0) || a.id.localeCompare(b.id));
  return published[0]?.id || '';
}
