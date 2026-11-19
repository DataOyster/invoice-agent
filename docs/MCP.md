# MCP server: ask Claude Desktop about the invoice data

**In one sentence:** MCP (Model Context Protocol) is a standard plug that gives an AI client a
set of tools; this server plugs eight read-only tools over our SQLite database into Claude
Desktop, so you can ask "which suppliers raised prices?" in plain language.

```
Claude Desktop --(MCP over stdin/stdout)--> invoice_agent.mcp_server --> ReadOnlyTools
                                                                      --> SQLite, opened read-only
```

The same `Repository` functions serve the agent, the pipeline and this server: one data layer,
several entry points.

## Tools

| Tool | Answers |
|---|---|
| `list_suppliers` | Who are our suppliers (and which one is only a competitor quoting prices)? |
| `supplier_products` | What does supplier S03 sell, and in which unit is the price agreed? |
| `price_increases` | Which suppliers raised prices between two dates? Each row cites the two price-list entries. |
| `price_history` | How has the agreed price of P101 changed over time? |
| `vat_rate` | Which VAT rate applies to food delivered on 2026-04-02? (The delivery date decides.) |
| `search_contract` | What does supplier S01's contract say about price increases? Top 3 clauses, quoted word for word (BM25). |
| `get_invoice` | Show archived invoice INV-H004. Its text is labelled as data, with an injection warning if the tripwire fires. |
| `list_flags` | Which flags are waiting for review, with evidence, explanation and verified quote? |

## Safety, and how it is tested

- **Every tool is read-only.** Each is annotated `read_only_hint=True`, and none can pay, approve,
  reject, decide or edit.
- **The database refuses writes.** It is opened in SQLite read-only mode (`mode=ro`), so even a buggy
  tool could not change prices, contracts, invoices, flags or the audit log.
- **No flag writing over MCP.** Flags are proposed by the pipeline and decided by a named person in
  the review UI. `Repository.decide_flag` is unreachable from this package; an AST check in the
  guardrail suite enforces this.
- **Invoice text is labelled.** Text returned from an invoice is marked as supplier data, and
  `get_invoice` runs the same injection tripwire as the pipeline.
- **Tests:**
  - guardrail case `mcp_server_cannot_write` (severity "never");
  - `tests/unit/test_mcp_server.py`, which covers annotations, write refusal, the answers, and a
    real call through the server object.

## Set it up

1. Build the demo database. This is offline: it replays the cached live runs, so no API key is needed.
   ```
   python -m invoice_agent.demo_db
   ```
   This gives `data/invoice_agent.db`: reference data, the 30 hard-split invoices and 15
   pending flags (the 10 injected anomalies, some on several lines).
2. In Claude Desktop, open **Settings → Desktop app → Extensions → Local MCP servers → Edit config**
   (older versions: **Settings → Developer → Edit Config**). This opens
   `claude_desktop_config.json`. If the file already has other keys, add `mcpServers` next to
   them; do not replace them. Add the server, with absolute paths to your clone (on
   macOS or Linux the interpreter is `.venv/bin/python`):
   ```json
   {
     "mcpServers": {
       "invoice-agent": {
         "command": "C:\\path\\to\\invoice-agent\\.venv\\Scripts\\python.exe",
         "args": ["-m", "invoice_agent.mcp_server", "--db",
                  "C:\\path\\to\\invoice-agent\\data\\invoice_agent.db"]
       }
     }
   }
   ```
3. **Quit Claude Desktop completely** (tray icon → Quit; closing the window is not enough) and
   start it again. Edit the file while the app is closed: a running app can rewrite the file and
   drop the new block. The server then shows under Local MCP servers, and its tools are
   available in a new chat.

## Questions to try

- "Which suppliers raised prices in July 2026? By how much?"
- "Did any supplier raise prices this month?" The honest answer is no: the synthetic price
  changes all start on 1 July 2026.
- "What does Kallgrund Fiskhandel's contract say about changing prices?"
- "Which flags are pending review? Explain the one on INV-H004 and quote the contract."
- "What VAT applies to food delivered on 31 March 2026, and on 1 April 2026?"
- "Approve flag 1." The server has no tool for this, and Claude should say a person must do it
  in the review UI.
