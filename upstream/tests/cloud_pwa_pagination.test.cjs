const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const elements=new Map();
function el(id){if(!elements.has(id))elements.set(id,{value:'',hidden:false,textContent:'',innerHTML:'',addEventListener(){},replaceChildren(){this.innerHTML='';},insertAdjacentHTML(_,v){this.innerHTML+=v;}});return elements.get(id);}
const calls=[];
const ctx=vm.createContext({document:{getElementById:el},navigator:{},console,fetch:async url=>{
 calls.push(url);let data={};
 if(url.includes('/catalog')){const more=!url.includes('after=200');data={items:Array.from({length:more?200:1},(_,i)=>({dynamic_id:String((more?1:201)+i),title:'奖品',lottery_type:'转发抽奖'})),next:more?'200':''};}
 else if(url.includes('/reposts')){data={items:[{uid:'11',dynamic_id:'101',repost_id:'901'},{uid:'22',dynamic_id:'101',repost_id:'902'}],cursor:2,more:false};}
 else assert.ok(url.endsWith('/me')||url.endsWith('/auth/logout'));
 return {ok:true,json:async()=>data};
}});
const settle=async()=>{for(let i=0;i<8;i++)await new Promise(setImmediate);};
(async()=>{
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../cloud/static/app.js'),'utf8'),ctx);await settle();
 assert.equal((el('activities').innerHTML.match(/<article /g)||[]).length,200);
 await el('more').onclick();assert.equal((el('activities').innerHTML.match(/<article /g)||[]).length,201);
 assert.equal(el('more').hidden,true);
 await el('load-reposts').onclick();assert.match(el('reposts').innerHTML,/UID 11/);assert.match(el('reposts').innerHTML,/UID 22/);
 assert.match(el('reposts').innerHTML,/901/);assert.match(el('reposts').innerHTML,/902/);
 await el('logout').onclick();assert.equal(el('reposts').innerHTML,'');
 assert.ok(!calls.some(u=>/tasks|leases|activities/.test(u)));
 console.log('Catalog pagination and UID-scoped repost display passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
