"""
Policy Reviewer Agent - 基于企业规范主动发现违规（第三层 RAG）

与其他 Agent 的区别：
- 其他 Agent 基于通用 LLM 知识发现问题
- PolicyReviewer 基于 RAG 检索到的企业规范条款主动审查

工作流：
1. risk_scan: 从知识库检索与代码相关的规范条款
2. LLM 判断: 将代码 + 规范条款交给 LLM，判断是否存在违规
3. 输出 PolicyFinding（带 evidence chain）

这意味着 RAG 不只是"给已有发现补引用"，而是真正参与审查流程。
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from .base_agent import BaseAgent
from knowledge.store import PolicyStore
from tools.models import EvidenceItem, PolicyClause, PolicyFinding, ReviewFinding


class PolicyReviewerAgent(BaseAgent):
    """企业规范审查 Agent - 主动发现违反公司规范的问题"""

    def __init__(self, client, model: str, policy_store: PolicyStore):
        super().__init__(client, model, "policy_reviewer")
        self.policy_store = policy_store

    async def analyze(
        self, code: str, language: str, context: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        主动审查代码是否违反企业规范

        流程：
        1. risk_scan 检索与代码相关的规范条款
        2. 将规范注入 prompt，让 LLM 逐条判断是否违反
        3. 返回结构化 PolicyFinding 列表

        Args:
            code: 待审查代码
            language: 编程语言
            context: 额外上下文

        Returns:
            {"findings": [...], "scanned_clauses": int, "violated_count": int}
        """
        # 第三层：主动检索可能被违反的规范
        scan_results = self.policy_store.risk_scan(code, top_k=10)

        if not scan_results:
            return {"findings": [], "scanned_clauses": 0, "violated_count": 0}

        # 过滤低相关性结果
        relevant_results = [(clause, score) for clause, score in scan_results if score > 0.2]
        if not relevant_results:
            return {"findings": [], "scanned_clauses": 0, "violated_count": 0}

        # 构建 prompt，让 LLM 判断哪些规范被违反
        clauses_text = self._format_clauses_for_prompt(relevant_results)
        system_prompt = self._build_system_prompt()
        user_message = self._build_user_message(code, language, clauses_text, context)

        result = await self._call_with_schema_validation(
            system_prompt,
            user_message,
            category="policy",
        )
        findings = self._attach_policy_evidence(result.get("findings", []), relevant_results)

        return {
            **result,
            "findings": findings,
            "scanned_clauses": len(relevant_results),
            "violated_count": len(findings),
        }

    def _build_system_prompt(self) -> str:
        from agents.prompt_loader import get_agent_system_prompt
        return get_agent_system_prompt("policy_reviewer", {})

    def _build_user_message(
        self,
        code: str,
        language: str,
        clauses_text: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        from agents.prompt_loader import get_user_message_template
        ctx = context or {}
        file_info = f"文件: {ctx.get('file_path', 'unknown')}\n" if ctx.get("file_path") else ""
        return get_user_message_template("policy_review", {
            "file_info": file_info,
            "language": language,
            "code": code,
            "clauses_text": clauses_text,
        })

    def _format_clauses_for_prompt(
        self, scan_results: List[Tuple[PolicyClause, float]]
    ) -> str:
        """格式化检索到的规范条款，用于注入 LLM prompt"""
        lines = []
        for clause, score in scan_results:
            lines.append(f"### [{clause.id}] {clause.title}")
            lines.append(f"- 领域: {clause.domain} | 严重级别: {clause.severity} | 相关度: {score:.2f}")
            lines.append(f"- 内容: {clause.content.strip()}")
            lines.append("")
        return "\n".join(lines)

    def _attach_policy_evidence(
        self,
        raw_findings: List[Dict[str, Any]],
        scan_results: List[Tuple[PolicyClause, float]],
    ) -> List[Dict[str, Any]]:
        clause_map: Dict[str, Tuple[PolicyClause, float]] = {
            clause.id: (clause, score) for clause, score in scan_results
        }
        output: List[Dict[str, Any]] = []
        for item in raw_findings:
            finding = ReviewFinding.model_validate(item)
            clause_id = finding.rule_id
            evidence_chain = []
            if clause_id and clause_id in clause_map:
                clause, score = clause_map[clause_id]
                evidence_chain.append(EvidenceItem(
                    clause=clause,
                    relevance_score=round(score, 4),
                    match_reason=f"PolicyReviewer 判定代码违反企业规范 [{clause_id}] {clause.title}",
                ).model_dump())
            data = finding.model_dump()
            data["policy_evidence"] = [
                {
                    "clause_id": evidence["clause"]["id"],
                    "title": evidence["clause"]["title"],
                    "domain": evidence["clause"]["domain"],
                    "severity": evidence["clause"]["severity"],
                    "relevance_score": evidence["relevance_score"],
                }
                for evidence in evidence_chain
            ]
            data["evidence_chain"] = evidence_chain
            output.append(data)
        return output

    def _parse_findings(
        self,
        response: Dict[str, Any],
        scan_results: List[Tuple[PolicyClause, float]],
    ) -> List[PolicyFinding]:
        """解析 LLM 返回的违规发现，构建带 evidence chain 的 PolicyFinding"""
        raw_text = "\n".join(response.get("text", []))
        data = self._extract_json(raw_text)

        if not data:
            return []

        # 构建 clause 查找表
        clause_map: Dict[str, Tuple[PolicyClause, float]] = {
            clause.id: (clause, score) for clause, score in scan_results
        }

        findings: List[PolicyFinding] = []
        for item in data.get("findings", []):
            clause_id = item.get("clause_id", "")
            if not clause_id:
                continue

            # 构建 evidence chain
            evidence_chain: List[EvidenceItem] = []
            clause_info = clause_map.get(clause_id)
            if clause_info:
                clause, score = clause_info
                evidence_chain.append(EvidenceItem(
                    clause=clause,
                    relevance_score=round(score, 4),
                    match_reason=f"PolicyReviewer 判定代码违反企业规范 [{clause_id}] {clause.title}",
                ))

            findings.append(PolicyFinding(
                clause_id=clause_id,
                violated_rule=item.get("violated_rule", ""),
                code_location=item.get("code_location"),
                explanation=item.get("explanation", ""),
                suggestion=item.get("suggestion", ""),
                evidence_chain=evidence_chain,
            ))

        return findings

    def _extract_json(self, text: str) -> Optional[Dict]:
        """从 LLM 响应文本中提取 JSON"""
        # 尝试 ```json 代码块
        match = re.search(r"```json\s*([\s\S]*?)```", text)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except json.JSONDecodeError:
                pass

        # 尝试直接解析
        try:
            return json.loads(text.strip())
        except json.JSONDecodeError:
            pass

        # 尝试找第一个 { ... } 块
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass

        return None
