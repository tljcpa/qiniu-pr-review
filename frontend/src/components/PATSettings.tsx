// GitHub PAT 绑定面板：粘贴 fine-grained PAT，验证并加密存储
import { useState } from "react";

interface Props {
  hasPat: boolean;
  githubUsername: string | null;
  onBind: (pat: string) => Promise<string>;
  onUnbind: () => Promise<void>;
  onClose: () => void;
}

export function PATSettings({ hasPat, githubUsername, onBind, onUnbind, onClose }: Props) {
  const [pat, setPat] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const handleBind = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setSuccess(null);
    setLoading(true);
    try {
      const ghUser = await onBind(pat.trim());
      setSuccess(`已绑定 GitHub 账号 @${ghUser}`);
      setPat("");
    } catch (err) {
      setError(String(err instanceof Error ? err.message : err));
    } finally {
      setLoading(false);
    }
  };

  const handleUnbind = async () => {
    setError(null);
    setSuccess(null);
    setLoading(true);
    try {
      await onUnbind();
      setSuccess("PAT 已解绑");
    } catch (err) {
      setError(String(err instanceof Error ? err.message : err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="w-full max-w-md border border-line bg-panel shadow-xl">
        {/* 标题栏 */}
        <div className="flex items-center justify-between border-b border-line px-4 py-2 font-mono text-xs">
          <span className="text-amber">pr-review:~$ config github-pat</span>
          <button onClick={onClose} className="text-faint hover:text-fg">[x]</button>
        </div>

        <div className="p-4 space-y-4 font-mono text-xs">
          {/* 当前状态 */}
          <div className="border border-line bg-bg px-3 py-2">
            {hasPat ? (
              <span className="text-fg">
                已绑定：<span className="text-amber">@{githubUsername ?? "unknown"}</span>
                {" "}— AI 修复功能已就绪
              </span>
            ) : (
              <span className="text-muted">未绑定 PAT — 绑定后可使用 AI 修复功能</span>
            )}
          </div>

          {/* 说明 */}
          <div className="text-faint space-y-1">
            <div>需要 GitHub fine-grained PAT，格式：<span className="text-muted">github_pat_...</span></div>
            <div>所需权限：Contents (read+write)、Pull Requests (read+write)</div>
            <div>PAT 经 AES-256-GCM 加密存储，服务端不返回明文</div>
          </div>

          {/* 绑定表单 */}
          <form onSubmit={(e) => void handleBind(e)} className="space-y-2">
            <label className="block text-muted">粘贴 PAT：</label>
            <input
              value={pat}
              onChange={(e) => setPat(e.target.value)}
              placeholder="github_pat_..."
              minLength={20}
              className="w-full border border-line bg-bg px-2 py-1 font-mono text-fg placeholder:text-faint focus:border-amber focus:outline-none"
              type="password"
            />
            <div className="flex gap-2">
              <button
                type="submit"
                disabled={loading || pat.length < 20}
                className="flex-1 border border-amberdim bg-panel2 py-1 font-bold text-amber transition-colors hover:bg-amberdim hover:text-bg disabled:cursor-not-allowed disabled:opacity-40"
              >
                {loading ? "验证中…" : "↵ 绑定"}
              </button>
              {hasPat && (
                <button
                  type="button"
                  onClick={() => void handleUnbind()}
                  disabled={loading}
                  className="border border-line px-3 py-1 text-muted transition-colors hover:border-sev_high hover:text-sev_high disabled:opacity-40"
                >
                  解绑
                </button>
              )}
            </div>
          </form>

          {error && (
            <div className="border-l-2 border-sev_high pl-2 text-sev_high">! {error}</div>
          )}
          {success && (
            <div className="border-l-2 border-amber pl-2 text-amber">{success}</div>
          )}
        </div>
      </div>
    </div>
  );
}
