'use strict';
const $=s=>document.querySelector(s);
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const tg=window.Telegram?.WebApp;
let state=null,persona='buyer',managerTab='orders',busy=false;
let toastTimer;
const money=v=>new Intl.NumberFormat('ru-RU',{style:'currency',currency:'RUB',maximumFractionDigits:2}).format(v/100);
const date=v=>v?new Intl.DateTimeFormat('ru-RU',{day:'numeric',month:'long'}).format(new Date(v.length===10?v+'T12:00:00':v)):'Дата уточняется';
const dateTime=v=>new Intl.DateTimeFormat('ru-RU',{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'}).format(new Date(v));
const icon=`<svg viewBox="0 0 40 40" fill="none" aria-hidden="true"><rect x="8" y="7" width="24" height="20" rx="2" stroke="currentColor" stroke-width="1.6"/><path d="M4 31h32M16 27v4m8-4v4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>`;

async function api(path,body){
 const headers={'Content-Type':'application/json','X-Telegram-Init-Data':tg?.initData||'','X-Demo-User':persona};
 const r=await fetch(path,{method:body?'POST':'GET',headers,body:body?JSON.stringify(body):undefined,signal:typeof AbortSignal.timeout==='function'?AbortSignal.timeout(15000):undefined});
 const result=await r.json();if(!r.ok)throw new Error(result.error||'Не удалось выполнить запрос.');return result;
}
function toast(text){$('#toast').textContent=text;$('#toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('#toast').hidden=true,4500);}
function info(title,text){$('#info-title').textContent=title;$('#info-text').textContent=text;$('#info-dialog').showModal();}
function link(kind){
 const url=state?.links[kind];
 if(!url){info(kind==='catalog'?'Каталог пока не подключён':'Связь с менеджером',state?.demo?'Это тестовая версия. Реальный каталог и контакт менеджера подключим после получения данных заказчика.':'Контакт или каталог ещё не настроен. Обратитесь в магазин по известному вам каналу связи.');return;}
 try{
  const u=new URL(url);if(u.protocol!=='https:')throw new Error();
  if(u.hostname==='t.me'&&tg?.openTelegramLink)tg.openTelegramLink(url);else if(tg?.openLink)tg.openLink(url);else window.open(url,'_blank','noopener,noreferrer');
 }catch{info('Ссылка недоступна','Попросите менеджера проверить настройки ссылки.');}
}
function commonIntro(title,subtitle){return `<div class="intro"><div><span class="eyebrow">${state.user.role==='manager'?'Рабочее пространство':'Личный кабинет'}</span><h1>${title}</h1><p>${subtitle}</p></div><button class="refresh" id="refresh" aria-label="Обновить заказы"><span>↻</span> Обновить</button></div>`;}
function side(){return `<aside><div class="side-card"><span class="eyebrow">Всегда на связи</span><h2>Есть вопрос<br>по заказу?</h2><p>Менеджер поможет с доставкой, оплатой и деталями вашего заказа.</p><button class="button primary full" data-link="support">Написать менеджеру <span>↗</span></button><button class="side-link" data-link="catalog">Открыть каталог <span>↗</span></button></div><div class="note-card"><strong>Доставка по этапам</strong><br>Следите за движением заказа в кабинете. Обновления этапов приходят в этот бот.${state.demo?'<br><br>В демо сообщения не отправляются. Этапы и даты приведены для примера.':''}</div></aside>`;}
function orderCard(o){
 const index=state.stages.findIndex(s=>s.code===o.status);
 return `<article class="order-card"><div class="order-top"><span class="order-number">${esc(o.number)}</span><span class="pill ${o.status==='delivered'?'delivered':''}">${esc(o.stage.title)}</span></div><div class="item-row"><div class="product-icon">${icon}</div><div><h3>${esc(o.item)}</h3><small>${money(o.amount)}${state.demo?' · демонстрационный заказ':''}</small></div></div><div class="progress" aria-label="Этап ${index+1} из ${state.stages.length}">${state.stages.map((s,i)=>`<span class="${i<=index?'done':''}"></span>`).join('')}</div><p class="order-description">${esc(o.stage.description)}</p><div class="order-bottom"><div class="eta">${o.eta?date(o.eta):'Дата поставки уточняется'}<small>${o.eta?(o.eta_confirmed?'Подтверждённая дата':'Предварительная дата'):'Сообщим после уточнения'}</small></div><button class="text-button" data-order="${o.id}">Подробнее о заказе →</button></div></article>`;
}
function buyerView(){
 const orders=state.orders;const active=orders.filter(o=>o.status!=='delivered');
 const main=orders.length?`<section class="hero"><span class="eyebrow">Всё под контролем</span><h2>От заказа до получения.<br>Каждый этап — на виду.</h2><p>Здесь собраны ваши заказы, последние обновления и информация о доставке.</p><div class="hero-summary"><div><strong>${active.length}</strong><span>В работе</span></div><div><strong>${orders.length}</strong><span>Всего заказов</span></div></div></section><div class="section-heading"><h3>Мои заказы</h3><span>${orders.length} в кабинете</span></div>${orders.map(orderCard).join('')}`:`<section class="empty"><div class="empty-icon" aria-hidden="true">▱</div><span class="eyebrow">Начнём знакомство</span><h2>Здесь появятся ваши заказы</h2><p>После оформления заказа менеджер добавит его в ваш кабинет. Здесь будут видны этапы доставки и дата поставки.</p><div class="empty-actions"><button class="button primary" data-link="catalog">Перейти в каталог ↗</button><button class="button" data-link="support">Связаться с менеджером</button></div><p class="hint">Уже оформили заказ, но не видите его?<br>Попросите менеджера проверить привязку к вашему Telegram.</p></section>`;
 return `${commonIntro(`Здравствуйте, ${esc(state.user.first_name||'покупатель')}`,`Ваши заказы и доставка — в одном месте.`)}<div class="layout"><section>${main}</section>${side()}</div>`;
}
function newOrderForm(){return `<form id="new-order" class="form-card"><span class="eyebrow">Новый заказ</span><h2>Добавить в кабинет</h2><label>Номер заказа<input name="number" placeholder="MJ-1044" required maxlength="64"></label><label>Telegram ID покупателя<input name="customer_id" inputmode="numeric" type="number" min="1" step="1" placeholder="Из сообщения /start" required ${state.demo?'value="1001"':''}></label><label>Имя покупателя<input name="customer_name" required maxlength="100" placeholder="Имя"></label><label>Товар<input name="item" required maxlength="300" placeholder="Название и комплектация"></label><label>Сумма, ₽<input name="rubles" type="number" min="0.01" step="0.01" required placeholder="0,00"></label><label>Партия карго<input name="shipment" maxlength="100" placeholder="Номер партии, если известен"></label><label>Предварительная поставка<input name="eta" type="date"></label><p class="form-note">ID покупателя используется для привязки заказа. Сумма не означает, что заказ оплачен.</p><button class="button primary full" type="submit">Создать заказ</button></form>`;}
function managerView(){
 const tabs=`<nav class="tabs" aria-label="Разделы менеджера"><button data-tab="orders" class="${managerTab==='orders'?'active':''}">Заказы</button><button data-tab="integrations" class="${managerTab==='integrations'?'active':''}">Подключения</button></nav>`;
 if(managerTab==='integrations'){
  const cards=[['Карго-система','Приём подписанных событий по номеру партии. Конкретный адаптер ещё не настроен.'],['Служба доставки','Нужно подтвердить службу доставки и получить документацию её API.'],['Сайт магазина','Нужно согласовать состав данных и направление синхронизации.'],['Платёжный сервис',state.integrations.payments==='manual_links'?'Разрешена ручная привязка HTTPS-ссылок. Автоматическое создание и подтверждение оплаты не подключены.':'Создание ссылок и подтверждение оплаты недоступны без данных провайдера.']];
  const names={pending:state.demo?'В очереди · демо':'В очереди',sent:'Отправлено',failed:'Ошибка доставки'};
  return `${commonIntro('Подключения','Состояние внешних сервисов и уведомлений.')}${tabs}<div class="integrations">${cards.map(c=>`<article class="integration"><span class="eyebrow">Внешний сервис</span><h2>${c[0]}</h2><p>${c[1]}</p><div class="state">${c[0]==='Платёжный сервис'&&state.integrations.payments==='manual_links'?'Ручные ссылки':'Не подключён'}</div></article>`).join('')}</div><section class="notifications"><h3>Очередь уведомлений</h3><p class="order-description">${state.demo?'В демо сообщения сохраняются в очереди, но не отправляются в Telegram.':'Доставку выполняет отдельный процесс бота.'}</p>${state.notifications.length?state.notifications.map(n=>`<div class="notification"><span>Событие №${n.id} · ${n.attempts} попыток</span><span>${names[n.state]||esc(n.state)}</span></div>`).join(''):'<p class="order-description">Уведомлений пока нет.</p>'}</section>`;
 }
 return `${commonIntro('Заказы магазина','Управляйте доставкой и помогайте покупателям.')}${tabs}<div class="manager-layout"><section><input class="search" id="search" type="search" placeholder="Номер заказа, покупатель или товар" aria-label="Поиск заказов"><div id="manager-orders">${managerRows(state.orders)}</div></section>${newOrderForm()}</div>`;
}
function managerRows(orders){return orders.length?orders.map(o=>`<article class="manager-row"><div><span class="order-number">${esc(o.number)} · ${esc(o.customer_name)}</span><h3>${esc(o.item)}</h3><p>${money(o.amount)} · ${o.shipment?esc(o.shipment):'Партия не назначена'}</p><p>Поставка: ${o.eta?date(o.eta):'дата уточняется'}</p><span class="pill">${esc(o.stage.title)}</span></div><button class="text-button" data-order="${o.id}">Открыть →</button></article>`).join(''):'<div class="empty"><h3>Заказов не найдено</h3><p>Добавьте заказ или измените поисковый запрос.</p></div>';}
function openDetail(id){
 const o=state.orders.find(v=>v.id===Number(id));if(!o)return;
 const manager=state.user.role==='manager';
 const timeline=`<ol class="timeline">${state.stages.map(s=>{const e=o.events.filter(e=>e.status===s.code).at(-1);return `<li class="${e?'complete':''}"><strong>${esc(s.title)}</strong>${s.code===o.status?'<span class="pill">Сейчас</span>':''}<p>${esc(s.description)}</p><small>${e?dateTime(e.created_at):'Событие пока не зарегистрировано'}</small></li>`}).join('')}</ol>`;
 const payment=o.payment_url?`<p>Ссылка предоставлена менеджером. Сам факт наличия ссылки не подтверждает оплату.</p><a class="button primary" href="${esc(o.payment_url)}" target="_blank" rel="noopener noreferrer">Открыть страницу оплаты ↗</a>`:`<p>Платёжная ссылка ещё не добавлена.${state.demo?' В демонстрационной версии оплата не подключена.':''}</p>`;
 const edit=manager?`<form id="status-form" class="form-card" data-id="${o.id}"><h3>Обновить этап</h3><label>Этап доставки<select name="status">${state.stages.map(s=>`<option value="${s.code}" ${s.code===o.status?'selected':''}>${esc(s.title)}</option>`).join('')}</select></label><label>Комментарий для покупателя<textarea name="note" maxlength="500" placeholder="Пояснение, которое увидит покупатель"></textarea></label><p class="form-note">Изменение этапа создаёт уведомление покупателю.${state.demo?' В демо оно останется в очереди.':''}</p><button class="button primary full">Сохранить этап</button></form><form id="details-form" class="form-card" data-id="${o.id}"><h3>Привязка и поставка</h3><label>Telegram ID покупателя<input name="customer_id" type="number" min="1" step="1" value="${o.customer_id}" required></label><label>Партия карго<input name="shipment" maxlength="100" value="${esc(o.shipment)}"></label><label>Дата поставки<input name="eta" type="date" value="${esc(o.eta)}"></label><label class="checkbox-label"><input name="eta_confirmed" type="checkbox" ${o.eta_confirmed?"checked":""}> Дата подтверждена</label><button class="button full">Сохранить данные</button></form><form id="payment-form" class="form-card" data-id="${o.id}"><h3>Ссылка на оплату</h3><label>HTTPS-ссылка провайдера<input name="url" type="url" placeholder="https://…" required ${state.integrations.payments==='not_connected'?'disabled':''}></label><p class="form-note">${state.integrations.payments==='not_connected'?'Сначала настройте разрешённый платёжный сервис. Автоматическое создание ссылки пока не реализовано.':'Только ручная привязка ссылки разрешённого провайдера. Подтверждение оплаты не подключено.'}</p><button class="button full" ${state.integrations.payments==='not_connected'?'disabled':''}>Сохранить ссылку</button></form>`:'';
 $('#detail-content').innerHTML=`<div class="dialog-top"><span class="eyebrow">${esc(o.number)}</span><button class="close-dialog icon-button" aria-label="Закрыть">×</button></div><h2>${esc(o.item)}</h2><p class="detail-subtitle">${money(o.amount)} · ${esc(o.stage.title)}<br>${o.eta?'Поставка: '+date(o.eta)+(o.eta_confirmed?' · подтверждена':' · предварительно'):'Дата поставки уточняется'}</p>${timeline}<section class="payment-block"><h3>Оплата заказа</h3>${payment}</section>${edit}<h2>История обновлений</h2>${o.events.slice().reverse().map(e=>`<div class="history-item">${esc(state.stages.find(s=>s.code===e.status)?.title)}${e.note?'<p>'+esc(e.note)+'</p>':''}<small>${dateTime(e.created_at)} · ${({manager:'Менеджер',integration:'Внешняя система',demo:'Демонстрация',migration:'Перенос данных'})[e.source]||esc(e.source)}</small></div>`).join('')}`;
 $('#detail-dialog').showModal();bindDetail();
}
function bindDetail(){
 document.querySelectorAll('.close-dialog').forEach(b=>b.onclick=()=>b.closest('dialog').close());
 const form=$('#status-form');if(form)form.onsubmit=async e=>{e.preventDefault();await submit(form,async()=>{const data=Object.fromEntries(new FormData(form));data.order_id=Number(form.dataset.id);await api('/api/orders/status',data);$('#detail-dialog').close();await refresh();toast('Этап сохранён.');});};
 const details=$('#details-form');if(details)details.onsubmit=async e=>{e.preventDefault();await submit(details,async()=>{const f=new FormData(details);await api('/api/orders/details',{order_id:Number(details.dataset.id),customer_id:Number(f.get('customer_id')),shipment:f.get('shipment'),eta:f.get('eta'),eta_confirmed:f.has('eta_confirmed')});$('#detail-dialog').close();await refresh();toast('Данные заказа сохранены.');});};
 const payment=$('#payment-form');if(payment)payment.onsubmit=async e=>{e.preventDefault();await submit(payment,async()=>{await api('/api/orders/payment',{order_id:Number(payment.dataset.id),url:new FormData(payment).get('url')});$('#detail-dialog').close();await refresh();toast('Ссылка добавлена.');});};
}
async function submit(form,action){
 const button=form.querySelector('button[type="submit"],button:last-child');if(button)button.disabled=true;
 try{await action();}catch(e){toast(e.message);}finally{if(button)button.disabled=false;}
}
function bind(){
 $('#refresh').onclick=()=>refresh(true);
 document.querySelectorAll('[data-link]').forEach(b=>b.onclick=()=>link(b.dataset.link));
 document.querySelectorAll('[data-order]').forEach(b=>b.onclick=()=>openDetail(b.dataset.order));
 document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>{managerTab=b.dataset.tab;render();});
 const search=$('#search');if(search)search.oninput=()=>{const q=search.value.trim().toLocaleLowerCase();$('#manager-orders').innerHTML=managerRows(state.orders.filter(o=>[o.number,o.item,o.customer_name].join(' ').toLocaleLowerCase().includes(q)));document.querySelectorAll('[data-order]').forEach(b=>b.onclick=()=>openDetail(b.dataset.order));};
 const form=$('#new-order');if(form)form.onsubmit=async e=>{e.preventDefault();await submit(form,async()=>{const d=Object.fromEntries(new FormData(form));d.amount=Math.round(Number(d.rubles)*100);delete d.rubles;d.customer_id=Number(d.customer_id);await api('/api/orders',d);await refresh();toast('Заказ добавлен в кабинет покупателя.');});};
}
function render(){
 $('#app').innerHTML=state.user.role==='manager'?managerView():buyerView();
 $('#demo-bar').hidden=!state.demo;$('#foot-mode').textContent=state.demo?'Тестовые данные · '+dateTime(state.updated_at):'Обновлено '+dateTime(state.updated_at);
 bind();
}
async function refresh(showToast=false){
 if(busy)return;busy=true;const button=$('#refresh');if(button)button.disabled=true;
 try{state=await api('/api/bootstrap');render();if(showToast)toast('Данные обновлены.');}
 catch(e){if(state)toast('Не удалось обновить: '+e.message);else{$('#app').innerHTML=`<div class="error-panel"><h2>Кабинет пока недоступен</h2><p>${esc(e.message)}</p><button id="retry" class="button">Попробовать снова</button></div>`;$('#retry').onclick=()=>refresh();}}
 finally{busy=false;if(button)button.disabled=false;}
}
$('#persona').onchange=async e=>{persona=e.target.value;managerTab='orders';state=null;$('#detail-dialog').close();await refresh();};
$('#support-top').onclick=()=>link('support');
document.querySelectorAll('.close-dialog').forEach(b=>b.onclick=()=>b.closest('dialog').close());
if(tg?.initData){tg.ready();tg.expand();try{tg.setHeaderColor('#f4f6fa');tg.setBackgroundColor('#f4f6fa');}catch{} }
refresh();
