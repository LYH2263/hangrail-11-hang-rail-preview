import { useEffect, useState } from "react";
import { api } from "../api/client";

type O = { id: number; ticket_code: string; garment_name: string; length_cm: number; status: string; due_at: string; store_id: number };
type Rail = { id: number; store_id: number; label: string; length_cm: number };
type Preview = { order_id: number; rail_id: number; rail_label: string; length_cm: number; fits: boolean; start_cm: number | null; end_cm: number | null };

function HangCell({ order, rails, onHung }: { order: O; rails: Rail[]; onHung: () => void }) {
  const [railId, setRailId] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const myRails = rails.filter(r => r.store_id === order.store_id);

  async function loadPreview(id: number) {
    setBusy(true); setErr(""); setPreview(null);
    try {
      setPreview(await api<Preview>("/hang/preview", { method: "POST", body: JSON.stringify({ order_id: order.id, rail_id: id }) }));
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }

  function pick(v: string) {
    setRailId(v);
    setPreview(null); setErr("");
    if (v) void loadPreview(Number(v));
  }

  async function confirm() {
    setBusy(true); setErr("");
    try {
      const payload = railId ? { order_id: order.id, rail_id: Number(railId) } : { order_id: order.id };
      const o = await api<O>("/hang", { method: "POST", body: JSON.stringify(payload) });
      onHung();
      setRailId(""); setPreview(null);
      return o;
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }

  return (
    <div className="hangcell">
      <div className="hangcell-row">
        <select value={railId} onChange={e => pick(e.target.value)} disabled={busy}>
          <option value="">自动扫杆</option>
          {myRails.map(r => <option key={r.id} value={r.id}>{r.label}（{r.length_cm}cm）</option>)}
        </select>
        <button onClick={confirm} disabled={busy || (railId !== "" && !(preview?.fits))}>
          {railId ? "确认上杆" : "自动上杆"}
        </button>
      </div>
      {busy && <div className="hangcell-hint">试算中…</div>}
      {preview?.fits && (
        <div className="hangcell-hint ok">
          {preview.rail_label} 试算落点：{preview.start_cm}–{preview.end_cm} cm
        </div>
      )}
      {preview && !preview.fits && (
        <div className="hangcell-hint err">{preview.rail_label} 放不下（衣长 {order.length_cm}cm），请换杆</div>
      )}
      {err && <div className="hangcell-hint err">{err}</div>}
    </div>
  );
}

export default function OrdersPage() {
  const [rows, setRows] = useState<O[]>([]);
  const [rails, setRails] = useState<Rail[]>([]);
  const [msg, setMsg] = useState("");
  const reload = () => api<O[]>("/orders").then(setRows);
  useEffect(() => {
    reload();
    api<Rail[]>("/rails").then(setRails);
  }, []);
  return (<>
    <h2>工单</h2>
    {msg && <div className="ok">{msg}</div>}
    <table className="table"><thead><tr><th>票号</th><th>衣物</th><th>衣长</th><th>状态</th><th>到期</th><th>上杆</th></tr></thead>
    <tbody>{rows.map(o => <tr key={o.id}><td className="mono">{o.ticket_code}</td><td>{o.garment_name}</td><td className="mono">{o.length_cm}cm</td><td>{o.status}</td>
      <td className="mono">{new Date(o.due_at).toLocaleString()}</td>
      <td>{(o.status === "ready" || o.status === "overdue") &&
        <HangCell order={o} rails={rails} onHung={() => { setMsg(`${o.ticket_code} 已上杆`); reload(); }} />}
      </td>
    </tr>)}</tbody></table>
  </>);
}
