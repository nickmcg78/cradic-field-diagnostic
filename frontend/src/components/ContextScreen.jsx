import { useState } from "react";
import "./ContextScreen.css";

const CUSTOMERS = [
  "Tassal DeCosti",
  "Ausfresh",
  "Lite n Easy",
  "Bindaree Food Group",
  "Baiada Hanwood",
  "Baiada Beresfield",
  "Hilton Foods",
  "Coles",
  "RROA",
  "MQF",
  "Huon Tasmania",
];
const MACHINES = ["Trave 340", "Trave 590"];

function fuzzyMatch(input) {
  if (!input.trim()) return null;
  const words = input.toLowerCase().split(/\s+/);
  for (const customer of CUSTOMERS) {
    const customerWords = customer.toLowerCase().split(/\s+/);
    if (words.some((w) => customerWords.some((cw) => cw.includes(w)))) {
      return customer;
    }
  }
  return null;
}

export default function ContextScreen({ onStart }) {
  const [machine, setMachine] = useState(null);
  const [customerInput, setCustomerInput] = useState("");
  const [confirmedCustomer, setConfirmedCustomer] = useState(null);

  const suggestion = confirmedCustomer ? null : fuzzyMatch(customerInput);

  function handleCustomerChange(e) {
    setCustomerInput(e.target.value);
    setConfirmedCustomer(null);
  }

  function confirmSuggestion() {
    setConfirmedCustomer(suggestion);
    setCustomerInput(suggestion);
  }

  function handleStart() {
    if (!machine) return;
    const customer = confirmedCustomer || (customerInput.trim() || null);
    onStart({ machine, customer });
  }

  return (
    <div className="ctx-container">
      <header className="ctx-header">
        <span className="ctx-header-icon">⚙</span>
        <div>
          <h1 className="ctx-header-title">Select Equip</h1>
          <p className="ctx-header-sub">powered by Cradic AI</p>
        </div>
      </header>

      <div className="ctx-body">
        <section className="ctx-section">
          <h2 className="ctx-label">Machine model</h2>
          <div className="ctx-machine-btns">
            {MACHINES.map((m) => (
              <button
                key={m}
                className={`ctx-machine-btn${machine === m ? " selected" : ""}`}
                onClick={() => setMachine(m)}
              >
                {m}
              </button>
            ))}
          </div>
        </section>

        <section className="ctx-section">
          <h2 className="ctx-label">Customer name <span className="ctx-optional">(optional)</span></h2>
          <div className="ctx-customer-wrap">
            <input
              className="ctx-customer-input"
              type="text"
              placeholder="Type customer name..."
              value={customerInput}
              onChange={handleCustomerChange}
            />
            {suggestion && (
              <button className="ctx-suggestion" onClick={confirmSuggestion}>
                Did you mean: <strong>{suggestion}</strong>?
              </button>
            )}
          </div>
        </section>

        <button
          className="ctx-start-btn"
          onClick={handleStart}
          disabled={!machine}
        >
          Start Session
        </button>
      </div>
    </div>
  );
}
