# n8n ↔ FastAPI endpoint map

Last updated: 2025-12-30

This document maps each **kumiho-n8n** node operation to the corresponding **kumiho-FastAPI** route and the underlying Python SDK calls.

## Assumptions / scope

- FastAPI mounts routers with these prefixes (see `kumiho-FastAPI/app/main.py`):
  - `/api/v1/projects`, `/api/v1/spaces`, `/api/v1/items`, `/api/v1/revisions`, `/api/v1/artifacts`, `/api/v1/bundles`, `/api/v1/graph`, `/api/v1/edges`, `/api/v1/resolve`, `/api/v1/attributes`, `/api/v1/events`, `/api/v1/mcp`, `/api/v1/tenant`
- n8n calls are made via `kumihoRequest()` (helper), so this doc only maps **method/path/params/body**.
- Some n8n nodes include backwards-compat fallbacks for older deployments; those legacy endpoints may not exist in the current FastAPI.

## Node operations → FastAPI routes

| n8n node | operation | n8n request | key params/body | FastAPI route | FastAPI handler | Python SDK calls | notes |
|---|---|---|---|---|---|---|---|
| Kumiho Project | Create or Get | `POST /api/v1/projects` | body: `{ name, description }` | `POST /api/v1/projects` | `create_project()` in `kumiho-FastAPI/app/core/projects.py` | `kumiho.create_project(name, description)` | n8n falls back to `GET /api/v1/projects/{name}` on 409 (and sometimes 5xx). |
| Kumiho Project | Get | `GET /api/v1/projects/{name}` | path: `{name}` | `GET /api/v1/projects/{name}` | `get_project()` in `kumiho-FastAPI/app/core/projects.py` | `kumiho.get_project(name)` |  |
| Kumiho Project | List | `GET /api/v1/projects` |  | `GET /api/v1/projects` | `list_projects()` in `kumiho-FastAPI/app/core/projects.py` | `kumiho.get_projects()` |  |
| Kumiho Project | Delete | `DELETE /api/v1/projects/{name}` | qs: `force` | `DELETE /api/v1/projects/{name}` | `delete_project()` in `kumiho-FastAPI/app/core/projects.py` | `kumiho.get_project(name)` → `project.delete(force=force)` |  |
| Kumiho Space | Create or Get | `POST /api/v1/spaces` | body: `{ parent_path, name }` | `POST /api/v1/spaces` | `create_space()` in `kumiho-FastAPI/app/core/spaces.py` | `kumiho.get_project(project)` → ensure root + parents → `space.create_space(name)` | n8n treats create as idempotent and falls back to `GET /api/v1/spaces/by-path?path=...` on 409/5xx. |
| Kumiho Space | Get | `GET /api/v1/spaces/by-path` | qs: `path` | `GET /api/v1/spaces/by-path` | `get_space()` in `kumiho-FastAPI/app/core/spaces.py` | `kumiho.get_project(project)` → `project.get_space(path)` | n8n also tries legacy `GET /api/v1/spaces/path?space_path=...` for older deployments. |
| Kumiho Space | List | `GET /api/v1/spaces` | qs: `parent_path`, `recursive` | `GET /api/v1/spaces` | `list_spaces()` in `kumiho-FastAPI/app/core/spaces.py` | `kumiho.get_project(project)` → `project.get_space(parent_path)` → `space.get_spaces(recursive)` | n8n sets `parent_path=/{projectName}`. |
| Kumiho Space | Delete | `DELETE /api/v1/spaces/by-path` | qs: `path`, `force` | `DELETE /api/v1/spaces/by-path` | `delete_space()` in `kumiho-FastAPI/app/core/spaces.py` | `kumiho.get_project(project)` → `project.get_space(path)` → `space.delete(force)` |  |
| Kumiho Item | Create or Get | `POST /api/v1/items` | body: `{ space_path, item_name, kind, metadata }` | `POST /api/v1/items` | `create_item()` in `kumiho-FastAPI/app/core/items.py` | `kumiho.get_project(project)` → `project.create_item(item_name, kind, parent_path=space_path, metadata=...)` | n8n falls back to `GET /api/v1/items/by-path` on 409/5xx. |
| Kumiho Item | Read (Kref) | `GET /api/v1/items/by-kref` | qs: `kref` | `GET /api/v1/items/by-kref` | `get_item_by_kref()` in `kumiho-FastAPI/app/core/items.py` | `kumiho.get_item(kref)` |  |
| Kumiho Item | Search | `GET /api/v1/items/search` | qs: `context_filter`, `name_filter`, `kind_filter` | `GET /api/v1/items/search` | `search_items()` in `kumiho-FastAPI/app/core/items.py` | `kumiho.item_search(context_filter, name_filter, kind_filter)` | also used by the standalone “Kumiho Search” node. |
| Kumiho Item | Set Attribute | `POST /api/v1/attributes` | body: `{ kref, key, value }` | `POST /api/v1/attributes` | `set_attribute()` in `kumiho-FastAPI/app/core/attributes.py` | `kumiho.set_attribute(kref, key, value)` |  |
| Kumiho Item | Update Metadata | `PATCH /api/v1/items/by-kref` | qs: `kref`, body: `{ metadata }` | `PATCH /api/v1/items/by-kref` | `update_item_metadata()` in `kumiho-FastAPI/app/core/items.py` | `kumiho.get_item(kref)` → `item.set_metadata(metadata)` |  |
| Kumiho Item | Deprecate | `POST /api/v1/items/deprecate` | qs: `kref`, `deprecated` | `POST /api/v1/items/deprecate` | `deprecate_item()` in `kumiho-FastAPI/app/core/items.py` | `kumiho.get_item(kref)` → `item.set_deprecated(deprecated)` |  |
| Kumiho Item | Delete | `DELETE /api/v1/items/by-kref` | qs: `kref` | `DELETE /api/v1/items/by-kref` | `delete_item()` in `kumiho-FastAPI/app/core/items.py` | `kumiho.get_item(kref)` → `item.delete(force=force)` | n8n does not pass `force`. |
| Kumiho Revision | Create or Get | `POST /api/v1/revisions` | body: `{ item_kref, metadata, number? }` | `POST /api/v1/revisions` | `create_revision()` in `kumiho-FastAPI/app/core/revisions.py` | `kumiho.get_item(item_kref)` → `item.create_revision(metadata, number)` | n8n falls back to `GET /api/v1/revisions/by-kref` with `r=number` or `t=latest`. |
| Kumiho Revision | Read (Kref) | `GET /api/v1/revisions/by-kref` | qs: `kref`, optional `r`, optional `t` | `GET /api/v1/revisions/by-kref` | `get_revision()` in `kumiho-FastAPI/app/core/revisions.py` | `kumiho.get_revision(kref)` |  |
| Kumiho Revision | Get by Kref | `GET /api/v1/revisions/by-kref` | qs: `kref` | `GET /api/v1/revisions/by-kref` | `get_revision()` in `kumiho-FastAPI/app/core/revisions.py` | `kumiho.get_revision(kref)` | Dedicated op that does not use `r`/`t`. |
| Kumiho Revision | Get by Tag | `GET /api/v1/revisions/by-kref` | qs: `kref` (item kref), `t` | `GET /api/v1/revisions/by-kref` | `get_revision()` in `kumiho-FastAPI/app/core/revisions.py` | `kumiho.get_revision(kref?tag=...)` (via kref params resolution) | n8n defaults tag to `latest` if empty. |
| Kumiho Revision | Get as of Timestamp | `GET /api/v1/revisions/as-of` | qs: `item_kref`, `tag`, `time` | `GET /api/v1/revisions/as-of` | `get_revision_as_of()` in `kumiho-FastAPI/app/core/revisions.py` | `kumiho.get_item(kref)` → `item.get_revision_by_time(time, tag)` | Time-travel query: find revision that had tag at timestamp. Supports YYYYMMDDHHMM or ISO 8601. |
| Kumiho Revision | List Artifacts | `GET /api/v1/artifacts` | qs: `revision_kref`, optional `r` | `GET /api/v1/artifacts` | `list_artifacts()` in `kumiho-FastAPI/app/core/artifacts.py` | `kumiho.get_revision(revision_kref)` → `revision.get_artifacts()` | n8n uses the revision `kref` field as `revision_kref`. |
| Kumiho Revision | Update Metadata | `PATCH /api/v1/revisions/by-kref` | qs: `kref`, optional `r`, body: `{ metadata }` | `PATCH /api/v1/revisions/by-kref` | `update_revision_metadata()` in `kumiho-FastAPI/app/core/revisions.py` | `kumiho.get_revision(kref)` → `revision.set_metadata(metadata)` |  |
| Kumiho Revision | Tag | (moved) |  | n/a | n/a | n/a | Tag operations moved to the dedicated “Kumiho Tag” node. |
| Kumiho Revision | Untag | (moved) |  | n/a | n/a | n/a | Tag operations moved to the dedicated “Kumiho Tag” node. |
| Kumiho Revision | Deprecate | `POST /api/v1/revisions/deprecate` | qs: `kref`, optional `r`, `deprecated` | `POST /api/v1/revisions/deprecate` | `deprecate_revision()` in `kumiho-FastAPI/app/core/revisions.py` | `kumiho.get_revision(kref)` → `revision.set_deprecated(deprecated)` |  |
| Kumiho Revision | Delete | `DELETE /api/v1/revisions/by-kref` | qs: `kref`, optional `r` | `DELETE /api/v1/revisions/by-kref` | `delete_revision()` in `kumiho-FastAPI/app/core/revisions.py` | `kumiho.get_revision(kref)` → `revision.delete(force=force)` | n8n does not pass `force`. |
| Kumiho Artifact | Create or Get | `POST /api/v1/artifacts` | body: `{ revision_kref, name, location, metadata }` | `POST /api/v1/artifacts` | `create_artifact()` in `kumiho-FastAPI/app/core/artifacts.py` | `kumiho.get_revision(revision_kref)` → `revision.create_artifact(name, location, metadata)` | n8n falls back to `GET /api/v1/artifacts/by-kref?revision_kref=...&name=...` on 409/5xx. |
| Kumiho Artifact | Get by Location | `GET /api/v1/artifacts/by-location` | qs: `location` | `GET /api/v1/artifacts/by-location` | `get_artifacts_by_location()` in `kumiho-FastAPI/app/core/artifacts.py` | `kumiho.get_artifacts_by_location(location)` | Returns a list of artifacts; useful for reverse lookups. |
| Kumiho Artifact | Read | `GET /api/v1/artifacts/by-kref` | qs: `revision_kref`, `name` | `GET /api/v1/artifacts/by-kref` | `get_artifact()` in `kumiho-FastAPI/app/core/artifacts.py` | `kumiho.get_revision(rev)` → `revision.get_artifact(name)` | n8n no longer accepts direct artifact kref input. |
| Kumiho Artifact | Update Metadata | `PATCH /api/v1/artifacts/by-kref` | qs: `kref`, body: `{ metadata }` | `PATCH /api/v1/artifacts/by-kref` | `update_artifact_metadata()` in `kumiho-FastAPI/app/core/artifacts.py` | `kumiho.get_artifact(kref)` → `artifact.set_metadata(metadata)` |  |
| Kumiho Artifact | Deprecate | `POST /api/v1/artifacts/deprecate` | qs: `kref`, `deprecated` | `POST /api/v1/artifacts/deprecate` | `deprecate_artifact()` in `kumiho-FastAPI/app/core/artifacts.py` | `kumiho.get_artifact(kref)` → `artifact.set_deprecated(deprecated)` |  |
| Kumiho Artifact | Delete | `DELETE /api/v1/artifacts/by-kref` | qs: `kref` | `DELETE /api/v1/artifacts/by-kref` | `delete_artifact()` in `kumiho-FastAPI/app/core/artifacts.py` | `kumiho.get_artifact(kref)` → `artifact.delete(force=force)` | n8n does not pass `force`. |
| Kumiho Bundle | Create or Get | `POST /api/v1/bundles` | body: `{ space_path, bundle_name, metadata }` | `POST /api/v1/bundles` | `create_bundle()` in `kumiho-FastAPI/app/core/bundles.py` | `kumiho.get_project(project)` → `project.get_space(space_path)` → `space.create_bundle(bundle_name, metadata)` | n8n computes bundle kref and falls back to `GET /api/v1/bundles/by-kref`. |
| Kumiho Bundle | Get | `GET /api/v1/bundles/by-kref` | qs: `kref` | `GET /api/v1/bundles/by-kref` | `get_bundle()` in `kumiho-FastAPI/app/core/bundles.py` | `kumiho.get_bundle(kref)` |  |
| Kumiho Bundle | Add Member | `POST /api/v1/bundles/members/add` | body: `{ bundle_kref, item_kref, metadata }` | `POST /api/v1/bundles/members/add` | `add_bundle_member()` in `kumiho-FastAPI/app/core/bundles.py` | `kumiho.get_bundle(bundle_kref)` + `kumiho.get_item(item_kref)` → `bundle.add_member(item, metadata)` |  |
| Kumiho Bundle | Remove Member | `POST /api/v1/bundles/members/remove` | body: `{ bundle_kref, item_kref, metadata }` | `POST /api/v1/bundles/members/remove` | `remove_bundle_member()` in `kumiho-FastAPI/app/core/bundles.py` | `kumiho.get_bundle(bundle_kref)` + `kumiho.get_item(item_kref)` → `bundle.remove_member(item, metadata)` |  |
| Kumiho Bundle | List Members | `GET /api/v1/bundles/members` | qs: `bundle_kref`, optional `revision_number` | `GET /api/v1/bundles/members` | `list_bundle_members()` in `kumiho-FastAPI/app/core/bundles.py` | `kumiho.get_bundle(bundle_kref)` → `bundle.get_members(revision_number?)` |  |
| Kumiho Bundle | History | `GET /api/v1/bundles/history` | qs: `bundle_kref` | `GET /api/v1/bundles/history` | `get_bundle_history()` in `kumiho-FastAPI/app/core/bundles.py` | `kumiho.get_bundle(bundle_kref)` → `bundle.get_history()` |  |
| Kumiho Graph | Create Edge | `POST /api/v1/graph/edges` | body: `{ source_kref, target_kref, edge_type, metadata }` | `POST /api/v1/graph/edges` | `create_edge()` in `kumiho-FastAPI/app/core/graph.py` | `kumiho.get_revision(src)` + `kumiho.get_revision(tgt)` → `source.create_edge(target_revision, edge_type, metadata=...)` |  |
| Kumiho Graph | List Edges | `GET /api/v1/graph/edges` | qs: `revision_kref`, optional `edge_type`, `direction` | `GET /api/v1/graph/edges` | `list_edges()` in `kumiho-FastAPI/app/core/graph.py` | `kumiho.get_revision(revision_kref)` → `revision.get_edges(edge_type_filter, direction)` | `direction`: 0=OUTGOING, 1=INCOMING, 2=BOTH. |
| Kumiho Graph | Get Dependencies | `GET /api/v1/graph/dependencies` | qs: `revision_kref`, `max_depth`, optional `edge_types[]` | `GET /api/v1/graph/dependencies` | `get_dependencies()` in `kumiho-FastAPI/app/core/graph.py` | `kumiho.get_revision(rev)` → `revision.get_all_dependencies(edge_type_filter, max_depth)` |  |
| Kumiho Graph | Find Path | `GET /api/v1/graph/path` | qs: `source_kref`, `target_kref`, `max_depth`, optional `edge_types[]` | `GET /api/v1/graph/path` | `find_path()` in `kumiho-FastAPI/app/core/graph.py` | `source.find_path_to(target, all_paths=True, ...)` |  |
| Kumiho Graph | Analyze Impact | `GET /api/v1/graph/impact` | qs: `revision_kref`, `max_depth`, optional `edge_types[]` | `GET /api/v1/graph/impact` | `analyze_impact()` in `kumiho-FastAPI/app/core/graph.py` | `revision.analyze_impact(edge_type_filter, max_depth)` |  |
| Kumiho Search | Search | `GET /api/v1/items/search` | qs: `context_filter`, `name_filter`, `kind_filter` | `GET /api/v1/items/search` | `search_items()` in `kumiho-FastAPI/app/core/items.py` | `kumiho.item_search(...)` | Dedicated thin node that only does item search. |
| Kumiho Resolve Kref | Resolve | `GET /api/v1/resolve` | qs: `kref`, optional `r`, `t`, `a` | `GET /api/v1/resolve` | `resolve_kref()` in `kumiho-FastAPI/app/core/resolve.py` | `kumiho.resolve(kref)` | FastAPI also offers `/api/v1/resolve/revision` (not used by n8n). |
| Kumiho Event Stream (Trigger) | Poll | `GET /api/v1/events/poll` | qs: `routing_key_filter`, `kref_filter`, optional `cursor`, `max_events` | `GET /api/v1/events/poll` | `poll_events()` in `kumiho-FastAPI/app/core/events.py` | `client.event_stream(..., timeout=timeout_seconds)` (iterated) | Trigger stores cursor in workflow static data. Node “Stream Name” param is currently unused. |
| Kumiho MCP Server | Start/Stop/Status | (no HTTP calls) |  | n/a | n/a | n/a | Node currently returns `status: not_implemented`. FastAPI has MCP endpoints under `/api/v1/mcp/*`. |
| Kumiho Tag | Set Tag | `POST /api/v1/revisions/tags` | qs: `kref`, optional `r`, body: `{ tag }` | `POST /api/v1/revisions/tags` | `tag_revision()` in `kumiho-FastAPI/app/core/revisions.py` | `kumiho.get_revision(kref)` → `revision.tag(tag)` | Tag management moved out of KumihoRevision node. |
| Kumiho Tag | Remove Tag | `DELETE /api/v1/revisions/tags` | qs: `kref`, `tag`, optional `r` | `DELETE /api/v1/revisions/tags` | `untag_revision()` in `kumiho-FastAPI/app/core/revisions.py` | `kumiho.get_revision(kref)` → `revision.untag(tag)` |  |
| Kumiho Tag | List Tags | `GET /api/v1/revisions/by-kref` | qs: `kref`, optional `r` | `GET /api/v1/revisions/by-kref` | `get_revision()` in `kumiho-FastAPI/app/core/revisions.py` | `kumiho.get_revision(kref)` | Returns the revision response; node emits just `{ tags: [...] }`. |
| Kumiho Tag | Has a Tag | `GET /api/v1/revisions/tags/check` | qs: `kref`, `tag`, optional `r` | `GET /api/v1/revisions/tags/check` | `has_tag()` in `kumiho-FastAPI/app/core/revisions.py` | `kumiho.get_revision(kref)` → `revision.has_tag(tag)` |  |
| Kumiho Tag | Was Tagged | `GET /api/v1/revisions/tags/history` | qs: `kref`, `tag`, optional `r` | `GET /api/v1/revisions/tags/history` | `was_tagged()` in `kumiho-FastAPI/app/core/revisions.py` | `kumiho.get_revision(kref)` → `revision.was_tagged(tag)` |  |

