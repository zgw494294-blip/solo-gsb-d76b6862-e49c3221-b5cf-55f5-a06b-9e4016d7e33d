"""依赖分析与敏感来源追踪。

语义：
- 复制（copy）继承上游列的内容来源；常量替换清空该列的内容来源。
- 筛选条件引用的列、连接键的内容来源会进入"行影响"：它影响结果的每一行，
  后续投影与常量替换都无法消除；连接还要合并两侧已有的行影响。
- 导出节点的每一列：内容来源或行影响中含敏感来源即判阻断，两类原因分别列出。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Set, Tuple

from .models import (
    FilterNode,
    JoinNode,
    ProjectionNode,
    ReviewRequest,
    SourceNode,
)

SourceRefT = Tuple[str, str]  # (源节点 id, 列名)


@dataclass
class NodeInfo:
    """某个节点输出关系的信息。"""

    columns: Dict[str, Set[SourceRefT]] = field(default_factory=dict)  # 列 -> 内容来源
    row: Set[SourceRefT] = field(default_factory=set)  # 行影响来源（关系级，作用于每一行）


def _referenced_nodes(node) -> List[str]:
    if isinstance(node, (ProjectionNode, FilterNode)):
        return [node.input]
    if isinstance(node, JoinNode):
        return [node.left, node.right]
    return []


def _declared_output_names(node) -> List[str]:
    if isinstance(node, SourceNode):
        return [c.name for c in node.columns]
    if isinstance(node, ProjectionNode):
        return [o.name for o in node.outputs]
    if isinstance(node, JoinNode):
        return [o.name for o in node.outputs]
    return []  # filter 保留输入列，不声明新列名


def review_request(req: ReviewRequest):
    """返回 (errors, infos)。errors 非空表示整次请求应被拒绝。"""
    errors: List[str] = []

    # 1) 节点 ID 唯一
    nodes_by_id: Dict[str, object] = {}
    for n in req.nodes:
        if n.id in nodes_by_id:
            errors.append(f"duplicate node id: '{n.id}'")
        else:
            nodes_by_id[n.id] = n

    # 2) 各节点输出列名互异（列名冲突）
    for n in nodes_by_id.values():
        names = _declared_output_names(n)
        seen: Set[str] = set()
        for name in names:
            if name in seen:
                errors.append(f"node '{n.id}': duplicate column name '{name}'")
            seen.add(name)

    # 3) 节点引用有效
    deps: Dict[str, Set[str]] = {nid: set() for nid in nodes_by_id}
    for n in nodes_by_id.values():
        for ref in _referenced_nodes(n):
            if ref not in nodes_by_id:
                errors.append(f"node '{n.id}': unknown referenced node '{ref}'")
            elif ref == n.id:
                errors.append(f"node '{n.id}': cannot reference itself")
            else:
                deps[n.id].add(ref)

    if req.export not in nodes_by_id:
        errors.append(f"export node '{req.export}' not found")

    if errors:
        return errors, {}

    # 4) 拓扑排序，检测环
    dependents: Dict[str, Set[str]] = {nid: set() for nid in nodes_by_id}
    indegree: Dict[str, int] = {}
    for nid, ds in deps.items():
        indegree[nid] = len(ds)
        for d in ds:
            dependents[d].add(nid)

    ready = sorted([nid for nid, deg in indegree.items() if deg == 0])
    topo: List[str] = []
    while ready:
        nid = ready.pop()
        topo.append(nid)
        for dep in sorted(dependents[nid]):
            indegree[dep] -= 1
            if indegree[dep] == 0:
                ready.append(dep)
    if len(topo) != len(nodes_by_id):
        cyclic = sorted(nid for nid, deg in indegree.items() if deg > 0)
        errors.append(f"dependency cycle detected involving nodes: {cyclic}")
        return errors, {}

    # 5) 按拓扑序计算每列内容来源与行影响，同时校验列级引用
    infos: Dict[str, NodeInfo] = {}
    for nid in topo:
        node = nodes_by_id[nid]
        info = NodeInfo()

        if isinstance(node, SourceNode):
            for c in node.columns:
                info.columns[c.name] = {(node.id, c.name)}

        elif isinstance(node, ProjectionNode):
            up = infos[node.input]
            info.row = set(up.row)
            for out in node.outputs:
                if out.from_column is not None and "from_column" in out.model_fields_set:
                    src = out.from_column
                    if src not in up.columns:
                        errors.append(
                            f"node '{nid}': projection output '{out.name}' references "
                            f"unknown column '{src}' of node '{node.input}'"
                        )
                        info.columns[out.name] = set()
                    else:
                        info.columns[out.name] = set(up.columns[src])  # 复制：继承内容来源
                else:
                    info.columns[out.name] = set()  # 常量替换：清空内容来源

        elif isinstance(node, FilterNode):
            up = infos[node.input]
            info.columns = {name: set(srcs) for name, srcs in up.columns.items()}  # 保留输入列
            info.row = set(up.row)
            for col in node.condition_columns:
                if col not in up.columns:
                    errors.append(
                        f"node '{nid}': filter condition references unknown column "
                        f"'{col}' of node '{node.input}'"
                    )
                else:
                    info.row |= up.columns[col]  # 条件列来源影响结果的每一行

        elif isinstance(node, JoinNode):
            left = infos[node.left]
            right = infos[node.right]
            info.row = set(left.row) | set(right.row)  # 合并两侧已有行影响
            for col in node.left_keys:
                if col not in left.columns:
                    errors.append(
                        f"node '{nid}': left join key '{col}' not found in node '{node.left}'"
                    )
                else:
                    info.row |= left.columns[col]  # 连接键来源影响结果的每一行
            for col in node.right_keys:
                if col not in right.columns:
                    errors.append(
                        f"node '{nid}': right join key '{col}' not found in node '{node.right}'"
                    )
                else:
                    info.row |= right.columns[col]
            for out in node.outputs:
                side_info = left if out.from_.side == "left" else right
                side_name = node.left if out.from_.side == "left" else node.right
                col = out.from_.column
                if col not in side_info.columns:
                    errors.append(
                        f"node '{nid}': join output '{out.name}' references unknown column "
                        f"'{col}' of node '{side_name}'"
                    )
                    info.columns[out.name] = set()
                else:
                    info.columns[out.name] = set(side_info.columns[col])

        infos[nid] = info

    if errors:
        return errors, {}
    return [], infos


def build_response(req: ReviewRequest, infos: Dict[str, NodeInfo], sensitive: Set[SourceRefT]):
    from .models import ColumnVerdict, ReviewResponse, SourceRef

    info = infos[req.export]
    columns: List[ColumnVerdict] = []
    for name, content in info.columns.items():
        content_hits = sorted(ref for ref in content if ref in sensitive)
        row_hits = sorted(ref for ref in info.row if ref in sensitive)
        columns.append(
            ColumnVerdict(
                name=name,
                blocked=bool(content_hits or row_hits),
                content_reasons=[SourceRef(node=n, column=c) for n, c in content_hits],
                row_reasons=[SourceRef(node=n, column=c) for n, c in row_hits],
            )
        )
    return ReviewResponse(
        export=req.export,
        status="blocked" if any(c.blocked for c in columns) else "passed",
        columns=columns,
    )


def sensitive_sources(req: ReviewRequest) -> Set[SourceRefT]:
    out: Set[SourceRefT] = set()
    for n in req.nodes:
        if isinstance(n, SourceNode):
            for c in n.columns:
                if c.sensitive:
                    out.add((n.id, c.name))
    return out
