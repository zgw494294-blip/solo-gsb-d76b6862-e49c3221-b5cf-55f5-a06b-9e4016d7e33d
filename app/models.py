"""请求/响应数据模型。

节点类型：
- source：     声明互异列名及敏感标记
- projection： 只保留明确列出的输出列，每列复制上游列或以常量替换
- filter：     声明条件引用的列，保留全部输入列
- join：       等值连接，声明左右输入、连接键、输出列及互异别名
"""
from __future__ import annotations

from typing import Annotated, Any, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ---------- 列与输出定义 ----------

class SourceColumn(BaseModel):
    name: str = Field(min_length=1)
    sensitive: bool = False


class ProjectionOutput(BaseModel):
    """投影输出列：必须且只能提供 `from`（复制上游列）或 `constant`（常量替换）之一。"""

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(min_length=1)
    from_column: Optional[str] = Field(default=None, alias="from")
    constant: Any = None

    @model_validator(mode="after")
    def _exactly_one_source(self) -> "ProjectionOutput":
        has_from = "from_column" in self.model_fields_set
        has_const = "constant" in self.model_fields_set
        if has_from == has_const:
            raise ValueError(
                f"projection output '{self.name}': exactly one of 'from' or 'constant' must be given"
            )
        return self


class JoinOutputFrom(BaseModel):
    side: Literal["left", "right"]
    column: str = Field(min_length=1)


class JoinOutput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(min_length=1)
    from_: JoinOutputFrom = Field(alias="from")


# ---------- 节点定义 ----------

class SourceNode(BaseModel):
    id: str = Field(min_length=1)
    type: Literal["source"]
    columns: List[SourceColumn] = Field(min_length=1)


class ProjectionNode(BaseModel):
    id: str = Field(min_length=1)
    type: Literal["projection"]
    input: str = Field(min_length=1)
    outputs: List[ProjectionOutput] = Field(min_length=1)


class FilterNode(BaseModel):
    id: str = Field(min_length=1)
    type: Literal["filter"]
    input: str = Field(min_length=1)
    condition_columns: List[str] = Field(min_length=1)


class JoinNode(BaseModel):
    id: str = Field(min_length=1)
    type: Literal["join"]
    left: str = Field(min_length=1)
    right: str = Field(min_length=1)
    left_keys: List[str] = Field(min_length=1)
    right_keys: List[str] = Field(min_length=1)
    outputs: List[JoinOutput] = Field(min_length=1)

    @model_validator(mode="after")
    def _keys_pair_up(self) -> "JoinNode":
        if len(self.left_keys) != len(self.right_keys):
            raise ValueError(
                f"join '{self.id}': left_keys and right_keys must have the same length"
            )
        return self


Node = Annotated[
    Union[SourceNode, ProjectionNode, FilterNode, JoinNode],
    Field(discriminator="type"),
]


# ---------- 顶层请求 ----------

class ReviewRequest(BaseModel):
    nodes: List[Node] = Field(min_length=1, max_length=60)
    export: str = Field(min_length=1, description="要审查的导出节点 ID")


# ---------- 响应 ----------

class SourceRef(BaseModel):
    node: str
    column: str


class ColumnVerdict(BaseModel):
    name: str
    blocked: bool
    content_reasons: List[SourceRef] = Field(
        default_factory=list, description="内容含敏感来源（源节点+列名）"
    )
    row_reasons: List[SourceRef] = Field(
        default_factory=list, description="行影响含敏感来源（源节点+列名）"
    )


class ReviewResponse(BaseModel):
    export: str
    status: Literal["passed", "blocked"]
    columns: List[ColumnVerdict]
