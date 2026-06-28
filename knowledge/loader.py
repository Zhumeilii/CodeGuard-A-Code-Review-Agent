"""
规范文档加载器 - 从 YAML 文件加载企业规范条款

支持的 YAML 格式：
    domain: security
    clauses:
      - id: SEC-001
        title: ...
        content: ...
        severity: critical
        tags: [...]
"""
from __future__ import annotations

from pathlib import Path
from typing import List

import yaml

from tools.models import PolicyClause


def load_policies_from_dir(policy_dir: str = None) -> List[PolicyClause]:
    """
    从 policies/ 目录加载所有 YAML 规范文件

    Args:
        policy_dir: 规范文件目录路径，默认为 knowledge/policies/

    Returns:
        PolicyClause 列表
    """
    policy_dir = policy_dir or str(Path(__file__).parent / "policies")
    policy_path = Path(policy_dir)

    if not policy_path.exists():
        return []

    clauses: List[PolicyClause] = []

    for yaml_file in sorted(policy_path.glob("*.yaml")):
        file_clauses = load_policy_file(str(yaml_file))
        clauses.extend(file_clauses)

    return clauses


def load_policy_file(file_path: str) -> List[PolicyClause]:
    """
    加载单个 YAML 规范文件

    Args:
        file_path: YAML 文件路径

    Returns:
        该文件中的 PolicyClause 列表
    """
    path = Path(file_path)
    if not path.exists():
        return []

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not data or "clauses" not in data:
        return []

    domain = data.get("domain", path.stem)
    clauses: List[PolicyClause] = []

    for item in data["clauses"]:
        if not item.get("id") or not item.get("content"):
            continue
        clauses.append(PolicyClause(
            id=item["id"],
            domain=domain,
            title=item.get("title", ""),
            content=item["content"],
            severity=item.get("severity", "medium"),
            tags=item.get("tags", []),
        ))

    return clauses
