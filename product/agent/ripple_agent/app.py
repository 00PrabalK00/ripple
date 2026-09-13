"""Ripple Agent: the always-on process for one robot.

Edge (ROS, policy, executors) + GLM control plane + Ambiguous channel + local dashboard.
Run with product/scripts/ripple_agent.sh.
"""
import argparse
import asyncio
import json
import os
import signal
from pathlib import Path


def load_env(path):
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key, value = line.split('=', 1)
            os.environ.setdefault(key.strip(), value.strip())


async def serve(args):
    import rclpy
    import uvicorn
    from ripple_edge.profile import load_profile
    from ripple_edge.runtime import EdgeRuntime
    from .channels import Ambiguous
    from .llm import GLM
    from .orchestrator import Orchestrator
    from .web import build_app

    root = args.root.resolve()
    load_env(args.env or root / '.env')
    if args.site:
        # One file per robot, written by `ripple setup`: the robot profile plus people and channels.
        from ripple_edge.contracts import Profile
        site = json.loads(args.site.read_text())
        profile, config = Profile.model_validate(site['profile']), site.get('agent') or {}
    else:
        if not (args.profile and args.config):
            raise SystemExit('Give --site ripple.json, or both --profile and --config')
        profile, config = load_profile(args.profile), json.loads(args.config.read_text())
    operators = {op['id']: op['name'] for op in config.get('operators', [])}
    os.environ['ROS_DOMAIN_ID'] = str(profile.domain_id)
    rclpy.init()
    edge = EdgeRuntime(profile, root, os.environ['DATABASE_URL'], operators, args.rosscope_binary)
    await edge.start()
    llm = GLM()
    amb_cfg = config.get('ambiguous') or {}
    orch = Orchestrator(edge, llm, escalation=('ambiguous', amb_cfg['escalation_channel_id']) if amb_cfg else None)
    orch.loop = asyncio.get_running_loop()
    await orch.restore()
    tasks = []
    if amb_cfg and not args.no_ambiguous:
        amb = Ambiguous(root, amb_cfg['user_id'], amb_cfg['workspace_id'],
                        {op['id']: op['name'] for op in config['operators'] if op.get('via') == 'ambiguous'},
                        amb_cfg['escalation_channel_id'], orch.submit, lambda t: orch.note('system', t, persist=False))
        if await amb.verify():
            orch.channels['ambiguous'] = amb
            tasks.append(asyncio.ensure_future(amb.listen()))
            # Workspace tools (reports, tasks, email, chat). Separate journal connection: a workspace
            # failure must never block robot recovery claims.
            from ripple_edge.journal import ActionJournal
            from .communications import Contacts
            from .workspace import AmbiguousCLI, WorkspaceTools
            comms_path = root / 'product/config/communications.json'
            comms = json.loads(comms_path.read_text()) if comms_path.exists() else {}
            contacts = Contacts(frozenset(x.lower() for x in comms.get('email_recipients', [])),
                                frozenset([amb_cfg['escalation_channel_id'], *comms.get('chat_channels', [])]))
            operator_ids = set(operators)

            def authorize(tool, payload):
                return contacts.authorize(tool, payload) or (
                    tool == 'workspace_task_create' and str(payload.get('assignee_id')) in operator_ids)
            orch.workspace = WorkspaceTools(AmbiguousCLI(root, amb_cfg['user_id'], amb_cfg['workspace_id']),
                                            ActionJournal(os.environ['DATABASE_URL'], root), authorize)
    server = uvicorn.Server(uvicorn.Config(build_app(orch, edge, args.port, args.test_api), host='127.0.0.1', port=args.port,
                                           log_level='warning', lifespan='off'))
    orch.note('system', f"Ripple is online for {profile.robot}: GLM {llm.model} is the control plane; "
                        f"{'Ambiguous connected' if 'ambiguous' in orch.channels else 'Ambiguous off'}; "
                        f"dashboard at http://127.0.0.1:{args.port}")
    tasks += [asyncio.ensure_future(orch.run()), asyncio.ensure_future(server.serve())]
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    server.should_exit = True
    for task in tasks:
        task.cancel()
    await edge.close()
    await llm.close()
    rclpy.shutdown()


def main():
    parser = argparse.ArgumentParser(description='Ripple Agent')
    parser.add_argument('--root', type=Path, required=True, help='Ripple checkout (database bridge, Ambiguous credential)')
    parser.add_argument('--site', type=Path, help='ripple.json from `ripple setup` (profile, operators and channels)')
    parser.add_argument('--profile', type=Path)
    parser.add_argument('--config', type=Path, help='Host-owned operators and channels')
    parser.add_argument('--env', type=Path)
    parser.add_argument('--port', type=int, default=8060)
    parser.add_argument('--rosscope-binary', type=Path)
    parser.add_argument('--no-ambiguous', action='store_true')
    parser.add_argument('--test-api', action='store_true', help='Enable localhost verification endpoints')
    asyncio.run(serve(parser.parse_args()))


if __name__ == '__main__':
    main()
