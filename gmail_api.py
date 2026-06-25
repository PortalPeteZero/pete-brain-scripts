"""gmail_api.py — importable alias for gmail-api.py.

The canonical helper is hyphenated (gmail-api.py) and can't be imported by name (`from gmail_api
import GmailAPI` raises). This underscore alias loads the canonical file by path and re-exports its
public surface, so the documented `from gmail_api import GmailAPI` works everywhere (register #19).
"""
import importlib.util as _ilu, os as _os

_impl_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "gmail-api.py")
_spec = _ilu.spec_from_file_location("_gmail_api_impl", _impl_path)
_impl = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_impl)

# re-export every public name from the canonical module (GmailAPI + any helpers)
for _k, _v in vars(_impl).items():
    if not _k.startswith("_"):
        globals()[_k] = _v
