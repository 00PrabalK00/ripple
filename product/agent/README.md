# Ripple workspace tools

`ripple_agent.workspace.WorkspaceTools` provides 16 typed tools for Ambiguous:

| Work | Tools |
| --- | --- |
| Tasks | List, read, create, update status |
| Reports | Create, read, update report documents |
| Sheets | List, create, read, append rows |
| Email | Search, read, draft, send |
| Chat | Send a channel message or thread reply |

The adapter uses the authenticated Ambiguous **CLI**, pinned to 0.9.0, from the
product checkout. No additional Ambiguous MCP server is installed. The existing
Codex event listener remains separate and must not be duplicated.

`definitions()` returns model function-tool schemas. Pass model calls to
`execute(name, arguments, request_id)` from the orchestrator. Inputs cannot supply
CLI commands, a workspace identity, budgets, or contact permissions. Identity and
workspace are verified before operations; body text travels as JSON on stdin, not
shell interpolation. Results are untrusted evidence for the agent, never robot
commands.

Writes use the existing PostgreSQL/Drizzle action journal. Every write is claimed
before invocation. A repeated request ID returns its earlier record instead of
performing another write. An uncertain result remains claimed and requires
readback/reconciliation; the tool does not retry and risk duplicate emails or rows.
Tasks, reports, drafts and sheets are read back before being called verified.
Reports support paragraph text; rich Markdown that changes during server conversion
may require manual verification. Task writes handle the API's nested `task` envelope.

The host loads `product/config/communications.json`; it is not a model tool input.
Prabal explicitly authorized mail to `pk3391@nyu.edu`. Other recipients, arbitrary
chat channels, and assignments to other people require additional authorization.
Email drafts do not send mail. A sent email is verified against the provider's
`delivery_status`; that does not prove arrival in the recipient's inbox. No
scheduled/bulk mail or automatic contact discovery is enabled.

## Running a tool

Install the product Python requirements and Node dependencies. Export `DATABASE_URL`
through the normal local secret environment. From the product checkout:

```bash
export PYTHONPATH="$PWD/product/agent:$PWD/product/ripple_edge:$PYTHONPATH"
printf '%s' '{"q":"Ripple","limit":10}' | python -m ripple_agent.workspace_cli \
  --root "$PWD" --config product/config/communications.json \
  --tool workspace_tasks_list --request-id task-list-example-1
```

For a model host, construct `AmbiguousCLI`, `WorkspaceTools`, and the shared journal
once; run blocking calls in a worker thread. `Contacts.authorize` is the configured
recipient policy. The always-on reasoning/recovery loop remains a separate,
uncompleted blueprint milestone; these tools are ready for that loop to call.

Live acceptance scripts are in `product/scripts/probe_workspace.py` and
`verify_workspace_and_send.py`. They use stable request IDs and saved artifact IDs
so an uncertain response does not cause a duplicate create/send. They are local
development probes, with the local secret-file path explicitly supplied in code.
Do not change their request IDs to bypass an uncertain send.

## Verified live on 12 September 2026

Created and read back a report, a verification sheet with appended rows, an
agent-owned task (then marked it done), and an email draft. Sent one engineering
update to the explicitly authorized operator address and verified the provider's
`sent` status. Evidence is in `product/evidence/workspace-tools-live.json` and
`workspace-verified-and-email.json`. Chat delivery was separately verified by the
two-DM session test; the new typed chat-send wrapper has not sent another message.
