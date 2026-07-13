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

export default function Chat({ authToken, sessionContext, onEndSession }) {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [notes, setNotes] = useState("");
  const [copyStatus, setCopyStatus] = useState("");
  const [techName, setTechName] = useState(() => localStorage.getItem("tech_name") || "");
  const [sendStatus, setSendStatus] = useState("");
  const [sending, setSending] = useState(false);
  const bottomRef = useRef(null);

  useEffect(() => {
    localStorage.setItem("tech_name", techName);
  }, [techName]);

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

  async function sendNote() {
    if (!notes.trim() || sending) return;
    if (!techName.trim()) {
      setSendStatus("Enter your name first");
      return;
    }
    setSending(true);
    setSendStatus("Sending...");
    try {
      const res = await fetch(`${API_URL}/notes`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${authToken}`,
        },
        body: JSON.stringify({
          tech_name: techName.trim(),
          customer: sessionContext?.customer || null,
          machine: sessionContext?.machine || null,
          note_text: notes,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        // Not stored (e.g. 503 storage not configured) — keep the note.
        setSendStatus(data.error || "Failed — copy your notes before leaving this page");
      } else if (data.emailed) {
        setSendStatus("Sent");
        setNotes(""); // confirmed success — safe to clear
      } else {
        setSendStatus("Saved (email pending setup)");
        setNotes(""); // stored in DB — safe to clear
      }
    } catch {
      setSendStatus("Failed — copy your notes before leaving this page");
    } finally {
      setSending(false);
    }
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
        body: JSON.stringify({
          question: queryWithContext,
          machine: sessionContext?.machine || null,
        }),
      });

      if (!res.ok) {
        let msg = `Server error ${res.status}`;
        try {
          const errData = await res.json();
          msg = errData.error || msg;
        } catch {
          /* non-JSON error body */
        }
        throw new Error(msg);
      }

      const contentType = res.headers.get("content-type") || "";

      if (contentType.includes("application/json") || !res.body) {
        // Non-streaming path (STREAMING=0, or no streaming support).
        const data = await res.json();
        setMessages((prev) => [...prev, { role: "ai", text: data.answer, timestamp: Date.now() }]);
      } else {
        // Streaming path — render the accumulating buffer as tokens arrive.
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        const ts = Date.now();
        let acc = "";
        let started = false;

        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          const chunk = decoder.decode(value, { stream: true });
          if (!chunk) continue;
          acc += chunk;
          if (!started) {
            started = true;
            setLoading(false); // first token in — drop the "checking..." indicator
            setMessages((prev) => [...prev, { role: "ai", text: acc, timestamp: ts }]);
          } else {
            setMessages((prev) => {
              const copy = prev.slice();
              copy[copy.length - 1] = { ...copy[copy.length - 1], text: acc };
              return copy;
            });
          }
        }
        acc += decoder.decode(); // flush any trailing bytes
        if (started) {
          setMessages((prev) => {
            const copy = prev.slice();
            copy[copy.length - 1] = { ...copy[copy.length - 1], text: acc };
            return copy;
          });
        } else {
          // Stream produced nothing — still show a (possibly empty) message.
          setMessages((prev) => [...prev, { role: "ai", text: acc, timestamp: ts }]);
        }
      }
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
          <p className="chat-header-sub">
            {sessionContext?.machine
              ? `${sessionContext.machine}${sessionContext.customer ? " · " + sessionContext.customer : ""}`
              : "powered by Cradic AI"}
          </p>
        </div>
        {onEndSession && (
          <button
            type="button"
            className="chat-newsession-btn"
            onClick={onEndSession}
            title="Start a new query on a different machine"
          >
            New machine
          </button>
        )}
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
          <div className="session-notes-actions">
            <button
              className="session-notes-copy-btn"
              onClick={copyNotes}
              disabled={!notes}
            >
              {copyStatus || "Copy Notes"}
            </button>
            <button
              className="session-notes-send-btn"
              onClick={sendNote}
              disabled={!notes.trim() || sending}
              title="Store the note and email it to the technical manager"
            >
              {sending ? "Sending..." : "Send to manager"}
            </button>
          </div>
        </div>
        <input
          id="session-notes-name"
          className="session-notes-name"
          type="text"
          placeholder="Your name (for the manager)"
          value={techName}
          onChange={(e) => setTechName(e.target.value)}
          autoComplete="name"
        />
        <textarea
          id="session-notes-input"
          className="session-notes-input"
          placeholder="Jot down observations, parts, next steps..."
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          rows={3}
        />
        {sendStatus && <div className="session-notes-status">{sendStatus}</div>}
      </div>
    </div>
  );
}
