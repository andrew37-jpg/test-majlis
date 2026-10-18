export function cabinetView(user) {
  return `<section class="cabinet-bar" aria-label="Кабинет"><div><span class="eyebrow">MAJLIS</span><strong>${user.role==='manager'?'Рабочий кабинет менеджера':'Личный кабинет покупателя'}</strong>${user.role==='manager'?`<small>${user.manager_all?'Все заказы — доступ назначен':'Только назначенные вам заказы'}</small>`:''}</div></section>`;
}
