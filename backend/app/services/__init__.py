"""services: 业务核心。

规划（按 PR 落地）：
- llm_provider.py   PR3  LLM 抽象层（DeepSeek + Azure 双后端）
- github_fetcher.py PR4  拉取 PR 元信息 / diff / 文件
- context_builder.py PR5 分层上下文（L1-L4）+ token 预算
- router.py         PR6  模型路由（chat -> reasoner -> 交叉验证）
- cache.py         PR8  diff hash 缓存与增量 review
"""
