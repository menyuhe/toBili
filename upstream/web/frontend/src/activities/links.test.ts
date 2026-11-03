// @vitest-environment jsdom
import { expect, it } from 'vitest';
import { buildActivityLink } from './index';

it('opens the general dynamic route for existing Opus records without changing stored data', () => {
  const item = { dynamic_id: '1263316672551321640', source_url: 'https://www.bilibili.com/opus/1263316672551321640' };
  const html = buildActivityLink(item);
  expect(html).toContain('href="https://t.bilibili.com/1263316672551321640"');
  expect(item.source_url).toContain('/opus/');
});
