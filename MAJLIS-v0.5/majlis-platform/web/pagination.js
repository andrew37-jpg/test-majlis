import {
  searchParams
} from './ui.js';

const keyOf = filters => searchParams(filters);
const unique = rows => [...new Map(rows.map(row => [row.id, row])).values()];

/** Owns list generations; stale responses cannot replace a newer selection. */
export class OrderPages {
  constructor(fetchPage) {
    this.fetchPage = fetchPage;
    this.key = null;
    this.pages = 1;
    this.value = null;
    this.generation = 0;
    this.loadingMore = false;
  }

  clear() {
    this.generation++;
    this.value = null;
    this.pages = 1;
    this.key = null;
  }

  async refresh(filters, signal) {
    const key = keyOf(filters);
    const target = key === this.key ? this.pages : 1;
    const generation = ++this.generation;
    const selection = {
      ...filters
    };
    let result = await this.fetchPage(searchParams(selection), signal);
    let rows = result.orders;
    let pages = 1;
    while (pages < target && result.next_cursor) {
      const next = await this.fetchPage(searchParams(selection, result.next_cursor), signal);
      if (next.user.id !== result.user.id || next.user.role !== result.user.role) {
        throw new Error('Аккаунт изменился. Откройте кабинет заново.');
      }
      rows = unique([...rows, ...next.orders]);
      result = {
        ...result,
        next_cursor: next.next_cursor
      };
      pages++;
    }
    if (generation !== this.generation) return null;
    this.key = key;
    this.pages = pages;
    this.value = {
      ...result,
      orders: unique(rows)
    };
    return this.value;
  }

  async more(filters) {
    if (this.loadingMore || !this.value?.next_cursor || keyOf(filters) !== this.key) return null;
    this.loadingMore = true;
    const generation = this.generation;
    const cursor = this.value.next_cursor;
    try {
      const next = await this.fetchPage(searchParams(filters, cursor));
      if (generation !== this.generation || this.value?.next_cursor !== cursor) return null;
      if (next.user.id !== this.value.user.id || next.user.role !== this.value.user.role) {
        this.clear();
        throw new Error('Аккаунт изменился. Откройте кабинет заново.');
      }
      this.value = {
        ...this.value,
        orders: unique([...this.value.orders, ...next.orders]),
        next_cursor: next.next_cursor
      };
      this.pages++;
      return this.value;
    } finally {
      this.loadingMore = false;
    }
  }
}
