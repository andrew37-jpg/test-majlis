import {
  escapeHtml as esc,
  dateTime
} from './ui.js';
import {
  eventTitle
} from './order-detail.js';
export function supportForm(draft) {
  return `<section class="form-card"><h2>Обратиться в магазин</h2><p>Сообщение появится в кабинете сотрудников с назначенным доступом. Пароли, банковские реквизиты и коды сюда не отправляйте.</p><form id="support-form"><label>Ваш вопрос<textarea name="text" maxlength="4096" required>${esc(draft.text)}</textarea></label><button class="button primary full" type="submit">Отправить обращение</button></form></section>`;
}
export function feedView(section, feed, stages, intro) {
  const title = section === 'inbox' ? 'Обращения клиентов' : 'Активность клиентов';
  const subtitle = section === 'inbox' ? 'Сообщения, отправленные в бот и через кабинет MAJLIS.' :
    'Изменения заказов в пределах вашего доступа.';
  const rows = feed?.items || [];
  return `${intro(title,subtitle)}${feed?.error?`<p class="freshness-warning" role="status">${esc(feed.error)}</p>`:''}${!feed?'<p role="status">Загружаем…</p>':!rows.length?'<section class="empty compact"><h2>Записей пока нет</h2><p>Здесь появятся новые события.</p></section>':`<div class="activity-list">${rows.map(item=>`<article class="form-card"><div class="section-heading"><strong>${section==='inbox'?esc(item.first_name||'Клиент')+' · ID '+item.customer_id:`Заказ ${esc(item.number)}`}</strong><small>${dateTime(item.created_at)}</small></div><h3>${section==='inbox'?(item.source==='telegram'?'Сообщение в бот':'Обращение из кабинета'):esc(eventTitle(item,stages))}</h3><p class="message-text">${esc(section==='inbox'?item.text:item.note||'Этап уточнён')}</p>${section==='activity'?`<button class="button" data-order="${item.order_id}">Открыть заказ</button>`:`<small>${esc(item.kind==='text'?'Текст':item.kind==='voice'?'Голосовое сообщение — файл не загружается':item.kind)}</small>`}</article>`).join('')}</div>`}${feed?.next_cursor?'<button id="feed-more" class="button full">Загрузить более ранние</button>':''}`;
}
