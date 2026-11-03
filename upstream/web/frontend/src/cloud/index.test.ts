// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
vi.mock('../api/client',()=>({fetchJSON:vi.fn()}));
vi.mock('../shell/confirm',()=>({openAppConfirm:vi.fn()}));
vi.mock('../shell/toast',()=>({showToast:vi.fn()}));
import {fetchJSON} from '../api/client';
import {bindCloud} from './index';
afterEach(()=>{vi.clearAllTimers();vi.useRealTimers();vi.resetAllMocks();document.body.innerHTML='';});
it('exposes activity sync without binding or task controls',async()=>{
 vi.useFakeTimers(); document.body.innerHTML='<section id="section-settings"><div class="settings-stack"></div></section>';
 vi.mocked(fetchJSON).mockResolvedValue({connected:true,message:'活动资料库',server:'https://relay.test',username:'test'});
 bindCloud();await vi.advanceTimersByTimeAsync(0);
 for(const id of ['cloud-bind','cloud-execution','cloud-conflicts-button'])expect(document.getElementById(id)?.hidden).toBe(true);
 expect(document.getElementById('cloud-panel')?.textContent).toContain('筛选、归档、任务及清理权限仍由本机管理');
 document.getElementById('cloud-sync')!.click();await vi.advanceTimersByTimeAsync(0);
 expect(vi.mocked(fetchJSON).mock.calls.map(([p])=>p)).toEqual(['/api/cloud/status','/api/cloud/sync','/api/cloud/status']);
});
