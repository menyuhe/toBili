// @vitest-environment jsdom
import {expect,it,vi} from 'vitest';
vi.mock('../api/client',()=>({fetchJSON:vi.fn()}));
vi.mock('../shell/confirm',()=>({openAppConfirm:vi.fn()}));
vi.mock('../shell/toast',()=>({showToast:vi.fn()}));
import {renderScheduleConfig,saveScheduleConfig} from './index';
import {fetchJSON} from '../api/client';
import {state} from '../state';
const config=()=>Object.assign(Object.fromEntries(['refresh_discovery','refresh_all','refresh_watch','refresh_status','participate_pool'].map(key=>[key,{enabled:true,minutes:180}])),{nonstop:false,nonstop_hourly_limit:20,nonstop_minutes:180,maintenance_minutes:30});
it('keeps unsaved edits during polling but isolates account changes',()=>{
 document.body.innerHTML='<form id="auto-config-form"></form>';
 renderScheduleConfig({uid:'101',config:config()});
 const input=document.querySelector('[name=refresh_all_minutes]') as HTMLInputElement;
 input.value='45';input.dispatchEvent(new Event('input',{bubbles:true}));
 renderScheduleConfig({uid:'101',config:config()});expect(input.value).toBe('45');
 renderScheduleConfig({uid:'202',config:config()});expect((document.querySelector('[name=refresh_all_minutes]') as HTMLInputElement).value).toBe('180');
});
it('saving a plan does not start jobs or call cloud endpoints',async()=>{
 document.body.innerHTML='<form id="auto-config-form"></form>';
 const cfg=config();renderScheduleConfig({uid:'303',config:cfg});
 state.autoScheduler={config:cfg} as any;
 vi.mocked(fetchJSON).mockResolvedValue({uid:'303',config:cfg});
 (document.querySelector("[name=nonstop_hourly_limit]") as HTMLInputElement).value="12";
 await saveScheduleConfig();
 expect(JSON.parse((vi.mocked(fetchJSON).mock.calls[0][1] as any).body).nonstop_hourly_limit).toBe(12);
 expect(vi.mocked(fetchJSON).mock.calls.map(([path])=>path)).toEqual(['/api/auto/config','/api/auto/status']);
});
