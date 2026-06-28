"""
Orchestrator Agent - 任务分解和结果汇总

负责：
1. 接收代码审查请求
2. 第一层 RAG：检索企业规范注入 Agent 上下文
3. 并发调度 5 个专业 Agent + PolicyReviewer（第三层 RAG）
4. 第二层 RAG：为所有 finding 挂载规范证据
5. 汇总所有 Agent 的结果，生成最终审查报告
"""
import asyncio
import hashlib
import os
from typing import Dict, List, Any, Optional, Tuple, Union
from anthropic import AsyncAnthropic

from .correctness_agent import CorrectnessAgent
from .security_agent import SecurityAgent
from .maintainability_agent import MaintainabilityAgent
from .policy_reviewer import PolicyReviewerAgent
from knowledge.store import PolicyStore
from knowledge.loader import load_policies_from_dir
from knowledge.evidence import EvidenceStore
from report.formatter import ReportFormatter


class Orchestrator:
    """协调器 - 管理多个专业 Agent 的并发执行，集成三层 RAG"""

    def __init__(self, model: str = None):
        self.model = model or os.getenv("MODEL", "claude-opus-4-6")

        # 根据环境变量选择客户端
        self.client = self._create_client()

        # 初始化企业规范知识库
        self.policy_store = PolicyStore()
        self._ensure_policies_indexed()

        # 初始化 3+1 审查 Agent。Policy Reviewer 仅在有规范索引时默认启用。
        self.agents = {
            "correctness": CorrectnessAgent(self.client, self.model),
            "security": SecurityAgent(self.client, self.model),
            "maintainability": MaintainabilityAgent(self.client, self.model),
        }

        # 第三层 RAG：PolicyReviewer Agent
        self.policy_reviewer = PolicyReviewerAgent(
            self.client, self.model, self.policy_store
        )

        self.formatter = ReportFormatter()

    def _create_client(self) -> Union[AsyncAnthropic, Any]:
        """根据环境变量创建合适的客户端"""
        base_url = os.getenv("LLM_BASE_URL", "")
        api_key = os.getenv("LLM_API_KEY")

        # 如果配置了 LLM_BASE_URL，使用 OpenAI 兼容客户端
        if base_url and api_key:
            try:
                from openai import AsyncOpenAI
            except ImportError:
                raise ImportError(
                    "检测到 LLM_BASE_URL 配置，需要安装 openai 包以使用 OpenAI 兼容接口。\n"
                    "请运行: pip install openai>=1.0.0"
                )
            return AsyncOpenAI(api_key=api_key, base_url=base_url)

        # 否则使用 Anthropic 客户端
        anthropic_key = os.getenv("ANTHROPIC_API_KEY")
        return AsyncAnthropic(api_key=anthropic_key) if anthropic_key else AsyncAnthropic()

    def _ensure_policies_indexed(self):
        """确保企业规范已索引到向量数据库"""
        clauses = load_policies_from_dir()
        if clauses:
            count = self.policy_store.index_clauses(clauses)
            print(f"📚 企业规范知识库已加载: {count} 条规范")

    async def review_code(
        self,
        code: str,
        language: str = "python",
        context: Dict[str, Any] = None,
        enabled_agents: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        执行完整的代码审查流程（集成三层 RAG）

        Layer 1 (上下文层): broad_retrieve → 注入 <company_knowledge> 到所有 Agent
        Layer 2 (证据层):   precise_retrieve → 为每个 finding 挂载规范证据
        Layer 3 (发现层):   PolicyReviewer 基于 risk_scan 主动发现违规

        Args:
            code: 要审查的代码
            language: 编程语言
            context: 额外上下文（文件路径、仓库信息等）

        Returns:
            包含所有 Agent 分析结果 + 证据链的字典
        """
        from tracing import get_tracer
        tracer = get_tracer()

        review_mode = (context or {}).get("review_mode", "code")
        print(f"🚀 开始代码审查 ({language}, mode={review_mode})...")

        async with tracer.async_span("orchestrator.review", {
            "language": language,
            "review_mode": review_mode,
        }) as span:

            # ═══ 第一层 RAG：上下文层 ═══
            # review 开始前，根据代码检索相关企业规范，注入到所有 Agent 的 context
            company_knowledge = self.policy_store.broad_retrieve(code, language, top_k=8)
            knowledge_text = self._format_company_knowledge(company_knowledge)

            enriched_context = {**(context or {})}
            if knowledge_text:
                enriched_context["company_policy"] = knowledge_text

            # ═══ 并发执行 4 个专业 Agent + 第三层 PolicyReviewer ═══
            agent_names = enabled_agents or self._default_agent_names()
            agent_names = self._normalize_agent_names(agent_names)
            valid_names = {"correctness", "security", "maintainability", "policy"}
            invalid_names = sorted(set(agent_names) - valid_names)
            if invalid_names:
                raise ValueError(f"未知 Agent: {', '.join(invalid_names)}")

            tasks = []
            task_names = []
            for name in agent_names:
                task_names.append(name)
                if name == "policy":
                    tasks.append(self.policy_reviewer.analyze(code, language, enriched_context))
                else:
                    tasks.append(self.agents[name].analyze(code, language, enriched_context))

            span.set_attribute("agents", agent_names)
            span.set_attribute("has_policy_context", bool(knowledge_text))
            results = await asyncio.gather(*tasks, return_exceptions=True)

            review_results = {}
            failed_agents = []
            for name, result in zip(task_names, results):
                if isinstance(result, Exception):
                    review_results[name] = {"error": str(result)}
                    failed_agents.append(name)
                else:
                    review_results[name] = result

            # ═══ 第二层 RAG：证据层 ═══
            # 为每个 finding 检索规范证据，挂载到 evidence chain
            evidence_store = EvidenceStore(self.policy_store)
            review_results = self._attach_evidence_to_findings(review_results, evidence_store)

            # 附加证据统计
            review_results["_evidence_summary"] = evidence_store.get_statistics()
            review_results["_company_knowledge_count"] = len(company_knowledge)

            span.set_attribute("failed_agents", failed_agents)
            span.set_attribute("evidence_count", evidence_store.get_statistics().get("total_attached", 0))
            print("✅ 代码审查完成")
            return review_results

    async def review_diff(
        self,
        diff: str,
        context: Dict[str, Any] = None,
        enabled_agents: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """审查 PR diff，只关注本次变更新增/修改行带来的问题。"""
        diff_context = {
            **(context or {}),
            "review_mode": "diff",
        }
        agents = enabled_agents or self._default_agent_names()
        return await self.review_code(
            diff,
            language="diff",
            context=diff_context,
            enabled_agents=agents,
        )

    def _default_agent_names(self) -> List[str]:
        names = ["correctness", "security", "maintainability"]
        if self.policy_store.indexed_count > 0:
            names.append("policy")
        return names

    def _normalize_agent_names(self, agent_names: List[str]) -> List[str]:
        """Map legacy agent names to the 3+1 architecture."""
        legacy_map = {
            "bug": "correctness",
            "perf": "correctness",
            "quality": "maintainability",
            "practice": "maintainability",
        }
        normalized = []
        for name in agent_names:
            mapped = legacy_map.get(name, name)
            if mapped not in normalized:
                normalized.append(mapped)
        return normalized

    def _format_company_knowledge(
        self, results: List[Tuple]
    ) -> str:
        """
        格式化第一层检索结果为 prompt 注入文本

        只保留相关性 > 0.25 的条款，避免注入噪声
        """
        if not results:
            return ""

        relevant = [(clause, score) for clause, score in results if score > 0.25]
        if not relevant:
            return ""

        lines = ["<company_knowledge>", "以下是与当前代码相关的企业内部规范，请在审查时参考：", ""]
        for clause, score in relevant:
            # 截断过长内容，控制 token 消耗
            content_preview = clause.content.strip().replace("\n", " ")[:200]
            lines.append(f"[{clause.id}] {clause.title} ({clause.domain}, {clause.severity})")
            lines.append(f"  {content_preview}")
            lines.append("")
        lines.append("</company_knowledge>")
        return "\n".join(lines)

    def _attach_evidence_to_findings(
        self, review_results: Dict[str, Any], evidence_store: EvidenceStore
    ) -> Dict[str, Any]:
        """
        第二层 RAG：为所有 Agent 的 finding 挂载规范证据

        遍历每个 Agent 的结果，对每个 finding 调用 precise_retrieve，
        将命中的规范条款作为 policy_evidence 挂载到 finding 上。
        """
        # Agent source → finding 列表字段名的映射
        for source in ("correctness", "security", "maintainability"):
            agent_result = review_results.get(source, {})
            if "error" in agent_result:
                continue

            for item in self._iter_agent_findings(source, agent_result):
                # 构建 finding 文本用于检索
                finding_text = " ".join(filter(None, [
                    item.get("message", ""),
                    item.get("recommendation", ""),
                    item.get("evidence", ""),
                    item.get("impact", ""),
                ]))

                if not finding_text.strip():
                    continue

                # 生成唯一 key
                text_hash = hashlib.md5(finding_text.encode()).hexdigest()[:8]
                finding_key = f"{source}:{item.get('line', '?')}:{text_hash}"

                # 检索并挂载证据
                evidences = evidence_store.attach_evidence(finding_key, finding_text, source)
                if evidences:
                    item["policy_evidence"] = [
                        {
                            "clause_id": e.clause.id,
                            "title": e.clause.title,
                            "domain": e.clause.domain,
                            "severity": e.clause.severity,
                            "relevance_score": e.relevance_score,
                        }
                        for e in evidences
                    ]

        return review_results

    def _iter_agent_findings(self, source: str, agent_result: Dict[str, Any]) -> List[Dict[str, Any]]:
        if agent_result.get("findings"):
            return agent_result.get("findings", [])
        legacy_fields = {
            "correctness": ("issues", "bugs"),
            "security": ("vulnerabilities",),
            "maintainability": ("issues", "recommendations"),
        }
        findings: List[Dict[str, Any]] = []
        for field in legacy_fields.get(source, ()):
            findings.extend(agent_result.get(field, []))
        return findings

    async def review_file(self, file_path: str) -> Dict[str, Any]:
        """审查单个文件"""
        from pathlib import Path

        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")

        code = path.read_text(encoding="utf-8")
        language = self._detect_language(path.suffix)

        context = {
            "file_path": str(path),
            "file_name": path.name,
        }

        return await self.review_code(code, language, context)

    def _detect_language(self, suffix: str) -> str:
        """根据文件后缀检测编程语言"""
        language_map = {
            ".py": "python",
            ".js": "javascript",
            ".ts": "typescript",
            ".java": "java",
            ".go": "go",
            ".rs": "rust",
            ".cpp": "cpp",
            ".c": "c",
        }
        return language_map.get(suffix.lower(), "unknown")
