import { useState, useRef, useEffect } from "react";
import Message from "./Message";
import "./Chat.css";

const API_URL = import.meta.env.VITE_API_URL || "http://localhost:5000";

function buildContextPrefix({ machine, customer }) {
  if (customer) {
    return `[Context: Machine = ${machine}, Customer = ${customer} — reference this customer's service history where relevant but do not exclude other sources] `;
  }
  return `[Context: Machine = ${machine}] `;
}

export default function Chat({ authToken, sessionContext }) {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [notes, setNotes] = useState("");
  const [copyStatus, setCopyStatus] = useState("");
  const bottomRef = useRef(null);

  async function copyNotes() {
    if (!notes) return;
    try {
      await navigator.clipboard.writeText(notes);
      setCopyStatus("Copied");
    } catch {
      setCopyStatus("Copy failed");
    }
    setTimeout(() => setCopyStatus(""), 1500);
  }

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  async function sendMessage() {
    const question = input.trim();
    if (!question || loading) return;

    setInput("");
    setError("");

    const userMsg = { role: "user", text: question, timestamp: Date.now() };
    const contextPrefix = sessionContext ? buildContextPrefix(sessionContext) : "";
    const queryWithContext = contextPrefix + question;
    setMessages((prev) => [...prev, userMsg]);
    setLoading(true);

    try {
      const res = await fetch(`${API_URL}/query`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${authToken}`,
        },
        body: JSON.stringify({ question: queryWithContext }),
      });

      const data = await res.json();

      if (!res.ok) {
        throw new Error(data.error || `Server error ${res.status}`);
      }

      const aiMsg = { role: "ai", text: data.answer, timestamp: Date.now() };
      setMessages((prev) => [...prev, aiMsg]);
    } catch (err) {
      setError(err.message || "Failed to get a response. Check connection.");
    } finally {
      setLoading(false);
    }
  }

  function handleKeyDown(e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  }

  return (
    <div className="chat-container">
      <header className="chat-header">
        <span className="chat-header-icon">⚙</span>
        <div>
          <h1 className="chat-header-title">Select Equip</h1>
          <p className="chat-header-sub">powered by Cradic AI</p>
        </div>
      </header>

      <div className="chat-messages">
        {messages.length === 0 && (
          <div className="chat-empty">
            <p>Ask about a fault, alarm code, or machine issue.</p>
          </div>
        )}
        {messages.map((msg, i) => (
          <Message key={i} role={msg.role} text={msg.text} timestamp={msg.timestamp} />
        ))}
        {loading && (
          <div className="chat-loading">
            <span className="loading-dot" /><span className="loading-dot" /><span className="loading-dot" />
            <span className="loading-text">Checking knowledge base...</span>
          </div>
        )}
        {error && <div className="chat-error">{error}</div>}
        <div ref={bottomRef} />
      </div>

      <div className="chat-input-bar">
        <textarea
          className="chat-input"
          placeholder="Describe the fault or ask a question..."
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          rows={1}
          disabled={loading}
        />
        <button
          className="chat-send-btn"
          onClick={sendMessage}
          disabled={loading || !input.trim()}
        >
          Send
        </button>
      </div>

      <div className="session-notes">
        <div className="session-notes-header">
          <label htmlFor="session-notes-input" className="session-notes-label">
            Session Notes
          </label>
          <button
            className="session-notes-copy-btn"
            onClick={copyNotes}
            disabled={!notes}
          >
            {copyStatus || "Copy Notes"}
          </button>
        </div>
        <textarea
          id="session-notes-input"
          className="session-notes-input"
          placeholder="Jot down observations, parts, next steps..."
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          rows={3}
        />
      </div>
    </div>
  );
}
