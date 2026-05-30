/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    // 故意覆盖默认调色板里会被滥用的项，强制只用下面这套语义色（见复盘 D-22）
    extend: {
      colors: {
        // 暖色深底（刻意避开 GitHub/slate/zinc 的冷灰），像本地终端工具
        bg: "#17150f",
        panel: "#1e1b14",
        panel2: "#241f16",
        line: "#322c20", // 边框/分隔
        fg: "#e4ddcf", // 主文字
        muted: "#9b917c", // 次要
        faint: "#6c6453", // 最弱（行号/时间戳）
        // 单主色：琥珀/赭，像编辑器警告 / CI 工具的强调
        amber: "#d08b3a",
        amberdim: "#8a5e2a",
        // 单克制强调：低饱和青，仅用于链接/可点
        link: "#7fa6a0",
        // 严重度语义色（低饱和，不做装饰）
        sev_high: "#c4584a",
        sev_med: "#c0913f",
        sev_low: "#7d7565",
      },
      fontFamily: {
        // 代码/日志/findings 正文用等宽 JetBrains Mono；UI(标题/说明/按钮)用 IBM Plex Sans
        mono: ["'JetBrains Mono'", "'Fira Code'", "Consolas", "Menlo", "monospace"],
        sans: ["'IBM Plex Sans'", "system-ui", "-apple-system", "sans-serif"],
      },
      borderRadius: {
        // 克制圆角：全站统一 2-3px，无大圆角
        DEFAULT: "2px",
        sm: "2px",
        md: "3px",
      },
      keyframes: {
        blink: { "0%,100%": { opacity: "1" }, "50%": { opacity: "0.15" } },
      },
      animation: {
        blink: "blink 1.1s step-start infinite",
      },
    },
  },
  plugins: [],
};
