import { useState, useEffect } from "react";
import Login from "./components/Login";
import ContextScreen from "./components/ContextScreen";
import Chat from "./components/Chat";

export default function App() {
  const [authToken, setAuthToken] = useState(null);
  const [sessionContext, setSessionContext] = useState(null);

  useEffect(() => {
    const stored = sessionStorage.getItem("auth_token");
    if (stored) {
      setAuthToken(stored);
    }
  }, []);

  function handleLogin(token) {
    setAuthToken(token);
  }

  function handleStart({ machine, customer }) {
    setSessionContext({ machine, customer });
  }

  if (!authToken) {
    return <Login onLogin={handleLogin} />;
  }

  if (!sessionContext) {
    return <ContextScreen onStart={handleStart} />;
  }

  return <Chat authToken={authToken} sessionContext={sessionContext} />;
}
