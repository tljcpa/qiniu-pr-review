// 认证状态 hook：登录/注册/登出/PAT 绑定
import { useCallback, useEffect, useState } from "react";
import {
  type AuthUser,
  type TokenResponse,
  apiBindPAT,
  apiGetMe,
  apiLogin,
  apiRegister,
  apiUnbindPAT,
  clearToken,
  saveToken,
  loadToken,
} from "./auth";

export type AuthStatus = "unknown" | "logged_out" | "logged_in";

interface AuthState {
  status: AuthStatus;
  user: AuthUser | null;
}

export function useAuth() {
  const [auth, setAuth] = useState<AuthState>({ status: "unknown", user: null });

  // 页面加载时用 sessionStorage 里的 token 恢复状态
  useEffect(() => {
    const token = loadToken();
    if (!token) {
      setAuth({ status: "logged_out", user: null });
      return;
    }
    apiGetMe()
      .then((user) => setAuth({ status: "logged_in", user }))
      .catch(() => {
        clearToken();
        setAuth({ status: "logged_out", user: null });
      });
  }, []);

  const handleToken = useCallback((resp: TokenResponse) => {
    saveToken(resp.access_token);
    const user: AuthUser = {
      user_id: resp.user_id,
      username: resp.username,
      github_username: null,
      has_pat: false,
    };
    setAuth({ status: "logged_in", user });
  }, []);

  const register = useCallback(
    async (username: string, password: string): Promise<void> => {
      const resp = await apiRegister(username, password);
      handleToken(resp);
    },
    [handleToken]
  );

  const login = useCallback(
    async (username: string, password: string): Promise<void> => {
      const resp = await apiLogin(username, password);
      handleToken(resp);
    },
    [handleToken]
  );

  const logout = useCallback(() => {
    clearToken();
    setAuth({ status: "logged_out", user: null });
  }, []);

  const refreshUser = useCallback(async () => {
    try {
      const user = await apiGetMe();
      setAuth((prev) => ({ ...prev, status: "logged_in", user }));
    } catch {
      // token 已失效
      clearToken();
      setAuth({ status: "logged_out", user: null });
    }
  }, []);

  const bindPAT = useCallback(
    async (pat: string): Promise<string> => {
      const result = await apiBindPAT(pat);
      await refreshUser();
      return result.github_username;
    },
    [refreshUser]
  );

  const unbindPAT = useCallback(async (): Promise<void> => {
    await apiUnbindPAT();
    await refreshUser();
  }, [refreshUser]);

  return { auth, register, login, logout, bindPAT, unbindPAT, refreshUser };
}
