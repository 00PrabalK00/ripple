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
    parser.add_argument('--rosscope-binary',type=Path,help='Operator-installed read-only RosScope bridge')
    args=parser.parse_args();profile=load_profile(args.profile)
    os.environ['ROS_DOMAIN_ID']=str(profile.domain_id)
    import rclpy
    from .observer import Observer
    rclpy.init();node=Observer(profile);started=time.monotonic();written=0
    reader=None
    if args.rosscope_binary:
        from .rosscope import RosScopeReader
        reader=RosScopeReader(args.rosscope_binary.resolve(),profile.domain_id)
        reader.start()
    try:
        while not args.duration or time.monotonic()-started<args.duration:
            rclpy.spin_once(node,timeout_sec=.1)
            if time.monotonic()-written>=1:
                path=Path(args.snapshot_file);temp=path.with_suffix('.tmp')
                snapshot=node.snapshot()
                if reader:snapshot['observations']['rosscope']=reader.snapshot().model_dump()
                temp.write_text(json.dumps(snapshot,allow_nan=False,indent=2)+'\n');temp.replace(path)
                written=time.monotonic()
    finally:
        if reader:reader.close()
        node.destroy_node();rclpy.shutdown()

if __name__=='__main__':main()
