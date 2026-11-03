// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { fetchJSON } from '../api/client';
import { selectIdentity } from './identity';

afterEach(() => vi.unstubAllGlobals());

it('pins the request account and rejects its late response after a switch', async () => {
  let release!: (response: Response) => void;
  const pending = new Promise<Response>(resolve => { release = resolve; });
  const fetch = vi.fn((_url: string, _options: RequestInit) => pending);
  vi.stubGlobal('fetch', fetch);
  selectIdentity('101');
  const request = fetchJSON('/api/activities');
  const failure = expect(request).rejects.toMatchObject({name: 'StaleAccountError'});
  expect(new Headers(fetch.mock.calls[0][1].headers).get('X-Account-UID')).toBe('101');
  selectIdentity('202');
  release(new Response(JSON.stringify({items: ['A only']}), {status: 200}));
  await failure;
});

it('keeps a global article save alive through account switching', async () => {
  let release!: (response: Response) => void;
  vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(resolve => { release = resolve; })));
  selectIdentity('101');
  const request = fetchJSON('/api/article/draft', {method: 'PUT'});
  selectIdentity('202');
  release(new Response(JSON.stringify({saved: true}), {status: 200}));
  await expect(request).resolves.toEqual({saved: true});
});
