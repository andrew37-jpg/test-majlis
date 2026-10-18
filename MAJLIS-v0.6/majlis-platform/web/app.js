import {
  detailView
} from './order-detail.js';
import {
  OrderPages
} from './pagination.js';
import {
  escapeHtml as esc,
  money,
  date,
  dateTime,
  rublesToKopecks,
  searchParams,
  icon
} from './ui.js';
const $ = selector => document.querySelector(selector);
const tg = window.Telegram?.WebApp;
const ui = {
  section: 'orders',
  filters: {
    q: '',
    bucket: 'all'
  },
  draft: {},
  dirty: false
};
let telegramActive = true;
let detailNumber = 0;
let state = null,
  busy = false,
  requestNumber = 0,
  controller = null,
  searchTimer, toastTimer, mutating = false;
const pages = new OrderPages((query, signal) => api('/api/bootstrap?' + query, null, signal));
const manager = () => state?.user.role === 'manager';

function haptic(type = 'selection') {
  try {
    if (type === 'selection') tg?.HapticFeedback?.selectionChanged();
    else if (type === 'impact') tg?.HapticFeedback?.impactOccurred('light');
    else tg?.HapticFeedback?.notificationOccurred(type);
  } catch {}
}

function toast(text) {
  $('#toast').textContent = text;
  $('#toast').hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => $('#toast').hidden = true, 4000);
}

function info(title, text) {
  $('#info-title').textContent = title;
  $('#info-text').textContent = text;
  if (!$('#info-dialog').open) $('#info-dialog').showModal();
  syncBack();
}

function connection(text = '', type = '') {
  const el = $('#connection');
  el.textContent = text;
  el.className = type;
  el.hidden = !text;
}
async function api(path, body, signal) {
  const timeout = typeof AbortSignal.timeout === 'function' ? AbortSignal.timeout(15000) :
    undefined;
  const merged = signal && timeout && typeof AbortSignal.any === 'function' ? AbortSignal.any([
    signal, timeout
  ]) : signal || timeout;
  const response = await fetch(path, {
    cache: 'no-store',
    method: body ? 'POST' : 'GET',
    headers: {
      'Content-Type': 'application/json',
      'X-Telegram-Init-Data': tg?.initData || ''
    },
    body: body ? JSON.stringify(body) : undefined,
    signal: merged
  });
  let result;
  try {
    result = await response.json();
  } catch {
    throw new Error('Сервер временно недоступен. Попробуйте обновить кабинет.');
  }
  if (!response.ok) {
    const error = new Error(result.error || 'Не удалось выполнить запрос.');
    error.status = response.status;
    throw error;
  }
  return result;
}

function expire() {
  detailNumber++;
  pages.clear();
  state = null;
  document.querySelectorAll('dialog[open]').forEach(d => d.close());
  $('#navigation').hidden = true;
  $('#app').innerHTML =
    `<section class="empty"><div class="empty-icon">${icon('box')}</div><h1>Откройте кабинет<br>через Telegram</h1><p>Если кабинет уже был открыт, закройте это окно и снова нажмите «Кабинет MAJLIS» в меню бота.</p><button id="retry" class="button">Попробовать снова</button></section>`;
  $('#retry').onclick = () => refresh();
  syncBack();
}

function external(url) {
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== 'https:' || parsed.username || parsed.password) throw new Error();
    if (parsed.hostname === 't.me' && tg?.openTelegramLink) tg.openTelegramLink(url);
    else if (tg?.openLink) tg.openLink(url);
    else window.open(url, '_blank', 'noopener,noreferrer');
  } catch {
    info('Ссылка недоступна', 'Попросите магазин проверить адрес ссылки.');
  }
}

function link(kind) {
  const url = state?.links[kind];
  if (url) external(url);
  else info(kind === 'catalog' ? 'Каталог пока не подключён' : 'Контакт магазина пока не указан',
    'Адрес добавит магазин после настройки. Никакое сообщение не было отправлено.');
}
async function copy(text) {
  try {
    await navigator.clipboard.writeText(String(text));
    toast('Скопировано');
    haptic('success');
  } catch {
    info('Скопируйте вручную', String(text));
  }
}

