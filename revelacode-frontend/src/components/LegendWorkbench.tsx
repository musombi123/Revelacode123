import React, { useCallback, useEffect, useMemo, useState } from 'react';

type Template = {
  id: string;
  domain: string;
  intent: string;
  inputs: Record<string, { type: string; required?: boolean; description?: string }>;
  triggers?: string[];
};

type Health = {
  service: string;
  status: string;
  runtime: string;
  model_in_runtime: boolean;
  teacher_configured: boolean;
  template_count: number;
  limits: string[];
};

type RunResult = {
  status: string;
  template_id?: string;
  result?: unknown;
  error?: string;
  certainty?: string;
  scope?: string;
};

const API_BASE = ((import.meta as any).env?.VITE_LEGEND_API_URL || '').replace(/\/$/, '');

function defaultValue(type: string): string {
  if (type === 'array') return '[]';
  if (type === 'object') return '{}';
  if (type === 'boolean') return 'false';
  if (type === 'number' || type === 'integer') return '0';
  return '';
}

function parseInput(type: string, raw: string): unknown {
  if (type === 'array' || type === 'object') return JSON.parse(raw);
  if (type === 'number' || type === 'integer') {
    const value = Number(raw);
    if (!Number.isFinite(value) || (type === 'integer' && !Number.isInteger(value))) throw new Error('Enter a valid ' + type + '.');
    return value;
  }
  if (type === 'boolean') {
    if (raw !== 'true' && raw !== 'false') throw new Error('Enter true or false.');
    return raw === 'true';
  }
  return raw;
}

