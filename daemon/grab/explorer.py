"""File Explorer -> the REAL path of the selected item.

This is CLAUDE.md's clean case: automatic, any file, original bytes, all three
at once, because Explorer will just tell us the path.

Implementation note — this uses the Shell COM automation object, not UI
Automation, and that's deliberate. Shell.Application hands back the true full
path (`item.Path`). UI Automation only exposes the list item's *display* name,
which silently drops the extension when "hide known file types" is on, and never
yields a real path at all — you'd be reconstructing one from the address bar and
hoping. Same dependency either way (both ride on pywin32), better answer. If you
want the UIA version instead, say so.

If this comes up empty (a Save dialog, a shell folder with no filesystem path),
the router falls through to the clipboard strategy — Ctrl+C in Explorer puts
CF_HDROP with the real paths on the clipboard, so the clean case still lands.
"""
import pythoncom
import win32com.client


def selected_paths(hwnd):
    """Real full paths of the items selected in this Explorer window ([] if none)."""
    try:
        pythoncom.CoInitialize()          # harmless if already initialised
    except Exception:
        pass
    try:
        shell = win32com.client.Dispatch("Shell.Application")
        for win in shell.Windows():
            try:
                if int(win.HWND) != int(hwnd):
                    continue
                return [str(i.Path) for i in win.Document.SelectedItems()]
            except Exception:
                continue                  # some shell windows refuse to answer
    except Exception:
        pass
    return []
