export function appendAssistantDeltaMessage(messages, runId, delta) {
  const rid = String(runId || '').trim();
  const text = typeof delta === 'string' ? delta : '';
  if (!rid || !text) {
    return Array.isArray(messages) ? messages : [];
  }
  const rows = Array.isArray(messages) ? messages : [];
  const idx = rows.findIndex((m) => m?.role === 'assistant' && m?.run_id === rid);
  if (idx >= 0) {
    const existing = rows[idx] || {};
    if (!existing.streaming && existing.content) {
      return rows;
    }
    const next = [...rows];
    next[idx] = {
      ...existing,
      role: 'assistant',
      run_id: rid,
      content: `${existing.content || ''}${text}`,
      streaming: true,
    };
    return next;
  }
  return [...rows, { role: 'assistant', run_id: rid, content: text, streaming: true }];
}

export function mergeStrategySnapshotMessages(snapshotMessages, currentMessages, runId, status) {
  const base = Array.isArray(snapshotMessages) ? snapshotMessages : [];
  const rid = String(runId || '').trim();
  if (!rid || status !== 'running') {
    return base;
  }
  if (base.some((m) => m?.role === 'assistant' && m?.run_id === rid)) {
    return base;
  }
  const current = Array.isArray(currentMessages) ? currentMessages : [];
  const partial = [...current]
    .reverse()
    .find((m) => m?.role === 'assistant' && m?.run_id === rid && m?.streaming && m?.content);
  return partial ? [...base, partial] : base;
}
