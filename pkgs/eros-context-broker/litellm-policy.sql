BEGIN;

-- A hard limit is an explicit exception. Existing Eros interop test keys
-- retain their bounded behavior; operational Eros keys become observe-only.
UPDATE "LiteLLM_VerificationToken"
SET metadata = jsonb_set(coalesce(metadata, '{}'::jsonb), '{hard_budget}', 'true'::jsonb, true)
WHERE key_alias IN ('eros-integration-test', 'eros-interop-test');

UPDATE "LiteLLM_VerificationToken"
SET metadata = jsonb_set(
        CASE
            WHEN max_budget IS NULL THEN
                jsonb_set(coalesce(metadata, '{}'::jsonb), '{budget_mode}', '"observe"'::jsonb, true)
            ELSE
                jsonb_set(
                    jsonb_set(
                        jsonb_set(coalesce(metadata, '{}'::jsonb), '{budget_mode}', '"observe"'::jsonb, true),
                        '{soft_budget_usd}',
                        coalesce(metadata->'soft_budget_usd', to_jsonb(max_budget)),
                        true
                    ),
                    '{soft_budget_window}',
                    coalesce(metadata->'soft_budget_window', to_jsonb(coalesce(budget_duration, '30d'))),
                    true
                )
        END,
        '{hard_budget_snapshot}',
        coalesce(
            metadata->'hard_budget_snapshot',
            jsonb_build_object(
                'max_budget', max_budget,
                'budget_duration', budget_duration,
                'budget_reset_at', budget_reset_at,
                'budget_limits', budget_limits,
                'model_max_budget', model_max_budget
            )
        ),
        true
    ),
    max_budget = NULL,
    budget_duration = NULL,
    budget_reset_at = NULL,
    budget_limits = NULL,
    model_max_budget = '{}'::jsonb
WHERE key_alias LIKE 'eros-%'
  AND (
      max_budget IS NOT NULL
      OR budget_duration IS NOT NULL
      OR budget_reset_at IS NOT NULL
      OR budget_limits IS NOT NULL
      OR coalesce(model_max_budget, '{}'::jsonb) <> '{}'::jsonb
  )
  AND lower(coalesce(metadata->>'hard_budget', 'false')) NOT IN ('true', '1', 'yes');

UPDATE "LiteLLM_VerificationToken"
SET metadata = jsonb_set(coalesce(metadata, '{}'::jsonb), '{budget_mode}', '"observe"'::jsonb, true)
WHERE key_alias LIKE 'eros-%'
  AND lower(coalesce(metadata->>'hard_budget', 'false')) NOT IN ('true', '1', 'yes');

-- Every operational Eros key may use the complete route catalog. Trust-domain
-- labels are accounting/provenance metadata, not access-control boundaries.
UPDATE "LiteLLM_VerificationToken"
SET models = ARRAY(
    SELECT DISTINCT model
    FROM unnest(coalesce(models, ARRAY[]::text[]) || ARRAY[
        'coding', 'local-embed', 'coding-openai', 'openai/*',
        'coding-haiku', 'coding-gemini', 'coding-strong',
        'claude-haiku-4-5', 'claude-sonnet-5', 'claude-opus-5',
        'g2-omniroute-openai-gpt4o-mini', 'g5-omniroute-bedrock-haiku',
        'tier0-local', 'tier1-general', 'tier1-coding', 'mini',
        'tier2-general', 'tier2-coding', 'auto', 'tier2-research',
        'tier3-quality', 'quality', 'gpt-5.4', 'gpt-5.6-terra',
        'axis-claude-sonnet-4-6', 'tier4-frontier', 'general-core',
        'coding-core', 'multimodal-long', 'review-strong',
        'research-candidate', 'reasoning-candidate', 'embedding-core',
        'claude-sonnet-4-6', 'qwen3-coder-next', 'qwen3-next-80b-a3b',
        'deepseek-v3.2', 'kimi-k2.5', 'glm-5', 'nova-2-lite',
        'titan-embed-text-v2', 'personal', 'work'
    ]) AS model
)
WHERE key_alias LIKE 'eros-%'
  AND lower(coalesce(metadata->>'hard_budget', 'false')) NOT IN ('true', '1', 'yes');

