"""Adapter around the Microsoft Power BI Modeling MCP server.

Facts established in Phase 1 (see docs/powerbi.md for sources):

- Package ``@microsoft/powerbi-modeling-mcp``, launched as ``npx -y ...@latest --start``
  (stdio). Targets: Power BI Desktop, Fabric workspace semantic models, PBIP/TMDL folders.
- Tool names documented in the project README (2026-09): connection_operations,
  database_operations, transaction_operations, model_operations, table_operations,
  column_operations, measure_operations, relationship_operations, dax_query_operations,
  trace_operations, partition_operations, user_hierarchy_operations,
  calculation_group_operations, security_role_operations, perspective_operations,
  named_expression_operations, function_operations, culture_operations,
  object_translation_operations, calendar_operations, query_group_operations.
- Write operations are off by default; ``--readwrite`` enables them with per-database
  confirmation, ``--readonly`` forbids them. Report visuals/layout are NOT supported.

The argument schemas of these tools have NOT been inspected yet (the server did not
connect in the Phase 1 environment). Phase 5 must read them from MCP ``tools/list`` at
runtime and fail loudly if an expected tool is missing, rather than guessing.

Not implemented yet: scheduled for Phase 5.
"""
