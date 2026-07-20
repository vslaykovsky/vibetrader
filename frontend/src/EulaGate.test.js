import { describe, expect, it } from 'vitest';
import { normalizeEulaPayload } from './EulaGate.jsx';


describe('normalizeEulaPayload', () => {
  it('accepts a versioned agreement with sections', () => {
    expect(normalizeEulaPayload({
      agreement: {
        title: 'Agreement',
        version: '2026-07-19',
        sections: [{ title: 'Terms', paragraphs: ['Text'] }],
      },
    })).toMatchObject({
      title: 'Agreement',
      version: '2026-07-19',
    });
  });

  it('rejects incomplete agreement payloads', () => {
    expect(normalizeEulaPayload(null)).toBeNull();
    expect(normalizeEulaPayload({ agreement: { title: 'Agreement', version: '', sections: [] } })).toBeNull();
  });
});
