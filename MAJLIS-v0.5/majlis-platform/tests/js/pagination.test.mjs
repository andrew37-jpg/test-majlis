import test from 'node:test';
import assert from 'node:assert/strict';
import {OrderPages} from '../../web/pagination.js';
import {escapeHtml,rublesToKopecks} from '../../web/ui.js';
const filters={q:'',bucket:'all'};
let orders=Array.from({length:305},(_,i)=>({id:305-i,item:'Original'}));
const page=query=>{const p=new URLSearchParams(query);const selected=orders.filter(o=>(!p.get('before')||o.id<Number(p.get('before')))&&(!p.get('q')||o.item.includes(p.get('q'))));return {user:{id:1,role:'buyer'},orders:selected.slice(0,100),next_cursor:selected.length>100?selected[99].id:null};};
test('several pages -> refresh -> continue, updated values and no duplicates',async()=>{
 const pages=new OrderPages(async query=>page(query));
 assert.equal((await pages.refresh(filters)).orders.length,100);
 assert.equal((await pages.more(filters)).orders.length,200);
 orders=orders.map(o=>({...o,item:'Updated'}));
 const refreshed=await pages.refresh(filters);assert.equal(refreshed.orders.length,200);assert.ok(refreshed.orders.every(o=>o.item==='Updated'));
 const next=await pages.more(filters);assert.equal(next.orders.length,300);assert.equal(new Set(next.orders.map(o=>o.id)).size,300);
 assert.equal((await pages.more(filters)).orders.length,305);
 assert.equal((await pages.refresh(filters)).orders.length,305);
 assert.equal((await pages.refresh({q:'Updated',bucket:'all'})).orders.length,100);
});
test('stale refresh cannot overwrite new filters',async()=>{
 let release;const delayed=new Promise(r=>release=r);let calls=0;
 const pages=new OrderPages(async query=>{if(++calls===1)await delayed;return page(query);});
 const old=pages.refresh(filters);const latest=await pages.refresh({q:'Missing',bucket:'all'});release();assert.equal(await old,null);assert.equal(latest.orders.length,0);assert.equal(pages.value.orders.length,0);
});
test('stale load-more cannot append into a new filter, double click deduplicated',async()=>{
 let release;const delayed=new Promise(r=>release=r);
 const pages=new OrderPages(async query=>{if(new URLSearchParams(query).has('before'))await delayed;return page(query);});
 await pages.refresh(filters);const old=pages.more(filters);assert.equal(await pages.more(filters),null);
 await pages.refresh({q:'Missing',bucket:'all'});release();assert.equal(await old,null);assert.equal(pages.value.orders.length,0);
});
test('HTML escaping and money are exact, invalid values rejected',()=>{
 assert.equal(escapeHtml('<img onerror="x">'), '&lt;img onerror=&quot;x&quot;&gt;');
 assert.equal(rublesToKopecks('120,01'),12001);
 for(const value of ['0','1.234','-1','Infinity'])assert.throws(()=>rublesToKopecks(value));
});

test('detail and transfer form escape personal fields and explain ownership',async()=>{
 const {detailView}=await import('../../web/order-detail.js');
 const stages=[{code:'confirmed',title:'Confirmed',description:'Description'}];
 const order={id:1,number:'<img src=x>',item:'<script>alert(1)</script>',customer_id:1,customer_name:'<img onerror="x">',amount:1,status:'confirmed',stage:stages[0],eta:'',shipment:'',events:[],payment_url:'',version:1};
 const buyer=detailView(order,stages,false,false);
 assert.ok(buyer.includes('&lt;script&gt;'));assert.ok(!buyer.includes('<script>'));assert.ok(!buyer.includes('details-form'));
 const manager=detailView(order,stages,true,false);
 assert.ok(manager.includes('transfer_confirmed'));assert.ok(manager.includes('&lt;img onerror=&quot;x&quot;&gt;'));assert.ok(manager.includes('customer_name'));
});
