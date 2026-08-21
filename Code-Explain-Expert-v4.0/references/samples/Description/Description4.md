# Description

## 中文版

Code-Probe 是把 Python 代码库变成可对话知识库的全栈 RAG 系统，自然语言提问得到带文件路径和行号引用的答案，RAG 全流程前端可见。其含金量在 RAG 工程化：字符分块按行边界对齐防语义断裂、SSE 四阶段流式让检索拼词生成持久化全程可见、Embedding 客户端 key 回退 LLM 配置、BackgroundTasks 后台索引加状态轮询。JSON 文件持久化无独立数据库，适合作为 RAG 工程化样本或扩展为多语言代码问答平台

## English

Code-Probe turns Python codebases into a chat-ready knowledge base — answers cite paths and line numbers. Highlights: line-aligned chunking, SSE four-phase streaming exposing RAG, embedding fallback to LLM creds. Stack: FastAPI + ChromaDB cosine + OpenAI, React 18 + Vite 5 + TS + Tailwind v4, JSON persistence. RAG sample or multilingual code Q&A.
