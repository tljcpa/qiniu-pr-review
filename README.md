# AI PR Review 助手

> 七牛云 × XEngineer 暑期实训营 · 题目三作品
>
> 指定一个 GitHub Pull Request，系统自动拉取代码变更并用大模型智能分析，输出**变更总结、风险代码识别、Review 建议**，并把 AI 的推理思维链展示出来，让评审不再是黑盒。

- 在线试用：**https://pr.qiniu.zdwktlj.top** （已上线，可直接体验）
- 演示视频：待录制（PR14 放到 README 首屏）
- 架构文档：[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- 决策与踩坑总账：[docs/复盘.md](docs/复盘.md)

---

## 核心特性

- **模型选择策略**：`deepseek-chat` 快扫标可疑 → `deepseek-reasoner` 深读并保留思维链（`reasoning_content`）→ 高风险项用 `GPT-4.1-mini` 交叉验证。直接回应题目"模型选择设计思路"。
- **分层上下文工程（L1-L4）**：按 diff 规模与 token 预算动态决定喂多少上下文，超预算时**主动声明"本段未完整 review"**而非强行截断输出错结论。
- **思维链可见化**：每条 finding 可展开查看 reasoner 的推理过程，可解释性拉满。
- **误报控制**：每条 finding 标 high/medium/low 置信度，低置信默认折叠；高风险项二次交叉验证。
- **增量评审 + 缓存**：同一 PR 多次 push 只 review 新增 diff（diff hash 缓存）。

## 快速开始

```bash
# 1. 配置环境变量
cp .env.example .env && vim .env        # 填入 DEEPSEEK_API_KEY 等

# 2. 安装依赖并启动后端
make install
make run                                 # 等价于 uvicorn app.main:app --port 8080

# 3. 冒烟测试
curl http://localhost:8080/api/health    # -> {"status":"ok",...}
```

Docker 方式：

```bash
docker compose up -d --build
```

## 系统架构

```mermaid
flowchart LR
    A[GitHub Fetcher] --> B[Context Builder<br/>分层 L1-L4]
    B --> C[LLM Router<br/>多模型路由]
    C --> D[Finding Aggregator<br/>置信度评分]
    D --> E[Review Report<br/>结构化输出]
    E --> F[Web UI<br/>思维链可展开]
    C -.-> G[(Cache<br/>diff hash)]
```

详见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## 设计文档

题目明确要求说明的三大设计点，各有一份专门文档：

- 模型选择：[docs/MODEL_SELECTION.md](docs/MODEL_SELECTION.md)
- 上下文获取：[docs/CONTEXT_ENGINEERING.md](docs/CONTEXT_ENGINEERING.md)
- 扩展方向：[docs/FUTURE_WORK.md](docs/FUTURE_WORK.md)
- 完整决策与踩坑总账：[docs/复盘.md](docs/复盘.md)

## 技术栈

| 层 | 选型 |
|---|---|
| 后端 | Python + FastAPI（async / Pydantic / 自动 OpenAPI） |
| LLM SDK | openai-python（DeepSeek 兼容 OpenAI 协议，一套通吃多 provider） |
| GitHub | PyGithub |
| 前端 | Vite + React + Tailwind + shadcn/ui（PR10） |
| 部署 | Docker Compose + Caddy 自动 HTTPS（PR11） |

明确**不使用** LangChain / LlamaIndex —— 核心链路自己实现，约几百行，更显工程功底，也便于精确控制上下文与 token 预算。

## 开发与提交规范

本项目全程走 **feature 分支 + PR** 工作流，每个 PR 只做一件事、含「标题 / 功能描述 / 实现思路 / 测试方式」四段说明，main 分支始终保持可运行。提交历史即开发过程的体现。

## AI 协作声明

本项目通过 **Claude Code** 辅助开发，并在产品内集成 DeepSeek / Azure OpenAI 模型。
所用 prompt、关键设计决策、AI 写错并被修正的记录，均沉淀在 [docs/复盘.md](docs/复盘.md)。
代码已经过人工审阅、测试与定型。

## 开源协议

[MIT](LICENSE)
