import { useState } from "react";
import "./Login.css";

export default function Login({ onLogin }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  function handleSubmit(e) {
    e.preventDefault();
    const correct = import.meta.env.VITE_APP_PASSWORD;
    if (password === correct) {
      sessionStorage.setItem("auth_token", password);
      onLogin(password);
    } else {
      setError("Incorrect password");
      setPassword("");
    }
  }

  return (
    <div className="login-container">
      <div className="login-card">
        <div className="login-logo">
          <span className="login-logo-icon">⚙</span>
        </div>
        <h1 className="login-title">Cradic AI</h1>
        <h2 className="login-subtitle">Field Diagnostic Tool</h2>
        <p className="login-brand">Select Equip</p>
        <form onSubmit={handleSubmit} className="login-form">
          <input
            type="password"
            className="login-input"
            placeholder="Enter access password"
            value={password}
            onChange={(e) => {
              setPassword(e.target.value);
              setError("");
            }}
            autoFocus
          />
          {error && <p className="login-error">{error}</p>}
          <button type="submit" className="login-button">
            Enter
          </button>
        </form>
      </div>
    </div>
  );
}
