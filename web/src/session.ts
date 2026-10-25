const KEY = "rfp.workbench.qa";

export type HistoryItem = {
  question: string;
  status: string;
  answer: string;
  trace_id: string;
  at: string;
};

export function readHistory(): HistoryItem[] {
  try {
    const raw = sessionStorage.getItem(KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.slice(0, 12) : [];
  } catch {
    return [];
  }
}

export function remember(item: HistoryItem): HistoryItem[] {
  const next = [item, ...readHistory()].slice(0, 12);
  sessionStorage.setItem(KEY, JSON.stringify(next));
  return next;
}
