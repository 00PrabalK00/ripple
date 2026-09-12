import argparse
import json
import os
from pathlib import Path
import time
from .profile import load_profile

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--profile',required=True)
    parser.add_argument('--snapshot-file',required=True,help='Development observation artifact; not production incident storage')
    parser.add_argument('--duration',type=float,default=0)
    args=parser.parse_args();profile=load_profile(args.profile)
    os.environ['ROS_DOMAIN_ID']=str(profile.domain_id)
    import rclpy
    from .observer import Observer
    rclpy.init();node=Observer(profile);started=time.monotonic();written=0
    try:
        while not args.duration or time.monotonic()-started<args.duration:
            rclpy.spin_once(node,timeout_sec=.1)
            if time.monotonic()-written>=1:
                path=Path(args.snapshot_file);temp=path.with_suffix('.tmp')
                temp.write_text(json.dumps(node.snapshot(),allow_nan=False,indent=2)+'\n');temp.replace(path)
                written=time.monotonic()
    finally:node.destroy_node();rclpy.shutdown()

if __name__=='__main__':main()
