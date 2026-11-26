import {Component, computed, signal} from '@angular/core';
import {bootstrapApplication} from '@angular/platform-browser';
import {coverage, delta, monthLabel, percent} from './format';

interface Row {
  entity_id: string | number; name: string; month: string; status: 'scored' | 'unscored';
  probability: number | null; ratio: number; delta_3: number | null;
  label: number | null; outcome_ratio: number | null; reason: string | null;
  cohort: 'heldout' | 'latest_in_snapshot';
  explanation: {base_log_odds: number; contributions: Record<string, number>} | null;
}
interface Cohort {month: string; months: string[]; total: number; scored: number; offset: number; predictions: Row[]}
interface Metrics {average_precision: number | null; precision_at_10pct: number | null; brier: number | null}
interface Benchmark {selected_model: string | null; models?: Record<string, {test?: Metrics}>; splits?: unknown;
  provenance?: {kind: string; run_id: string}}
const terms: Record<string, string> = {ratio:'Razão atual', lag_1:'Razão há um mês', lag_3:'Razão há três meses',
  delta_3:'Variação em três meses', log_balance:'Log do saldo monitorado'};
interface History {entity_id: string | number; history: Row[]}
const api = '/api/v1';
const limit = 50;
async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(api + path, {signal, headers: {Accept: 'application/json'}});
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json() as Promise<T>;
}

@Component({selector:'radar-app', templateUrl:'./app.html'})
class App {
  readonly percent = percent;
  readonly delta = delta;
  readonly monthLabel = monthLabel;
  readonly cohort = signal<Cohort | null>(null);
  readonly months = signal<string[]>([]);
  readonly month = signal('');
  readonly loading = signal(false);
  readonly error = signal(false);
  readonly benchmark = signal<Benchmark | null>(null);
  readonly benchmarkState = signal<'loading' | 'ready' | 'error'>('loading');
  readonly selected = signal<Row | null>(null);
  readonly history = signal<Row[]>([]);
  readonly historyState = signal<'loading' | 'ready' | 'error'>('loading');
  readonly rows = computed(() => this.cohort()?.predictions ?? []);
  readonly scored = computed(() => this.rows().filter(row => row.status === 'scored').length);
  readonly cohortCoverage = computed(() => {
    const data = this.cohort();
    return data ? coverage(this.rows().length, this.scored(), data.total, data.scored) : null;
  });
  readonly modelRows = computed(() => Object.entries(this.benchmark()?.models ?? {}).filter(([, value]) => value.test));
  readonly factors = computed(() => Object.entries(this.selected()?.explanation?.contributions ?? {})
    .sort((a,b) => Math.abs(b[1]) - Math.abs(a[1])).map(([name,value]) => ({name: terms[name] ?? name, value})));
  readonly pageSize = limit;
  private cohortRequest?: AbortController;
  private historyRequest?: AbortController;
  private opener?: HTMLElement;

  constructor() { void this.load(); void this.loadBenchmark(); }

  async load(month = this.month(), offset = 0) {
    this.cohortRequest?.abort();
    const request = this.cohortRequest = new AbortController();
    this.month.set(month);
    this.loading.set(true);
    this.error.set(false);
    this.cohort.set(null);
    const query = new URLSearchParams({limit: String(limit), offset: String(offset)});
    if (month) query.set('month', month);
    try {
      const data = await get<Cohort>(`/predictions?${query}`, request.signal);
      if (request.signal.aborted) return;
      this.months.set(data.months);
      this.month.set(data.month);
      this.cohort.set(data);
    } catch {
      if (!request.signal.aborted) this.error.set(true);
    } finally {
      if (!request.signal.aborted) this.loading.set(false);
    }
  }

  async loadBenchmark() {
    this.benchmarkState.set('loading');
    try {
      this.benchmark.set(await get<Benchmark>('/benchmark'));
      this.benchmarkState.set('ready');
    } catch { this.benchmarkState.set('error'); }
  }

  open(row: Row, dialog: HTMLDialogElement, event: MouseEvent) {
    this.opener = event.currentTarget as HTMLElement;
    this.selected.set(row);
    void this.loadHistory(row.entity_id);
    dialog.showModal();
  }

  close() {
    if (!this.selected()) return;
    this.historyRequest?.abort();
    this.selected.set(null);
    this.opener?.focus();
  }

  async loadHistory(id: Row['entity_id']) {
    this.historyRequest?.abort();
    const request = this.historyRequest = new AbortController();
    this.historyState.set('loading');
    this.history.set([]);
    try {
      const result = await get<History>(`/entities/${encodeURIComponent(id)}`, request.signal);
      if (request.signal.aborted) return;
      this.history.set([...result.history].sort((a,b) => b.month.localeCompare(a.month)));
      this.historyState.set('ready');
    } catch { if (!request.signal.aborted) this.historyState.set('error'); }
  }

  reason(reason: string | null): string {
    if (reason === 'missing_or_nonfinite_features') return 'Histórico insuficiente ou inválido';
    if (reason === 'benchmark_unavailable') return 'Modelo indisponível para este recorte';
    return 'Motivo não informado';
  }
  signed(value: number): string {
    return new Intl.NumberFormat('pt-BR', {maximumFractionDigits: 3, signDisplay: 'always'}).format(value);
  }
  evidence(value: unknown): string { return JSON.stringify(value, null, 2); }
}
bootstrapApplication(App).catch(console.error);