function intro(title, subtitle) {
  return `<div class="intro">
<div>
<span class="eyebrow">${manager()?'Рабочее пространство':'Личный кабинет'}</span>
<h1>${title}</h1>
<p>${subtitle}</p>
</div>
<button id="refresh" class="refresh ${busy?'spinning':''}" aria-label="Обновить заказы" ${busy?'disabled':''}>${icon('refresh')}<span>Обновить</span>
</button>
</div>`;
}

function navigation() {
  const sections = manager() ? [
    ['orders', 'box', 'Заказы'],
    ['new', 'plus', 'Добавить'],
    ['help', 'help', 'Помощь']
  ] : [
    ['orders', 'box', 'Заказы'],
    ['catalog', 'grid', 'Каталог'],
    ['help', 'help', 'Помощь']
  ];
  $('#navigation').innerHTML = sections.map(([id, symbol, label]) =>
    `<button data-section="${id}" class="${ui.section===id?'active':''}" ${ui.section===id?'aria-current="page"':''}>${icon(symbol)}<span>${label}</span></button>`
  ).join('');
  $('#navigation').hidden = false;
}

function navigate(section) {
  if (section === ui.section) return;
  ui.section = section;
  haptic();
  render();
  window.scrollTo({
    top: 0,
    behavior: 'instant'
  });
  syncBack();
}

function counts() {
  return state.summary || {
    total: state.orders.length,
    active: state.orders.filter(o => o.status !== 'delivered').length,
    delivered: state.orders.filter(o => o.status === 'delivered').length,
    matching: state.orders.length
  };
}

function filters() {
  const totals = counts();
  return `<section class="order-tools" aria-label="Поиск и фильтры">
<div class="search-wrap">${icon('search')}<input id="order-search" type="search" maxlength="100" placeholder="${manager()?'Номер, товар, покупатель или партия':'Номер заказа, товар или партия'}" aria-label="Поиск заказов" value="${esc(ui.filters.q)}">
</div>
<div class="filter-chips" role="group" aria-label="Статус заказов">${[['all','Все',totals.total],['active','В работе',totals.active],['delivered','Получены',totals.delivered]].map(([id,label,n])=>`<button data-filter="${id}" class="${ui.filters.bucket===id?'selected':''}" aria-pressed="${ui.filters.bucket===id}">${label}<span>${n}</span>
</button>`).join('')}</div>
</section>`;
}

function orderCard(order) {
  const index = state.stages.findIndex(s => s.code === order.status);
  return `<article class="order-card">
<div class="order-top">
<button class="order-number copy-inline" data-copy="${esc(order.number)}" aria-label="Скопировать номер ${esc(order.number)}">${esc(order.number)} ${icon('copy')}</button>
<span class="pill ${order.status==='delivered'?'delivered':''}">${esc(order.stage.title)}</span>
</div>
<div class="item-row">
<div class="product-icon">${icon('box')}</div>
<div>
<h3>${esc(order.item)}</h3>
<small>${money(order.amount)}</small>${manager()?`<small>${esc(order.customer_name)} · ${order.shipment?esc(order.shipment):'Без партии'}</small>`:''}</div>
</div>
<div class="stage-caption">
<span>Этап ${index+1} из ${state.stages.length}</span>
<span>${order.status==='delivered'?'Доставка завершена':'По последнему обновлению'}</span>
</div>
<div class="progress" aria-label="Этап ${index+1} из ${state.stages.length}">${state.stages.map((s,i)=>`<span class="${order.events.some(event=>event.status===s.code&&(!event.kind||event.kind==='status'))?'done':''} ${s.code===order.status?'current':''}">
</span>`).join('')}</div>
<p class="order-description">${esc(order.stage.description)}</p>
<div class="order-bottom">
<div class="eta">${icon('clock')}<div>${order.eta?date(order.eta):'Дата поставки уточняется'}<small>${order.eta?(order.eta_confirmed?'Дата подтверждена':'Ориентировочная дата'):'Появится после уточнения'}</small>
</div>
</div>
<button class="text-button" data-order="${order.id}">Подробнее ${icon('arrow')}</button>
</div>
</article>`;
}

