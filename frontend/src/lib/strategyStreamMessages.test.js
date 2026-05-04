import { describe, expect, it } from 'vitest';
import {
  appendAssistantDeltaMessage,
  mergeStrategySnapshotMessages,
} from './strategyStreamMessages.js';

describe('appendAssistantDeltaMessage', () => {
  it('appends deltas to a streaming assistant message for the run', () => {
    const first = appendAssistantDeltaMessage(
      [{ role: 'user', content: 'hello' }],
      'run-1',
      'Hel',
    );
    const second = appendAssistantDeltaMessage(first, 'run-1', 'lo');

    expect(second).toEqual([
      { role: 'user', content: 'hello' },
      { role: 'assistant', run_id: 'run-1', content: 'Hello', streaming: true },
    ]);
  });
});

describe('mergeStrategySnapshotMessages', () => {
  it('keeps an in-flight assistant message while a snapshot is still running', () => {
    const merged = mergeStrategySnapshotMessages(
      [{ role: 'user', content: 'hello' }],
      [
        { role: 'user', content: 'hello' },
        { role: 'assistant', run_id: 'run-1', content: 'Partial', streaming: true },
      ],
      'run-1',
      'running',
    );

    expect(merged).toEqual([
      { role: 'user', content: 'hello' },
      { role: 'assistant', run_id: 'run-1', content: 'Partial', streaming: true },
    ]);
  });
});
