# 舟序当前后端

从参考后端迁移到当前工程的独立 FastAPI 模块化后端，迁移起点见 source-baseline.json；没有复制参考工程的凭证、虚拟环境或用户运行数据。

- application/accounts、workspace：账户认证、对话和材料的权限与持久化。
- harness：可恢复运行、流式事件、停止/继续、检查点与上下文管理。
- application/maritime：八项面向用户的业务规范、案件/成员/事项/版本/审计，以及按明确输入调用的精确金额和真实引用校验。
- infrastructure/search/maritime_corpus.py：只读在线 BM25 与同源本地全文索引；内容寻址引用快照。
- interfaces/api/maritime.py：账户隔离的业务接口，技能和案件快照必须经服务端解析。

当前关闭原可选向量 RAG，避免把缺少编码器伪装成混合检索；海事检索对用户统一呈现资料结果，在线检索失败时由服务端使用同源本地全文索引。可选适配器及测试保留，启用前需另行验证环境与效果。

从工作区根运行 `uv sync --project backend --group dev --extra materials`，再运行 `backend/.venv/Scripts/python.exe -m uvicorn backend.interfaces.api.app:create_app --factory --host 127.0.0.1 --port 8025`。

完整启动、验证和专业边界见[根 README](../README.md)。专业语义正确性没有由结构检查或引用可读性自动认证。
