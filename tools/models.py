"""
数据模型 - Agentic Fix Loop 的核心数据结构
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


class VerifyMode(str, Enum):
    """验证模式"""
    SANDBOX = "sandbox"  # 仅跑 pytest，省 token
    FULL = "full"        # pytest + LLM 重审（默认）


class FixStatus(str, Enum):
    """修复状态"""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class IssueRef(BaseModel):
    """从各 Review Agent 结果中提取的单条问题"""
    source: str           # "bug" | "security" | "quality" | "perf" | "practice"
    severity: str         # "low" | "medium" | "high" | "critical"
    line: Optional[int] = None
    message: str
    fix_hint: str         # 各 agent 的 fix/remediation/suggestion 字段


Severity = Literal["low", "medium", "high", "critical"]
FindingCategory = Literal["correctness", "security", "maintainability", "policy"]


class ReviewFinding(BaseModel):
    """统一 review finding schema，供所有审查 Agent 输出。"""
    category: FindingCategory
    type: str = "other"
    severity: Severity = "medium"
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    line: Optional[int] = None
    existing_code: str = ""
    message: str
    evidence: str = ""
    impact: str = ""
    recommendation: str = ""
    rule_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    policy_evidence: List[Dict[str, Any]] = Field(default_factory=list)

    @field_validator("message")
    @classmethod
    def message_must_not_be_empty(cls, value: str) -> str:
        value = (value or "").strip()
        if not value:
            raise ValueError("message must not be empty")
        return value

    @field_validator("type", "existing_code", "evidence", "impact", "recommendation")
    @classmethod
    def normalize_text_fields(cls, value: str) -> str:
        return (value or "").strip()


class AgentReviewResult(BaseModel):
    """统一 Agent 审查结果 schema。"""
    findings: List[ReviewFinding] = Field(default_factory=list)
    summary: str = ""
    risk_level: Severity = "low"
    score: Optional[int] = Field(default=None, ge=0, le=100)
    schema_version: str = "review-finding.v1"
    confidence_threshold: float = Field(default=0.0, ge=0.0, le=1.0)
    dropped_low_confidence: int = 0


class LLMOutputValidationError(ValueError):
    """Raised when an LLM response cannot be parsed or validated."""


class FilePatch(BaseModel):
    """单文件补丁"""
    file_path: str
    original_snippet: str   # 用原始代码片段定位，避免行号漂移
    patched_snippet: str    # 替换后的代码片段
    start_line: int
    end_line: int
    description: str


class FixPlan(BaseModel):
    """FixAgent 生成的修复计划"""
    issues_addressed: List[IssueRef] = Field(default_factory=list)
    patches: List[FilePatch] = Field(default_factory=list)
    affected_callers: List[str] = Field(default_factory=list)  # RepoMap 提供
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    revised_strategy: Optional[str] = None  # Reflection 后注入的修订策略


class SandboxResult(BaseModel):
    """pytest subprocess 执行结果"""
    passed: bool
    total: int = 0
    failed: int = 0
    errors: List[str] = Field(default_factory=list)
    stdout: str = ""
    returncode: int = 0
    duration_seconds: float = 0.0
    no_tests_found: bool = False


class LLMReviewResult(BaseModel):
    """LLM 重审结果"""
    approved: bool
    resolved_issues: List[str] = Field(default_factory=list)
    remaining_issues: List[IssueRef] = Field(default_factory=list)
    new_issues: List[IssueRef] = Field(default_factory=list)
    reasoning: str = ""


class VerifyResult(BaseModel):
    """综合验证结果"""
    mode: VerifyMode
    sandbox: Optional[SandboxResult] = None
    llm_review: Optional[LLMReviewResult] = None
    overall_passed: bool = False
    failure_reason: Optional[str] = None


class ReflectResult(BaseModel):
    """Reflection 步骤输出"""
    root_cause: str
    revised_strategy: str
    should_retry: bool
    skip_issue_messages: List[str] = Field(default_factory=list)


class IterationRecord(BaseModel):
    """单次迭代的完整记录"""
    iteration: int
    fix_plan: FixPlan
    verify_result: VerifyResult
    reflect_result: Optional[ReflectResult] = None


class LoopState(BaseModel):
    """Fix Loop 完整运行状态"""
    file_path: str
    language: str
    original_code: str
    current_code: str
    review_results: Dict[str, Any] = Field(default_factory=dict)
    repo_map_summary: Optional[str] = None
    fix_plan: Optional[FixPlan] = None
    verify_result: Optional[VerifyResult] = None
    iteration: int = 0
    max_iterations: int = 3
    status: FixStatus = FixStatus.PENDING
    history: List[IterationRecord] = Field(default_factory=list)


# ── 企业规范 & 证据链模型 ──────────────────────────────────────────────


class PolicyClause(BaseModel):
    """企业规范中的单条条款"""
    id: str                                     # e.g. "SEC-003"
    domain: str                                 # "security" | "payment" | "testing" | "incident"
    title: str
    content: str                                # 条款正文
    severity: str = "medium"                    # 违反时的默认严重级别
    tags: List[str] = Field(default_factory=list)


class EvidenceItem(BaseModel):
    """证据链中的单条证据"""
    clause: PolicyClause                        # 命中的规范条款
    relevance_score: float                      # 检索相关性分数
    match_reason: str                           # 为什么匹配（用于可解释性）


class PolicyFinding(BaseModel):
    """第三层：policy-reviewer 主动发现的违规"""
    clause_id: str
    violated_rule: str
    code_location: Optional[int] = None
    explanation: str
    suggestion: str
    evidence_chain: List[EvidenceItem] = Field(default_factory=list)


class ReviewResultWithEvidence(BaseModel):
    """带证据链的审查结果（扩展现有结果）"""
    agent_findings: Dict[str, Any] = Field(default_factory=dict)
    policy_findings: List[PolicyFinding] = Field(default_factory=list)
    company_knowledge: List[PolicyClause] = Field(default_factory=list)
    evidence_store: List[EvidenceItem] = Field(default_factory=list)