function emptyOrders() {
  if (ui.filters.q || ui.filters.bucket !== 'all')
    return `<section class="empty compact"><div class="empty-icon">${icon('search')}</div><h2>Не нашли заказ</h2><p>Попробуйте другой запрос или покажите все заказы.</p><button id="reset-filters" class="button primary">Сбросить фильтры</button></section>`;
  return `<section class="empty">
<div class="empty-icon">${icon('box')}</div>
<span class="eyebrow">Начнём знакомство</span>
<h2>Здесь появятся ваши заказы</h2>
<p>${manager()?'Добавьте первый заказ и укажите Telegram ID покупателя.':'После оформления магазин привяжет заказ к вашему Telegram. Здесь появятся этапы и дата поставки.'}</p>
<button class="button primary" data-section="${manager()?'new':'help'}">${manager()?'Добавить заказ':'Как найти мой заказ?'} ${icon('arrow')}</button>${!manager()?'<button class="button" data-section="catalog">Открыть каталог</button>':''}</section>`;
}

function ordersView() {
  const summary = counts();
  const cards = state.orders.map(orderCard).join('') || emptyOrders();
  return `${intro(manager()?'Заказы магазина':`Здравствуйте, ${esc(state.user.first_name||'покупатель')}`,manager()?'Поиск, доставка и работа с покупателями.':'Ваши заказы — рядом, на каждом этапе.')}<section class="overview">
<div>
<span class="eyebrow">${manager()?'Сводка магазина':'Сопровождение заказа'}</span>
<h2>${summary.active?'Следующий этап —<br>всегда на виду.':'От заказа<br>до получения.'}</h2>
<p>Статусы, история и дата поставки в одном кабинете.</p>
</div>
<div class="summary-grid">
<div>
<strong>${summary.active}</strong>
<span>В работе</span>
</div>
<div>
<strong>${summary.delivered}</strong>
<span>Получены</span>
</div>
</div>
</section>${filters()}<div class="section-heading">
<h2>${manager()?'Заказы':'Мои заказы'}</h2>
<span>${summary.matching} найдено</span>
</div>
<div class="cards-grid" ${busy?'aria-busy="true"':''}>${cards}</div>${state.next_cursor?'<button id="load-more" class="button full load-more">Загрузить ещё заказы</button>':''}`;
}

function catalogView() {
  return `${intro('Каталог','Подберите технику для нового заказа.')}<section class="empty">
<div class="empty-icon">${icon('grid')}</div>
<h2>${state.links.catalog?'Перейти к товарам':'Каталог появится здесь'}</h2>
<p>${state.links.catalog?'Каталог магазина откроется по отдельной ссылке. Ваш кабинет останется доступен в Telegram.':'Магазин ещё не указал адрес каталога. Здесь появится кнопка перехода, когда он будет подключён.'}</p>${state.links.catalog?'<button class="button primary" data-link="catalog">Открыть каталог '+icon('link')+'</button>':'<button class="button" data-section="orders">Вернуться к заказам</button>'}</section>`;
}

function helpView() {
  const items = [
    ['Заказ оформлен, но в кабинете пусто',
      'Передайте магазину ваш Telegram ID — он указан ниже. Менеджер проверит привязку заказа к вашему аккаунту.'
    ],
    ['Чем отличаются даты поставки?',
      'Ориентировочная дата — предварительная оценка магазина. Подтверждённая дата отмечается отдельно. Если дата не указана, точный срок пока неизвестен.'
    ],
    ['Что означают этапы?',
      'В карточке заказа есть пояснение текущего этапа. В разделе «Подробнее» можно посмотреть историю фактических изменений.'
    ],
    ['Как оплатить заказ?',
      'Если менеджер добавил платёжную ссылку, она появится в подробностях заказа. Наличие ссылки или сумма заказа не означают, что оплата подтверждена.'
    ],
    ['Почему данные могли не обновиться?',
      'Нажмите обновление. Если связь недоступна, кабинет покажет предупреждение, а ранее загруженные данные могут быть устаревшими.'
    ]
  ];
  return `${intro('Помощь','Быстрые ответы и связь с магазином.')}<div class="help-grid">
<section class="profile-card">
<div class="avatar">${esc((state.user.first_name||'М').slice(0,1))}</div>
<h2>${esc(state.user.first_name||'Покупатель')}</h2>
<p>${manager()?'Менеджер магазина':'Ваш Telegram ID для привязки заказа'}</p>
<button class="id-copy" data-copy="${state.user.id}">${state.user.id} ${icon('copy')}</button>
<button class="button primary full" data-link="support" ${state.links.support?'':'disabled'}>${icon('chat')} Написать магазину</button>${!state.links.support?'<p class="hint">Контакт магазина ещё не настроен.</p>':''}<button class="button full" id="export-data">Скачать мои данные</button>
<p class="hint">Кабинет хранит имя, Telegram ID и данные заказов. Для удаления и полной выгрузки обратитесь в магазин. Копии и журнал требуют отдельной обработки.</p>
</section>
<section class="faq">
<h2>Частые вопросы</h2>${items.map(([title,text])=>`<details>
<summary>${title}</summary>
<p>${text}</p>
</details>`).join('')}</section>
</div>${manager()?integrationsView():''}`;
}

