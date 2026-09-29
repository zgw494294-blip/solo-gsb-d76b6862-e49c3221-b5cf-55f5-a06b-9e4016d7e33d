# 本地数据导出风险审查 API

基于 Python / FastAPI 的本地 HTTP 服务：给定一张由至多 60 个节点组成的有向无环数据流图和一个导出节点，按依赖关系追踪每一列的**内容来源**与**行影响**，任一含敏感来源即判定该导出**阻断**，并按两类原因分别列出源节点与列名。

## 启动

单条命令（Docker Compose）：

```bash
docker compose up --build
```

服务监听 **8000** 端口：

- `POST /review` — 审查接口
- `GET /health` — 健康检查
- `GET /docs` — Swagger UI（交互式调试）

也可以本地运行（Python 3.11+）：

```bash
pip install -r requirements.txt
uvicorn app.main:app --port 8000
```

运行测试：

```bash
pip install -r requirements-dev.txt
pytest
```

## 节点模型

请求体：`{"nodes": [...], "export": "<节点ID>"}`。`nodes` 至多 60 个，可乱序；`export` 指定要审查的导出节点。

| type         | 字段                                                                 | 语义 |
|--------------|----------------------------------------------------------------------|------|
| `source`     | `columns: [{name, sensitive}]`                                       | 声明互异列名与敏感标记；每列内容来源为自身 |
| `projection` | `input`, `outputs: [{name, from}]` 或 `[{name, constant}]`           | 只保留明确列出的输出列；`from` 复制上游列并继承其内容来源，`constant` 以常量替换并清空该列内容来源 |
| `filter`     | `input`, `condition_columns: [...]`                                  | 保留全部输入列；条件引用列的来源进入**行影响** |
| `join`       | `left`, `right`, `left_keys`, `right_keys`, `outputs: [{name, from: {side, column}}]` | 等值连接；输出别名互异；连接键来源进入行影响，并合并两侧已有行影响 |

关键规则：

- **行影响**作用于结果的每一行，后续投影与常量替换都不能消除（筛选引用敏感列、连接键带敏感来源都会产生行影响）。
- 导出节点的每一列：内容来源或行影响中含敏感来源即判阻断；`content_reasons` 与 `row_reasons` 分别列出两类原因对应的源节点和列名。
- 以下情况**整次请求被拒绝**（HTTP 422，返回全部错误）：节点 ID 重复、列名冲突（同一节点输出列名重复）、无效引用（引用不存在的节点或列）、依赖环、节点数超过 60。

## 调用示例

```bash
curl -s http://localhost:8000/review \
  -H 'Content-Type: application/json' \
  -d '{
    "nodes": [
      {
        "id": "users",
        "type": "source",
        "columns": [
          {"name": "id",    "sensitive": false},
          {"name": "email", "sensitive": true},
          {"name": "city",  "sensitive": false}
        ]
      },
      {
        "id": "active",
        "type": "filter",
        "input": "users",
        "condition_columns": ["email"]
      },
      {
        "id": "export_proj",
        "type": "projection",
        "input": "active",
        "outputs": [
          {"name": "city",    "from": "city"},
          {"name": "contact", "constant": "REDACTED"}
        ]
      }
    ],
    "export": "export_proj"
  }'
```

响应（`email` 被常量替换清除了内容来源，但筛选条件引用了它，行影响仍然阻断导出）：

```json
{
  "export": "export_proj",
  "status": "blocked",
  "columns": [
    {
      "name": "city",
      "blocked": true,
      "content_reasons": [],
      "row_reasons": [{"node": "users", "column": "email"}]
    },
    {
      "name": "contact",
      "blocked": true,
      "content_reasons": [],
      "row_reasons": [{"node": "users", "column": "email"}]
    }
  ]
}
```

非法请求示例（重复节点 ID / 引用不存在的列 / 环等）返回 `422`，`detail` 为错误列表：

```json
{"detail": ["duplicate node id: 'users'", "node 'p': projection output 'x' references unknown column 'nope' of node 'users'"]}
```
