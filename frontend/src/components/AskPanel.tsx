import { useEffect, useRef, useState, type FormEvent } from 'react';
import { api, type AskTurn } from '../lib/api';

const SUGGESTIONS = [
  'Was the internet down this week, and whose fault was it?',
  'Why might video calls stutter in the evening?',
  'Which device uses the most bandwidth?',
  'Is anything on my network exposed to the internet?',
];

const TOOL_LABEL: Record<string, string> = {
  network_status: 'live status',
  active_incidents: 'open incidents',
  timeline_events: 'timeline',
  diagnosis: 'diagnosis',
  isp_report: 'ISP report',
  devices: 'devices',
  device_detail: 'device detail',
  bandwidth: 'bandwidth',
  health_score: 'health score',
  exposure: 'exposure scan',
  line_and_speed: 'Bell line',
  path_tests: 'traceroutes',
};

type Msg = AskTurn & { tools?: string[]; typing?: boolean };

const TYPE_MIN_CPS = 80; // characters a second for a short answer
const TYPE_MAX_S = 5; // a long answer speeds up so none takes longer than this

const reducedMotion = () => typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

/** An answer that types itself out. Tap it to show the rest at once; reduced motion shows it whole. */
export function TypedText({ text, onTick, onDone }: { text: string; onTick?: () => void; onDone?: () => void }) {
  const chars = useRef<string[]>([]);
  if (chars.current.join('') !== text) chars.current = Array.from(text);
  const total = chars.current.length;
  const [shown, setShown] = useState(() => (reducedMotion() ? total : 0));
  const done = shown >= total;
  const tick = useRef(onTick);
  const fin = useRef(onDone);
  tick.current = onTick;
  fin.current = onDone;

  useEffect(() => {
    if (shown >= total) return;
    const cps = Math.max(TYPE_MIN_CPS, total / TYPE_MAX_S);
    const start = Date.now() - (shown / cps) * 1000;
    const id = window.setInterval(() => {
      setShown(Math.min(total, Math.ceil(((Date.now() - start) / 1000) * cps)));
    }, 30);
    return () => window.clearInterval(id);
    // Restarting on `shown` would reset the pace, so only the text decides.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text]);
  useEffect(() => {
    tick.current?.();
    if (done) fin.current?.();
  }, [shown, done]);

  if (done) return <>{text}</>;
  return (
    <span onClick={() => setShown(total)} className="cursor-pointer">
      <span className="sr-only">{text}</span>
      <span aria-hidden="true">
        {chars.current.slice(0, shown).join('')}
        <span className="animate-pulse text-sky-400">▍</span>
      </span>
    </span>
  );
}

/** "Ask NetWatch": a chat panel, shown only once an Anthropic API key is saved. */
export function AskPanel() {
  const [available, setAvailable] = useState(false);
  const [open, setOpen] = useState(false);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const log = useRef<HTMLDivElement>(null);
  const tray = useRef<HTMLElement>(null);
  const launcher = useRef<HTMLButtonElement>(null);
  const field = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api.askStatus().then((s) => setAvailable(s.available), () => setAvailable(false));
  }, [open]);
  // The tray slides away when you tap anywhere outside it (the tap still reaches what you
  // tapped), or press Esc. The conversation stays, so reopening picks up where you left off.
  useEffect(() => {
    if (!open) return;
    const away = (e: PointerEvent) => {
      const t = e.target as Node | null;
      if (t && (tray.current?.contains(t) || launcher.current?.contains(t))) return;
      setOpen(false);
    };
    const esc = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      setOpen(false);
      launcher.current?.focus();
    };
    document.addEventListener('pointerdown', away);
    document.addEventListener('keydown', esc);
    return () => {
      document.removeEventListener('pointerdown', away);
      document.removeEventListener('keydown', esc);
    };
  }, [open]);
  useEffect(() => {
    if (open) field.current?.focus({ preventScroll: true });
  }, [open]);
  // Scroll only the conversation, never the page (scrollIntoView also moves the page on phones).
  const toBottom = () => {
    const el = log.current;
    if (el) el.scrollTop = el.scrollHeight;
  };
  useEffect(toBottom, [msgs, busy, error]);
  const finishTyping = (i: number) =>
    setMsgs((cur) => (cur[i]?.typing ? cur.map((m, j) => (j === i ? { ...m, typing: false } : m)) : cur));

  async function send(q: string) {
    const question = q.trim();
    if (!question || busy) return;
    // A new question finishes any answer still typing.
    const history: Msg[] = [...msgs.map((m) => (m.typing ? { ...m, typing: false } : m)), { role: 'user', content: question }];
    setMsgs(history);
    setText('');
    setBusy(true);
    setError(null);
    try {
      const r = await api.ask(history.map(({ role, content }) => ({ role, content })));
      const answer = typeof r?.answer === 'string' && r.answer ? r.answer : '(no answer)';
      setMsgs([...history, { role: 'assistant', content: answer, tools: Array.isArray(r?.tools) ? r.tools : [], typing: true }]);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setMsgs(msgs); // drop the unanswered question so it can be asked again
      setText(question);
    } finally {
      setBusy(false);
    }
  }
  const submit = (e: FormEvent) => {
    e.preventDefault();
    void send(text);
  };

  if (!available && !open) return null;
  return (
    <>
      <button
        ref={launcher}
        onClick={() => setOpen(true)}
        inert={open}
        aria-hidden={open}
        tabIndex={open ? -1 : 0}
        className={`fixed bottom-4 right-4 z-40 rounded-full bg-sky-600 px-4 py-2.5 text-sm font-medium text-white shadow-lg transition-[opacity,transform] duration-200 hover:bg-sky-500 motion-reduce:transition-none ${open ? 'pointer-events-none scale-90 opacity-0' : 'scale-100 opacity-100'}`}
      >
        Ask NetWatch
      </button>
      <aside
        ref={tray}
        aria-label="Ask NetWatch"
        inert={!open}
        aria-hidden={!open}
        className={`fixed inset-x-0 bottom-0 z-50 flex max-h-[85dvh] flex-col rounded-t-xl bg-slate-900 shadow-2xl ring-1 ring-slate-700 transition-[transform,visibility] duration-300 ease-out motion-reduce:transition-none sm:inset-x-auto sm:bottom-4 sm:right-4 sm:h-[36rem] sm:w-[26rem] sm:rounded-xl ${open ? 'visible translate-y-0' : 'invisible translate-y-[calc(100%+2rem)]'}`}
      >
        <div className="flex items-center justify-between border-b border-slate-800 px-4 py-2">
          <h2 className="text-sm font-semibold text-white">Ask NetWatch</h2>
          <div className="flex gap-2">
            {msgs.length > 0 && <button onClick={() => setMsgs([])} className="text-xs text-slate-400 hover:text-white">New chat</button>}
            <button onClick={() => { setOpen(false); launcher.current?.focus(); }} aria-label="Close" className="px-1 text-slate-400 hover:text-white">✕</button>
          </div>
        </div>
        <div ref={log} className="min-h-0 flex-1 space-y-3 overflow-y-auto overscroll-contain p-4 text-sm" aria-live="polite">
          {msgs.length === 0 && (
            <div className="space-y-2">
              <p className="text-slate-400">Ask about outages, slowness, devices or security. Answers come from NetWatch's own data.</p>
              {SUGGESTIONS.map((s) => (
                <button key={s} onClick={() => void send(s)} className="block w-full rounded-md bg-slate-800/70 px-3 py-2 text-left text-slate-200 hover:bg-slate-800">
                  {s}
                </button>
              ))}
            </div>
          )}
          {msgs.map((m, i) => (
            <div key={i} className={m.role === 'user' ? 'ml-8 rounded-lg bg-sky-900/50 px-3 py-2 text-slate-100' : 'mr-4 text-slate-200'}>
              <div className="whitespace-pre-wrap break-words">
                {m.typing ? <TypedText text={m.content} onTick={toBottom} onDone={() => finishTyping(i)} /> : m.content}
              </div>
              {!m.typing && m.tools && m.tools.length > 0 && (
                <div className="mt-1 text-[11px] text-slate-500">Looked at: {[...new Set(m.tools)].map((t) => TOOL_LABEL[t] ?? t).join(', ')}</div>
              )}
            </div>
          ))}
          {busy && <p className="text-slate-500">Looking into it…</p>}
          {error && <p role="alert" className="text-rose-400">{error}</p>}
        </div>
        <form onSubmit={submit} className="flex gap-2 border-t border-slate-800 p-3">
          <input ref={field} aria-label="Question" className="min-w-0 flex-1 rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 focus:border-sky-500 focus:outline-none"
            placeholder={available ? 'Ask about your network…' : 'Save an API key in Admin first'} value={text} maxLength={2000} disabled={!available}
            onChange={(e) => setText(e.target.value)} />
          <button type="submit" disabled={busy || !text.trim() || !available} className="rounded-md bg-sky-600 px-3 py-2 text-sm font-medium text-white hover:bg-sky-500 disabled:opacity-50">
            Ask
          </button>
        </form>
      </aside>
    </>
  );
}
