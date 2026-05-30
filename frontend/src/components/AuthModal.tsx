// 登录/注册模态框：深色终端风格，单一表单切换两种模式
import { useState } from "react";

interface Props {
  onClose: () => void;
  onRegister: (username: string, password: string) => Promise<void>;
  onLogin: (username: string, password: string) => Promise<void>;
}

export function AuthModal({ onClose, onRegister, onLogin }: Props) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      if (mode === "register") {
        await onRegister(username.trim(), password);
      } else {
        await onLogin(username.trim(), password);
      }
      onClose();
    } catch (err) {
      setError(String(err instanceof Error ? err.message : err));
    } finally {
      setLoading(false);
    }
  };

  return (
    // 半透明遮罩
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="w-full max-w-sm border border-line bg-panel shadow-xl">
        {/* 标题栏 */}
        <div className="flex items-center justify-between border-b border-line px-4 py-2 font-mono text-xs">
          <span className="text-amber">
            pr-review:~$ {mode === "login" ? "auth login" : "auth register"}
          </span>
          <button onClick={onClose} className="text-faint hover:text-fg">
            [x]
          </button>
        </div>

        {/* 模式切换 */}
        <div className="flex border-b border-line font-mono text-xs">
          <button
            className={`flex-1 py-1.5 transition-colors ${mode === "login" ? "bg-panel2 text-amber" : "text-muted hover:text-fg"}`}
            onClick={() => { setMode("login"); setError(null); }}
          >
            login
          </button>
          <button
            className={`flex-1 py-1.5 transition-colors ${mode === "register" ? "bg-panel2 text-amber" : "text-muted hover:text-fg"}`}
            onClick={() => { setMode("register"); setError(null); }}
          >
            register
          </button>
        </div>

        <form onSubmit={(e) => void submit(e)} className="p-4 space-y-3">
          <div className="font-mono text-xs">
            <label className="block text-muted mb-1">username</label>
            <input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="your-username"
              minLength={2}
              required
              autoFocus
              className="w-full border border-line bg-bg px-2 py-1 font-mono text-fg placeholder:text-faint focus:border-amber focus:outline-none"
            />
          </div>
          <div className="font-mono text-xs">
            <label className="block text-muted mb-1">password</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="min 6 characters"
              minLength={6}
              required
              className="w-full border border-line bg-bg px-2 py-1 font-mono text-fg placeholder:text-faint focus:border-amber focus:outline-none"
            />
          </div>

          {error && (
            <div className="border-l-2 border-sev_high pl-2 font-mono text-xs text-sev_high">
              ! {error}
            </div>
          )}

          <button
            type="submit"
            disabled={loading || !username.trim() || !password}
            className="w-full border border-amberdim bg-panel2 py-1.5 font-mono font-bold text-amber transition-colors hover:bg-amberdim hover:text-bg disabled:cursor-not-allowed disabled:opacity-40"
          >
            {loading ? "…" : mode === "login" ? "↵ login" : "↵ register"}
          </button>
        </form>
      </div>
    </div>
  );
}
