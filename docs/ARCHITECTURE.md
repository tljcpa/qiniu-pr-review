# 系统架构

> 本文档将在 PR2 补全（含 mermaid 架构图、模块职责、数据流、上下文分层与模型路由细节）。
> 当前为占位，避免 README 链接失效。

骨架阶段已确定的高层结构：

```
GitHub Fetcher -> Context Builder -> LLM Router -> Finding Aggregator -> Review Report -> Web UI
                                          |
                                     Cache (diff hash)
```

详见 [复盘.md](复盘.md) 的决策日志。
