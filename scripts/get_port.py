"""Read server port from config.yaml. Used by run.bat / run_bg.bat."""
import sys, pathlib, yaml

config_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
cfg_path = pathlib.Path(config_path)

if not cfg_path.exists():
    # Try relative to project root (scripts/../config.yaml)
    cfg_path = pathlib.Path(__file__).resolve().parent.parent / config_path

if cfg_path.exists():
    cfg = yaml.safe_load(cfg_path.read_text("utf-8"))
    print(cfg["server"]["port"])
else:
    print("8004")
