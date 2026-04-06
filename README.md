# 1C OData MCP Server

MCP server that wraps common 1C Управление предприятием standard OData read operations via REST API.

## Tools

- `probe_odata` - validates connectivity and credentials
- `get_odata` - performs GET for a specific OData resource
- `list_entity_sets` - returns entity set names from the OData service document

## Requirements

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/)

## Run locally

Clone or download this repo

```bash
uv --directory {PATH_TO_FOLDER} run python server.py
```

Environment variables (or pass values directly in tool arguments):

- `ODATA_HOST`
- `ODATA_USER`
- `ODATA_PASS`

## Install in Claude Desktop

1. Open Claude Desktop MCP config.
2. Copy the content from `claude-desktop.example.json`.
3. Merge the `mcpServers.1c-odata` section into your config.
4. Restart Claude Desktop.

After restart, Claude agents will be able to call:

- `probe_odata`
- `get_odata`
- `list_entity_sets`
