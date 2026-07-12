import { useState } from "react";
import { CUSTOMERS } from "../customers";
import "./ContextScreen.css";

const MACHINES = [
  "Trave 340",
  "Trave 350",
  "Trave 367",
  "Trave 590",
  "Trave 1000",
  "Trave 1200",
  "Trave 1400",
];

export default function ContextScreen({ onStart }) {
  const [machine, setMachine] = useState(null);
  const [customerInput, setCustomerInput] = useState("");

  function handleStart() {
    if (!machine) return;
    const customer = customerInput.trim() || null;
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
          <h2 className="ctx-label">
            Customer name <span className="ctx-optional">(optional)</span>
          </h2>
          <div className="ctx-customer-wrap">
            <input
              className="ctx-customer-input"
              type="text"
              list="customer-list"
              placeholder="Type or select a customer..."
              value={customerInput}
              onChange={(e) => setCustomerInput(e.target.value)}
              autoComplete="off"
            />
            <datalist id="customer-list">
              {CUSTOMERS.map((c) => (
                <option key={c} value={c} />
              ))}
            </datalist>
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
