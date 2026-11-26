const percentage = new Intl.NumberFormat('pt-BR', {style:'percent', maximumFractionDigits:2});
const decimal = new Intl.NumberFormat('pt-BR', {maximumFractionDigits:2});
const numeric = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
export const percent = (value: unknown) => numeric(value) ? percentage.format(value) : '—';
export const delta = (value: unknown) => numeric(value) ? `${value > 0 ? '+' : value < 0 ? '−' : ''}${decimal.format(Math.abs(value) * 100)} p.p.` : '—';
export const monthLabel = (value: string) => /^\d{4}-(0[1-9]|1[0-2])$/.test(value) ? `${value.slice(5)}/${value.slice(0,4)}` : value;
export const coverage = (loaded: number, scored: number, total: number, reported?: number) => {
  if (total <= 0) return null;
  if (typeof reported === 'number' && Number.isInteger(reported) && reported >= 0 && reported <= total) return reported / total;
  return loaded === total ? scored / total : null;
};