function integrationsView() {
  const providers = [
    ['Карго', 'cargo'],
    ['Доставка', 'delivery'],
    ['Сайт', 'website'],
    ['Оплата', 'payments']
  ];
  const names = {
    pending: 'В очереди',
    sending: 'Отправляется',
    sent: 'Отправлено',
    failed: 'Не доставлено'
  };
  return `<section class="service-panel">
<h2>Подключения магазина</h2>
<div class="integration-grid">${providers.map(([name,key])=>`<div>
<strong>${name}</strong>
<span>${state.integrations[key]==='manual_links'?'Ручные ссылки':'Не подключено'}</span>
</div>`).join('')}</div>
<h3>Уведомления</h3>${state.notifications.length?state.notifications.map(n=>`<div class="notification">
<span>№${n.id} · попыток: ${n.attempts}</span>
<span>${names[n.state]||esc(n.state)}</span>
</div>`).join(''):'<p class="hint">Уведомлений пока нет.</p>'}</section>`;
}

function newOrderView() {
  const draft = ui.draft;
  return `${intro('Новый заказ','Добавьте товар в кабинет покупателя.')}<form id="new-order" class="form-card narrow">
<div class="form-heading">${icon('plus')}<div>
<h2>Данные заказа</h2>
<p>Черновик сохраняется при переходе между разделами до закрытия приложения.</p>
</div>
</div>${[['number','Номер заказа','MJ-1044',64],['customer_id','Telegram ID покупателя','Из команды /id',19],['customer_name','Имя покупателя','Имя',100],['item','Товар и комплектация','Название товара',300],['rubles','Сумма, ₽','0,00',16],['shipment','Партия карго','Можно указать позже',100]].map(([key,label,placeholder,max])=>`<label>${label}<input name="${key}" maxlength="${max}" placeholder="${placeholder}" value="${esc(draft[key]||'')}" ${key==='customer_id'?'inputmode="numeric"':key==='rubles'?'inputmode="decimal"':''} ${key!=='shipment'?'required':''}>
</label>`).join('')}<label>Ориентировочная поставка<input name="eta" type="date" value="${esc(draft.eta||'')}">
</label>
<p class="form-note">Сумма заказа не подтверждает оплату. Все данные можно уточнить после создания.</p>
<button class="button primary full" type="submit">Создать заказ ${icon('arrow')}</button>
</form>`;
}

function render() {
  if (!state) return;
  const focus = document.activeElement;
  const saved = focus?.id === 'order-search' ? {
    start: focus.selectionStart,
    end: focus.selectionEnd
  } : null;
  $('#app').innerHTML = ui.section === 'catalog' ? catalogView() : ui.section === 'help' ?
    helpView() : ui.section === 'new' && manager() ? newOrderView() : ordersView();
  $('#preview-notice').hidden = !state.demo;
  $('#foot-mode').textContent = 'Обновлено ' + dateTime(state.updated_at);
  navigation();
  bind();
  if (saved) {
    const input = $('#order-search');
    input?.focus({
      preventScroll: true
    });
    try {
      input?.setSelectionRange(saved.start, saved.end);
    } catch {}
  }
  syncBack();
}