export default function LegendWorkbench() {
  const [health, setHealth] = useState<Health | null>(null);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [values, setValues] = useState<Record<string, string>>({});
  const [result, setResult] = useState<RunResult | null>(null);
  const [connection, setConnection] = useState<'loading' | 'online' | 'offline'>('loading');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const selected = useMemo(() => templates.find(item => item.id === selectedId) || null, [templates, selectedId]);

  const connect = useCallback(async () => {
    setConnection('loading');
    setError('');
    try {
      const responses = await Promise.all([fetch(API_BASE + '/v1/health'), fetch(API_BASE + '/v1/templates')]);
      if (!responses[0].ok || !responses[1].ok) throw new Error('The Legend Child API returned an unsuccessful response.');
      const healthData = await responses[0].json() as Health;
      const templateData = await responses[1].json() as { items: Template[] };
      setHealth(healthData);
      setTemplates(templateData.items || []);
      setSelectedId(current => current || (templateData.items?.[0]?.id ?? ''));
      setConnection('online');
    } catch (cause) {
      setConnection('offline');
      setHealth(null);
      setTemplates([]);
      setError(cause instanceof Error ? cause.message : 'Could not reach the Legend Child API.');
    }
  }, []);

  useEffect(() => { void connect(); }, [connect]);

  useEffect(() => {
    if (!selected) {
      setValues({});
      setResult(null);
      return;
    }
    const next: Record<string, string> = {};
    Object.entries(selected.inputs).forEach(([name, schema]) => { next[name] = defaultValue(schema.type); });
    setValues(next);
    setResult(null);
  }, [selectedId, selected]);

  const execute = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!selected) return;
    setBusy(true);
    setResult(null);
    setError('');
    try {
      const inputs: Record<string, unknown> = {};
      Object.entries(selected.inputs).forEach(([name, schema]) => {
        const raw = values[name] ?? defaultValue(schema.type);
        if (schema.required === false && raw === '') return;
        inputs[name] = parseInput(schema.type, raw);
      });
      const response = await fetch(API_BASE + '/v1/compose', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ template_id: selected.id, inputs })
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || 'Composition request failed.');
      setResult(payload as RunResult);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not execute this template.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="legend-shell">
      <header className="legend-topbar">
        <a className="legend-brand" href="/" aria-label="RevelaCode Legend home">
          <span className="brand-mark">R</span>
          <span>REVELACODE <b>LEGEND</b></span>
        </a>
        <div className="topbar-right">
          <span className={'connection-dot ' + connection} />
          <span>{connection === 'online' ? 'CHILD CONNECTED' : connection === 'loading' ? 'CONNECTING' : 'ENGINE OFFLINE'}</span>
          <button className="refresh-button" onClick={() => void connect()} type="button">Reconnect</button>
        </div>
      </header>

      <section className="legend-hero">
        <div className="eyebrow"><span /> RESEARCH ENGINE / 0.1.0</div>
        <h1>Knowledge that<br /><em>can prove its path.</em></h1>
        <p>Compose deterministic procedures from a versioned T-Lang library. Unknown procedures stay unknown until a verifiable method exists.</p>
        <div className="hero-tags">
          <span>NO MODEL IN RUNTIME</span>
          <span>DETERMINISTIC COMPOSITION</span>
          <span>MACHINE-TESTED METHODS</span>
        </div>
      </section>

      <section className="status-grid" aria-label="Engine status">
        <div className="status-card"><span className="status-label">RUNTIME</span><strong>{health?.runtime || '—'}</strong><small>{health?.model_in_runtime === false ? 'Foundation model not loaded' : 'Awaiting engine'}</small></div>
        <div className="status-card"><span className="status-label">MASTER COPY</span><strong>{health ? health.template_count : '—'}</strong><small>Templates available to this child</small></div>
        <div className="status-card"><span className="status-label">TEACHER LINK</span><strong>{health ? (health.teacher_configured ? 'READY' : 'NOT SET') : '—'}</strong><small>Optional external proposal source</small></div>
        <div className="status-card"><span className="status-label">KNOWLEDGE POLICY</span><strong>UNKNOWN</strong><small>No verified method means no answer</small></div>
      </section>

      <section className="workbench">
        <div className="section-heading"><div><div className="eyebrow">01 / COMPOSER</div><h2>Run a verified method</h2></div><span className="section-index">TLANG / EXEC</span></div>

        {connection === 'offline' && (
          <div className="notice notice-warning">
            <strong>Child engine is not connected.</strong>
            <span>{error || 'Set VITE_LEGEND_API_URL to the deployed Child API, then reconnect.'}</span>
            <code>VITE_LEGEND_API_URL=https://your-child-api.example.com</code>
          </div>
        )}

        {connection === 'online' && (
          <div className="composer-layout">
            <aside className="template-list">
              <div className="list-heading"><span>MASTER LIBRARY</span><span>{templates.length} METHODS</span></div>
              {templates.map(item => (
                <button type="button" key={item.id} className={'template-item ' + (selectedId === item.id ? 'active' : '')} onClick={() => setSelectedId(item.id)}>
                  <span className="template-domain">{item.domain}</span>
                  <strong>{item.intent}</strong>
                  <small>{item.id}</small>
                </button>
              ))}
            </aside>
            <div className="composer-panel">
              {selected ? (
                <>
                  <div className="method-heading"><span className="method-domain">{selected.domain}</span><h3>{selected.intent}</h3><code>{selected.id}</code></div>
                  <form onSubmit={execute}>
                    <div className="form-heading">INPUT PARAMETERS</div>
                    {Object.entries(selected.inputs).map(([name, schema]) => (
                      <label className="field" key={name}>
                        <span>{name}<small>{schema.type}{schema.required === false ? ' · optional' : ' · required'}</small></span>
                        {schema.type === 'boolean' ? (
                          <select value={values[name] ?? 'false'} onChange={event => setValues(old => ({ ...old, [name]: event.target.value }))}>
                            <option value="false">false</option><option value="true">true</option>
                          </select>
                        ) : (
                          <textarea rows={schema.type === 'array' || schema.type === 'object' ? 3 : 2} value={values[name] ?? ''} onChange={event => setValues(old => ({ ...old, [name]: event.target.value }))} placeholder={schema.type === 'array' ? '[1, 2, 3]' : schema.type === 'object' ? '{"key":"value"}' : 'Enter ' + name} />
                        )}
                      </label>
                    ))}
                    <button className="run-button" disabled={busy} type="submit">{busy ? 'COMPOSING…' : 'EXECUTE METHOD'} <span>↗</span></button>
                  </form>
                  {error && <div className="notice notice-error">{error}</div>}
                  {result && (
                    <div className={'result-panel ' + result.status.toLowerCase()}>
                      <div className="result-heading"><span>EXECUTION RESULT</span><b>{result.status}</b></div>
                      <pre>{JSON.stringify(result.result ?? result.error ?? result, null, 2)}</pre>
                      <small>{result.certainty || 'not_established'}{result.scope ? ' · ' + result.scope : ''}</small>
                    </div>
                  )}
                </>
              ) : <div className="empty-state">Select a method from the library.</div>}
            </div>
          </div>
        )}
      </section>

      <section className="principles">
        <div className="section-heading"><div><div className="eyebrow">02 / SYSTEM CONTRACT</div><h2>What a PASS means</h2></div><span className="section-index">NO FICTION</span></div>
        <div className="principle-grid">
          <article><b>01</b><h3>Restricted logic</h3><p>Only declared T-Lang operators run. No arbitrary code, imports, or shell execution.</p></article>
          <article><b>02</b><h3>Finite evidence</h3><p>Independent test vectors must produce their declared outputs. They do not prove every possible case.</p></article>
          <article><b>03</b><h3>Unknown is valid</h3><p>The engine must refuse to invent a method when its library cannot support the operation.</p></article>
          <article><b>04</b><h3>Home decides</h3><p>Signed child proposals are checked again by GitHub Home before merge.</p></article>
        </div>
      </section>

      <footer className="legend-footer"><span>REVELACODE LEGEND / RESEARCH BUILD 0.1.0</span><span>COMPOSE · VERIFY · VERSION · REPEAT</span></footer>
    </main>
  );
}
