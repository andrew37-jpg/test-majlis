import {
  escapeHtml as esc
} from './ui.js';
export function editForms(order, stages, paymentConnected) {
  return `<details class="manager-edit">
<summary>Изменить заказ</summary>
<form id="commercial-form" data-order-id="${order.id}" class="form-card">
<h3>Товар и сумма</h3>
<label>Товар и комплектация<input name="item" maxlength="300" value="${esc(order.item)}" required></label>
<label>Сумма, ₽<input name="rubles" inputmode="decimal" value="${(order.amount/100).toFixed(2)}" required></label>
<p class="form-note">Изменение товара или суммы удалит прежнюю ссылку из кабинета. Сам счёт у провайдера этой операцией не отменяется. Сумма не подтверждает оплату.</p>
<button class="button full" type="submit">Сохранить товар и сумму</button>
</form>
<form id="tracking-form" data-order-id="${order.id}" class="form-card">
<h3>Отслеживание</h3>
<label>Трек-номер<input name="tracking_number" maxlength="100" value="${esc(order.tracking?.number||'')}"></label>
<label>Перевозчик<input name="carrier" maxlength="100" value="${esc(order.tracking?.carrier||'')}"></label>
<p class="form-note">Укажите только подтверждённые данные. Это ручное уточнение, а не подключение перевозчика.</p>
<button type="submit" class="button full">Сохранить отслеживание</button></form>
<form id="status-form" data-order-id="${order.id}" class="form-card">
<h3>Этап доставки</h3>
<label>Этап<select name="status">${stages.map(s=>`<option value="${s.code}" ${s.code===order.status?'selected':''}>${esc(s.title)}</option>`).join('')}</select>
</label>
<label>Комментарий покупателю<textarea name="note" maxlength="500" placeholder="Уточнение по заказу">
</textarea>
</label>
<p class="form-note">Изменение этапа добавит уведомление в очередь.</p>
<button class="button primary full" type="submit">Сохранить этап</button>
</form>
<form id="details-form" data-order-id="${order.id}" class="form-card">
<h3>Клиент и поставка</h3>
<p class="form-note">При передаче укажите имя нового покупателя. Прежняя ссылка оплаты сбросится, старая история останется у магазина.</p>
<label>Имя покупателя<input name="customer_name" maxlength="100" value="${esc(order.customer_name)}" required>
</label>
<label>Telegram ID<input name="customer_id" inputmode="numeric" value="${order.customer_id}" required>
</label>
<label class="checkbox-label">
<input name="transfer_confirmed" type="checkbox"> Подтверждаю передачу другому покупателю (если изменён ID)</label>
<label>Партия<input name="shipment" maxlength="100" value="${esc(order.shipment)}">
</label>
<label>Дата поставки<input name="eta" type="date" value="${esc(order.eta)}">
</label>
<label class="checkbox-label">
<input name="eta_confirmed" type="checkbox" ${order.eta_confirmed?'checked':''}> Дата подтверждена</label>
<button class="button full" type="submit">Сохранить поставку</button>
</form>
<form id="payment-form" data-order-id="${order.id}" class="form-card">
<h3>Готовая платёжная ссылка</h3>
${order.payment_url?`<div class="quick-actions"><button type="button" id="copy-payment" class="button">Копировать ссылку</button><button type="button" id="revoke-payment" class="button">Убрать из кабинета</button></div>`:''}
<label>HTTPS-ссылка<input name="url" type="url" maxlength="2000" value="${esc(order.payment_url)}" required ${!paymentConnected?'disabled':''}>
</label>
<p class="form-note">${!paymentConnected?'Сервис оплаты ещё не настроен.':'Только разрешённый сервис; подтверждение платежа не подключено.'}</p>
<button class="button full" type="submit" ${!paymentConnected?'disabled':''}>Сохранить ссылку</button>
</form>
</details>`;
}