function bind() {
  $('#export-data')?.addEventListener('click', exportData);
  $('#refresh')?.addEventListener('click', () => refresh(true));
  document.querySelectorAll('[data-section]').forEach(b => b.onclick = () => navigate(b.dataset
    .section));
  document.querySelectorAll('[data-link]').forEach(b => b.onclick = () => link(b.dataset.link));
  document.querySelectorAll('[data-copy]').forEach(b => b.onclick = () => copy(b.dataset.copy));
  document.querySelectorAll('[data-order]').forEach(b => b.onclick = () => openDetail(Number(b
    .dataset.order)));
  document.querySelectorAll('[data-filter]').forEach(b => b.onclick = () => {
    if (b.dataset.filter === ui.filters.bucket) return;
    ui.filters.bucket = b.dataset.filter;
    haptic();
    refresh();
  });
  $('#reset-filters')?.addEventListener('click', () => {
    ui.filters = {
      q: '',
      bucket: 'all'
    };
    refresh();
  });
  $('#order-search')?.addEventListener('input', e => {
    ui.filters.q = e.target.value;
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => refresh(), 350);
  });
  $('#load-more')?.addEventListener('click', loadMore);
  const form = $('#new-order');
  if (form) {
    form.oninput = () => {
      ui.draft = Object.fromEntries(new FormData(form));
      setDirty(true);
    };
    form.onsubmit = e => {
      e.preventDefault();
      submit(form, async () => {
        const data = Object.fromEntries(new FormData(form));
        data.amount = rublesToKopecks(data.rubles);
        delete data.rubles;
        if (!/^\d+$/.test(data.customer_id) || !Number.isSafeInteger(Number(data
            .customer_id))) throw new Error('Проверьте Telegram ID покупателя.');
        data.customer_id = Number(data.customer_id);
        await api('/api/orders', data);
        ui.draft = {};
        setDirty(false);
        ui.section = 'orders';
        ui.filters = {
          q: '',
          bucket: 'all'
        };
        await refresh();
        toast('Заказ создан');
      });
    };
  }
}

function setDirty(value) {
  ui.dirty = value;
  try {
    if (value) tg?.enableClosingConfirmation();
    else tg?.disableClosingConfirmation();
  } catch {}
}
async function refresh(notify = false) {
  const number = ++requestNumber;
  controller?.abort();
  controller = new AbortController();
  busy = true;
  connection('Обновляем заказы…', 'updating');
  $('#refresh')?.classList.add('spinning');
  try {
    const result = await pages.refresh(ui.filters, controller.signal);
    if (!result || number !== requestNumber) return;
    state = result;
    connection();
    render();
    if (notify) toast('Данные обновлены');
  } catch (error) {
    if (number !== requestNumber || error.name === 'AbortError') return;
    if (error.status === 401) {
      connection();
      expire();
    } else if (state) {
      connection('Не удалось обновить. Показаны ранее загруженные данные.', 'warning');
      toast(error.message);
    } else {
      connection('Соединение с кабинетом недоступно.', 'warning');
      $('#app').innerHTML =
        `<section class="empty"><div class="empty-icon">${icon('refresh')}</div><h2>Не удалось загрузить кабинет</h2><p>${esc(error.message)}</p><button id="retry" class="button primary">Повторить</button></section>`;
      $('#retry').onclick = () => refresh();
    }
  } finally {
    if (number === requestNumber) {
      busy = false;
      $('#refresh')?.classList.remove('spinning');
      if ($('#refresh')) $('#refresh').disabled = false;
    }
  }
}
async function loadMore() {
  if (busy || !state.next_cursor) return;
  const button = $('#load-more');
  button.disabled = true;
  button.textContent = 'Загружаем…';
  try {
    const result = await pages.more(ui.filters);
    if (result) {
      state = result;
      render();
    }
  } catch (error) {
    if (error.status === 401) expire();
    else toast(error.message);
  } finally {
    if (button.isConnected) {
      button.disabled = false;
      button.textContent = 'Загрузить ещё заказы';
    }
  }
}
async function submit(form, action) {
  if (mutating) return;
  mutating = true;
  const buttons = [...form.querySelectorAll('button')];
  buttons.forEach(b => b.disabled = true);
  const button = form.querySelector('button[type="submit"]') || buttons.at(-1),
    label = button?.innerHTML;
  if (button) button.textContent = 'Сохраняем…';
  try {
    await action();
    haptic('success');
  } catch (error) {
    haptic('error');
    if (error.status === 401) expire();
    else {
      toast(error.message);
      const note = form.querySelector('.form-error') || document.createElement('p');
      note.className = 'form-error';
      note.setAttribute('role', 'alert');
      note.textContent = error.message;
      if (!note.isConnected) form.append(note);
      if (error.status === 409 && form.dataset.orderId && !form.querySelector('.reload-order')) {
        const reload = document.createElement('button');
        reload.type = 'button';
        reload.className = 'button full reload-order';
        reload.textContent = 'Загрузить актуальный заказ';
        reload.onclick = () => reloadDetail(form);
        form.append(reload);
      }
    }
  } finally {
    mutating = false;
    buttons.forEach(b => b.disabled = false);
    if (button && label) button.innerHTML = label;
  }
}
async function reloadDetail(form) {
  const draft = new FormData(form),
    id = Number(form.dataset.orderId),
    formId = form.id;
  try {
    const order = await api('/api/orders/' + id);
    $('#detail-dialog').close();
    await openDetail(id, order);
    document.querySelector('.manager-edit').open = true;
    const target = document.getElementById(formId);
    for (const field of target.elements) {
      if (!field.name) continue;
      if (field.type === 'checkbox') field.checked = draft.has(field.name);
      else if (draft.has(field.name)) field.value = draft.get(field.name);
    }
    target.scrollIntoView({
      block: 'center'
    });
    toast('Данные обновлены. Проверьте их и сохраните повторно.');
  } catch (error) {
    if (error.status === 401) expire();
    else toast(error.message);
  }
}
async function openDetail(id, provided = null) {
  const generation = ++detailNumber;
  let order = provided;
  if (!order) {
    try {
      order = await api('/api/orders/' + id);
    } catch (error) {
      if (error.status === 401) expire();
      else {
        if (error.status === 404) await refresh();
        toast(error.message);
      }
      return;
    }
  }
  if (!state || generation !== detailNumber || !order) return;
  haptic('impact');
  $('#detail-content').innerHTML = detailView(order, state.stages, manager(), state.integrations
    .payments !== 'not_connected');
  $('#detail-dialog').showModal();
  $('#detail-dialog').scrollTop = 0;
  bindDetail(order);
  syncBack();
}


