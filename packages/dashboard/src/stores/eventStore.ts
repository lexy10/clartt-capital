import { create } from 'zustand';
import type { TradingEvent, EventFilters, PaginatedEventResponse } from '../types/event';
import { apiClient } from '../services/ApiClient';
import { wsManager } from '../services/WebSocketManager';

interface EventStoreState {
  events: TradingEvent[];
  filters: EventFilters;
  loading: boolean;
  totalCount: number;
  currentPage: number;
  totalPages: number;
  selectedEvent: TradingEvent | null;
  fetchEvents: () => Promise<void>;
  setFilters: (filters: Partial<EventFilters>) => void;
  prependEvent: (event: TradingEvent) => void;
  selectEvent: (event: TradingEvent | null) => void;
  searchByAggregateId: (id: string) => Promise<void>;
  searchByCorrelationId: (id: string) => Promise<void>;
}

const DEFAULT_FILTERS: EventFilters = {
  page: 1,
  page_size: 50,
  sort: 'desc',
};

let wsSubId: string | null = null;

export const useEventStore = create<EventStoreState>((set, get) => ({
  events: [],
  filters: { ...DEFAULT_FILTERS },
  loading: false,
  totalCount: 0,
  currentPage: 1,
  totalPages: 0,
  selectedEvent: null,

  fetchEvents: async () => {
    const { filters } = get();
    set({ loading: true });
    try {
      const res: PaginatedEventResponse = await apiClient.events.getEvents(filters);
      set({
        events: res.events,
        totalCount: res.total_count,
        currentPage: res.current_page,
        totalPages: res.total_pages,
        loading: false,
      });
    } catch (err) {
      console.error('Failed to fetch events:', err);
      set({ loading: false });
    }
  },

  setFilters: (partial: Partial<EventFilters>) => {
    const { filters, fetchEvents } = get();
    const newFilters = { ...filters, ...partial };
    set({ filters: newFilters });
    fetchEvents();
  },

  prependEvent: (event: TradingEvent) => {
    set((state) => ({
      events: [event, ...state.events],
      totalCount: state.totalCount + 1,
    }));
  },

  selectEvent: (event: TradingEvent | null) => {
    set({ selectedEvent: event });
  },

  searchByAggregateId: async (id: string) => {
    const { fetchEvents } = get();
    set({ filters: { ...DEFAULT_FILTERS, aggregate_id: id } });
    await fetchEvents();
  },

  searchByCorrelationId: async (id: string) => {
    const { fetchEvents } = get();
    set({ filters: { ...DEFAULT_FILTERS, correlation_id: id } });
    await fetchEvents();
  },
}));

/** Subscribe to real-time event updates via WebSocket */
export function subscribeEventUpdates(): void {
  if (wsSubId) return;
  wsManager.emit('subscribeEvents');
  wsSubId = wsManager.subscribe('events', (data: TradingEvent) => {
    useEventStore.getState().prependEvent(data);
  });
}

/** Unsubscribe from real-time event updates */
export function unsubscribeEventUpdates(): void {
  if (wsSubId) {
    wsManager.unsubscribe(wsSubId);
    wsSubId = null;
  }
}
