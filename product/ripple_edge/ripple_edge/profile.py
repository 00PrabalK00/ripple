from pathlib import Path
import yaml
from .contracts import Profile

def load_profile(path):
    return Profile.model_validate(yaml.safe_load(Path(path).read_text()))
