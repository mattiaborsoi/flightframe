# Stand-in for IDF's dynamic proto loader. The vendored *_pb2 modules use
# flat "import constants_pb2" imports, so this directory itself must be on
# sys.path before they load.
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
import constants_pb2, sec0_pb2, sec1_pb2, sec2_pb2, session_pb2            # noqa: E401,F401
import wifi_constants_pb2, wifi_config_pb2, wifi_scan_pb2, wifi_ctrl_pb2   # noqa: E401,F401