function bindDetail(order) {
  $('#history-more')?.addEventListener('click', async () => {
    const generation = detailNumber,
      button = $('#history-more');
    button.disabled = true;
    try {
      const page = await api('/api/orders/' + order.id + '/history?before=' + order
        .history_cursor);
      if (generation !== detailNumber || !$('#detail-dialog').open) return;
      if (page.customer_generation !== order.customer_generation) {
        $('#detail-dialog').close();
        await openDetail(order.id);
        return;
      }
      order.events = [...page.events, ...order.events];
      order.history_cursor = page.next_cursor;
      $('#detail-content').innerHTML = detailView(order, state.stages, manager(), state
        .integrations.payments !== 'not_connected');
      bindDetail(order);
    } catch (error) {
      if (error.status === 401) expire();
      else {
        toast(error.message);
        if (error.status === 404) {
          $('#detail-dialog').close();
          await refresh();
        }
      }
    } finally {
      if (button.isConnected) button.disabled = false;
    }
  });
  $('#detail-content').querySelectorAll('.close-dialog').forEach(b => b.onclick = () => $(
    '#detail-dialog').close());
  $('#detail-content').querySelectorAll('[data-copy]').forEach(b => b.onclick = () => copy(b.dataset
    .copy));
  $('#copy-question').onclick = () => copy(
    `Здравствуйте! Подскажите, пожалуйста, информацию по заказу ${order.number}.`);
  $('#open-payment')?.addEventListener('click', () => external(order.payment_url));
  const save = async (path, data) => {
    await api(path, {
      order_id: order.id,
      version: order.version,
      ...data
    });
    $('#detail-dialog').close();
    await refresh();
    toast('Изменения сохранены');
  };
  const commercial = $('#commercial-form');
  if (commercial) commercial.onsubmit = e => {
    e.preventDefault();
    submit(commercial, () => {
      const data = new FormData(commercial);
      return save('/api/orders/commercial', {
        item: data.get('item'),
        amount: rublesToKopecks(data.get('rubles'))
      });
    });
  };
  $('#copy-payment')?.addEventListener('click', () => copy(order.payment_url));
  $('#revoke-payment')?.addEventListener('click', () => {
    const form = $('#payment-form');
    submit(form, () => save('/api/orders/payment', {
      url: ''
    }));
  });
  const status = $('#status-form');
  if (status) status.onsubmit = e => {
    e.preventDefault();
    submit(status, () => save('/api/orders/status', Object.fromEntries(new FormData(status))));
  };
  const details = $('#details-form');
  if (details) {
    details.elements.customer_id.addEventListener('input', () => {
      if (Number(details.elements.customer_id.value) !== order.customer_id) {
        details.elements.customer_name.value = '';
        details.elements.transfer_confirmed.checked = false;
      }
    });
  }
  if (details) details.onsubmit = e => {
    e.preventDefault();
    submit(details, () => {
      const form = new FormData(details),
        customer = Number(form.get('customer_id'));
      if (!Number.isSafeInteger(customer) || customer <= 0) throw new Error(
        'Проверьте Telegram ID');
      return save('/api/orders/details', {
        customer_id: customer,
        transfer_confirmed: form.has('transfer_confirmed'),
        customer_name: form.get('customer_name'),
        shipment: form.get('shipment'),
        eta: form.get('eta'),
        eta_confirmed: form.has('eta_confirmed')
      });
    });
  };
  const payment = $('#payment-form');
  if (payment) payment.onsubmit = e => {
    e.preventDefault();
    submit(payment, () => save('/api/orders/payment', {
      url: new FormData(payment).get('url')
    }));
  };
}

