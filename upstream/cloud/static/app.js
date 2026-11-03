'use strict';
const $=id=>document.getElementById(id);
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let rows=[],cursor='',busy=false,generation=0;
async function api(path,body){const response=await fetch('/api/v1'+path,{method:body===undefined?'GET':'POST',credentials:'same-origin',cache:'no-store',headers:body===undefined?{}:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});const value=await response.json();if(!response.ok)throw Error(value.detail||'请求失败');return value;}
function render(){const q=$('search').value.trim().toLowerCase();const filtered=rows.filter(a=>(a.title+' '+a.dynamic_id).toLowerCase().includes(q));$('count').textContent=`已加载 ${rows.length} 条，当前显示 ${filtered.length} 条`;$('activities').innerHTML=filtered.map(a=>`<article class="card"><p class="muted">${esc(a.lottery_type)} · ${esc(a.dynamic_id)}</p><h2>${esc(a.title||a.dynamic_id)}</h2><p>开奖：${a.lottery_time?esc(new Date(a.lottery_time*1000).toLocaleString('zh-CN')):'尚未公布'}</p><a href="https://t.bilibili.com/${encodeURIComponent(a.dynamic_id)}" target="_blank" rel="noopener noreferrer">查看原活动 ↗</a></article>`).join('');$('more').hidden=!cursor;}
async function load(reset=false){if(busy)return;busy=true;const stamp=generation;try{const result=await api('/catalog?after='+encodeURIComponent(reset?'':cursor));if(stamp!==generation)return;const existing=new Map((reset?[]:rows).map(a=>[a.dynamic_id,a]));for(const item of result.items)existing.set(item.dynamic_id,item);rows=[...existing.values()];cursor=result.next||'';render();$('notice').textContent='活动资料已更新';}catch(e){$('notice').textContent=e.message;}finally{busy=false;}}
async function enter(){await api('/me');$('login-panel').hidden=true;$('dashboard').hidden=false;$('logout').hidden=false;await load(true);}
$('login-form').addEventListener('submit',async e=>{e.preventDefault();const button=e.target.querySelector('button');button.disabled=true;try{await api('/auth/login',{username:$('username').value,password:$('password').value,device_name:'活动网站',kind:'pwa'});$('password').value='';await enter();}catch(e){$('notice').textContent=e.message;}finally{button.disabled=false;}});
$('logout').onclick=async()=>{try{await api('/auth/logout',{});generation++;rows=[];cursor='';repostCursor=0;$('reposts').replaceChildren();$('more-reposts').hidden=true;render();$('dashboard').hidden=true;$('login-panel').hidden=false;$('logout').hidden=true;}catch(e){$('notice').textContent=e.message;}};
$('refresh').onclick=()=>load(true);$('more').onclick=()=>load();$('search').oninput=render;
enter().catch(()=>{$('notice').textContent='请登录后查看活动资料';});
if('serviceWorker' in navigator)navigator.serviceWorker.getRegistrations().then(rs=>Promise.all(rs.map(r=>r.unregister())));

let repostCursor=0,repostBusy=false;
async function loadReposts(reset=false){
  if(repostBusy)return;repostBusy=true;const stamp=generation;
  try{
    const data=await api('/reposts?after='+(reset?0:repostCursor));
    if(stamp!==generation)return;
    if(reset)$('reposts').replaceChildren();
    $('reposts').insertAdjacentHTML('beforeend',data.items.map(r=>`<p>活动 <a href="https://t.bilibili.com/${esc(r.dynamic_id)}" target="_blank" rel="noopener noreferrer">${esc(r.dynamic_id)}</a> · UID ${esc(r.uid)} · <a href="https://t.bilibili.com/${esc(r.repost_id)}" target="_blank" rel="noopener noreferrer">本人转发 ${esc(r.repost_id)}</a></p>`).join(''));
    repostCursor=data.cursor;$('more-reposts').hidden=!data.more;
    if(reset&&!data.items.length)$('reposts').textContent='尚无转发关联，请在新版桌面端同步。';
  }catch(e){$('notice').textContent=e.message;}finally{repostBusy=false;}
}
$('load-reposts').onclick=()=>loadReposts(true);
$('more-reposts').onclick=()=>loadReposts();
