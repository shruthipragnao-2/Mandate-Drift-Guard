import { useState } from "react";
import { ApiError, createTransaction } from "../api";
import { DEMO_MANDATES } from "../demoMandates";
import type { TransactionCreateResponse } from "../types";

function nowLocalDatetimeValue(): string {
  const d = new Date();
  d.setSeconds(0, 0);
  d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
  return d.toISOString().slice(0, 16);
}

export function SimulateTransaction({ onOpenCase }: { onOpenCase: (caseId: string) => void }) {
  const [mandateId, setMandateId] = useState(DEMO_MANDATES[0]?.id ?? "");
  const [merchant, setMerchant] = useState("");
  const [category, setCategory] = useState("");
  const [amount, setAmount] = useState("");
  const [occurredAt, setOccurredAt] = useState(nowLocalDatetimeValue());

  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<TransactionCreateResponse | null>(null);

  const selectedMandate = DEMO_MANDATES.find((m) => m.id === mandateId);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    setResult(null);
    try {
      const res = await createTransaction({
        mandate_id: mandateId,
        merchant,
        category,
        amount: Number(amount),
        occurred_at: new Date(occurredAt).toISOString(),
        idempotency_key: crypto.randomUUID(),
      });
      setResult(res);
    } catch (err) {
      setError(err instanceof ApiError ? `${err.status}: ${JSON.stringify(err.detail)}` : String(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="page">
      <div className="page-header">
        <h1>Simulate Transaction</h1>
      </div>
      <p className="muted">
        Submits a real transaction to the running pipeline (deterministic signals, then --
        if a threshold crosses -- a real Anthropic API call). This is the live demo trigger,
        not a mock.
      </p>

      <form className="simulate-form" onSubmit={handleSubmit}>
        <label>
          Mandate
          <select value={mandateId} onChange={(e) => setMandateId(e.target.value)} required>
            {DEMO_MANDATES.map((m) => (
              <option key={m.id} value={m.id}>
                {m.purpose}
              </option>
            ))}
          </select>
        </label>
        {selectedMandate && (
          <p className="muted">Allowed categories: {selectedMandate.allowed_categories.join(", ")}</p>
        )}

        <label>
          Merchant
          <input type="text" value={merchant} onChange={(e) => setMerchant(e.target.value)} required />
        </label>

        <label>
          Category
          <input type="text" value={category} onChange={(e) => setCategory(e.target.value)} required />
        </label>

        <label>
          Amount
          <input
            type="number"
            min="0.01"
            step="0.01"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            required
          />
        </label>

        <label>
          Occurred at
          <input
            type="datetime-local"
            value={occurredAt}
            onChange={(e) => setOccurredAt(e.target.value)}
            required
          />
        </label>

        <button className="button-confirm" type="submit" disabled={submitting}>
          {submitting ? "Submitting..." : "Submit transaction"}
        </button>
      </form>

      {error && <div className="banner banner-error">{error}</div>}

      {result && (
        <div className={`banner ${result.state === "held" ? "banner-warning" : "banner-success"}`}>
          <p>
            Transaction <strong>{result.transaction_id}</strong> -- state:{" "}
            <strong>{result.state}</strong>
            {result.gate_decision && <> (gate: {result.gate_decision})</>}
          </p>
          {result.case_id ? (
            <button className="button-secondary" onClick={() => onOpenCase(result.case_id!)}>
              View case &rarr;
            </button>
          ) : (
            <p className="muted">No case opened -- the deterministic threshold was never crossed.</p>
          )}
        </div>
      )}
    </div>
  );
}
