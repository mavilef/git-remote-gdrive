import logging
import sys

# Move your configuration into a single call to basicConfig
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s][%(name)s][%(levelname)s] %(message)s",
    stream=sys.stderr,
    force=True 
)
