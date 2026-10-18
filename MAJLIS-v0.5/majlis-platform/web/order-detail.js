import {
  escapeHtml as esc,
  money,
  date,
  dateTime,
  icon
} from './ui.js';
import {
  editForms
} from './order-editor.js';
export function detailView(order, stages, isManager, paymentConnected) {
  const current = stages.findIndex(s => s.code === order.status);
  const timeline =
    `<ol class="timeline">${stages.map((stage,index)=>{const event=order.events.filter(e=>e.status===stage.code).at(-1);return `<li class="${event?'complete':''} ${index===current?'current':''}">
<div>
<strong>${esc(stage.title)}</strong>${index===current?'<span class="pill">Сейчас</span>':''}</div>
<p>${esc(stage.description)}</p>
<small>${event?dateTime(event.created_at):'Пока не зарегистрирован'}</small>
</li>`;}).join('')}</ol>`;
  const next = stages[current + 1];
  const payment = order.payment_url ?
    `<p>Откроется страница платёжного сервиса. Наличие ссылки не подтверждает оплату.</p><button id="open-payment" class="button primary">Открыть страницу оплаты ${icon('link')}</button>` :
    '<p>Платёжная ссылка ещё не добавлена.</p>';
  return `<div class="dialog-top">
<button class="order-number copy-inline" data-copy="${esc(order.number)}">${esc(order.number)} ${icon('copy')}</button>
<button class="close-dialog icon-button" aria-label="Закрыть">${icon('close')}</button>
</div>
<h2 id="detail-title">${esc(order.item)}</h2>
<p class="detail-subtitle">${money(order.amount)} · ${esc(order.stage.title)}</p>
<section class="current-stage">
<span class="eyebrow">Что происходит сейчас</span>
<h3>${esc(order.stage.title)}</h3>
<p>${esc(order.stage.description)}</p>${next?`<small>Следующий предусмотренный этап: ${esc(next.title)}. Время перехода пока не указано.</small>`:'<small>Доставка отмечена как завершённая.</small>'}</section>
<div class="delivery-date">${icon('clock')}<div>
<strong>${order.eta?date(order.eta):'Дата поставки уточняется'}</strong>
<p>${order.eta?(order.eta_confirmed?'Подтверждённая дата':'Ориентировочная дата — может уточняться'):'Магазин добавит дату после уточнения.'}</p>
</div>
</div>
<h3>Путь заказа</h3>${timeline}<section class="payment-block">
<h3>Оплата</h3>${payment}</section>
<div class="quick-actions">
<button class="button" data-copy="${esc(order.number)}">${icon('copy')} Номер заказа</button>
<button class="button" id="copy-question">${icon('chat')} Вопрос о заказе</button>
</div>${isManager?editForms(order,stages,paymentConnected):''}<h3>История изменений</h3>
<p class="hint">История загружается страницами. Более ранние события можно открыть ниже.</p>${order.events.slice().reverse().map(event=>`<div class="history-item">
<strong>${esc(stages.find(s=>s.code===event.status)?.title||event.status)}</strong>${event.note?'<p>'+esc(event.note)+'</p>':''}<small>${isManager?esc(event.customer_id?'Покупатель '+event.customer_id+' · поколение '+event.customer_generation:'Старая история: владелец не подтверждён')+' · ':''}${dateTime(event.created_at)} · ${event.source==='manager'?'Магазин':event.source==='integration'?'Внешняя система':'Обновление'}</small>
</div>`).join('')}${order.history_cursor?'<button class="button full" id="history-more">Показать более ранние события</button>':''}`;
}
