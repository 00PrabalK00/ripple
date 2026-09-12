"""Host entry point for the typed workspace tools; JSON input and output."""
import argparse
import json
import os
from pathlib import Path
import sys
from .workspace import AmbiguousCLI,WorkspaceTools
from .communications import Contacts

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,required=True,help='Ripple checkout containing the saved Ambiguous CLI credential')
    parser.add_argument('--config',type=Path,required=True,help='Host-owned identity and contact policy')
    parser.add_argument('--tool',required=True)
    parser.add_argument('--request-id',required=True,help='Stable ID reused when reconciling the same operation')
    args=parser.parse_args()
    config=json.loads(args.config.read_text())
    cli=AmbiguousCLI(args.root,config['user_id'],config['workspace_id'])
    policy=Contacts(frozenset(x.lower() for x in config.get('email_recipients',[])),
        frozenset(config.get('chat_channels',[])))
    # Shared local persistence transport; importing it creates no ROS access.
    from ripple_edge.journal import ActionJournal
    journal=ActionJournal(os.environ['DATABASE_URL'],args.root)
    try:
        tools=WorkspaceTools(cli,journal,policy.authorize)
        print(json.dumps(tools.execute(args.tool,json.load(sys.stdin),args.request_id),allow_nan=False))
    finally:journal.close()

if __name__=='__main__':main()
