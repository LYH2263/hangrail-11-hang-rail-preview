import { useEffect, useState } from "react";
import { api } from "../api/client";
type O = { id: number; store_id: number; ticket_code: string; garment_name: string; length_cm: number; status: string; due_at: string };
type R = { id: number; store_id: number; label: string; length_cm: number };
type Preview = { rail_id: number; rail_label: string; fits: boolean; start_cm: number | null; end_cm: number | null };

function HangCell({ order, rails, onDone }: { order: O; rails: R[]; onDone: () => void }) {
  const [railId, setRailId] = useState<number | "">("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  async function choose(id: number | "") {
    setRailId(id);
    setPreview(null);
    setErr("");
    if (id === "") return; // 不指定：保持自动扫杆，无需试算
    setBusy(true);
    try {
      const p = await api<Preview>("/hang/preview", { method: "POST", body: JSON.stringify({ order_id: order.id, rail_id: id }) });
      setPreview(p);
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }

  async function confirm() {
    // 指定杆必须试算可放才允许确认；自动扫杆直接交给后端
    if (railId !== "" && (!preview || !preview.fits)) return;
    setBusy(true);
    setErr("");
    try {
      await api<O>("/hang", {
        method: "POST",
        body: JSON.stringify(railId === "" ? { order_id: order.id } : { order_id: order.id, rail_id: railId }),
      });
      setRailId(""); setPreview(null);
      onDone();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setPreview(null);
    } finally { setBusy(false); }
  }

  const storeRails = rails.filter(r => r.store_id === order.store_id);
  return (
    <div className="hang-cell">
      <div className="hang-controls">
        <select value={railId} onChange={e => choose(e.target.value === "" ? "" : Number(e.target.value))} disabled={busy}>
          <option value="">自动扫杆</option>
          {storeRails.map(r => <option key={r.id} value={r.id}>{r.label}（{r.length_cm}cm）</option>)}
        </select>
        <button onClick={confirm} disabled={busy || (railId !== "" && (!preview || !preview.fits))}>
          {busy ? "…" : "确认上杆"}
        </button>
      </div>
      {preview && (preview.fits
        ? <div className="ok hang-preview">试算落点：{preview.rail_label} {preview.start_cm}–{preview.end_cm} cm</div>
        : <div className="err hang-preview">{preview.rail_label} 空间不足，无法上杆（需 {order.length_cm}cm）</div>)}
      {err && <div className="err hang-preview">{err}</div>}
    </div>
  );
}

export default function OrdersPage() {
  const [rows, setRows] = useState<O[]>([]);
  const [rails, setRails] = useState<R[]>([]);
  const [msg, setMsg] = useState("");
  const reload = () => api<O[]>("/orders").then(setRows);
  useEffect(() => {
    reload();
    api<R[]>("/rails").then(setRails);
  }, []);
  return (<>
    <h2>工单</h2>
    {msg && <div className="ok">{msg}</div>}
    <table className="table"><thead><tr><th>票号</th><th>衣物</th><th>衣长</th><th>状态</th><th>到期</th><th>上杆</th></tr></thead>
    <tbody>{rows.map(o => <tr key={o.id}><td className="mono">{o.ticket_code}</td><td>{o.garment_name}</td><td className="mono">{o.length_cm}cm</td><td>{o.status}</td>
      <td className="mono">{new Date(o.due_at).toLocaleString()}</td>
      <td>{(o.status === "ready" || o.status === "overdue")
        ? <HangCell order={o} rails={rails} onDone={() => { setMsg(`${o.ticket_code} 已上杆`); reload(); }} />
        : null}</td>
    </tr>)}</tbody></table>
  </>);
}
