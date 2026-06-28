"""
企业规范知识库 - 向量检索层

三种检索模式：
1. broad_retrieve:   第一层（上下文层），宽检索，注入背景知识到 Agent prompt
2. precise_retrieve: 第二层（证据层），精确检索，为已有 finding 补充规范依据
3. risk_scan:        第三层（发现层），主动扫描，供 PolicyReviewer 判断违规

底层使用 ChromaDB 作为向量存储，支持 embedding 语义检索。
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple

from tools.models import PolicyClause


class PolicyStore:
    """企业规范向量存储与检索"""

    def __init__(self, persist_dir: str = None):
        """
        初始化向量存储

        Args:
            persist_dir: ChromaDB 持久化目录，默认为 knowledge/.chromadb
        """
        persist_dir = persist_dir or os.path.join(
            os.path.dirname(__file__), ".chromadb"
        )

        try:
            import chromadb
            from chromadb.config import Settings
        except ImportError:
            raise ImportError(
                "需要安装 chromadb 以使用企业规范知识库。\n"
                "请运行: pip install chromadb>=0.4.0"
            )

        self._client = chromadb.Client(Settings(
            persist_directory=persist_dir,
            anonymized_telemetry=False,
        ))
        self._collection = self._client.get_or_create_collection(
            name="company_policies",
            metadata={"hnsw:space": "cosine"},
        )
        # 内存缓存，避免重复反序列化
        self._clause_cache: Dict[str, PolicyClause] = {}
        self._indexed_count = 0

    @property
    def indexed_count(self) -> int:
        return self._indexed_count

    def index_clauses(self, clauses: List[PolicyClause]) -> int:
        """
        批量索引规范条款到向量数据库

        Args:
            clauses: PolicyClause 列表

        Returns:
            成功索引的条款数
        """
        if not clauses:
            return 0

        # 构建索引文档：title + content + tags 拼接，提升检索语义覆盖
        documents = []
        ids = []
        metadatas = []

        for clause in clauses:
            doc_text = f"{clause.title}\n{clause.content}"
            if clause.tags:
                doc_text += f"\n关键词: {', '.join(clause.tags)}"

            documents.append(doc_text)
            ids.append(clause.id)
            metadatas.append({
                "domain": clause.domain,
                "severity": clause.severity,
                "tags": ",".join(clause.tags),
                "title": clause.title,
            })
            self._clause_cache[clause.id] = clause

        self._collection.upsert(
            ids=ids,
            documents=documents,
            metadatas=metadatas,
        )
        self._indexed_count = len(clauses)
        return len(clauses)

    def broad_retrieve(
        self, code_snippet: str, language: str = "", top_k: int = 8
    ) -> List[Tuple[PolicyClause, float]]:
        """
        第一层：宽检索 - 根据代码片段检索相关规范

        用于 review 开始前注入 <company_knowledge> 到 Agent prompt。
        检索范围广，top_k 较大，提供背景知识。

        Args:
            code_snippet: 待审查的代码片段
            language: 编程语言
            top_k: 返回条数

        Returns:
            (PolicyClause, relevance_score) 列表，按相关性降序
        """
        if not code_snippet.strip():
            return []

        # 截断过长代码，避免 embedding 超限
        query_text = code_snippet[:2000]
        if language:
            query_text = f"[{language}] {query_text}"

        results = self._collection.query(
            query_texts=[query_text],
            n_results=min(top_k, self._indexed_count or top_k),
        )
        return self._parse_results(results)

    def precise_retrieve(
        self,
        finding_text: str,
        domain: Optional[str] = None,
        top_k: int = 3,
    ) -> List[Tuple[PolicyClause, float]]:
        """
        第二层：精确检索 - 根据 finding 内容检索最相关的规范条款

        用于为已有发现补充权威依据（evidence chain）。
        检索范围窄，top_k 小，要求高相关性。

        Args:
            finding_text: finding 的 title + message + suggestion 拼接文本
            domain: 可选的 domain 过滤（如 "security"）
            top_k: 返回条数

        Returns:
            (PolicyClause, relevance_score) 列表
        """
        if not finding_text.strip():
            return []

        where_filter = {"domain": domain} if domain else None

        results = self._collection.query(
            query_texts=[finding_text[:1000]],
            n_results=min(top_k, self._indexed_count or top_k),
            where=where_filter,
        )
        return self._parse_results(results)

    def risk_scan(
        self,
        code_snippet: str,
        domains: Optional[List[str]] = None,
        top_k: int = 10,
    ) -> List[Tuple[PolicyClause, float]]:
        """
        第三层：主动扫描 - 检索所有可能被违反的规范

        供 PolicyReviewerAgent 使用：先检索相关规范，再让 LLM 判断是否违反。
        检索范围最广，top_k 最大。

        Args:
            code_snippet: 待审查代码
            domains: 可选的 domain 过滤列表
            top_k: 返回条数

        Returns:
            (PolicyClause, relevance_score) 列表
        """
        if not code_snippet.strip():
            return []

        where_filter = None
        if domains:
            where_filter = {"domain": {"$in": domains}}

        results = self._collection.query(
            query_texts=[code_snippet[:3000]],
            n_results=min(top_k, self._indexed_count or top_k),
            where=where_filter,
        )
        return self._parse_results(results)

    def get_clause_by_id(self, clause_id: str) -> Optional[PolicyClause]:
        """根据 ID 获取条款"""
        return self._clause_cache.get(clause_id)

    def _parse_results(self, results: dict) -> List[Tuple[PolicyClause, float]]:
        """将 ChromaDB 查询结果转为 (PolicyClause, score) 列表"""
        output: List[Tuple[PolicyClause, float]] = []

        if not results or not results.get("ids") or not results["ids"][0]:
            return output

        ids = results["ids"][0]
        distances = results["distances"][0] if results.get("distances") else [0.0] * len(ids)
        metadatas = results["metadatas"][0] if results.get("metadatas") else [{}] * len(ids)
        documents = results["documents"][0] if results.get("documents") else [""] * len(ids)

        for i, clause_id in enumerate(ids):
            # cosine distance → similarity score
            score = 1.0 - distances[i] if distances[i] <= 1.0 else 0.0

            # 优先从缓存获取完整 clause
            clause = self._clause_cache.get(clause_id)
            if not clause:
                # 从 metadata 重建（降级路径）
                meta = metadatas[i] if i < len(metadatas) else {}
                clause = PolicyClause(
                    id=clause_id,
                    domain=meta.get("domain", "unknown"),
                    title=meta.get("title", ""),
                    content=documents[i] if i < len(documents) else "",
                    severity=meta.get("severity", "medium"),
                    tags=meta.get("tags", "").split(",") if meta.get("tags") else [],
                )

            output.append((clause, score))

        return output
