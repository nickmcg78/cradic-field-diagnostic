import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import "./Message.css";

export default function Message({ role, text, timestamp }) {
  const isUser = role === "user";
  const timeStr = new Date(timestamp).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });

  return (
    <div className={`message-wrapper ${isUser ? "message-user" : "message-ai"}`}>
      <div className={`message-bubble ${isUser ? "bubble-user" : "bubble-ai"}`}>
        {isUser ? (
          <p className="message-text-plain">{text}</p>
        ) : (
          <div className="message-markdown">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
          </div>
        )}
        <span className="message-timestamp">{timeStr}</span>
      </div>
    </div>
  );
}
