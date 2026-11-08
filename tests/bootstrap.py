import sys
import types
from pathlib import Path

package = types.ModuleType("astrbot_plugin_60s")
package.__path__ = [str(Path(__file__).resolve().parents[1])]
sys.modules.setdefault("astrbot_plugin_60s", package)
