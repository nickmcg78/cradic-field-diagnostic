import { useState, useEffect } from "react";
import Login from "./components/Login";
import Chat from "./components/Chat";

export default function App() {
  const [authToken, setAuthToken] = useState(null);

  useEffect(() => {
    const stored = sessionStorage.getItem("auth_token");
    if (stored) {
      setAuthToken(stored);
    }
  }, []);

  function handleLogin(token) {
    setAuthToken(token);
  }

  if (!authToken) {
    return <Login onLogin={handleLogin} />;
  }

  return <Chat authToken={authToken} />;
}