## Related FastAPI endpoints not currently called by n8n nodes

These are present in FastAPI, but no current n8n node uses them directly.

| FastAPI route | handler | purpose |
|---|---|---|
| `GET /api/v1/revisions` | `list_revisions()` in `kumiho-FastAPI/app/core/revisions.py` | List all revisions for an item (by `item_kref`). |
| `GET /api/v1/revisions/latest` | `get_latest_revision()` in `kumiho-FastAPI/app/core/revisions.py` | Convenience “latest revision” endpoint. |
| `GET /api/v1/revisions/peek` | `peek_next_revision()` in `kumiho-FastAPI/app/core/revisions.py` | Peek next revision number. |
| `GET /api/v1/revisions/tags/check` | `has_tag()` in `kumiho-FastAPI/app/core/revisions.py` | Check if revision has a tag. |
| `GET /api/v1/revisions/tags/history` | `was_tagged()` in `kumiho-FastAPI/app/core/revisions.py` | Check if revision was ever tagged. |
| `GET /api/v1/edges` | `list_edges()` in `kumiho-FastAPI/app/core/edges.py` | List edges for a revision (separate from `/api/v1/graph/*`). |
| `POST /api/v1/edges` | `create_edge()` in `kumiho-FastAPI/app/core/edges.py` | Create edge (alternate to `/api/v1/graph/edges`). |
| `DELETE /api/v1/edges` | `delete_edge()` in `kumiho-FastAPI/app/core/edges.py` | Delete edge. |
| `GET /api/v1/events/stream` | `stream_events()` in `kumiho-FastAPI/app/core/events.py` | SSE event stream endpoint. |
| `GET /api/v1/tenant/bootstrap` | `bootstrap()` in `kumiho-FastAPI/app/core/tenant.py` | Tenant config bootstrap. |
| `GET /api/v1/tenant/whoami` | `whoami()` in `kumiho-FastAPI/app/core/tenant.py` | Auth identity / membership check. |
| `GET /api/v1/tenant/usage` | `get_tenant_usage()` in `kumiho-FastAPI/app/core/tenant.py` | Tenant usage / limits. |
| `GET|POST /api/v1/mcp/tools` | `mcp_tools_proxy()` in `kumiho-FastAPI/app/core/mcp.py` | StreamableHTTP MCP proxy endpoint. |
| `GET /api/v1/mcp/list` | `list_tools()` in `kumiho-FastAPI/app/core/mcp.py` | List tools (legacy REST). |
| `POST /api/v1/mcp/invoke` | `call_tool()` in `kumiho-FastAPI/app/core/mcp.py` | Invoke tool (legacy REST). |

## Legacy/backwards-compat notes

- Some n8n nodes include fallback calls intended for older deployments (example: `GET /api/v1/spaces/path?space_path=...`). Those legacy routes are not present in the current FastAPI router set.
