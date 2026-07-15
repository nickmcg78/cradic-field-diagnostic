import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import "./Message.css";

// Manual filenames contain spaces (e.g. "User Trave 590.pdf"). In a markdown
// link [text](dest), a raw space truncates the destination — react-markdown
// would parse the href as just "/manuals/User", which 404s and triggers the
// SPA fallback to index.html, reloading the app and wiping the tech's session.
// Deterministically %20-encode spaces inside every markdown link destination
// so citations for all Trave models resolve correctly. Idempotent: existing
// %20 sequences are left untouched.
function encodeLinkSpaces(md) {
  if (!md) return md;
  return md.replace(
    /(\]\()([^)]+)(\))/g,
    (_match, open, dest, close) => open + dest.replace(/ /g, "%20") + close
  );
}

// Force every link inside answer text to open in a new tab, so clicking a
// reference can never navigate the SPA away and destroy the session.
const markdownComponents = {
  a: ({ node, ...props }) => (
    <a {...props} target="_blank" rel="noopener noreferrer" />
  ),
};

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
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={markdownComponents}
            >
              {encodeLinkSpaces(text)}
            </ReactMarkdown>
          </div>
        )}
        <span className="message-timestamp">{timeStr}</span>
      </div>
    </div>
  );
}
