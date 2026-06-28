"""
EvidenceStore - 证据链管理（第二层 RAG）

职责：
- 为每个 candidate finding 检索相关企业规范
- 去重：同一条款不重复挂载到同一 finding
- 输出结构化的 evidence chain，支持可追溯性

使用方式：
    evidence_store = EvidenceStore(policy_store)
    evidences = evidence_store.attach_evidence(
        finding_key="bug:42:abc123",
        finding_text="SQL 拼接导致注入风险",
        source="security",
    )
"""
from __future__ import annotations

from typing import Dict, List, Optional

from tools.models import EvidenceItem, PolicyClause
from .store import PolicyStore


# Agent source → 优先检索的 policy domain 映射
_SOURCE_DOMAIN_MAP: Dict[str, Optional[str]] = {
    "security": "security",
    "bug": None,           # bug 可能关联任何 domain
    "perf": None,
    "quality": None,
    "practice": "testing",
    "policy": None,        # policy_reviewer 自身的发现不再二次检索
}

# 相关性阈值：低于此分数的检索结果不挂载
_RELEVANCE_THRESHOLD = 0.30


class EvidenceStore:
    """为审查发现构建证据链"""

    def __init__(self, policy_store: PolicyStore):
        self._policy_store = policy_store
        # finding_key → 已挂载的证据列表
        self._attached: Dict[str, List[EvidenceItem]] = {}
        # 全局去重：记录已挂载的 (finding_key, clause_id) 对
        self._seen_pairs: set = set()

    def attach_evidence(
        self,
        finding_key: str,
        finding_text: str,
        source: str,
    ) -> List[EvidenceItem]:
        """
        第二层核心方法：为单个 finding 检索并挂载证据

        Args:
            finding_key: finding 唯一标识（如 "bug:line_42:hash"）
            finding_text: finding 的 title + message + suggestion 拼接
            source: agent 来源（"bug", "security", "quality", "perf", "practice"）

        Returns:
            本次挂载的 EvidenceItem 列表
        """
        # 已处理过的 finding 直接返回缓存
        if finding_key in self._attached:
            return self._attached[finding_key]

        # policy_reviewer 自身的发现已经带了 evidence，跳过
        if source == "policy":
            self._attached[finding_key] = []
            return []

        # 根据 source 确定优先检索的 domain
        domain = _SOURCE_DOMAIN_MAP.get(source)

        # 精确检索
        results = self._policy_store.precise_retrieve(
            finding_text, domain=domain, top_k=3
        )

        evidences: List[EvidenceItem] = []
        for clause, score in results:
            # 过滤低相关性结果
            if score < _RELEVANCE_THRESHOLD:
                continue

            # 去重：同一 finding 不重复挂载同一条款
            pair_key = (finding_key, clause.id)
            if pair_key in self._seen_pairs:
                continue
            self._seen_pairs.add(pair_key)

            evidences.append(EvidenceItem(
                clause=clause,
                relevance_score=round(score, 4),
                match_reason=(
                    f"[{source}] finding 与企业规范 [{clause.id}] {clause.title} "
                    f"语义匹配 (score={score:.2f})"
                ),
            ))

        self._attached[finding_key] = evidences
        return evidences

    def get_evidence_for_finding(self, finding_key: str) -> List[EvidenceItem]:
        """获取指定 finding 的证据链"""
        return self._attached.get(finding_key, [])

    def get_all_evidence(self) -> List[EvidenceItem]:
        """获取所有已挂载的证据（按 clause_id 去重）"""
        seen_ids: set = set()
        result: List[EvidenceItem] = []
        for evidences in self._attached.values():
            for ev in evidences:
                if ev.clause.id not in seen_ids:
                    seen_ids.add(ev.clause.id)
                    result.append(ev)
        return result

    def get_statistics(self) -> Dict[str, int]:
        """获取证据挂载统计"""
        total_findings = len(self._attached)
        findings_with_evidence = sum(
            1 for evs in self._attached.values() if evs
        )
        total_evidences = sum(len(evs) for evs in self._attached.values())
        unique_clauses = len({
            ev.clause.id
            for evs in self._attached.values()
            for ev in evs
        })

        return {
            "total_findings_processed": total_findings,
            "findings_with_evidence": findings_with_evidence,
            "total_evidence_items": total_evidences,
            "unique_clauses_cited": unique_clauses,
        }