function syncBack() {
  const open = !!document.querySelector('dialog[open]');
  try {
    if (open || ui.section !== 'orders') tg?.BackButton?.show();
    else tg?.BackButton?.hide();
  } catch {}
}

function back() {
  detailNumber++;
  const dialogs = [...document.querySelectorAll('dialog[open]')];
  if (dialogs.length) dialogs.at(-1).close();
  else navigate('orders');
  syncBack();
}

function syncTheme() {
  const dark = tg?.colorScheme ? tg.colorScheme === 'dark' : matchMedia(
    '(prefers-color-scheme: dark)').matches;
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  const background = dark ? '#101623' : '#f4f6fa';
  document.querySelector('meta[name="theme-color"]').content = background;
  try {
    tg?.setHeaderColor(background);
    tg?.setBackgroundColor(background);
    if (tg?.isVersionAtLeast?.('7.10')) tg.setBottomBarColor(background);
  } catch {}
}

function syncInsets() {
  const inset = tg?.contentSafeAreaInset || {};
  for (const edge of ['top', 'bottom']) document.documentElement.style.setProperty('--tg-safe-' +
    edge, (inset[edge] || 0) + 'px');
}
$('.brand').onclick = e => {
  if (state) {
    e.preventDefault();
    navigate('orders');
  }
};
$('#support-top').innerHTML = icon('help');
$('#support-top').onclick = () => state ? navigate('help') : info('Открытие кабинета',
  'Откройте бота MAJLIS в Telegram и нажмите кнопку кабинета.');
document.querySelectorAll('#info-dialog .close-dialog').forEach(b => b.onclick = () => $(
  '#info-dialog').close());
document.querySelectorAll('dialog').forEach(d => d.addEventListener('close', syncBack));
window.addEventListener('offline', () => connection(
  'Нет соединения. Ранее загруженные данные могут быть устаревшими.', 'warning'));
window.addEventListener('online', () => refresh());
document.addEventListener('visibilitychange', () => {
  if (!document.hidden && state && !ui.dirty && !mutating && !document.querySelector(
      'dialog[open]')) refresh();
});
document.addEventListener('focusin', e => document.body.classList.toggle('keyboard-open', e.target
  .matches('input,textarea,select')));
document.addEventListener('focusout', () => setTimeout(() => document.body.classList.toggle(
  'keyboard-open', document.activeElement.matches('input,textarea,select')), 0));
try {
  tg?.ready();
  tg?.expand();
  tg?.BackButton?.onClick(back);
  tg?.onEvent?.('themeChanged', syncTheme);
  tg?.onEvent?.('contentSafeAreaChanged', syncInsets);
  tg?.onEvent?.('activated', () => {
    telegramActive = true;
    if (state && !ui.dirty && !mutating && !document.querySelector('dialog[open]')) refresh();
  });
  tg?.onEvent?.('deactivated', () => {
    telegramActive = false;
  });
} catch {}
syncTheme();
syncInsets();
matchMedia('(prefers-color-scheme: dark)').addEventListener?.('change', syncTheme);
setInterval(() => {
  if (state && telegramActive && !busy && !mutating && !ui.dirty && !document.hidden && ui
    .section === 'orders' && !document.querySelector('dialog[open]')) refresh();
}, 30000);
refresh();

async function exportData() {
  try {
    const data = await api('/api/me/export');
    const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], {
      type: 'application/json'
    }));
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = 'majlis-my-data.json';
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    toast('Сохраните файл. В нём ваши персональные данные.');
  } catch (error) {
    if (error.status === 401) expire();
    else toast(error.message);
  }
}
