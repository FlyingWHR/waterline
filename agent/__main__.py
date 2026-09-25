import sys

from agent.cli import main

sys.stdout.reconfigure(line_buffering=True)  # keep our lines in order with the profiler's stderr
sys.exit(main())