-- Every operational key gets an Eros-owned permission row for the complete
-- shared discovery fabric. Reassigning the token avoids broadening any row
-- that is also referenced by a non-Eros key.
INSERT INTO "LiteLLM_ObjectPermissionTable" (
    object_permission_id, mcp_servers, vector_stores, mcp_access_groups,
    mcp_tool_permissions, agents, agent_access_groups, blocked_tools,
    models, mcp_toolsets, search_tools, mcp_tool_search_enabled
) VALUES (
    'eros-shared-tool-search-permissions', ARRAY[
        'recallium', 'graphify', 'context7', 'playwright', 'duckduckgo',
        'gitlab', 'kubernetes', 'aws', 'terraform', 'eros-context-shared'
    ]::text[],
    NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, true
)
ON CONFLICT (object_permission_id) DO UPDATE
SET mcp_servers = EXCLUDED.mcp_servers,
    mcp_tool_search_enabled = true;

UPDATE "LiteLLM_VerificationToken"
SET object_permission_id = 'eros-shared-tool-search-permissions'
WHERE key_alias LIKE 'eros-%'
  AND lower(coalesce(metadata->>'hard_budget', 'false')) NOT IN ('true', '1', 'yes')
  AND object_permission_id IS DISTINCT FROM 'eros-shared-tool-search-permissions';

-- Private assertions stay separated while each domain also sees shared
-- ontology, capabilities, verified results, and memory.
INSERT INTO "LiteLLM_ObjectPermissionTable" (
    object_permission_id, mcp_servers, vector_stores, mcp_access_groups,
    mcp_tool_permissions, agents, agent_access_groups, blocked_tools,
    models, mcp_toolsets, search_tools, mcp_tool_search_enabled
)
VALUES (
    'eros-context-work-permissions',
    ARRAY[
        'recallium', 'graphify', 'context7', 'playwright', 'duckduckgo',
        'gitlab', 'kubernetes', 'aws', 'terraform',
        'eros-context-shared', 'eros-context-work'
    ]::text[],
    NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, true
)
ON CONFLICT (object_permission_id) DO UPDATE
SET mcp_servers = EXCLUDED.mcp_servers,
    mcp_tool_search_enabled = true;

INSERT INTO "LiteLLM_ObjectPermissionTable" (
    object_permission_id, mcp_servers, vector_stores, mcp_access_groups,
    mcp_tool_permissions, agents, agent_access_groups, blocked_tools,
    models, mcp_toolsets, search_tools, mcp_tool_search_enabled
)
VALUES (
    'eros-context-personal-permissions',
    ARRAY[
        'recallium', 'graphify', 'context7', 'playwright', 'duckduckgo',
        'gitlab', 'kubernetes', 'aws', 'terraform',
        'eros-context-shared', 'eros-context-personal'
    ]::text[],
    NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, true
)
ON CONFLICT (object_permission_id) DO UPDATE
SET mcp_servers = EXCLUDED.mcp_servers,
    mcp_tool_search_enabled = true;

UPDATE "LiteLLM_VerificationToken"
SET object_permission_id = 'eros-context-work-permissions',
    metadata = jsonb_set(
        jsonb_set(coalesce(metadata, '{}'::jsonb), '{trust_domain}', '"work"'::jsonb, true),
        '{consumer}', '"nyx"'::jsonb, true
    )
WHERE key_alias = 'eros-nyx-all-routing'
  AND EXISTS (
      SELECT 1 FROM "LiteLLM_ObjectPermissionTable"
      WHERE object_permission_id = 'eros-context-work-permissions'
  );

UPDATE "LiteLLM_VerificationToken"
SET object_permission_id = 'eros-context-personal-permissions',
    metadata = jsonb_set(
        jsonb_set(coalesce(metadata, '{}'::jsonb), '{trust_domain}', '"personal"'::jsonb, true),
        '{consumer}', to_jsonb(
            CASE
                WHEN key_alias LIKE 'eros-ghost-%' THEN 'ghost'
                WHEN key_alias LIKE 'eros-mbair-%' THEN 'mbair'
                WHEN key_alias LIKE 'eros-VNJTECMBCD-%' THEN 'VNJTECMBCD'
                ELSE 'personal'
            END
        ), true
    )
WHERE (
        key_alias LIKE 'eros-ghost-all-routing%'
        OR key_alias = 'eros-mbair-all-routing'
        OR key_alias = 'eros-VNJTECMBCD-all-routing'
    )
  AND EXISTS (
      SELECT 1 FROM "LiteLLM_ObjectPermissionTable"
      WHERE object_permission_id = 'eros-context-personal-permissions'
  );

GRANT SELECT ON "LiteLLM_VerificationToken", "LiteLLM_SpendLogs",
    "LiteLLM_DailyUserSpend", "LiteLLM_DailyTagSpend" TO eros_context;

COMMIT;
